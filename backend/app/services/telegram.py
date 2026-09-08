import logging
from datetime import datetime, timezone
import httpx

from ..config import settings

log = logging.getLogger("thesisguard.telegram")


def telegram_configured() -> bool:
    return bool(
        settings.telegram_alerts_enabled
        and settings.telegram_bot_token.strip()
        and settings.telegram_chat_id.strip()
    )


def format_alert_message(
    *,
    symbol: str,
    severity: str,
    confidence: str,
    mark_price: float | None,
    reasons: list[str],
    source_conflict: bool,
    spread_bps: float | None,
    explanation: str,
) -> str:
    sev = severity.upper()
    emoji = "🔴" if severity == "red" else "🟡" if severity == "yellow" else "🟢"
    reason_text = "\n".join(f"• {r}" for r in reasons) if reasons else "• No material reason recorded"
    price_text = "—" if mark_price is None else f"{mark_price:,.8f}".rstrip("0").rstrip(".")
    spread_text = "—" if spread_bps is None else f"{spread_bps:.2f} bps"
    conflict_text = "YES" if source_conflict else "NO"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return (
        f"{emoji} ThesisGuard {sev} ALERT\n"
        f"Asset: {symbol}\n"
        f"Confidence: {confidence.upper()}\n"
        f"Mark price: {price_text}\n"
        f"Source conflict: {conflict_text}\n"
        f"Spread: {spread_text}\n\n"
        f"Triggers:\n{reason_text}\n\n"
        f"Interpretation: {explanation}\n\n"
        f"Time: {now}\n"
        f"Monitoring only · no auto-trading"
    )


async def send_message(text: str) -> bool:
    if not telegram_configured():
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                log.warning("Telegram Bot API returned ok=false: %s", data.get("description"))
                return False
        return True
    except Exception as exc:
        log.warning("Telegram alert delivery failed: %r", exc)
        return False


async def send_startup_message(symbol_count: int) -> bool:
    text = (
        "✅ ThesisGuard worker online\n"
        f"Telegram alerts: ACTIVE\n"
        f"Watching: {symbol_count} symbols\n"
        "Risk alerts will be pushed automatically when a material alert is created."
    )
    return await send_message(text)
