from backend.app.config import settings
from backend.app.services.telegram import format_alert_message, localized


def test_format_alert_message_contains_bilingual_risk_context():
    previous = settings.telegram_language
    settings.telegram_language = "bilingual"
    try:
        text = format_alert_message(
            symbol="LINKUSDT",
            severity="yellow",
            confidence="low",
            mark_price=12.3456,
            reasons=["price 3.00% below entry", "below configured level 12.5"],
            source_conflict=False,
            spread_bps=None,
            explanation="Price signal observed; structural confirmation is still required.",
        )
    finally:
        settings.telegram_language = previous

    assert "ThesisGuard YELLOW ALERT" in text
    assert "ThesisGuard YELLOW 风险警报" in text
    assert "价格较开仓价低 3.00%" in text
    assert "LINKUSDT" in text
    assert "Monitoring only · no auto-trading" in text
    assert "仅用于监控与决策支持" in text


def test_language_modes():
    previous = settings.telegram_language
    try:
        settings.telegram_language = "zh"
        assert localized("English", "中文") == "中文"

        settings.telegram_language = "en"
        assert localized("English", "中文") == "English"

        settings.telegram_language = "bilingual"
        text = localized("English", "中文")
        assert "中文" in text
        assert "English" in text
    finally:
        settings.telegram_language = previous
