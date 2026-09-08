from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median


@dataclass(slots=True)
class PriceObservation:
    source: str
    price: float
    observed_at: datetime


@dataclass(slots=True)
class ConsensusResult:
    price: float
    conflict: bool
    spread_bps: float
    confidence: str
    sources: list[str]


def price_consensus(observations: list[PriceObservation], conflict_bps: float = 35.0, stale_after_seconds: int = 90) -> ConsensusResult:
    if not observations:
        raise ValueError("at least one observation is required")

    now = datetime.now(timezone.utc)
    fresh = [o for o in observations if (now - o.observed_at).total_seconds() <= stale_after_seconds]
    if not fresh:
        fresh = observations

    prices = [o.price for o in fresh]
    mid = median(prices)
    spread_bps = 0.0 if len(prices) == 1 else (max(prices) - min(prices)) / mid * 10_000
    conflict = len(prices) > 1 and spread_bps > conflict_bps

    if len(fresh) >= 2 and not conflict:
        confidence = "high"
    elif conflict or len(fresh) == 1:
        confidence = "low"
    else:
        confidence = "medium"

    return ConsensusResult(mid, conflict, spread_bps, confidence, [o.source for o in fresh])
