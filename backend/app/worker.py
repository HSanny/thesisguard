import asyncio
import json
import logging
import httpx
from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlalchemy import select
from .config import settings
from .db import init_db, SessionLocal, MarketTick, AlertRecord, ServiceHeartbeat, IntelligenceEvent, MarketSourceSnapshot
from .portfolio import load_portfolio, ensure_portfolio_seeded
from .services.binance import BinanceFuturesClient, BinanceMarkPriceStream, MarkPriceEvent
from .services.consensus import build_multi_source_consensus
from .services.risk_engine import Evidence, EvidenceType, assess
from .services.exchanges import BybitPublicMarketClient, OKXPublicMarketClient, ExchangeSnapshot
from .services.intelligence import (
    already_alerted,
    annotate_corroboration,
    fetch_bls_calendar,
    fetch_coinmarketcal,
    fetch_fed_monetary,
    fetch_fomc_calendar,
    fetch_gdelt,
    format_event_message,
    immediate_push_candidate,
    record_intelligence_alert,
    upcoming_stage,
    upsert_events,
)
from .services.telegram import (
    format_alert_message,
    localized,
    send_message as send_telegram_message,
    send_startup_message,
    telegram_configured,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx request INFO logs can expose secret-bearing URLs (Telegram bot tokens live in
# the path). Keep transport libraries quiet and log only sanitized application events.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("thesisguard.worker")


@dataclass
class SymbolState:
    event: MarkPriceEvent | None = None
    rest_price: float | None = None
    open_interest: float | None = None
    last_persisted_at: datetime | None = None
    recent_marks: list[float] = field(default_factory=list)
    external: dict[str, ExchangeSnapshot] = field(default_factory=dict)
    external_received_at: dict[str, datetime] = field(default_factory=dict)


class WorkerState:
    def __init__(self) -> None:
        self.symbols: list[str] = []
        self.states: dict[str, SymbolState] = {}
        self.portfolio: dict = {}
        self.lock = asyncio.Lock()
        self.generation = 0
        self.last_ws_event_at: datetime | None = None
        self.last_tick_persisted_at: datetime | None = None

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


def _fresh_external_prices(state: SymbolState, now: datetime | None = None) -> dict[str, float | None]:
    now = now or datetime.now(timezone.utc)
    max_age = max(90, settings.exchange_validation_seconds * 3)
    out: dict[str, float | None] = {}
    for source, snap in state.external.items():
        received = state.external_received_at.get(source)
        if received is None or (now - received).total_seconds() > max_age:
            continue
        out[source] = snap.mark_price
    return out


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

    consensus = build_multi_source_consensus(
        price,
        _fresh_external_prices(state),
        settings.price_conflict_bps,
    )
    d = assess(evidence, price_conflict=consensus.conflict)
    if d.severity == "green":
        return None

    return d.severity, {
        "confidence": d.confidence,
        "score": d.score,
        "reasons": d.reasons,
        "counter": d.counter_evidence,
        "source_conflict": consensus.conflict,
        "spread_bps": consensus.max_spread_bps,
        "source_confidence": consensus.confidence,
        "source_count": consensus.source_count,
        "source_prices": consensus.source_prices,
    }


def evaluate_planned_entry(entry: dict, state: SymbolState) -> tuple[str, str, dict] | None:
    if not state.event:
        return None
    if entry.get("side", "LONG") != "LONG":
        return None

    price = state.event.mark_price
    symbol = entry["symbol"].upper()
    threshold_pct = 2.0
    state_name = None
    trigger_desc = None
    distance_pct = None

    if entry.get("trigger") is not None:
        trigger = float(entry["trigger"])
        distance_pct = (price / trigger - 1) * 100
        if price <= trigger:
            state_name = "reassess"
        elif distance_pct <= threshold_pct:
            state_name = "approaching"
        trigger_desc = f"{trigger:g}"

    elif entry.get("trigger_lte") is not None:
        trigger = float(entry["trigger_lte"])
        distance_pct = (price / trigger - 1) * 100
        if price <= trigger:
            state_name = "reassess"
        elif distance_pct <= threshold_pct:
            state_name = "approaching"
        trigger_desc = f"<= {trigger:g}"

    elif entry.get("trigger_range"):
        low, high = [float(x) for x in entry["trigger_range"]]
        if low <= price <= high:
            state_name = "reassess"
            distance_pct = 0.0
        elif price > high:
            distance_pct = (price / high - 1) * 100
            if distance_pct <= threshold_pct:
                state_name = "approaching"
        trigger_desc = f"{low:g}–{high:g}"

    if not state_name:
        return None

    severity = "yellow" if state_name == "reassess" else "blue"
    return severity, state_name, {
        "symbol": symbol,
        "price": price,
        "trigger": trigger_desc,
        "distance_pct": distance_pct,
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
                state.last_ws_event_at = datetime.now(timezone.utc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("websocket loop error: %s", exc)
            await asyncio.sleep(2)


async def rest_validation_loop(state: WorkerState) -> None:
    client = BinanceFuturesClient()
    oi_cycle = 0
    blocked_451_announced = False

    while True:
        sleep_seconds = settings.rest_validation_seconds
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

            _heartbeat("worker-rest", {
                "status": "live",
                "symbols": len(state.symbols),
                "last_validation": datetime.now(timezone.utc).isoformat(),
            })
            blocked_451_announced = False

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 451:
                sleep_seconds = max(900, settings.rest_validation_seconds)
                _heartbeat("worker-rest", {
                    "status": "blocked_451",
                    "last_failure": datetime.now(timezone.utc).isoformat(),
                    "detail": "Binance Futures REST unavailable from current deployment egress",
                    "retry_seconds": sleep_seconds,
                })
                if not blocked_451_announced:
                    blocked_451_announced = True
                    log.warning(
                        "Binance Futures REST blocked with 451; backing off for %ss",
                        sleep_seconds,
                    )
                    await _push_system_message(localized(
                        (
                            "⚠️ ThesisGuard Data Source DEGRADED\n"
                            "Binance Futures REST validation is blocked (HTTP 451) from the current cloud egress.\n"
                            "Primary WebSocket monitoring can remain live. Binance REST price validation and Binance OI are unavailable.\n"
                            "OKX/Bybit cross-exchange validation continues independently when those feeds are reachable."
                        ),
                        (
                            "⚠️ ThesisGuard 数据源降级\n"
                            "当前云端出口访问 Binance Futures REST 被 HTTP 451 阻断。\n"
                            "主 WebSocket 行情仍可继续监控；Binance REST 价格校验与 Binance OI 暂不可用。\n"
                            "只要 OKX/Bybit 可访问，跨交易所验证仍会独立继续运行。"
                        ),
                    ))
            else:
                log.warning("REST validation HTTP failure: status=%s", status)

        except Exception as exc:
            log.warning("REST validation failed: %s", type(exc).__name__)

        await asyncio.sleep(sleep_seconds)


async def multi_source_validation_loop(state: WorkerState) -> None:
    if not settings.multi_source_enabled:
        return

    clients = {
        "bybit": BybitPublicMarketClient(settings.bybit_rest_base),
        "okx": OKXPublicMarketClient(settings.okx_rest_base),
    }
    previous_external_count = 0

    while True:
        symbols = list(state.symbols)
        now = datetime.now(timezone.utc)
        results = await asyncio.gather(
            *(client.snapshots(symbols) for client in clients.values()),
            return_exceptions=True,
        )

        source_status: dict[str, dict] = {}
        snapshots_to_store: list[MarketSourceSnapshot] = []

        for (source, _client), result in zip(clients.items(), results):
            if isinstance(result, Exception):
                source_status[source] = {
                    "status": "error",
                    "error_type": type(result).__name__,
                    "symbols": 0,
                }
                log.warning("%s market validation failed: %s", source, type(result).__name__)
                continue

            source_status[source] = {
                "status": "live",
                "symbols": len(result),
            }
            for symbol, snap in result.items():
                s = state.states.setdefault(symbol, SymbolState())
                s.external[source] = snap
                s.external_received_at[source] = now
                snapshots_to_store.append(MarketSourceSnapshot(
                    source=source,
                    symbol=symbol,
                    mark_price=snap.mark_price,
                    index_price=snap.index_price,
                    funding_rate=snap.funding_rate,
                    open_interest_usd=snap.open_interest_usd,
                    source_timestamp=snap.source_timestamp,
                    received_at=now,
                ))

        if snapshots_to_store:
            with SessionLocal() as db:
                db.add_all(snapshots_to_store)
                db.commit()

        live_sources = sum(1 for v in source_status.values() if v.get("status") == "live")
        _heartbeat("worker-sources", {
            "status": "live" if live_sources else "degraded",
            "live_sources": live_sources,
            "sources": source_status,
            "last_validation": now.isoformat(),
        })

        if previous_external_count > 0 and live_sources == 0:
            await _push_system_message(localized(
                (
                    "⚠️ ThesisGuard External Market Validation LOST\n"
                    "Both OKX and Bybit validation feeds are currently unavailable.\n"
                    "Binance WebSocket may still be live, but cross-exchange confidence is reduced."
                ),
                (
                    "⚠️ ThesisGuard 外部市场验证中断\n"
                    "OKX 与 Bybit 当前均无法提供验证数据。\n"
                    "Binance WebSocket 可能仍正常，但跨交易所置信度已经降低。"
                ),
            ))
        elif previous_external_count == 0 and live_sources > 0:
            await _push_system_message(localized(
                (
                    "✅ ThesisGuard Cross-Exchange Validation LIVE\n"
                    f"Independent sources available: {live_sources}/2 (OKX / Bybit).\n"
                    "Price consensus can now use multiple exchanges."
                ),
                (
                    "✅ ThesisGuard 跨交易所验证已上线\n"
                    f"独立数据源可用：{live_sources}/2（OKX / Bybit）。\n"
                    "价格共识现在可以使用多个交易所进行交叉验证。"
                ),
            ))

        previous_external_count = live_sources
        await asyncio.sleep(settings.exchange_validation_seconds)


async def persistence_loop(state: WorkerState) -> None:
    while True:
        now = datetime.now(timezone.utc)
        positions = {x["symbol"].upper(): x for x in state.portfolio.get("positions", [])}
        planned_entries = {}
        for item in state.portfolio.get("planned_entries", []):
            planned_entries.setdefault(item["symbol"].upper(), []).append(item)
        persisted_this_cycle = 0

        for symbol in list(state.symbols):
            s = state.states.get(symbol)
            if not s or not s.event:
                continue
            if s.last_persisted_at and (now - s.last_persisted_at).total_seconds() < settings.persist_interval_seconds:
                continue

            consensus = build_multi_source_consensus(
                s.event.mark_price,
                _fresh_external_prices(s, now),
                settings.price_conflict_bps,
            )
            external_values = [
                value for source, value in consensus.source_prices.items()
                if source != "binance_ws"
            ]
            external_reference = (
                sum(external_values) / len(external_values)
                if external_values else None
            )
            with SessionLocal() as db:
                db.add(MarketTick(
                    symbol=symbol,
                    mark_price=s.event.mark_price,
                    index_price=s.event.index_price,
                    rest_price=external_reference,
                    funding_rate=s.event.funding_rate,
                    open_interest=s.open_interest,
                    spread_bps=consensus.max_spread_bps,
                    source_conflict=consensus.conflict,
                    source_confidence=consensus.confidence,
                    event_time=s.event.event_time,
                    received_at=now,
                ))
                db.commit()

            s.last_persisted_at = now
            state.last_tick_persisted_at = now
            persisted_this_cycle += 1

            # Event-only mode uses market data as context, never as an alert trigger.
            if settings.telegram_event_only:
                continue

            if symbol in positions:
                decision = evaluate_position(positions[symbol], s)
                if decision:
                    severity, details = decision
                    dedupe_key = f"{symbol}:{severity}:{','.join(details['reasons'])}"
                    with SessionLocal() as db:
                        latest = db.scalar(
                            select(AlertRecord)
                            .where(AlertRecord.dedupe_key == dedupe_key)
                            .order_by(AlertRecord.created_at.desc())
                            .limit(1)
                        )
                        if latest:
                            ts = latest.created_at if latest.created_at.tzinfo else latest.created_at.replace(tzinfo=timezone.utc)
                            should_write = (now - ts).total_seconds() > 3600
                        else:
                            should_write = True
                        if should_write:
                            explanation = (
                                "Price signal observed; ThesisGuard does not treat an "
                                "unconfirmed crossing as thesis invalidation."
                            )
                            db.add(AlertRecord(
                                severity=severity,
                                confidence=details["confidence"],
                                symbol=symbol,
                                title=f"{symbol} risk state changed",
                                explanation=explanation,
                                evidence_json=json.dumps(details),
                                dedupe_key=dedupe_key,
                            ))
                            db.commit()

                            telegram_text = format_alert_message(
                                symbol=symbol,
                                severity=severity,
                                confidence=details["confidence"],
                                mark_price=s.event.mark_price,
                                reasons=details["reasons"],
                                source_conflict=details["source_conflict"],
                                spread_bps=details["spread_bps"],
                                explanation=explanation,
                            )
                            delivered = await send_telegram_message(telegram_text)
                            if settings.telegram_alerts_enabled:
                                log.info(
                                    "Telegram alert %s for %s",
                                    "delivered" if delivered else "not delivered",
                                    symbol,
                                )


            for planned in planned_entries.get(symbol, []):
                planned_decision = evaluate_planned_entry(planned, s)
                if not planned_decision:
                    continue
                severity, entry_state, details = planned_decision
                dedupe_key = f"planned:{symbol}:{entry_state}:{details['trigger']}"
                with SessionLocal() as db:
                    latest = db.scalar(
                        select(AlertRecord)
                        .where(AlertRecord.dedupe_key == dedupe_key)
                        .order_by(AlertRecord.created_at.desc())
                        .limit(1)
                    )
                    should_write = True
                    if latest:
                        ts = latest.created_at if latest.created_at.tzinfo else latest.created_at.replace(tzinfo=timezone.utc)
                        should_write = (now - ts).total_seconds() > 6 * 3600
                    if should_write:
                        explanation = (
                            f"Planned entry is {entry_state}. Current mark={details['price']:.8f}; "
                            f"configured trigger={details['trigger']}. Reassess market structure before entry."
                        )
                        db.add(AlertRecord(
                            severity=severity,
                            confidence="medium" if entry_state == "reassess" else "low",
                            symbol=symbol,
                            title=f"{symbol} planned entry {entry_state}",
                            explanation=explanation,
                            evidence_json=json.dumps(details),
                            dedupe_key=dedupe_key,
                        ))
                        db.commit()

                        icon = "🎯" if entry_state == "reassess" else "🔵"
                        await send_telegram_message(localized(
                            (
                                f"{icon} ThesisGuard Planned Entry\n"
                                f"Asset: {symbol}\n"
                                f"State: {entry_state.upper()}\n"
                                f"Mark price: {details['price']:.8f}\n"
                                f"Configured trigger: {details['trigger']}\n"
                                f"Distance: {details['distance_pct']:.2f}%\n\n"
                                "Reassess structure, leverage and thesis before placing an order. "
                                "No auto-trading."
                            ),
                            (
                                f"{icon} ThesisGuard 计划入场提醒\n"
                                f"资产：{symbol}\n"
                                f"状态：{'进入重新评估区' if entry_state == 'reassess' else '接近计划价'}\n"
                                f"标记价格：{details['price']:.8f}\n"
                                f"配置触发位：{details['trigger']}\n"
                                f"距离：{details['distance_pct']:.2f}%\n\n"
                                "请在下单前重新评估市场结构、杠杆与投资逻辑。系统不会自动交易。"
                            ),
                        ))

        if persisted_this_cycle > 0:
            _heartbeat("worker-ws", {
                "symbols": len(state.symbols),
                "last_ws_event": state.last_ws_event_at.isoformat() if state.last_ws_event_at else None,
                "last_persist": state.last_tick_persisted_at.isoformat() if state.last_tick_persisted_at else None,
                "rows_persisted": persisted_this_cycle,
            })

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


async def _push_system_message(text: str, *, critical: bool = False) -> bool:
    if not settings.telegram_alerts_enabled:
        return False
    if settings.telegram_event_only and not critical:
        return False
    return await send_telegram_message(text)


def _event_market_context(event: IntelligenceEvent, state: WorkerState) -> list[dict]:
    try:
        assets = json.loads(event.affected_assets_json or "[]")
        themes = set(json.loads(event.themes_json or "[]"))
    except Exception:
        assets = []
        themes = set()

    symbols = [
        str(symbol).upper()
        for symbol in assets
        if str(symbol).upper().endswith("USDT") and str(symbol).upper() != "XAUUSDT"
    ]

    # Broad crypto events without a specific token still get BTC/ETH market context.
    if "crypto" in themes and not symbols:
        symbols = ["BTCUSDT", "ETHUSDT"]

    context: list[dict] = []
    now = datetime.now(timezone.utc)
    for symbol in symbols[:6]:
        s = state.states.get(symbol)
        if not s or not s.event:
            continue
        consensus = build_multi_source_consensus(
            s.event.mark_price,
            _fresh_external_prices(s, now),
            settings.price_conflict_bps,
        )
        context.append({
            "symbol": symbol,
            "price": s.event.mark_price,
            "confidence": consensus.confidence,
            "source_count": consensus.source_count,
        })
    return context


async def critical_source_health_loop(state: WorkerState) -> None:
    """Only interrupt the user when all usable market-price sources are unavailable."""
    if not settings.telegram_critical_system_alerts_enabled:
        return

    started_at = datetime.now(timezone.utc)
    outage_announced = False
    threshold = max(180, settings.stale_after_seconds * 2)

    while True:
        now = datetime.now(timezone.utc)
        primary_live = bool(
            state.last_tick_persisted_at
            and (now - state.last_tick_persisted_at).total_seconds() <= threshold
        )
        external_live = any(
            bool(_fresh_external_prices(symbol_state, now))
            for symbol_state in state.states.values()
        )
        all_sources_lost = not primary_live and not external_live
        past_startup_grace = (now - started_at).total_seconds() > threshold

        if all_sources_lost and past_startup_grace and not outage_announced:
            outage_announced = True
            await _push_system_message(localized(
                (
                    "🔴 ThesisGuard CRITICAL DATA OUTAGE\n"
                    "No usable live price source is currently available from Binance WS, OKX or Bybit.\n"
                    "Event alerts will continue to collect news, but market-price context may be unavailable."
                ),
                (
                    "🔴 ThesisGuard 严重数据中断\n"
                    "Binance WebSocket、OKX 与 Bybit 当前都没有可用的实时价格数据。\n"
                    "新闻事件仍会继续采集，但事件推送中的市场价格上下文可能暂时缺失。"
                ),
            ), critical=True)

        elif outage_announced and (primary_live or external_live):
            outage_announced = False
            await _push_system_message(localized(
                (
                    "✅ ThesisGuard MARKET DATA RECOVERED\n"
                    "At least one live market-price source is available again."
                ),
                (
                    "✅ ThesisGuard 市场数据已恢复\n"
                    "至少一个实时市场价格数据源已经恢复可用。"
                ),
            ), critical=True)

        await asyncio.sleep(30)


async def market_health_loop(state: WorkerState) -> None:
    """Push feed-live/stale/recovered status without blocking market collection."""
    if not settings.telegram_feed_health_enabled:
        return
    live_announced = False
    stale_announced = False
    last_hourly = datetime.now(timezone.utc)

    while True:
        now = datetime.now(timezone.utc)
        last_persist = state.last_tick_persisted_at

        if last_persist is not None:
            age = (now - last_persist).total_seconds()

            if not live_announced:
                live_announced = True
                stale_announced = False
                await _push_system_message(localized(
                    (
                        "✅ ThesisGuard Market Feed LIVE\n"
                        f"Watching: {len(state.symbols)} symbols\n"
                        f"Last persisted tick age: {age:.1f}s\n"
                        "Market collection and PostgreSQL persistence are active."
                    ),
                    (
                        "✅ ThesisGuard 市场数据流正常\n"
                        f"监控资产：{len(state.symbols)} 个\n"
                        f"最新落库行情距今：{age:.1f} 秒\n"
                        "行情采集与 PostgreSQL 持久化均正常。"
                    ),
                ))

            if age > settings.stale_after_seconds and not stale_announced:
                stale_announced = True
                await _push_system_message(localized(
                    (
                        "⚠️ ThesisGuard Market Feed STALE\n"
                        f"No persisted market tick for {int(age)} seconds.\n"
                        "Risk alerts may be incomplete until the feed recovers."
                    ),
                    (
                        "⚠️ ThesisGuard 市场数据流过期\n"
                        f"已有 {int(age)} 秒没有新的行情成功落库。\n"
                        "在数据恢复前，风险提醒可能不完整。"
                    ),
                ))

            if age <= settings.stale_after_seconds and stale_announced:
                stale_announced = False
                await _push_system_message(localized(
                    (
                        "✅ ThesisGuard Market Feed RECOVERED\n"
                        f"Persistence resumed. Latest tick age: {age:.1f}s."
                    ),
                    (
                        "✅ ThesisGuard 市场数据流已恢复\n"
                        f"行情落库已经恢复；最新行情距今 {age:.1f} 秒。"
                    ),
                ))

            if (
                settings.telegram_health_heartbeat_seconds > 0
                and (now - last_hourly).total_seconds() >= settings.telegram_health_heartbeat_seconds
            ):
                last_hourly = now
                await _push_system_message(localized(
                    (
                        "💓 ThesisGuard Health\n"
                        f"Market feed: {'LIVE' if age <= settings.stale_after_seconds else 'STALE'}\n"
                        f"Watching: {len(state.symbols)} symbols\n"
                        f"Last persisted tick age: {age:.1f}s"
                    ),
                    (
                        "💓 ThesisGuard 运行状态\n"
                        f"市场数据流：{'正常' if age <= settings.stale_after_seconds else '过期'}\n"
                        f"监控资产：{len(state.symbols)} 个\n"
                        f"最新落库行情距今：{age:.1f} 秒"
                    ),
                ))

        await asyncio.sleep(10)


async def _process_new_intelligence_events(events: list[IntelligenceEvent], state: WorkerState) -> None:
    for event in events:
        if not immediate_push_candidate(event):
            continue
        dedupe_key = f"intel:new:{event.canonical_key}"
        if already_alerted(dedupe_key):
            continue
        explanation = (
            f"New {event.event_kind} event detected from {event.source_name}; "
            f"importance={event.importance:.1f}/10, source tier={event.source_tier}."
        )
        record_intelligence_alert(event, dedupe_key, explanation)
        delivered = await send_telegram_message(
            format_event_message(
                event,
                market_context=_event_market_context(event, state),
            )
        )
        log.info(
            "intelligence Telegram %s: %s",
            "delivered" if delivered else "not delivered",
            event.title[:120],
        )


async def intelligence_news_loop(state: WorkerState) -> None:
    if not settings.intelligence_enabled:
        return

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "ThesisGuard/0.3 market-intelligence"},
    ) as client:
        last_crypto_calendar_poll: datetime | None = None
        while True:
            collected = []
            try:
                collected.extend(await fetch_fed_monetary(client))
            except Exception as exc:
                log.warning("Fed intelligence fetch failed: %s", type(exc).__name__)

            if settings.gdelt_enabled:
                try:
                    collected.extend(await fetch_gdelt(client))
                except Exception as exc:
                    log.warning("GDELT intelligence fetch failed: %s", type(exc).__name__)

            now = datetime.now(timezone.utc)
            should_poll_crypto_calendar = (
                settings.coinmarketcal_api_key.strip()
                and (
                    last_crypto_calendar_poll is None
                    or (now - last_crypto_calendar_poll).total_seconds() >= settings.crypto_calendar_poll_seconds
                )
            )
            if should_poll_crypto_calendar:
                try:
                    collected.extend(await fetch_coinmarketcal(client))
                    last_crypto_calendar_poll = now
                except Exception as exc:
                    log.warning("CoinMarketCal fetch failed: %s", type(exc).__name__)

            if collected:
                collected = [
                    event for event in collected
                    if event.importance >= settings.intelligence_store_min_importance
                ]
                collected = annotate_corroboration(collected)
                created = upsert_events(collected)
                if created:
                    log.info("intelligence ingestion created %s new event(s)", len(created))
                    await _process_new_intelligence_events(created, state)

            _heartbeat("worker-intel", {
                "last_news_poll": datetime.now(timezone.utc).isoformat(),
                "sources": ["fed", "gdelt"] + (["coinmarketcal"] if settings.coinmarketcal_api_key.strip() else []),
            })
            await asyncio.sleep(settings.intelligence_poll_seconds)


async def intelligence_calendar_loop() -> None:
    if not settings.intelligence_enabled:
        return

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "ThesisGuard/0.3 market-intelligence"},
    ) as client:
        while True:
            try:
                events = await fetch_bls_calendar(client)
                fomc_events = await fetch_fomc_calendar(client)
                events.extend(fomc_events)
                created = upsert_events(events)
                log.info(
                    "official calendars synchronized: %s event(s), %s new",
                    len(events),
                    len(created),
                )
            except Exception as exc:
                log.warning("BLS calendar fetch failed: %s", type(exc).__name__)

            _heartbeat("worker-calendar", {
                "last_calendar_poll": datetime.now(timezone.utc).isoformat(),
                "sources": ["bls", "fomc"],
            })
            await asyncio.sleep(settings.intelligence_calendar_poll_seconds)


