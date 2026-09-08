# ThesisGuard

Thesis-aware real-time portfolio monitoring for leveraged crypto and macro-sensitive positions.

ThesisGuard is designed to answer a harder question than “did price move?”:

> **Did the latest market move actually invalidate the investment thesis, or is it deleveraging, liquidity noise, duplicated news, or a temporary macro shock?**

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

## MVP architecture

- FastAPI API
- Binance USDⓈ-M Futures market data
- Configurable portfolio / planned entries in YAML
- Risk-evidence engine
- Alert audit storage
- Regression tests, including the SK Hynix false-risk-escalation case
- Docker / Railway deployment scaffolding

## Local run

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
```

Open:

- API: http://localhost:8000
- Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

Run tests:

```bash
pytest backend/tests -q
```

## Docker

```bash
docker compose up --build
```

## Product direction

1. Binance WebSocket as the primary live source; REST as validation.
2. Secondary exchange / CoinGecko sanity checks.
3. OI, funding, liquidation and taker-flow history.
4. News/event ingestion with canonical-event deduplication and timestamp validation.
5. Thesis graph per asset with invalidation conditions.
6. Editable dashboard for positions, conviction, rules and planned entries.
7. Telegram / push alerts with confidence and counter-evidence.
8. Decision journal and false-alert / missed-alert evaluation.
9. Backtesting alert rules against historical episodes.
10. Multi-user SaaS architecture.

## Disclaimer

ThesisGuard is a monitoring and decision-support system, not an automated trading system and not financial advice. It does not place trades or claim exact liquidation prices.
