import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..config import settings
from ..db import AlertRecord, IntelligenceEvent, SessionLocal
from .telegram import localized

log = logging.getLogger("thesisguard.intelligence")

BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FED_MONETARY_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
COINDESK_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
COINMARKETCAL_URL = "https://api.coinmarketcal.com/v2/events"

GDELT_TOPICS = {
    "geopolitics": '("Iran" OR "Iranian" OR "US Iran" OR "Israel Iran" OR "Strait of Hormuz" OR "Middle East war")',
    "energy": '(oil OR crude OR OPEC OR "Strait of Hormuz" OR refinery OR tanker)',
    "crypto": '(bitcoin OR ethereum OR crypto OR stablecoin OR memecoin OR "meme coin" OR token OR blockchain)',
    "macro": '("Federal Reserve" OR inflation OR CPI OR PPI OR payrolls OR "Treasury yields" OR recession)',
}


@dataclass(slots=True)
class NormalizedEvent:
    canonical_key: str
    event_kind: str
    status: str
    title: str
    summary: str
    source_name: str
    source_url: str
    source_tier: int
    confidence: str
    importance: float
    published_at: datetime | None = None
    event_time: datetime | None = None
    affected_assets: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _canonical_key(prefix: str, stable_value: str) -> str:
    digest = hashlib.sha256(stable_value.strip().encode("utf-8")).hexdigest()[:40]
    return f"{prefix}:{digest}"


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", value).strip()


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def source_tier(url: str) -> int:
    domain = _domain(url)
    official = (
        "bls.gov", "federalreserve.gov", "sec.gov", "cftc.gov", "treasury.gov",
        "whitehouse.gov", "state.gov", "defense.gov", "eia.gov",
    )
    tier2 = ("reuters.com", "apnews.com", "bloomberg.com", "wsj.com", "ft.com")
    tier3 = ("coindesk.com", "theblock.co", "decrypt.co", "cointelegraph.com", "forbes.com")

    if any(domain == d or domain.endswith("." + d) for d in official):
        return 1
    if any(domain == d or domain.endswith("." + d) for d in tier2):
        return 2
    if any(domain == d or domain.endswith("." + d) for d in tier3):
        return 3
    return 4


def confidence_for_tier(tier: int) -> str:
    if tier == 1:
        return "high"
    if tier in (2, 3):
        return "medium"
    return "low"


