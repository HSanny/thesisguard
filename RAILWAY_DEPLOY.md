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
