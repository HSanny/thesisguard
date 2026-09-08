from dataclasses import dataclass
from statistics import median


@dataclass(slots=True)
class PriceCheck:
    reference_price: float | None
    spread_bps: float | None
    conflict: bool
    confidence: str


@dataclass(slots=True)
class MultiSourceConsensus:
    reference_price: float | None
    source_prices: dict[str, float]
    source_count: int
    max_spread_bps: float | None
    conflict: bool
    confidence: str


def compare_ws_to_rest(ws_price: float, rest_price: float | None, conflict_bps: float = 35.0) -> PriceCheck:
    if rest_price is None or rest_price <= 0:
        return PriceCheck(rest_price, None, False, "low")
    midpoint = (ws_price + rest_price) / 2
    spread_bps = abs(ws_price - rest_price) / midpoint * 10_000
    conflict = spread_bps > conflict_bps
    return PriceCheck(rest_price, spread_bps, conflict, "low" if conflict else "high")


def build_multi_source_consensus(
    primary_price: float,
    external_prices: dict[str, float | None],
    conflict_bps: float = 35.0,
) -> MultiSourceConsensus:
    prices = {"binance_ws": float(primary_price)}
    for source, value in external_prices.items():
        if value is not None and value > 0:
            prices[source] = float(value)

    values = list(prices.values())
    reference = median(values) if values else None

    if len(values) <= 1 or reference is None:
        return MultiSourceConsensus(
            reference_price=reference,
            source_prices=prices,
            source_count=len(values),
            max_spread_bps=None,
            conflict=False,
            confidence="low",
        )

    spreads = [
        abs(value - reference) / reference * 10_000
        for value in values
        if reference > 0
    ]
    max_spread = max(spreads) if spreads else None
    conflict = bool(max_spread is not None and max_spread > conflict_bps)

    if conflict:
        confidence = "low"
    elif len(values) >= 3:
        confidence = "high"
    else:
        confidence = "medium"

    return MultiSourceConsensus(
        reference_price=reference,
        source_prices=prices,
        source_count=len(values),
        max_spread_bps=max_spread,
        conflict=conflict,
        confidence=confidence,
    )
