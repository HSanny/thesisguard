from backend.app.services.risk_engine import Evidence, EvidenceType, assess


def test_skhynix_like_liquidity_sweep_is_not_red_without_confirmation():
    """Regression test from the real monitoring failure case.

    Stale/duplicated bearish reports + one liquidity sweep must not become a
    high-confidence red alert when fundamentals are intact and price has not
    confirmed a sustained structural break.
    """
    evidence = [
        Evidence(EvidenceType.MACRO, "older risk-off headline", 2, fresh=False, duplicate_group="macro1"),
        Evidence(EvidenceType.MACRO, "repost of same headline", 2, fresh=False, duplicate_group="macro1"),
        Evidence(EvidenceType.DELEVERAGING, "forced deleveraging spike", 2, fresh=True, independently_confirmed=True),
        Evidence(EvidenceType.VOLATILITY, "intraday stop sweep", 2, fresh=True, independently_confirmed=False),
        Evidence(EvidenceType.COUNTER, "HBM/AI memory thesis unchanged", 3, fresh=True, independently_confirmed=True),
        Evidence(EvidenceType.COUNTER, "sector relative strength recovered", 2, fresh=True, independently_confirmed=True),
    ]
    d = assess(evidence)
    assert d.severity != "red"
    assert "HBM/AI memory thesis unchanged" in d.counter_evidence
