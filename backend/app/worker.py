import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlalchemy import select
from .config import settings
from .db import init_db, SessionLocal, MarketTick, AlertRecord, ServiceHeartbeat
from .portfolio import load_portfolio, ensure_portfolio_seeded
from .services.binance import BinanceFuturesClient, BinanceMarkPriceStream, MarkPriceEvent
from .services.consensus import compare_ws_to_rest
from .services.risk_engine import Evidence, EvidenceType, assess

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("thesisguard.worker")


@dataclass
class SymbolState:
    event: MarkPriceEvent | None = None
    rest_price: float | None = None
    open_interest: float | None = None
    last_persisted_at: datetime | None = None
    recent_marks: list[float] = field(default_factory=list)


class WorkerState:
    def __init__(self) -> None:
        self.symbols: list[str] = []
        self.states: dict[str, SymbolState] = {}
        self.portfolio: dict = {}
        self.lock = asyncio.Lock()
        self.generation = 0

    async def refresh_portfolio(self) -> None:
        p = load_portfolio()
        symbols = [x["symbol"] for x in p.get("positions", [])]
        symbols += [x["symbol"] for x in p.get("planned_entries", [])]
        symbols += p.get("watchlist", [])
        symbols = sorted(set(s.upper() for s in symbols))
        async with self.lock:
            if symbols != self.symbols:
                self.generation += 1
            self.portfolio = p
            self.symbols = symbols
            for symbol in symbols:
                self.states.setdefault(symbol, SymbolState())


def _heartbeat(service: str, detail: dict) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.scalar(select(ServiceHeartbeat).where(ServiceHeartbeat.service == service).limit(1))
        if row:
            row.detail = json.dumps(detail)
            row.updated_at = now
        else:
            db.add(ServiceHeartbeat(service=service, detail=json.dumps(detail), updated_at=now))
        db.commit()


def evaluate_position(position: dict, state: SymbolState) -> tuple[str, dict] | None:
    if not state.event:
        return None
    price = state.event.mark_price
    levels = sorted(position.get("key_levels", []))
    entry = float(position["entry"])
    side = position.get("side", "LONG")
    if side != "LONG":
        return None

    pct = (price / entry - 1) * 100
    evidence: list[Evidence] = []
    if pct <= -2.0:
        evidence.append(Evidence(EvidenceType.VOLATILITY, f"price {pct:.2f}% below entry", 2.0, True, False))

    below = [x for x in levels if x < entry and price < x]
    if below:
        nearest = max(below)
        evidence.append(Evidence(EvidenceType.PRICE_CONFIRMED, f"below configured level {nearest}", 2.0, True, False))

    check = compare_ws_to_rest(price, state.rest_price, settings.price_conflict_bps)
    d = assess(evidence, price_conflict=check.conflict)
    if d.severity == "green":
        return None

    return d.severity, {
        "confidence": d.confidence,
        "score": d.score,
        "reasons": d.reasons,
        "counter": d.counter_evidence,
        "source_conflict": check.conflict,
        "spread_bps": check.spread_bps,
    }


async def websocket_loop(state: WorkerState) -> None:
    while True:
        await state.refresh_portfolio()
        symbols = list(state.symbols)
        if not symbols:
            await asyncio.sleep(5)
            continue
        stream = BinanceMarkPriceStream(symbols)
        generation = state.generation
        log.info("connecting Binance USD-M WS for %s", ",".join(symbols))
        try:
            async for event in stream.events():
                current = set(state.symbols)
                if event.symbol not in current or state.generation != generation:
                    log.info("subscriptions changed; reconnecting Binance WS")
                    break
                s = state.states.setdefault(event.symbol, SymbolState())
                s.event = event
                s.recent_marks = (s.recent_marks + [event.mark_price])[-12:]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("websocket loop error: %s", exc)
            await asyncio.sleep(2)


