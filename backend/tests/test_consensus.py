from backend.app.services.consensus import compare_ws_to_rest, build_multi_source_consensus


def test_ws_rest_agreement_is_high_confidence():
    r = compare_ws_to_rest(100.0, 100.01, conflict_bps=35)
    assert r.conflict is False
    assert r.confidence == "high"
    assert r.spread_bps < 35


def test_ws_rest_conflict_lowers_confidence():
    r = compare_ws_to_rest(100.0, 101.0, conflict_bps=35)
    assert r.conflict is True
    assert r.confidence == "low"


def test_three_source_agreement_is_high_confidence():
    r = build_multi_source_consensus(
        100.0,
        {"okx": 100.02, "bybit": 99.99},
        conflict_bps=35,
    )
    assert r.conflict is False
    assert r.confidence == "high"
    assert r.source_count == 3
    assert set(r.source_prices) == {"binance_ws", "okx", "bybit"}


def test_two_source_agreement_is_medium_confidence():
    r = build_multi_source_consensus(
        100.0,
        {"okx": 100.01, "bybit": None},
        conflict_bps=35,
    )
    assert r.conflict is False
    assert r.confidence == "medium"
    assert r.source_count == 2


def test_cross_exchange_outlier_forces_low_confidence():
    r = build_multi_source_consensus(
        100.0,
        {"okx": 100.01, "bybit": 102.0},
        conflict_bps=35,
    )
    assert r.conflict is True
    assert r.confidence == "low"
