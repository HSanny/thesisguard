import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx

from ..config import settings

log = logging.getLogger("thesisguard.telegram")


def language_mode() -> str:
    value = (settings.telegram_language or "bilingual").strip().lower()
    return value if value in {"bilingual", "zh", "en"} else "bilingual"


def localized(en: str, zh: str) -> str:
    mode = language_mode()
    if mode == "zh":
        return zh
    if mode == "en":
        return en
    return f"{zh}\n\n────────── EN ──────────\n{en}"


def telegram_configured() -> bool:
    return bool(
        settings.telegram_alerts_enabled
        and settings.telegram_bot_token.strip()
        and settings.telegram_chat_id.strip()
    )


def _translate_reason(reason: str) -> str:
    match = re.fullmatch(r"price (-?\d+(?:\.\d+)?)% below entry", reason)
    if match:
        return f"价格较开仓价低 {match.group(1)}%"
    match = re.fullmatch(r"below configured level (.+)", reason)
    if match:
        return f"价格跌破配置关键位 {match.group(1)}"
    return reason


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
    reason_en = "\n".join(f"• {r}" for r in reasons) if reasons else "• No material reason recorded"
    reason_zh = "\n".join(f"• {_translate_reason(r)}" for r in reasons) if reasons else "• 暂无重大触发原因"
    price_text = "—" if mark_price is None else f"{mark_price:,.8f}".rstrip("0").rstrip(".")
    spread_text = "—" if spread_bps is None else f"{spread_bps:.2f} bps"
    conflict_en = "YES" if source_conflict else "NO"
    conflict_zh = "是" if source_conflict else "否"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    zh = (
        f"{emoji} ThesisGuard {sev} 风险警报\n"
        f"资产：{symbol}\n"
        f"置信度：{confidence.upper()}\n"
        f"标记价格：{price_text}\n"
        f"数据源冲突：{conflict_zh}\n"
        f"价差：{spread_text}\n\n"
        f"触发原因：\n{reason_zh}\n\n"
        "解读：检测到价格风险信号；在缺少结构性确认前，"
        "ThesisGuard 不会把单次跌破直接判定为投资逻辑失效。\n\n"
        f"时间：{now}\n"
        "仅用于监控与决策支持 · 不自动交易"
    )
    en = (
        f"{emoji} ThesisGuard {sev} ALERT\n"
        f"Asset: {symbol}\n"
        f"Confidence: {confidence.upper()}\n"
        f"Mark price: {price_text}\n"
        f"Source conflict: {conflict_en}\n"
        f"Spread: {spread_text}\n\n"
        f"Triggers:\n{reason_en}\n\n"
        f"Interpretation: {explanation}\n\n"
        f"Time: {now}\n"
        "Monitoring only · no auto-trading"
    )
    return localized(en, zh)


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
    return await send_message(
        localized(
            (
                "✅ ThesisGuard worker online\n"
                "Telegram alerts: ACTIVE\n"
                f"Watching: {symbol_count} symbols\n"
                "Risk and intelligence alerts will be pushed automatically."
            ),
            (
                "✅ ThesisGuard Worker 已上线\n"
                "Telegram 推送：已启用\n"
                f"监控资产：{symbol_count} 个\n"
                "风险与市场情报将在触发条件满足时自动推送。"
            ),
        )
    )
