import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..config import settings
from ..db import IntelligenceEvent, MarketTick, SessionLocal
from ..portfolio import load_portfolio
from .telegram import localized, send_message, telegram_configured

log = logging.getLogger("thesisguard.telegram_query")

CRYPTO_NAME_MAP = {
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
    "virtuals": "VIRTUALUSDT",
    "bittensor": "TAOUSDT",
    "tao": "TAOUSDT",
    "gold": "XAUUSDT",
    "xau": "XAUUSDT",
}

BROAD_THEMES = {"macro", "rates", "geopolitics", "energy", "inflation", "regulation", "crypto"}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_hours(text: str, default: int | None = None) -> int:
    default = default or settings.telegram_query_default_hours
    lower = text.lower()
    m = re.search(r"(\d{1,3})\s*(?:h|hr|hrs|hour|hours|小时)", lower)
    if m:
        return max(1, min(24 * 30, int(m.group(1))))
    m = re.search(r"(\d{1,2})\s*(?:d|day|days|天)", lower)
    if m:
        return max(1, min(30, int(m.group(1)))) * 24
    if any(x in lower for x in ("today", "今天", "今日")):
        return 24
    return default


def _symbol_from_text(text: str) -> str | None:
    lower = text.lower()
    for name, symbol in CRYPTO_NAME_MAP.items():
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return symbol
    m = re.search(r"\b([A-Z]{2,12})USDT\b", text.upper())
    return m.group(0) if m else None


def _event_dict(row: IntelligenceEvent) -> dict:
    return {
        "title": row.title,
        "importance": float(row.importance),
        "confidence": row.confidence,
        "source": row.source_name,
        "event_time": row.event_time,
        "first_seen_at": row.first_seen_at,
        "affected_assets": json.loads(row.affected_assets_json or "[]"),
        "themes": json.loads(row.themes_json or "[]"),
    }


def _recent_events(hours: int, min_importance: float = 5.0, limit: int = 12) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with SessionLocal() as db:
        rows = db.scalars(
            select(IntelligenceEvent)
            .where(IntelligenceEvent.first_seen_at >= cutoff)
            .where(IntelligenceEvent.importance >= min_importance)
            .order_by(IntelligenceEvent.importance.desc(), IntelligenceEvent.first_seen_at.desc())
            .limit(limit)
        ).all()
    return [_event_dict(r) for r in rows]


def _upcoming_events(hours: int = 24 * 7, min_importance: float = 7.0, limit: int = 10) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    with SessionLocal() as db:
        rows = db.scalars(
            select(IntelligenceEvent)
            .where(IntelligenceEvent.status == "scheduled")
            .where(IntelligenceEvent.event_time.is_not(None))
            .where(IntelligenceEvent.event_time >= now)
            .where(IntelligenceEvent.event_time <= cutoff)
            .where(IntelligenceEvent.importance >= min_importance)
            .order_by(IntelligenceEvent.event_time.asc())
            .limit(limit)
        ).all()
    return [_event_dict(r) for r in rows]


def _market_move(symbol: str, hours: int) -> dict | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with SessionLocal() as db:
        latest = db.scalar(
            select(MarketTick)
            .where(MarketTick.symbol == symbol)
            .order_by(MarketTick.received_at.desc())
            .limit(1)
        )
        first = db.scalar(
            select(MarketTick)
            .where(MarketTick.symbol == symbol)
            .where(MarketTick.received_at >= cutoff)
            .order_by(MarketTick.received_at.asc())
            .limit(1)
        )
    if not latest:
        return None
    change = None
    if first and first.mark_price:
        change = (latest.mark_price / first.mark_price - 1) * 100
    return {
        "symbol": symbol,
        "price": latest.mark_price,
        "change_pct": change,
        "confidence": latest.source_confidence,
        "source_conflict": latest.source_conflict,
        "received_at": latest.received_at,
    }


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def _fmt_move(move: dict | None, hours: int) -> str:
    if not move:
        return "—"
    change = move["change_pct"]
    pct = "n/a" if change is None else f"{change:+.2f}%/{hours}h"
    confidence = str(move.get("confidence") or "low").upper()
    conflict = " · CONFLICT" if move.get("source_conflict") else ""
    return f"{_fmt_price(move['price'])} · {pct} · {confidence}{conflict}"


