from dataclasses import dataclass


@dataclass(slots=True)
class PriceCheck:
    reference_price: float | None
    spread_bps: float | None
    conflict: bool
    confidence: str


def compare_ws_to_rest(ws_price: float, rest_price: float | None, conflict_bps: float = 35.0) -> PriceCheck:
    if rest_price is None or rest_price <= 0:
        return PriceCheck(rest_price, None, False, "low")
    midpoint = (ws_price + rest_price) / 2
    spread_bps = abs(ws_price - rest_price) / midpoint * 10_000
    conflict = spread_bps > conflict_bps
    return PriceCheck(rest_price, spread_bps, conflict, "low" if conflict else "high")
