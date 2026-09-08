import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..config import settings
from ..db import AlertRecord, IntelligenceEvent, SessionLocal

log = logging.getLogger("thesisguard.intelligence")

BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FED_MONETARY_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
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

    if "hunter biden" in text and any(x in text for x in ("laptop", "token", "coin", "crypto")):
        importance = max(importance, 7.5)
        themes.update({"crypto", "political_memecoin", "catalyst"})

    if scheduled:
        themes.add("scheduled")

    return min(10.0, importance), sorted(assets), sorted(themes)


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


async def fetch_bls_calendar(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    response = await client.get(BLS_ICS_URL)
    response.raise_for_status()
    return parse_bls_ics(response.text)


async def fetch_fed_monetary(client: httpx.AsyncClient) -> list[NormalizedEvent]:
    response = await client.get(FED_MONETARY_RSS_URL)
    response.raise_for_status()
    return parse_rss(response.text, source_name="Federal Reserve", event_kind="official_release")


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
        response = await client.get(GDELT_DOC_URL, params=params)
        response.raise_for_status()
        try:
            data = response.json()
        except Exception:
            log.warning("GDELT returned non-JSON response for topic=%s", topic)
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
    return event.source_tier == 3 and event.importance >= 8.5


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


def format_event_message(event: IntelligenceEvent, *, stage: str | None = None) -> str:
    assets = json.loads(event.affected_assets_json or "[]")
    themes = json.loads(event.themes_json or "[]")
    when = event.event_time or event.published_at or event.first_seen_at
    when_text = when.astimezone(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d %H:%M SGT") if when else "—"
    prefix = "🗓 UPCOMING" if stage else ("🔴 HIGH IMPACT" if event.importance >= 9 else "🟡 MARKET INTELLIGENCE")
    stage_text = f" · {stage} reminder" if stage else ""
    return (
        f"{prefix}{stage_text}\n"
        f"{event.title}\n\n"
        f"Importance: {event.importance:.1f}/10\n"
        f"Confidence: {event.confidence.upper()} · Source tier: {event.source_tier}\n"
        f"Time: {when_text}\n"
        f"Themes: {', '.join(themes) if themes else '—'}\n"
        f"Affected: {', '.join(assets) if assets else 'broad market / unclassified'}\n"
        f"Source: {event.source_name}\n\n"
        f"{event.summary[:900] if event.summary else 'New event detected. Thesis impact requires confirmation against market response.'}"
    )