def _event_relevance(event: dict, symbol: str) -> tuple[bool, str]:
    assets = set(event.get("affected_assets") or [])
    themes = set(event.get("themes") or [])
    if symbol in assets:
        if "thesis_risk" in themes or "security" in themes:
            return True, "thesis risk candidate"
        if "project_update" in themes or "thesis_change_candidate" in themes:
            return True, "thesis update candidate"
        return True, "directly related"
    if symbol == "XAUUSDT" and themes & {"macro", "rates", "geopolitics", "energy", "inflation"}:
        return True, "macro/regime context"
    if symbol != "XAUUSDT" and themes & BROAD_THEMES:
        return True, "market/regime context"
    return False, ""


def _market_overview(hours: int) -> str:
    portfolio = load_portfolio()
    portfolio_symbols = [str(x["symbol"]).upper() for x in portfolio.get("positions", [])]
    symbols = []
    for s in ["BTCUSDT", "ETHUSDT"] + portfolio_symbols:
        if s not in symbols:
            symbols.append(s)

    moves = [(s, _market_move(s, hours)) for s in symbols[:10]]
    events = _recent_events(hours, min_importance=6.0, limit=5)
    upcoming = _upcoming_events(48, min_importance=8.0, limit=3)

    zh_lines = ["📊 ThesisGuard 市场概览", f"回看窗口：最近 {hours} 小时", "", "市场价格："]
    en_lines = ["📊 ThesisGuard Market Overview", f"Lookback: last {hours} hours", "", "Market prices:"]

    for symbol, move in moves:
        zh_lines.append(f"• {symbol}: {_fmt_move(move, hours)}")
        en_lines.append(f"• {symbol}: {_fmt_move(move, hours)}")

    zh_lines += ["", "近期重要事件："]
    en_lines += ["", "Recent important events:"]
    if events:
        for e in events:
            zh_lines.append(f"• [{e['importance']:.1f}/10] {e['title']} · {e['source']}")
            en_lines.append(f"• [{e['importance']:.1f}/10] {e['title']} · {e['source']}")
    else:
        zh_lines.append("• 当前数据库中没有达到阈值的重要新事件。")
        en_lines.append("• No recent events above the current importance threshold.")

    if upcoming:
        zh_lines += ["", "未来48小时重大事件："]
        en_lines += ["", "Major events in the next 48h:"]
        for e in upcoming:
            when = _aware(e["event_time"]).astimezone(ZoneInfo("Asia/Singapore")).strftime("%m-%d %H:%M SGT")
            zh_lines.append(f"• {when} · {e['title']} [{e['importance']:.1f}/10]")
            en_lines.append(f"• {when} · {e['title']} [{e['importance']:.1f}/10]")

    zh_lines += [
        "",
        "解读原则：价格本身不是 thesis 变化；需要结合事件、项目基本面和跨市场结构判断。",
    ]
    en_lines += [
        "",
        "Interpretation rule: price alone is not a thesis change; event, fundamentals and market structure matter.",
    ]
    return localized("\n".join(en_lines), "\n".join(zh_lines))


def _recent_overview(hours: int) -> str:
    events = _recent_events(hours, min_importance=5.0, limit=10)
    zh = [f"📰 最近 {hours} 小时事件", ""]
    en = [f"📰 Events from the last {hours} hours", ""]
    if not events:
        zh.append("当前没有达到存储阈值的新事件。")
        en.append("No recent events above the storage threshold.")
        return localized("\n".join(en), "\n".join(zh))

    for e in events:
        when = _aware(e["first_seen_at"]).astimezone(ZoneInfo("Asia/Singapore")).strftime("%m-%d %H:%M")
        assets = ", ".join(e["affected_assets"][:5]) or "broad market"
        line = f"• {when} · [{e['importance']:.1f}/10] {e['title']} · {e['source']} · {assets}"
        zh.append(line)
        en.append(line)
    return localized("\n".join(en), "\n".join(zh))