async def rest_validation_loop(state: WorkerState) -> None:
    client = BinanceFuturesClient()
    oi_cycle = 0
    while True:
        try:
            prices = await client.all_prices()
            for symbol in list(state.symbols):
                if symbol in prices:
                    state.states.setdefault(symbol, SymbolState()).rest_price = prices[symbol]
            oi_cycle += settings.rest_validation_seconds
            if oi_cycle >= settings.oi_refresh_seconds:
                oi_cycle = 0
                symbols = list(state.symbols)
                results = await asyncio.gather(
                    *(client.open_interest(s) for s in symbols),
                    return_exceptions=True,
                )
                for symbol, result in zip(symbols, results):
                    if not isinstance(result, Exception):
                        state.states.setdefault(symbol, SymbolState()).open_interest = float(result)
            _heartbeat("worker-rest", {"symbols": len(state.symbols), "last_validation": datetime.now(timezone.utc).isoformat()})
        except Exception as exc:
            log.warning("REST validation failed: %s", exc)
        await asyncio.sleep(settings.rest_validation_seconds)


async def persistence_loop(state: WorkerState) -> None:
    while True:
        now = datetime.now(timezone.utc)
        positions = {x["symbol"].upper(): x for x in state.portfolio.get("positions", [])}
        for symbol in list(state.symbols):
            s = state.states.get(symbol)
            if not s or not s.event:
                continue
            if s.last_persisted_at and (now - s.last_persisted_at).total_seconds() < settings.persist_interval_seconds:
                continue
            check = compare_ws_to_rest(s.event.mark_price, s.rest_price, settings.price_conflict_bps)
            with SessionLocal() as db:
                db.add(MarketTick(
                    symbol=symbol,
                    mark_price=s.event.mark_price,
                    index_price=s.event.index_price,
                    rest_price=s.rest_price,
                    funding_rate=s.event.funding_rate,
                    open_interest=s.open_interest,
                    spread_bps=check.spread_bps,
                    source_conflict=check.conflict,
                    source_confidence=check.confidence,
                    event_time=s.event.event_time,
                    received_at=now,
                ))
                db.commit()
            s.last_persisted_at = now

            if symbol in positions:
                decision = evaluate_position(positions[symbol], s)
                if decision:
                    severity, details = decision
                    dedupe_key = f"{symbol}:{severity}:{','.join(details['reasons'])}"
                    with SessionLocal() as db:
                        latest = db.scalar(select(AlertRecord).where(AlertRecord.dedupe_key == dedupe_key).order_by(AlertRecord.created_at.desc()).limit(1))
                        if latest:
                            ts = latest.created_at if latest.created_at.tzinfo else latest.created_at.replace(tzinfo=timezone.utc)
                            should_write = (now - ts).total_seconds() > 3600
                        else:
                            should_write = True
                        if should_write:
                            db.add(AlertRecord(
                                severity=severity,
                                confidence=details["confidence"],
                                symbol=symbol,
                                title=f"{symbol} risk state changed",
                                explanation="Price signal observed; v0.2 will not treat an unconfirmed crossing as thesis invalidation.",
                                evidence_json=json.dumps(details),
                                dedupe_key=dedupe_key,
                            ))
                            db.commit()
        _heartbeat("worker-ws", {"symbols": len(state.symbols), "last_persist": now.isoformat()})
        await asyncio.sleep(1)


async def portfolio_refresh_loop(state: WorkerState) -> None:
    last_symbols: tuple[str, ...] = ()
    while True:
        await state.refresh_portfolio()
        current = tuple(state.symbols)
        if current != last_symbols:
            log.info("portfolio/watchlist changed: %s", current)
            last_symbols = current
        await asyncio.sleep(settings.portfolio_refresh_seconds)


async def main() -> None:
    init_db()
    ensure_portfolio_seeded()
    state = WorkerState()
    await state.refresh_portfolio()
    await asyncio.gather(
        websocket_loop(state),
        rest_validation_loop(state),
        persistence_loop(state),
        portfolio_refresh_loop(state),
    )


if __name__ == "__main__":
    asyncio.run(main())
