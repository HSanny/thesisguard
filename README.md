# ThesisGuard

**Thesis-aware real-time portfolio monitoring for leveraged crypto and macro-sensitive positions.**

ThesisGuard is designed to answer a harder question than “did price move?”:

> **Did the latest market move actually invalidate the investment thesis, or is it deleveraging, liquidity noise, duplicated news, or a temporary macro shock?**

## v0.3 — live market + event intelligence

ThesisGuard v0.2 moves the MVP from polling-only scaffolding to an always-on live monitoring stack:

- **Binance USDⓈ-M WebSocket is the primary market feed** using `@markPrice@1s`.
- **Binance REST validates the live feed** and supplies periodic open interest.
- **WS↔REST price conflicts lower source confidence** instead of producing a directional alert.
- **PostgreSQL is the shared source of truth** for market history, alerts, heartbeats and editable portfolio rules.
- **API and worker run as separate always-on services** and can be deployed from the same GitHub repository.
- **Dashboard updates every 3 seconds** with mark price, REST check, funding, OI and source confidence.
- **Portfolio/rules can be edited live in the dashboard**; the worker reloads the latest DB-backed config without redeploy.
- **Market ticks are downsampled before persistence** to avoid writing every 1-second WebSocket event to Postgres.
- **Cross-exchange consensus** validates Binance WebSocket prices against independent OKX and Bybit public derivatives feeds. Per-source mark price, funding and OI USD are persisted separately, and confidence increases only when independent sources agree.
- **Worker heartbeats are visible in `/api/health`** and on the dashboard.
- **Alert deduplication and conservative price-only handling** reduce panic-inducing false escalation.
- **Telegram push delivery** sends material risk alerts, feed health/recovery notices, planned-entry states and high-impact event intelligence from the always-on worker.
- **Event intelligence** normalizes scheduled macro catalysts and breaking-news discovery into a shared PostgreSQL event store with source tier, confidence, importance, themes and affected assets.
- **BLS official calendar ingestion** tracks upcoming U.S. releases such as CPI, PPI and employment data and schedules 24h / 2h / 15m reminders.
- **Federal Reserve monetary-policy RSS** supplies official policy releases.
- **GDELT global discovery** scans macro, crypto, energy and geopolitical themes; unknown sources remain low confidence and cannot directly become trusted high-impact alerts.
- **Optional CoinMarketCal integration** adds curated crypto catalysts, releases and conference/event coverage when an API key is configured.

## Core principles

- Freshness before narrative: stale headlines are background, not new risk.
- Multi-source validation: conflicting prices reduce confidence and cannot independently escalate risk.
- Evidence deduplication: ten articles about one event are still one event.
- Regime classification: distinguish fundamental deterioration, systemic macro risk-off, derivatives deleveraging, and liquidity sweeps.
- Price confirmation: one wick, one liquidation burst, or one OI move is not enough for a red alert.
- Counter-evidence: reclaims, spot buying, falling OI, cooler funding, and relative strength can reduce severity.
- Thesis-aware risk: LINK, XLM, VIRTUAL, XRP, XAU and future assets are evaluated against their own fundamental theses.
- Explainability: every alert stores confidence, triggers, counter-evidence, and unresolved items.
- Decision journal: future releases will link market state to entry/exit decisions for post-trade review.

## Current portfolio seed

The repository currently seeds the active working portfolio from `config/portfolio.yaml`. On first startup it is copied into PostgreSQL and subsequent edits are versioned in the database.

Current watchlist includes BTC, ETH and TAO alongside active positions/planned entries.

## Local run with Docker

```bash
docker compose up --build
```

Then open:

- Dashboard: http://localhost:8000
- API docs: http://localhost:8000/docs
- Health / worker heartbeat: http://localhost:8000/api/health
- Latest market state: http://localhost:8000/api/market/latest

## Local run without Docker

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
```

In another terminal:

```bash
python -m backend.app.worker
```

SQLite is supported for local development. For API + worker running as separate processes/containers, PostgreSQL is recommended.

## Tests

```bash
pytest backend/tests -q
```

The suite includes a regression case based on the SK Hynix false-risk-escalation incident: duplicated/stale bearish information plus a liquidity/deleveraging event must not become a high-confidence red alert when thesis damage and structural confirmation are absent.

## Railway

See [`RAILWAY_DEPLOY.md`](RAILWAY_DEPLOY.md).

v0.2 uses three Railway services in one project:

```text
PostgreSQL
   ▲       ▲
   │       │
 API     Worker
(public)  │
          ▼
   Binance USD-M WS
   + REST validation
```

The same GitHub repository is connected to both API and worker, but each service uses a different custom start command.

## API

- `GET /api/health`
- `GET /api/portfolio`
- `PUT /api/portfolio`
- `GET /api/market/latest`
- `GET /api/market/history/{symbol}`
- `GET /api/alerts`

## Product roadmap

### v0.3 next increments

- Secondary exchange / market-data sanity source independent of Binance.
- OI change, funding regime and taker buy/sell historical features.
- Structural break confirmation: persistence, reclaim and failed-retest logic.
- Semantic event clustering / cross-source corroboration before escalating noisy stories.
- LLM-assisted event summaries and explicit thesis-impact mapping.
- Portfolio crypto-beta / concentration metrics.

### v0.4

- News/event ingestion with canonical-event deduplication and timestamp validation.
- Source reliability hierarchy (official > tier-1 wire > specialist > aggregator/social).
- Asset thesis graph and explicit thesis-invalidation conditions.
- Macro engine: DXY, Treasury yields, real-rate proxies, oil, CPI/PPI/NFP/FOMC.

### v0.5+

- Decision journal and alert-outcome tracking.
- False-positive / missed-alert evaluation.
- Historical replay/backtesting of monitoring rules.
- Multi-user SaaS, auth, billing and exchange connectors.

## Intellectual property and security

**ThesisGuard is proprietary software. Copyright © 2026 HSanny. All Rights Reserved.**

No general open-source license is granted for ThesisGuard's proprietary code. See:

- [LICENSE](LICENSE) — proprietary license / All Rights Reserved
- [COPYRIGHT.md](COPYRIGHT.md) — proprietary copyright and usage notice
- [SECURITY.md](SECURITY.md) — private vulnerability-reporting and secret-handling policy
- [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) — direct dependency register and license policy

CI performs an automated license audit of the complete installed Python dependency tree and blocks deny-listed license families pending explicit review. The Railway deployment candidate uses the BSD-3-Clause `pg8000` PostgreSQL driver to keep the proprietary SaaS dependency profile simpler.

## Important boundary

ThesisGuard is a monitoring and decision-support system. It does **not** automatically place trades and does not infer exact liquidation prices from leverage alone.