def _portfolio_correlation(hours: int) -> str:
    portfolio = load_portfolio()
    positions = portfolio.get("positions", [])
    events = _recent_events(hours, min_importance=5.0, limit=30)

    zh = [f"🧭 持仓 × 最近 {hours} 小时事件", ""]
    en = [f"🧭 Portfolio × events from the last {hours} hours", ""]

    if not positions:
        zh.append("当前没有配置持仓。")
        en.append("No configured positions.")
        return localized("\n".join(en), "\n".join(zh))

    for pos in positions:
        symbol = str(pos["symbol"]).upper()
        relevant = []
        for event in events:
            matched, reason = _event_relevance(event, symbol)
            if matched:
                relevant.append((event, reason))
        move = _market_move(symbol, hours)
        zh.append(f"{symbol} · {_fmt_move(move, hours)}")
        en.append(f"{symbol} · {_fmt_move(move, hours)}")
        if relevant:
            for event, reason in relevant[:3]:
                zh.append(f"  • {reason}: [{event['importance']:.1f}/10] {event['title']}")
                en.append(f"  • {reason}: [{event['importance']:.1f}/10] {event['title']}")
        else:
            zh.append("  • 暂无直接或高相关事件；不要仅由价格波动推断 thesis 改变。")
            en.append("  • No direct/high-relevance event; do not infer thesis change from price alone.")

    return localized("\n".join(en), "\n".join(zh))


def _asset_overview(symbol: str, hours: int) -> str:
    events = _recent_events(hours, min_importance=5.0, limit=30)
    relevant = []
    for event in events:
        matched, reason = _event_relevance(event, symbol)
        if matched:
            relevant.append((event, reason))

    move = _market_move(symbol, hours)
    zh = [f"🔎 {symbol} 最近 {hours} 小时", f"当前：{_fmt_move(move, hours)}", ""]
    en = [f"🔎 {symbol} · last {hours} hours", f"Current: {_fmt_move(move, hours)}", ""]

    if relevant:
        zh.append("相关事件：")
        en.append("Relevant events:")
        for event, reason in relevant[:8]:
            zh.append(f"• {reason} · [{event['importance']:.1f}/10] {event['title']} · {event['source']}")
            en.append(f"• {reason} · [{event['importance']:.1f}/10] {event['title']} · {event['source']}")
    else:
        zh.append("暂无达到阈值的直接相关事件。")
        en.append("No directly relevant event above threshold.")

    zh += ["", "判断：若只有价格变化而没有项目/宏观事件确认，不视为 thesis 已改变。"]
    en += ["", "Assessment: price movement alone is not treated as a thesis change without event/fundamental confirmation."]
    return localized("\n".join(en), "\n".join(zh))


def _upcoming_overview(hours: int) -> str:
    events = _upcoming_events(hours, min_importance=7.0, limit=12)
    zh = [f"🗓 未来 {hours} 小时重大事件", ""]
    en = [f"🗓 Major events in the next {hours} hours", ""]
    if not events:
        zh.append("当前没有达到提醒阈值的已知预定事件。")
        en.append("No known scheduled event above the alert threshold.")
        return localized("\n".join(en), "\n".join(zh))

    for e in events:
        when = _aware(e["event_time"]).astimezone(ZoneInfo("Asia/Singapore")).strftime("%m-%d %H:%M SGT")
        line = f"• {when} · [{e['importance']:.1f}/10] {e['title']} · {e['source']}"
        zh.append(line)
        en.append(line)
    return localized("\n".join(en), "\n".join(zh))


