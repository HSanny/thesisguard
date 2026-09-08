from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
import httpx
import websockets
from ..config import settings

log = logging.getLogger("thesisguard.binance")


@dataclass(slots=True)
class RestMarketData:
    symbol: str
    price: float | None = None
    open_interest: float | None = None
    observed_at: datetime | None = None


@dataclass(slots=True)
class MarkPriceEvent:
    symbol: str
    mark_price: float
    index_price: float | None
    funding_rate: float | None
    event_time: datetime


class BinanceFuturesClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.binance_fapi_base

    async def all_prices(self) -> dict[str, float]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=8.0) as client:
            r = await client.get("/fapi/v1/ticker/price")
            r.raise_for_status()
            return {row["symbol"]: float(row["price"]) for row in r.json()}

    async def open_interest(self, symbol: str) -> float:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=8.0) as client:
            r = await client.get("/fapi/v1/openInterest", params={"symbol": symbol})
            r.raise_for_status()
            return float(r.json()["openInterest"])


class BinanceMarkPriceStream:
    def __init__(self, symbols: list[str], ws_base: str | None = None) -> None:
        self.symbols = sorted(set(s.upper() for s in symbols))
        self.ws_base = ws_base or settings.binance_ws_base

    @property
    def url(self) -> str:
        streams = "/".join(f"{s.lower()}@markPrice@1s" for s in self.symbols)
        return f"{self.ws_base}{streams}"

    async def events(self):
        backoff = 1
        while True:
            try:
                log.info("opening Binance USD-M websocket: %s", self.url)
                async with websockets.connect(
                    self.url,
                    ping_interval=120,
                    ping_timeout=30,
                    close_timeout=5,
                    max_queue=4096,
                ) as ws:
                    backoff = 1
                    log.info("Binance USD-M websocket connected")
                    async for message in ws:
                        event = parse_mark_price_message(message)
                        if event:
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Binance websocket connection/read failed: %r; retrying in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


def parse_mark_price_message(message: str | bytes | dict) -> MarkPriceEvent | None:
    import json
    if isinstance(message, (str, bytes)):
        payload = json.loads(message)
    else:
        payload = message
    data = payload.get("data", payload)
    if data.get("e") != "markPriceUpdate":
        return None
    event_ms = int(data.get("E") or data.get("T") or 0)
    return MarkPriceEvent(
        symbol=data["s"],
        mark_price=float(data["p"]),
        index_price=float(data["i"]) if data.get("i") not in (None, "") else None,
        funding_rate=float(data["r"]) if data.get("r") not in (None, "") else None,
        event_time=datetime.fromtimestamp(event_ms / 1000, tz=timezone.utc),
    )
