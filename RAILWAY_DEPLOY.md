# Railway deployment — ThesisGuard v0.2

ThesisGuard v0.2 uses **three Railway services in one project**:

1. `postgres` — Railway PostgreSQL
2. `api` — public FastAPI/dashboard service
3. `worker` — private always-on Binance WebSocket + REST validation worker

Both `api` and `worker` point to the same `HSanny/thesisguard` GitHub repository and share the same `DATABASE_URL`.

For the first production-candidate deployment, use branch `railway-v0.2.1`. Do not deploy `main` until this candidate has been validated in Railway.

## 1. Add PostgreSQL

Create a Railway PostgreSQL database.

## 2. API service

Connect `HSanny/thesisguard`, branch `railway-v0.2.1`.

Custom Start Command:

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT
```

Set:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Generate a public domain for this service.

## 3. Worker service

Add the same GitHub repository a second time as another persistent service.

Custom Start Command:

```bash
python -m backend.app.worker
```

Set the same:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

No public domain is required for the worker.

## 4. Recommended variables

```text
REST_VALIDATION_SECONDS=30
OI_REFRESH_SECONDS=60
PERSIST_INTERVAL_SECONDS=5
PORTFOLIO_REFRESH_SECONDS=5
PRICE_CONFLICT_BPS=35
```

## 5. Verify

Open:

- `/` — live dashboard
- `/api/health` — worker heartbeat
- `/api/market/latest` — latest persisted market states
- `/docs` — API docs

A healthy deployment should show `worker live` on the dashboard within roughly one REST-validation cycle.

## Why no startCommand in railway.json?

Both API and worker build from the same repo but require different process commands. Set each service's custom start command in Railway rather than forcing one shared start command in `railway.json`.


## Private GitHub repository not visible in Railway

Railway supports private GitHub repositories, but its GitHub App must have explicit
access to the repository.

1. In GitHub, open **Settings → Applications → Installed GitHub Apps → Railway → Configure**.
2. Under **Repository access**, choose **Only select repositories** and add
   `HSanny/thesisguard` (or grant access to all repositories if that is your intended policy).
3. Save the GitHub App configuration.
4. In Railway, refresh the GitHub repository picker.
5. If it still does not appear, disconnect/reconnect the GitHub integration in
   Railway Account Integrations and refresh the repository list again.
6. Confirm Railway is connected to the GitHub account that owns `HSanny/thesisguard`.

Once the repo appears, select branch `railway-v0.2.1` for both the API and worker services.


## Telegram push alerts

ThesisGuard can send material risk alerts directly from the worker through the
official Telegram Bot API.

Configure these variables on the **worker service only**:

```text
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_BOT_TOKEN=<BotFather token>
TELEGRAM_CHAT_ID=<target private chat/group/channel id or @channelusername>
TELEGRAM_SEND_STARTUP=true
```

Security rules:

- Store `TELEGRAM_BOT_TOKEN` only in Railway Variables/Secrets.
- Never commit the token to GitHub, `.env.example`, screenshots, logs, or issues.
- The API service does not need the Telegram token.
- The worker sends a startup confirmation when enabled, then sends only newly
  persisted material alerts subject to the existing alert deduplication window.

If the worker log says `Telegram alerts enabled` and
`Telegram startup notification delivered`, delivery is active.


## Market intelligence / event monitoring

The worker also runs non-blocking intelligence collectors. A failure in Telegram,
GDELT, BLS, Fed or an optional crypto-calendar provider must not stop the market
WebSocket/persistence loop.

Recommended worker variables:

```text
TELEGRAM_FEED_HEALTH_ENABLED=true
TELEGRAM_HEALTH_HEARTBEAT_SECONDS=3600

INTELLIGENCE_ENABLED=true
INTELLIGENCE_POLL_SECONDS=120
INTELLIGENCE_CALENDAR_POLL_SECONDS=3600
CRYPTO_CALENDAR_POLL_SECONDS=900
INTELLIGENCE_STORE_MIN_IMPORTANCE=5.0
INTELLIGENCE_PUSH_MIN_IMPORTANCE=7.0

