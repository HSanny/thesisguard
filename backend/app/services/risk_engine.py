from dataclasses import dataclass, field
from enum import Enum


class EvidenceType(str, Enum):
    FUNDAMENTAL = "fundamental"
    MACRO = "macro"
    DELEVERAGING = "deleveraging"
    VOLATILITY = "volatility"
    PRICE_CONFIRMED = "price_confirmed"
    COUNTER = "counter"


@dataclass(slots=True)
class Evidence:
    kind: EvidenceType
    label: str
    weight: float
    fresh: bool = True
    independently_confirmed: bool = False
    duplicate_group: str | None = None


@dataclass(slots=True)
class RiskDecision:
    severity: str
    confidence: str
    score: float
    reasons: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)


def assess(evidence: list[Evidence], price_conflict: bool = False) -> RiskDecision:
    seen_groups: set[str] = set()
    unique: list[Evidence] = []
    for e in evidence:
        if not e.fresh:
            continue
        if e.duplicate_group:
            if e.duplicate_group in seen_groups:
                continue
            seen_groups.add(e.duplicate_group)
        unique.append(e)

    positive = [e for e in unique if e.kind != EvidenceType.COUNTER]
    counter = [e for e in unique if e.kind == EvidenceType.COUNTER]

    score = sum(e.weight for e in positive) - sum(abs(e.weight) for e in counter)
    has_price_confirmation = any(e.kind == EvidenceType.PRICE_CONFIRMED and e.independently_confirmed for e in unique)
    has_thesis_damage = any(e.kind == EvidenceType.FUNDAMENTAL and e.independently_confirmed for e in unique)
    has_systemic_macro = any(e.kind == EvidenceType.MACRO and e.independently_confirmed for e in unique)

    if score >= 7 and (has_thesis_damage or has_systemic_macro or has_price_confirmation):
        severity = "red"
    elif score >= 3:
        severity = "yellow"
    else:
        severity = "green"

    confirmed_count = sum(e.independently_confirmed for e in positive)
    if price_conflict:
        confidence = "low"
    elif confirmed_count >= 2 and len(positive) >= 2:
        confidence = "high"
    elif confirmed_count >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    return RiskDecision(
        severity=severity,
        confidence=confidence,
        score=score,
        reasons=[e.label for e in positive],
        counter_evidence=[e.label for e in counter],
    )
