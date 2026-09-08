from datetime import datetime, timezone

from backend.app.services.binance import MarkPriceEvent
from backend.app.worker import SymbolState, evaluate_planned_entry


def _state(price: float) -> SymbolState:
    return SymbolState(
        event=MarkPriceEvent(
            symbol="ETHUSDT",
            mark_price=price,
            index_price=price,
            funding_rate=0.0,
            event_time=datetime.now(timezone.utc),
        )
    )


def test_planned_entry_approaching():
    result = evaluate_planned_entry(
        {"symbol": "ETHUSDT", "side": "LONG", "trigger": 2200},
        _state(2230),
    )
    assert result is not None
    severity, state, details = result
    assert severity == "blue"
    assert state == "approaching"
    assert details["distance_pct"] < 2


def test_planned_entry_reassess_at_trigger():
    result = evaluate_planned_entry(
        {"symbol": "ETHUSDT", "side": "LONG", "trigger": 2200},
        _state(2195),
    )
    assert result is not None
    severity, state, _ = result
    assert severity == "yellow"
    assert state == "reassess"


def test_planned_entry_far_does_not_alert():
    assert evaluate_planned_entry(
        {"symbol": "ETHUSDT", "side": "LONG", "trigger": 2200},
        _state(2500),
    ) is None
