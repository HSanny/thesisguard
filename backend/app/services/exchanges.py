import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


@dataclass(slots=True)
class ExchangeSnapshot:
    source: str
    symbol: str
    mark_price: float | None
    index_price: float | None
    funding_rate: float | None
    open_interest_usd: float | None
    source_timestamp: datetime | None


def _float(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt_ms(value) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def to_okx_inst_id(symbol: str) -> str:
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        raise ValueError(f"unsupported OKX symbol: {symbol}")
    return f"{symbol[:-4]}-USDT-SWAP"


def from_okx_inst_id(inst_id: str) -> str | None:
    parts = inst_id.upper().split("-")
    if len(parts) == 3 and parts[1] == "USDT" and parts[2] == "SWAP":
        return parts[0] + "USDT"
    return None


class BybitPublicMarketClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def snapshots(self, symbols: list[str]) -> dict[str, ExchangeSnapshot]:
        wanted = {s.upper() for s in symbols}
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(
                self.base_url + "/v5/market/tickers",
                params={"category": "linear"},
            )
            response.raise_for_status()
            data = response.json()

        if int(data.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit retCode={data.get('retCode')}")

        result: dict[str, ExchangeSnapshot] = {}
        top_ts = _dt_ms(data.get("time"))
        for row in (data.get("result") or {}).get("list", []) or []:
            symbol = str(row.get("symbol") or "").upper()
            if symbol not in wanted:
                continue
            result[symbol] = ExchangeSnapshot(
                source="bybit",
                symbol=symbol,
                mark_price=_float(row.get("markPrice")),
                index_price=_float(row.get("indexPrice")),
                funding_rate=_float(row.get("fundingRate")),
                open_interest_usd=_float(row.get("openInterestValue")),
                source_timestamp=top_ts,
            )
        return result


class OKXPublicMarketClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def snapshots(self, symbols: list[str]) -> dict[str, ExchangeSnapshot]:
        wanted = {s.upper() for s in symbols}
        async with httpx.AsyncClient(timeout=12.0) as client:
            mark_resp, oi_resp = await asyncio.gather(
                client.get(
                    self.base_url + "/api/v5/public/mark-price",
                    params={"instType": "SWAP"},
                ),
                client.get(
                    self.base_url + "/api/v5/public/open-interest",
                    params={"instType": "SWAP"},
                ),
            )
            mark_resp.raise_for_status()
            oi_resp.raise_for_status()
            marks = mark_resp.json()
            oi = oi_resp.json()

            if str(marks.get("code")) != "0":
                raise RuntimeError(f"OKX mark-price code={marks.get('code')}")
            if str(oi.get("code")) != "0":
                raise RuntimeError(f"OKX open-interest code={oi.get('code')}")

            rows: dict[str, ExchangeSnapshot] = {}
            for row in marks.get("data", []) or []:
                symbol = from_okx_inst_id(str(row.get("instId") or ""))
                if not symbol or symbol not in wanted:
                    continue
                rows[symbol] = ExchangeSnapshot(
                    source="okx",
                    symbol=symbol,
                    mark_price=_float(row.get("markPx")),
                    index_price=None,
                    funding_rate=None,
                    open_interest_usd=None,
                    source_timestamp=_dt_ms(row.get("ts")),
                )

            for row in oi.get("data", []) or []:
                symbol = from_okx_inst_id(str(row.get("instId") or ""))
                if not symbol or symbol not in rows:
                    continue
                snap = rows[symbol]
                snap.open_interest_usd = _float(row.get("oiUsd"))
                snap.source_timestamp = _dt_ms(row.get("ts")) or snap.source_timestamp

            funding_targets = [
                (symbol, to_okx_inst_id(symbol))
                for symbol in rows
            ]
            funding_results = await asyncio.gather(
                *(
                    client.get(
                        self.base_url + "/api/v5/public/funding-rate-history",
                        params={"instId": inst_id, "limit": "1"},
                    )
                    for _, inst_id in funding_targets
                ),
                return_exceptions=True,
            )
            for (symbol, _), response in zip(funding_targets, funding_results):
                if isinstance(response, Exception):
                    continue
                try:
                    response.raise_for_status()
                    payload = response.json()
                    first = (payload.get("data") or [None])[0]
                    if str(payload.get("code")) == "0" and first:
                        rows[symbol].funding_rate = _float(
                            first.get("realizedRate") or first.get("fundingRate")
                        )
                except Exception:
                    continue

        return rows