GDELT_ENABLED=true
GDELT_TIMESPAN=30min
GDELT_MAX_RECORDS=50

# Optional: enables curated crypto upcoming-event/calendar ingestion.
COINMARKETCAL_API_KEY=
```

Built-in sources:

- U.S. BLS official online calendar (scheduled macro releases, including CPI/PPI/jobs)
- Federal Reserve official monetary-policy RSS
- GDELT DOC API as a broad global-news discovery layer
- CoinMarketCal v2 events when an API key is configured

Telegram health notifications:

- first successfully persisted live market tick → `Market Feed LIVE`
- feed stale longer than `STALE_AFTER_SECONDS` → `Market Feed STALE`
- persistence resumes → `Market Feed RECOVERED`
- optional periodic health heartbeat
- 24h / 2h / 15m reminders for high-impact scheduled events
- high-impact trusted-source intelligence alerts
- planned-entry `APPROACHING` / `REASSESS` alerts

After Telegram delivery has been verified, set `TELEGRAM_SEND_STARTUP=false` to
avoid restart noise during deployments.


## Telegram language

Set on the worker service:

```text
TELEGRAM_LANGUAGE=bilingual
```

Allowed values:

- `bilingual` — Chinese first, followed by English
- `zh` — Chinese only
- `en` — English only

This applies to risk alerts, feed-health messages, planned-entry alerts, macro reminders
and market-intelligence notifications. External news headlines and source excerpts remain
in their original language for provenance; ThesisGuard adds localized structured labels
and interpretation around them.


## Cross-exchange market validation

The worker can validate the Binance WebSocket against independent public
derivatives data from OKX and Bybit. No trading API keys are required.

Recommended worker variables:

```text
MULTI_SOURCE_ENABLED=true
EXCHANGE_VALIDATION_SECONDS=30
OKX_REST_BASE=https://www.okx.com
BYBIT_REST_BASE=https://api.bybit.com
```

Confidence policy:

- Binance WS only -> LOW
- Binance WS + one independent exchange -> MEDIUM when aligned
- Binance WS + OKX + Bybit -> HIGH when aligned
- material cross-exchange divergence -> LOW / source conflict

Per-source mark price, funding rate and open-interest USD snapshots are persisted
in `market_source_snapshots` and exposed at `/api/market/sources/latest`.
A single exchange failure does not stop the market worker.


## Event-only Telegram mode

Recommended production configuration:

```text
TELEGRAM_EVENT_ONLY=true
TELEGRAM_SEND_STARTUP=false
TELEGRAM_FEED_HEALTH_ENABLED=false
TELEGRAM_HEALTH_HEARTBEAT_SECONDS=0
TELEGRAM_CRITICAL_SYSTEM_ALERTS_ENABLED=true
```

With event-only mode enabled:

- live prices continue to be collected and persisted continuously
- price moves and configured key levels do not create Telegram alerts
- planned-entry proximity does not create Telegram alerts
- routine feed-live/recovered/source-degraded/heartbeat messages are suppressed
- medium/high-impact news, project updates, thesis-change candidates, scheduled macro
  events and geopolitical events remain eligible for Telegram delivery
- event notifications attach current prices for related crypto assets using the
  cross-exchange consensus state
- only a critical all-market-source outage may interrupt the user with a system alert

Market data is therefore context for an event, not an alert trigger.


## Interactive Telegram analyst

Enable on the worker:

```text
TELEGRAM_QUERIES_ENABLED=true
TELEGRAM_QUERY_DEFAULT_HOURS=6
```

The same Telegram bot can answer read-only queries from the configured
`TELEGRAM_CHAT_ID`. Other chat IDs are ignored.

Examples:

- `/market` or `how's the market going?`
- `/recent 6h` or `最近6小时发生了什么`
- `/portfolio 24h` or `最近事件对我的持仓有什么影响`
- `/asset LINK 12h` or `LINK 最近有什么事`
- `/upcoming 7d` or `未来7天有什么重大事件`

Answers are generated from ThesisGuard's own Postgres event store, portfolio
configuration and multi-source market data. Queries are read-only and do not
modify positions or place orders.
