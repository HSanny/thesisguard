import asyncio
import json
import logging
from sqlalchemy import select
from .config import settings
from .db import init_db, SessionLocal, MarketSnapshot, AlertRecord
from .portfolio import load_portfolio
from .services.binance import BinanceFuturesClient
from .services.risk_engine import Evidence, EvidenceType, assess

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("thesisguard.worker")


def symbols_from_portfolio() -> list[str]:
    p = load_portfolio()
    symbols = [x["symbol"] for x in p.get("positions", [])]
    symbols.extend(x["symbol"] for x in p.get("planned_entries", []))
    symbols.extend(p.get("watchlist", []))
    return sorted(set(symbols))


def evaluate_position(position: dict, price: float) -> tuple[str, str] | None:
    levels = sorted(position.get("key_levels", []))
    entry = float(position["entry"])
    side = position.get("side", "LONG")
    if side != "LONG":
        return None

    pct = (price / entry - 1) * 100
    evidence: list[Evidence] = []
    if pct <= -2.0:
        evidence.append(Evidence(EvidenceType.VOLATILITY, f"price {pct:.2f}% below entry", 2.0, True, False))
    if levels:
        below = [x for x in levels if x < entry and price < x]
        if below:
            nearest = max(below)
            evidence.append(Evidence(EvidenceType.PRICE_CONFIRMED, f"below configured level {nearest}", 2.0, True, False))

    d = assess(evidence)
    if d.severity == "green":
        return None
    return d.severity, json.dumps({"confidence": d.confidence, "score": d.score, "reasons": d.reasons, "counter": d.counter_evidence})


async def poll_once(client: BinanceFuturesClient) -> None:
    portfolio = load_portfolio()
    positions = {x["symbol"]: x for x in portfolio.get("positions", [])}
    for symbol in symbols_from_portfolio():
        try:
            snap = await client.snapshot(symbol)
        except Exception as exc:
            log.warning("snapshot failed %s: %s", symbol, exc)
            continue

        with SessionLocal() as db:
            db.add(MarketSnapshot(symbol=snap.symbol, price=snap.price, funding_rate=snap.funding_rate, open_interest=snap.open_interest, observed_at=snap.observed_at))
            db.commit()

        if symbol in positions:
            decision = evaluate_position(positions[symbol], snap.price)
            if decision:
                severity, details = decision
                with SessionLocal() as db:
                    latest = db.scalar(select(AlertRecord).where(AlertRecord.symbol == symbol).order_by(AlertRecord.created_at.desc()).limit(1))
                    if not latest or latest.severity != severity:
                        payload = json.loads(details)
                        db.add(AlertRecord(
                            severity=severity,
                            confidence=payload["confidence"],
                            symbol=symbol,
                            title=f"{symbol} risk state changed",
                            explanation="Price-only signal detected. Thesis confirmation is still required before escalation to red.",
                            evidence_json=details,
                        ))
                        db.commit()


async def main() -> None:
    init_db()
    client = BinanceFuturesClient()
    while True:
        await poll_once(client)
        await asyncio.sleep(settings.poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
