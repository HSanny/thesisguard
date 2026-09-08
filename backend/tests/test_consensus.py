from backend.app.services.consensus import compare_ws_to_rest


def test_ws_rest_agreement_is_high_confidence():
    r = compare_ws_to_rest(100.0, 100.01, conflict_bps=35)
    assert r.conflict is False
    assert r.confidence == "high"
    assert r.spread_bps < 35


def test_ws_rest_conflict_lowers_confidence():
    r = compare_ws_to_rest(100.0, 101.0, conflict_bps=35)
    assert r.conflict is True
    assert r.confidence == "low"
