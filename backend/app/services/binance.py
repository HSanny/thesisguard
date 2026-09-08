from dataclasses import dataclass
from datetime import datetime, timezone
import httpx
from ..config import settings


@dataclass(slots=True)
class BinanceSnapshot:
    symbol: str
    price: float
    funding_rate: float | None
    open_interest: float | None
    observed_at: datetime


class BinanceFuturesClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.binance_fapi_base

    async def snapshot(self, symbol: str) -> BinanceSnapshot:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=8.0) as client:
            price_r, premium_r, oi_r = await _gather(client, symbol)
        price = float(price_r["price"])
        funding = float(premium_r["lastFundingRate"]) if premium_r.get("lastFundingRate") not in (None, "") else None
        oi = float(oi_r["openInterest"]) if oi_r.get("openInterest") not in (None, "") else None
        return BinanceSnapshot(
            symbol=symbol,
            price=price,
            funding_rate=funding,
            open_interest=oi,
            observed_at=datetime.now(timezone.utc),
        )


async def _gather(client: httpx.AsyncClient, symbol: str):
    import asyncio

    async def get(path: str):
        r = await client.get(path, params={"symbol": symbol})
        r.raise_for_status()
        return r.json()

    return await asyncio.gather(
        get("/fapi/v1/ticker/price"),
        get("/fapi/v1/premiumIndex"),
        get("/fapi/v1/openInterest"),
    )