def classify_event(title: str, summary: str = "", *, scheduled: bool = False) -> tuple[float, list[str], list[str]]:
    text = f"{title} {summary}".lower()
    importance = 4.0
    themes: set[str] = set()
    assets: set[str] = set()

    macro_high = {
        "consumer price index": 10.0,
        "cpi": 9.5,
        "producer price index": 9.0,
        "ppi": 8.5,
        "employment situation": 10.0,
        "nonfarm payroll": 10.0,
        "payrolls": 9.0,
        "fomc": 10.0,
        "federal funds": 9.5,
        "interest rate decision": 10.0,
    }
    for term, score in macro_high.items():
        if term in text:
            importance = max(importance, score)
            themes.update({"macro", "rates"})
            assets.update({"BTCUSDT", "ETHUSDT", "XAUUSDT"})

    if any(x in text for x in ("federal reserve", "powell", "treasury yield", "real yield")):
        importance = max(importance, 7.5)
        themes.update({"macro", "rates"})
        assets.update({"BTCUSDT", "ETHUSDT", "XAUUSDT"})

    if any(x in text for x in ("iran", "hormuz", "middle east war", "missile", "airstrike", "ceasefire")):
        importance = max(importance, 8.0)
        themes.update({"geopolitics", "energy"})
        assets.update({"BTCUSDT", "ETHUSDT", "XAUUSDT"})

    if any(x in text for x in ("oil", "crude", "opec", "refinery", "tanker")):
        importance = max(importance, 7.0)
        themes.update({"energy", "inflation"})
        assets.update({"BTCUSDT", "ETHUSDT", "XAUUSDT"})

    crypto_terms = {
        "bitcoin": "BTCUSDT",
        "btc": "BTCUSDT",
        "ethereum": "ETHUSDT",
        "ether": "ETHUSDT",
        "eth": "ETHUSDT",
        "chainlink": "LINKUSDT",
        "link": "LINKUSDT",
        "xrp": "XRPUSDT",
        "stellar": "XLMUSDT",
        "xlm": "XLMUSDT",
        "virtual": "VIRTUALUSDT",
        "bittensor": "TAOUSDT",
        "tao": "TAOUSDT",
    }
    for term, asset in crypto_terms.items():
        if re.search(rf"\b{re.escape(term)}\b", text):
            assets.add(asset)
            themes.add("crypto")

    if any(x in text for x in ("etf", "stablecoin", "sec ", "cftc", "regulation", "regulatory")):
        importance = max(importance, 7.0)
        themes.update({"crypto", "regulation"})

    if any(x in text for x in ("hack", "exploit", "bridge attack", "bankruptcy", "insolvency")):
        importance = max(importance, 8.0)
        themes.update({"crypto", "security"})

    if any(x in text for x in ("token launch", "launches", "memecoin", "meme coin", "airdrop")):
        importance = max(importance, 5.5)
        themes.update({"crypto", "catalyst"})

    if any(x in text for x in (
        "mainnet", "upgrade", "hard fork", "protocol update", "governance proposal",
        "tokenomics", "token unlock", "partnership", "integration", "adoption",
        "validator", "staking", "buyback", "burn", "treasury", "reserve",
        "ccip", "rwa", "swift", "payment abstraction",
    )):
        importance = max(importance, 7.0)
        themes.update({"crypto", "project_update", "thesis_change_candidate"})

    if any(x in text for x in (
        "delist", "delisting", "lawsuit", "investigation", "shutdown",
        "critical vulnerability", "network halt", "chain halt", "depeg",
    )):
        importance = max(importance, 8.0)
        themes.update({"crypto", "thesis_risk", "thesis_change_candidate"})

    if "hunter biden" in text and any(x in text for x in ("laptop", "token", "coin", "crypto")):
        importance = max(importance, 7.5)
        themes.update({"crypto", "political_memecoin", "catalyst"})

    if scheduled:
        themes.add("scheduled")

    return min(10.0, importance), sorted(assets), sorted(themes)



_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
    "after", "as", "at", "by", "from", "is", "are", "be", "will", "says", "said",
    "new", "latest", "amid", "over", "into", "its", "their", "this", "that",
}


def _headline_tokens(title: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9$]+", title.lower())
    return {t for t in tokens if len(t) > 2 and t not in _STOPWORDS}


def _corroboration_domain(event: NormalizedEvent) -> str:
    publisher_url = str(event.metadata.get("publisher_url") or "")
    return _domain(publisher_url or event.source_url) or event.source_name.lower()


