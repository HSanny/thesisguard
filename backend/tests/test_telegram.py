from backend.app.services.telegram import format_alert_message


def test_format_alert_message_contains_risk_context():
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

    assert "ThesisGuard YELLOW ALERT" in text
    assert "LINKUSDT" in text
    assert "Confidence: LOW" in text
    assert "12.3456" in text
    assert "price 3.00% below entry" in text
    assert "Monitoring only · no auto-trading" in text
