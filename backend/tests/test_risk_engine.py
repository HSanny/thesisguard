from backend.app.services.risk_engine import Evidence, EvidenceType, assess


def test_duplicate_headlines_do_not_stack():
    evidence = [
        Evidence(EvidenceType.MACRO, "bearish headline A", 3, duplicate_group="same_event"),
        Evidence(EvidenceType.MACRO, "bearish headline B", 3, duplicate_group="same_event"),
    ]
    d = assess(evidence)
    assert d.score == 3
    assert d.severity == "yellow"


def test_source_conflict_caps_confidence():
    evidence = [Evidence(EvidenceType.PRICE_CONFIRMED, "breakdown", 8, independently_confirmed=True)]
    d = assess(evidence, price_conflict=True)
    assert d.confidence == "low"
