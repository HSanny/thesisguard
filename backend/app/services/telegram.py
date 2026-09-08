import asyncio
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


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def send_message(text: str, max_attempts: int = 3) -> bool:
    if not telegram_configured():
        return False

    # Telegram Bot API requires the token in the URL path. Never log this URL.
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(url, json=payload)
            except httpx.RequestError as exc:
                log.warning(
                    "Telegram request failed (%s), attempt %s/%s",
                    type(exc).__name__,
                    attempt,
                    max_attempts,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 5))
                    continue
                return False

            data = _safe_json(response)

            if response.status_code == 429:
                parameters = data.get("parameters") or {}
                retry_after = parameters.get("retry_after", 1)
                try:
                    retry_after = max(1, int(retry_after))
                except (TypeError, ValueError):
                    retry_after = 1

                log.warning(
                    "Telegram rate limited (429); retry_after=%ss, attempt %s/%s",
                    retry_after,
                    attempt,
                    max_attempts,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(retry_after + 1)
                    continue
                return False

            if response.status_code >= 400:
                description = str(data.get("description") or "request rejected")
                log.warning(
                    "Telegram Bot API rejected message: status=%s description=%s",
                    response.status_code,
                    description[:300],
                )
                return False

            if not data.get("ok", False):
                description = str(data.get("description") or "ok=false")
                log.warning("Telegram Bot API returned ok=false: %s", description[:300])
                return False

            return True

    return False


async def send_startup_message(symbol_count: int) -> bool:
    text = (
        "✅ ThesisGuard worker online\n"
        "Telegram alerts: ACTIVE\n"
        f"Watching: {symbol_count} symbols\n"
        "Risk alerts will be pushed automatically when a material alert is created."
    )
    return await send_message(text)