def build_query_response(text: str) -> str:
    stripped = text.strip()
    lower = stripped.lower()
    hours = _parse_hours(stripped)

    if lower in {"/start", "/help", "help", "帮助"}:
        return localized(
            (
                "Ask ThesisGuard:\n"
                "• how's the market going? / /market\n"
                "• recent 6h events / /recent 6h\n"
                "• correlate recent events to my positions / /portfolio\n"
                "• LINK recent news / /asset LINK\n"
                "• upcoming 7d events / /upcoming 7d"
            ),
            (
                "可以这样问 ThesisGuard：\n"
                "• 市场怎么样 / /market\n"
                "• 最近6小时发生了什么 / /recent 6h\n"
                "• 最近事件对我的持仓有什么影响 / /portfolio\n"
                "• LINK 最近有什么事 / /asset LINK\n"
                "• 未来7天有什么重大事件 / /upcoming 7d"
            ),
        )

    if lower.startswith("/upcoming") or any(x in lower for x in ("upcoming", "未来", "接下来")):
        return _upcoming_overview(_parse_hours(stripped, 24 * 7))

    if lower.startswith("/portfolio") or (
        any(x in lower for x in ("portfolio", "positions", "持仓", "仓位"))
        and any(x in lower for x in ("event", "news", "impact", "影响", "事件", "新闻", "相关"))
    ):
        return _portfolio_correlation(hours)

    symbol = _symbol_from_text(stripped)
    if lower.startswith("/asset") or (symbol and any(x in lower for x in ("news", "event", "recent", "最近", "新闻", "事件", "怎么", "怎么样"))):
        if symbol:
            return _asset_overview(symbol, hours)

    if lower.startswith("/recent") or any(
        x in lower for x in ("what happened", "recent event", "recent news", "发生了什么", "最近事件", "最近新闻")
    ):
        return _recent_overview(hours)

    if lower.startswith("/market") or any(
        x in lower for x in ("how's the market", "hows the market", "market going", "market overview", "市场怎么样", "行情怎么样", "市场如何")
    ):
        return _market_overview(hours)

    return localized(
        "I can answer market overview, recent events, portfolio-event correlation, asset news, and upcoming catalysts. Send /help for examples.",
        "我目前可以回答市场概览、近期事件、持仓事件关联、单个资产新闻和未来重大事件。发送 /help 查看示例。",
    )


async def telegram_query_loop() -> None:
    if not settings.telegram_queries_enabled or not telegram_configured():
        return

    token = settings.telegram_bot_token.strip()
    allowed_chat = settings.telegram_chat_id.strip()
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    offset: int | None = None

    async with httpx.AsyncClient(timeout=35.0) as client:
        # Drop old backlog on worker boot. Only new user queries should receive responses.
        try:
            response = await client.get(url, params={"offset": -1, "timeout": 0})
            data = response.json() if response.status_code < 400 else {}
            results = data.get("result") or []
            if results:
                offset = int(results[-1]["update_id"]) + 1
        except Exception as exc:
            log.warning("Telegram query initialization failed: %s", type(exc).__name__)

        while True:
            try:
                params = {"timeout": 25, "allowed_updates": json.dumps(["message"])}
                if offset is not None:
                    params["offset"] = offset
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

                for update in data.get("result", []) or []:
                    offset = int(update["update_id"]) + 1
                    message = update.get("message") or {}
                    chat_id = str((message.get("chat") or {}).get("id") or "")
                    text = str(message.get("text") or "").strip()

                    if not text or chat_id != allowed_chat:
                        continue

                    try:
                        answer = build_query_response(text)
                    except Exception as exc:
                        log.warning("Telegram query handling failed: %s", type(exc).__name__)
                        answer = localized(
                            "Query failed internally. Market monitoring continues normally.",
                            "查询处理出现内部错误；市场监控本身仍会继续正常运行。",
                        )
                    await send_message(answer)

            except asyncio.CancelledError:
                raise
            except httpx.HTTPStatusError as exc:
                log.warning("Telegram getUpdates HTTP failure: status=%s", exc.response.status_code)
                await asyncio.sleep(5)
            except Exception as exc:
                log.warning("Telegram query polling failed: %s", type(exc).__name__)
                await asyncio.sleep(5)