def annotate_corroboration(events: list[NormalizedEvent], threshold: float = 0.52) -> list[NormalizedEvent]:
    """Annotate similar headlines with independent-source corroboration metadata."""
    token_sets = [_headline_tokens(e.title) for e in events]
    for i, event in enumerate(events):
        domains = {_corroboration_domain(event)}
        for j, other in enumerate(events):
            if i == j:
                continue
            a, b = token_sets[i], token_sets[j]
            if not a or not b:
                continue
            overlap = len(a & b) / max(1, len(a | b))
            if overlap >= threshold:
                domains.add(_corroboration_domain(other))
        event.metadata["corroboration_count"] = len(domains)
        event.metadata["corroborating_sources"] = sorted(domains)[:12]
    return events


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    candidates = (
        "%Y%m%dT%H%M%SZ",
        "%Y%m%dT%H%M%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in candidates:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None



class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return " ".join(self.parts)


def parse_fomc_calendar_html(html_text: str, *, year: int) -> list[NormalizedEvent]:
    extractor = _TextExtractor()
    extractor.feed(html_text)
    text = extractor.text()

    marker = f"{year} FOMC Meetings"
    if marker not in text:
        return []

    section = text.split(marker, 1)[1]
    next_marker = f"{year + 1} FOMC Meetings"
    if next_marker in section:
        section = section.split(next_marker, 1)[0]

    month_map = {
        "January": 1, "February": 2, "March": 3, "April": 4,
        "May": 5, "June": 6, "July": 7, "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }
    pattern = re.compile(
        r"\b(" + "|".join(month_map) + r")\b\s+(\d{1,2})-(\d{1,2})(\*)?"
    )

    events: list[NormalizedEvent] = []
    eastern = ZoneInfo("America/New_York")
    for match in pattern.finditer(section):
        month_name, start_day, end_day, sep_flag = match.groups()
        month = month_map[month_name]
        end_date = datetime(year, month, int(end_day), 14, 0, tzinfo=eastern).astimezone(timezone.utc)
        title = f"FOMC meeting conclusion — {month_name} {start_day}-{end_day}, {year}"
        importance, assets, themes = classify_event("FOMC Federal Reserve interest rate decision", scheduled=True)
        events.append(NormalizedEvent(
            canonical_key=f"fomc:{year}:{month:02d}:{int(end_day):02d}",
            event_kind="macro_calendar",
            status="scheduled",
            title=title,
            summary=(
                "Federal Open Market Committee scheduled meeting. Event time is modeled "
                "at the customary 2:00 PM ET statement time on the final meeting day; "
                "confirm official timing if the Federal Reserve publishes an exception."
            ),
            source_name="Federal Reserve",
            source_url=FOMC_CALENDAR_URL,
            source_tier=1,
            confidence="high",
            importance=importance,
            event_time=end_date,
            affected_assets=assets,
            themes=sorted(set(themes + ["fomc", "monetary_policy"])),
            metadata={
                "meeting_start_day": int(start_day),
                "meeting_end_day": int(end_day),
                "summary_of_economic_projections": bool(sep_flag),
                "modeled_statement_time_et": "14:00",
            },
        ))
    return events


def parse_rss(xml_text: str, *, source_name: str, event_kind: str = "news") -> list[NormalizedEvent]:
    root = ElementTree.fromstring(xml_text)
    events: list[NormalizedEvent] = []
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title"))
        link = _clean_text(item.findtext("link"))
        description = _clean_text(item.findtext("description"))
        guid = _clean_text(item.findtext("guid")) or link or title
        pub_raw = _clean_text(item.findtext("pubDate"))
        try:
            published = parsedate_to_datetime(pub_raw).astimezone(timezone.utc) if pub_raw else None
        except Exception:
            published = None
        tier = source_tier(link)
        importance, assets, themes = classify_event(title, description)
        events.append(NormalizedEvent(
            canonical_key=_canonical_key(source_name.lower().replace(" ", "_"), guid),
            event_kind=event_kind,
            status="reported",
            title=title,
            summary=description[:2000],
            source_name=source_name,
            source_url=link,
            source_tier=tier,
            confidence=confidence_for_tier(tier),
            importance=importance,
            published_at=published,
            affected_assets=assets,
            themes=themes,
        ))
    return events




def parse_google_news_rss(xml_text: str, *, topic: str) -> list[NormalizedEvent]:
    root = ElementTree.fromstring(xml_text)
    events: list[NormalizedEvent] = []
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title"))
        link = _clean_text(item.findtext("link"))
        description = _clean_text(item.findtext("description"))
        guid = _clean_text(item.findtext("guid")) or link or title
        pub_raw = _clean_text(item.findtext("pubDate"))
        source_node = item.find("source")
        publisher = _clean_text(source_node.text if source_node is not None else "") or "Google News"
        publisher_url = ""
        if source_node is not None:
            publisher_url = _clean_text(source_node.attrib.get("url"))
        try:
            published = parsedate_to_datetime(pub_raw).astimezone(timezone.utc) if pub_raw else None
        except Exception:
            published = None

        tier = source_tier(publisher_url or link)
        importance, assets, themes = classify_event(title, description)
        themes = sorted(set(themes + [topic, "news_discovery", "google_news_fallback"]))
        events.append(NormalizedEvent(
            canonical_key=_canonical_key("google_news", guid),
            event_kind="news",
            status="reported",
            title=title,
            summary=description[:2000],
            source_name=publisher,
            source_url=link,
            source_tier=tier,
            confidence=confidence_for_tier(tier),
            importance=importance,
            published_at=published,
            affected_assets=assets,
            themes=themes,
            metadata={"publisher_url": publisher_url, "discovery_source": "google_news"},
        ))
    return events


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_value(props: dict[str, tuple[str, str]], key: str) -> tuple[str, str] | None:
    return props.get(key)


def _parse_ics_dt(param_key: str, value: str) -> datetime | None:
    tzid = None
    if ";" in param_key:
        for part in param_key.split(";")[1:]:
            if part.startswith("TZID="):
                tzid = part.split("=", 1)[1]
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    try:
        dt = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
        if value.endswith("Z"):
            return dt.replace(tzinfo=timezone.utc)
        if tzid:
            return dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(timezone.utc)
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def parse_bls_ics(text: str) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    current: dict[str, tuple[str, str]] | None = None
    for line in _unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT" and current is not None:
            summary = (current.get("SUMMARY") or ("", ""))[1].replace("\\,", ",")
            uid = (current.get("UID") or ("", summary))[1]
            description = (current.get("DESCRIPTION") or ("", ""))[1].replace("\\n", " ")
            url = (current.get("URL") or ("", BLS_ICS_URL))[1] or BLS_ICS_URL
            dt_prop = next((v for k, v in current.items() if k.startswith("DTSTART")), None)
            event_time = _parse_ics_dt(dt_prop[0], dt_prop[1]) if dt_prop else None
            importance, assets, themes = classify_event(summary, description, scheduled=True)
            events.append(NormalizedEvent(
                canonical_key=_canonical_key("bls", uid),
                event_kind="macro_calendar",
                status="scheduled",
                title=_clean_text(summary),
                summary=_clean_text(description),
                source_name="U.S. Bureau of Labor Statistics",
                source_url=url,
                source_tier=1,
                confidence="high",
                importance=importance,
                event_time=event_time,
                affected_assets=assets,
                themes=themes,
                metadata={"uid": uid},
            ))
            current = None
            continue
        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            base = key.split(";", 1)[0]
            current[key] = (key, value)
            if base not in current:
                current[base] = (key, value)
    return events


def parse_gdelt(data: dict, *, topic: str) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    for row in data.get("articles", []) or []:
        title = _clean_text(row.get("title"))
        url = str(row.get("url") or "")
        if not title or not url:
            continue
        tier = source_tier(url)
        importance, assets, themes = classify_event(title)
        themes = sorted(set(themes + [topic, "news_discovery"]))
        events.append(NormalizedEvent(
            canonical_key=_canonical_key("gdelt", url),
            event_kind="news",
            status="reported",
            title=title,
            summary="",
            source_name=str(row.get("domain") or _domain(url) or "GDELT discovery"),
            source_url=url,
            source_tier=tier,
            confidence=confidence_for_tier(tier),
            importance=importance,
            published_at=_parse_dt(row.get("seendate")),
            affected_assets=assets,
            themes=themes,
            metadata={
                "source_country": row.get("sourcecountry"),
                "language": row.get("language"),
                "discovered_via": "GDELT",
            },
        ))
    return events


def parse_coinmarketcal(data: dict) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    for row in data.get("data", []) or []:
        event_id = str(row.get("id") or "")
        title = _clean_text(row.get("title"))
        if not event_id or not title:
            continue
        source_url = str(row.get("sourceUrl") or "https://coinmarketcal.com")
        description = _clean_text(row.get("description"))
        impact = row.get("impact")
        base_importance, assets, themes = classify_event(title, description, scheduled=True)
        coin_assets = []
        for coin in row.get("coins", []) or []:
            symbol = str(coin.get("symbol") or "").upper()
            if symbol:
                coin_assets.append(symbol + "USDT")
        importance = max(base_importance, float(impact)) if impact is not None else base_importance
        events.append(NormalizedEvent(
            canonical_key=f"coinmarketcal:{event_id}",
            event_kind="crypto_calendar",
            status="scheduled",
            title=title,
            summary=description,
            source_name="CoinMarketCal",
            source_url=source_url,
            source_tier=3,
            confidence="medium",
            importance=min(10.0, importance),
            event_time=_parse_dt(row.get("date")),
            affected_assets=sorted(set(assets + coin_assets)),
            themes=sorted(set(themes + ["crypto_calendar"])),
            metadata={
                "event_id": event_id,
                "displayed_date": row.get("displayedDate"),
                "is_estimated": row.get("isEstimated"),
                "categories": row.get("categories") or [],
            },
        ))
    return events


async def fetch_fomc_calendar(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    response = await client.get(FOMC_CALENDAR_URL)
    response.raise_for_status()
    now = datetime.now(timezone.utc)
    events = parse_fomc_calendar_html(response.text, year=now.year)
    events.extend(parse_fomc_calendar_html(response.text, year=now.year + 1))
    return events


async def fetch_bls_calendar(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    response = await client.get(BLS_ICS_URL)
    response.raise_for_status()
    return parse_bls_ics(response.text)


async def fetch_fed_monetary(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    response = await client.get(FED_MONETARY_RSS_URL)
    response.raise_for_status()
    return parse_rss(response.text, source_name="Federal Reserve", event_kind="official_release")


async def fetch_coindesk_rss(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    response = await client.get(COINDESK_RSS_URL)
    response.raise_for_status()
    return parse_rss(response.text, source_name="CoinDesk", event_kind="news")


async def fetch_google_news_fallback(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    all_events: list[NormalizedEvent] = []
    for topic, query in GDELT_TOPICS.items():
        try:
            response = await client.get(
                GOOGLE_NEWS_RSS_URL,
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            )
            response.raise_for_status()
            all_events.extend(parse_google_news_rss(response.text, topic=topic))
        except httpx.HTTPStatusError as exc:
            log.warning(
                "Google News fallback failed: topic=%s status=%s",
                topic,
                exc.response.status_code,
            )
        except Exception as exc:
            log.warning(
                "Google News fallback failed: topic=%s error=%s",
                topic,
                type(exc).__name__,
            )
    return all_events


async def fetch_gdelt(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    all_events: list[NormalizedEvent] = []
    for topic, query in GDELT_TOPICS.items():
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(settings.gdelt_max_records),
            "timespan": settings.gdelt_timespan,
            "sort": "datedesc",
        }
        try:
            response = await client.get(GDELT_DOC_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            log.warning(
                "GDELT topic fetch failed: topic=%s status=%s retry_after=%s",
                topic,
                status,
                exc.response.headers.get("retry-after"),
            )
            if status in {403, 429, 451}:
                break
            continue
        except Exception as exc:
            log.warning(
                "GDELT topic fetch failed: topic=%s error=%s",
                topic,
                type(exc).__name__,
            )
            continue

        try:
            data = response.json()
        except Exception:
            log.warning(
                "GDELT returned non-JSON response: topic=%s content_type=%s",
                topic,
                response.headers.get("content-type"),
            )
            continue
        all_events.extend(parse_gdelt(data, topic=topic))
    return all_events


async def fetch_coinmarketcal(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    if not settings.coinmarketcal_api_key.strip():
        return []
    now = datetime.now(timezone.utc)
    params = {
        "from": now.date().isoformat(),
        "to": (now + timedelta(days=30)).date().isoformat(),
        "limit": "100",
        "sortBy": "date_asc",
    }
    response = await client.get(
        COINMARKETCAL_URL,
        params=params,
        headers={"x-api-key": settings.coinmarketcal_api_key, "Accept": "application/json"},
    )
    response.raise_for_status()
    return parse_coinmarketcal(response.json())


def upsert_events(events: list[NormalizedEvent]) -> list[IntelligenceEvent]:
    now = datetime.now(timezone.utc)
    created: list[IntelligenceEvent] = []
    with SessionLocal() as db:
        for event in events:
            row = db.scalar(
                select(IntelligenceEvent)
                .where(IntelligenceEvent.canonical_key == event.canonical_key)
                .limit(1)
            )
            if row:
                row.last_seen_at = now
                row.title = event.title
                row.summary = event.summary
                row.status = event.status
                row.source_url = event.source_url
                row.source_tier = event.source_tier
                row.confidence = event.confidence
                row.importance = event.importance
                row.published_at = event.published_at
                row.event_time = event.event_time
                row.affected_assets_json = json.dumps(event.affected_assets)
                row.themes_json = json.dumps(event.themes)
                row.metadata_json = json.dumps(event.metadata)
            else:
                row = IntelligenceEvent(
                    canonical_key=event.canonical_key,
                    event_kind=event.event_kind,
                    status=event.status,
                    title=event.title,
                    summary=event.summary,
                    source_name=event.source_name,
                    source_url=event.source_url,
                    source_tier=event.source_tier,
                    confidence=event.confidence,
                    importance=event.importance,
                    published_at=event.published_at,
                    event_time=event.event_time,
                    first_seen_at=now,
                    last_seen_at=now,
                    affected_assets_json=json.dumps(event.affected_assets),
                    themes_json=json.dumps(event.themes),
                    metadata_json=json.dumps(event.metadata),
                )
                db.add(row)
                db.flush()
                created.append(row)
        db.commit()
    return created


def already_alerted(dedupe_key: str) -> bool:
    with SessionLocal() as db:
        return db.scalar(
            select(AlertRecord.id)
            .where(AlertRecord.dedupe_key == dedupe_key)
            .limit(1)
        ) is not None


def record_intelligence_alert(event: IntelligenceEvent, dedupe_key: str, explanation: str) -> None:
    with SessionLocal() as db:
        db.add(AlertRecord(
            severity="yellow" if event.importance < 9 else "red",
            confidence=event.confidence,
            symbol=None,
            title=event.title[:255],
            explanation=explanation,
            evidence_json=json.dumps({
                "event_id": event.id,
                "event_kind": event.event_kind,
                "importance": event.importance,
                "source_name": event.source_name,
                "source_url": event.source_url,
                "source_tier": event.source_tier,
                "themes": json.loads(event.themes_json or "[]"),
                "affected_assets": json.loads(event.affected_assets_json or "[]"),
            }),
            dedupe_key=dedupe_key,
        ))
        db.commit()


def immediate_push_candidate(event: IntelligenceEvent) -> bool:
    if event.status == "scheduled":
        return False
    if event.importance < settings.intelligence_push_min_importance:
        return False
    if event.source_tier <= 2:
        return True

    try:
        metadata = json.loads(event.metadata_json or "{}")
    except Exception:
        metadata = {}
    corroboration = int(metadata.get("corroboration_count") or 1)

    if event.source_tier == 3:
        return event.importance >= 7.5 and corroboration >= 2
    if event.source_tier == 4:
        return event.importance >= 8.0 and corroboration >= 3
    return False


def upcoming_stage(event_time: datetime | None, now: datetime | None = None) -> str | None:
    if event_time is None:
        return None
    now = now or datetime.now(timezone.utc)
    ts = event_time if event_time.tzinfo else event_time.replace(tzinfo=timezone.utc)
    seconds = (ts - now).total_seconds()
    if seconds < 0:
        return None
    if seconds <= 15 * 60:
        return "15m"
    if seconds <= 2 * 3600:
        return "2h"
    if seconds <= 24 * 3600:
        return "24h"
    return None


def format_event_message(
    event: IntelligenceEvent,
    *,
    stage: str | None = None,
    market_context: list[dict] | None = None,
) -> str:
    assets = json.loads(event.affected_assets_json or "[]")
    themes = json.loads(event.themes_json or "[]")
    when = event.event_time or event.published_at or event.first_seen_at
    when_text = when.astimezone(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d %H:%M SGT") if when else "—"
    prefix_en = "🗓 UPCOMING" if stage else ("🔴 HIGH IMPACT" if event.importance >= 9 else "🟡 MARKET INTELLIGENCE")
    prefix_zh = "🗓 即将发生" if stage else ("🔴 高影响事件" if event.importance >= 9 else "🟡 市场情报")
    stage_en = f" · {stage} reminder" if stage else ""
    stage_zh = f" · 提前 {stage} 提醒" if stage else ""
    summary = event.summary[:900] if event.summary else "New event detected. Thesis impact requires confirmation against market response."

    theme_zh_map = {
        "macro": "宏观", "rates": "利率", "geopolitics": "地缘政治", "energy": "能源",
        "inflation": "通胀", "crypto": "加密市场", "regulation": "监管", "security": "安全事件",
        "catalyst": "催化剂", "scheduled": "预定事件", "fomc": "FOMC",
        "monetary_policy": "货币政策", "news_discovery": "新闻发现",
        "political_memecoin": "政治 Meme 币", "crypto_calendar": "加密日历",
        "project_update": "项目方更新", "thesis_change_candidate": "逻辑变化候选",
        "thesis_risk": "项目逻辑风险",
    }
    themes_zh = [theme_zh_map.get(t, t) for t in themes]

    market_context = market_context or []
    if market_context:
        zh_market_lines = []
        en_market_lines = []
        for item in market_context:
            symbol = item.get("symbol", "—")
            price = item.get("price")
            price_text = "—" if price is None else f"{float(price):,.8f}".rstrip("0").rstrip(".")
            confidence = str(item.get("confidence") or "low").upper()
            source_count = int(item.get("source_count") or 1)
            zh_market_lines.append(
                f"• {symbol}: {price_text} · 共识 {confidence} · {source_count} 个数据源"
            )
            en_market_lines.append(
                f"• {symbol}: {price_text} · consensus {confidence} · {source_count} source(s)"
            )
        zh_market = "\n\n事件发生时相关加密资产价格：\n" + "\n".join(zh_market_lines)
        en_market = "\n\nRelated crypto prices at alert time:\n" + "\n".join(en_market_lines)
    else:
        zh_market = ""
        en_market = ""

    zh = (
        f"{prefix_zh}{stage_zh}\n"
        f"原文标题：{event.title}\n\n"
        f"重要度：{event.importance:.1f}/10\n"
        f"置信度：{event.confidence.upper()} · 来源等级：Tier {event.source_tier}\n"
        f"时间：{when_text}\n"
        f"主题：{', '.join(themes_zh) if themes_zh else '—'}\n"
        f"潜在影响：{', '.join(assets) if assets else '广泛市场 / 尚未分类'}\n"
        f"来源：{event.source_name}\n"
        f"原文链接：{event.source_url}\n\n"
        f"原文摘要：{summary}"
        f"{zh_market}"
    )
    en = (
        f"{prefix_en}{stage_en}\n"
        f"{event.title}\n\n"
        f"Importance: {event.importance:.1f}/10\n"
        f"Confidence: {event.confidence.upper()} · Source tier: {event.source_tier}\n"
        f"Time: {when_text}\n"
        f"Themes: {', '.join(themes) if themes else '—'}\n"
        f"Affected: {', '.join(assets) if assets else 'broad market / unclassified'}\n"
        f"Source: {event.source_name}\n"
        f"Source link: {event.source_url}\n\n"
        f"{summary}"
        f"{en_market}"
    )
    return localized(en, zh)