async def upcoming_event_loop(state: WorkerState) -> None:
    """Send 24h, 2h and 15m reminders for high-impact scheduled events."""
    while True:
        now = datetime.now(timezone.utc)
        horizon = now.timestamp() + 24 * 3600

        with SessionLocal() as db:
            events = db.scalars(
                select(IntelligenceEvent)
                .where(IntelligenceEvent.status == "scheduled")
                .where(IntelligenceEvent.importance >= settings.intelligence_push_min_importance)
                .order_by(IntelligenceEvent.event_time.asc())
            ).all()

        for event in events:
            if event.event_time is None:
                continue
            ts = event.event_time if event.event_time.tzinfo else event.event_time.replace(tzinfo=timezone.utc)
            if ts.timestamp() > horizon:
                continue
            stage = upcoming_stage(ts, now)
            if stage is None:
                continue
            dedupe_key = f"intel:upcoming:{event.canonical_key}:{stage}"
            if already_alerted(dedupe_key):
                continue
            explanation = f"Scheduled high-impact event reminder ({stage}) from {event.source_name}."
            record_intelligence_alert(event, dedupe_key, explanation)
            delivered = await send_telegram_message(
                format_event_message(
                    event,
                    stage=stage,
                    market_context=_event_market_context(event, state),
                )
            )
            log.info(
                "upcoming event Telegram %s: %s [%s]",
                "delivered" if delivered else "not delivered",
                event.title[:120],
                stage,
            )

        await asyncio.sleep(60)


async def main() -> None:
    init_db()
    ensure_portfolio_seeded()
    state = WorkerState()
    await state.refresh_portfolio()

    background = []
    if settings.telegram_alerts_enabled:
        if telegram_configured():
            log.info("Telegram alerts enabled")
            if settings.telegram_send_startup:
                # Never block market collection on Telegram flood control or network errors.
                background.append(asyncio.create_task(send_startup_message(len(state.symbols))))
        else:
            log.warning(
                "TELEGRAM_ALERTS_ENABLED=true but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing"
            )

    await asyncio.gather(
        websocket_loop(state),
        rest_validation_loop(state),
        persistence_loop(state),
        multi_source_validation_loop(state),
        portfolio_refresh_loop(state),
        market_health_loop(state),
        critical_source_health_loop(state),
        intelligence_news_loop(state),
        intelligence_calendar_loop(),
        upcoming_event_loop(state),
    )


if __name__ == "__main__":
    asyncio.run(main())
