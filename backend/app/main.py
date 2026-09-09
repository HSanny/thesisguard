import json
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from .db import init_db, SessionLocal, MarketTick, AlertRecord, ServiceHeartbeat, IntelligenceEvent, MarketSourceSnapshot
from .portfolio import load_portfolio, save_portfolio, ensure_portfolio_seeded

app = FastAPI(title="ThesisGuard", version="0.3.4")


class PortfolioPayload(BaseModel):
    data: dict


@app.on_event("startup")
def startup() -> None:
    init_db()
    ensure_portfolio_seeded()


@app.get("/api/health")
def health() -> dict:
    with SessionLocal() as db:
        heartbeats = db.scalars(select(ServiceHeartbeat)).all()
    now = datetime.now(timezone.utc)
    services = []
    for h in heartbeats:
        ts = h.updated_at if h.updated_at.tzinfo else h.updated_at.replace(tzinfo=timezone.utc)
        services.append({"service": h.service, "updated_at": ts.isoformat(), "age_seconds": (now-ts).total_seconds(), "detail": json.loads(h.detail or "{}")})
    return {"ok": True, "service": "thesisguard-api", "version": "0.3.4", "workers": services}


@app.get("/api/portfolio")
def get_portfolio() -> dict:
    return load_portfolio()


@app.put("/api/portfolio")
def put_portfolio(payload: PortfolioPayload) -> dict:
    if "positions" not in payload.data:
        raise HTTPException(400, "positions is required")
    version = save_portfolio(payload.data)
    return {"ok": True, "version": version, "portfolio": payload.data}


@app.get("/api/market/latest")
def latest_market() -> list[dict]:
    with SessionLocal() as db:
        symbols = db.scalars(select(MarketTick.symbol).distinct()).all()
        rows = []
        for symbol in symbols:
            row = db.scalar(select(MarketTick).where(MarketTick.symbol == symbol).order_by(MarketTick.event_time.desc()).limit(1))
            if row:
                rows.append(_tick(row))
    return sorted(rows, key=lambda x: x["symbol"])


@app.get("/api/market/sources/latest")
def latest_market_sources() -> list[dict]:
    with SessionLocal() as db:
        pairs = db.execute(
            select(MarketSourceSnapshot.source, MarketSourceSnapshot.symbol).distinct()
        ).all()
        rows = []
        for source, symbol in pairs:
            row = db.scalar(
                select(MarketSourceSnapshot)
                .where(MarketSourceSnapshot.source == source)
                .where(MarketSourceSnapshot.symbol == symbol)
                .order_by(MarketSourceSnapshot.received_at.desc())
                .limit(1)
            )
            if row:
                rows.append({
                    "source": row.source,
                    "symbol": row.symbol,
                    "mark_price": row.mark_price,
                    "index_price": row.index_price,
                    "funding_rate": row.funding_rate,
                    "open_interest_usd": row.open_interest_usd,
                    "source_timestamp": row.source_timestamp.isoformat() if row.source_timestamp else None,
                    "received_at": row.received_at.isoformat(),
                })
    return sorted(rows, key=lambda x: (x["symbol"], x["source"]))


@app.get("/api/market/history/{symbol}")
def market_history(symbol: str, limit: int = Query(180, ge=1, le=2000)) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(MarketTick).where(MarketTick.symbol == symbol.upper()).order_by(MarketTick.event_time.desc()).limit(limit)).all()
    return [_tick(r) for r in reversed(rows)]


@app.get("/api/alerts")
def alerts(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(AlertRecord).order_by(AlertRecord.created_at.desc()).limit(limit)).all()
    return [
        {"severity": r.severity, "confidence": r.confidence, "symbol": r.symbol, "title": r.title,
         "explanation": r.explanation, "evidence": json.loads(r.evidence_json or "{}"), "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@app.get("/api/intelligence/events")
def intelligence_events(
    limit: int = Query(100, ge=1, le=500),
    min_importance: float = Query(0.0, ge=0.0, le=10.0),
) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(IntelligenceEvent)
            .where(IntelligenceEvent.importance >= min_importance)
            .order_by(IntelligenceEvent.first_seen_at.desc())
            .limit(limit)
        ).all()
    return [_intel_event(r) for r in rows]


@app.get("/api/intelligence/upcoming")
def intelligence_upcoming(
    hours: int = Query(168, ge=1, le=24 * 60),
    min_importance: float = Query(6.0, ge=0.0, le=10.0),
) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() + hours * 3600
    with SessionLocal() as db:
        rows = db.scalars(
            select(IntelligenceEvent)
            .where(IntelligenceEvent.status == "scheduled")
            .where(IntelligenceEvent.importance >= min_importance)
            .where(IntelligenceEvent.event_time.is_not(None))
            .order_by(IntelligenceEvent.event_time.asc())
        ).all()
    out = []
    for row in rows:
        ts = row.event_time if row.event_time.tzinfo else row.event_time.replace(tzinfo=timezone.utc)
        if now.timestamp() <= ts.timestamp() <= cutoff:
            out.append(_intel_event(row))
    return out


def _intel_event(r: IntelligenceEvent) -> dict:
    return {
        "id": r.id,
        "kind": r.event_kind,
        "status": r.status,
        "title": r.title,
        "summary": r.summary,
        "source": r.source_name,
        "source_url": r.source_url,
        "source_tier": r.source_tier,
        "confidence": r.confidence,
        "importance": r.importance,
        "published_at": r.published_at.isoformat() if r.published_at else None,
        "event_time": r.event_time.isoformat() if r.event_time else None,
        "first_seen_at": r.first_seen_at.isoformat(),
        "last_seen_at": r.last_seen_at.isoformat(),
        "affected_assets": json.loads(r.affected_assets_json or "[]"),
        "themes": json.loads(r.themes_json or "[]"),
        "metadata": json.loads(r.metadata_json or "{}"),
    }


def _tick(r: MarketTick) -> dict:
    return {
        "symbol": r.symbol,
        "mark_price": r.mark_price,
        "index_price": r.index_price,
        "rest_price": r.rest_price,
        "funding_rate": r.funding_rate,
        "open_interest": r.open_interest,
        "spread_bps": r.spread_bps,
        "source_conflict": r.source_conflict,
        "source_confidence": r.source_confidence,
        "event_time": r.event_time.isoformat(),
        "received_at": r.received_at.isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return r"""
<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ThesisGuard v0.3.4</title>
<style>
:root{font-family:Inter,system-ui,sans-serif;background:#0b0d10;color:#e8eaed}body{margin:0}.wrap{max-width:1280px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;align-items:end;gap:16px}.muted{color:#8f98a3}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:20px 0}.card{background:#14181d;border:1px solid #262c33;border-radius:14px;padding:16px}.sym{font-weight:700;font-size:18px}.price{font-size:28px;font-weight:750;margin:8px 0}.ok{color:#66d19e}.warn{color:#f6c85f}.bad{color:#ff7b72}.pill{font-size:12px;padding:3px 8px;border-radius:999px;background:#222831}.row{display:flex;justify-content:space-between;gap:10px;margin-top:7px}textarea{width:100%;min-height:480px;background:#0d1117;color:#dce3ea;border:1px solid #30363d;border-radius:10px;padding:12px;box-sizing:border-box}button{background:#2f81f7;color:white;border:0;border-radius:8px;padding:10px 14px;font-weight:700;cursor:pointer}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #252b31;font-size:13px}@media(max-width:700px){.wrap{padding:14px}.top{display:block}}
</style></head><body><div class='wrap'>
<div class='top'><div><h1 style='margin-bottom:4px'>ThesisGuard <span class='pill'>v0.3.4 INTERACTIVE</span></h1><div class='muted'>Interactive event-driven market intelligence · ask the bot about market, events and portfolio impact</div></div><div id='health' class='muted'>checking worker…</div></div>
<div id='market' class='grid'></div>
<div class='card'><h2>Cross-exchange sources</h2><div id='sources' class='muted'>loading…</div></div>
<div class='card'><h2>Upcoming catalysts</h2><div id='upcoming' class='muted'>loading…</div></div>
<div class='card' style='margin-top:12px'><h2>Market intelligence</h2><div id='intel' class='muted'>loading…</div></div>
<div class='card' style='margin-top:12px'><h2>Portfolio / thesis rules</h2><p class='muted'>Stored in Postgres. Saving here updates both API and worker without redeploy.</p><textarea id='portfolio'></textarea><div style='margin-top:10px'><button onclick='savePortfolio()'>Save live rules</button> <span id='saved' class='muted'></span></div></div>
<div class='card' style='margin-top:12px'><h2>Recent alerts</h2><div id='alerts'></div></div>
</div><script>
let portfolioLoaded=false;
function f(x,d=4){return x==null?'—':Number(x).toLocaleString(undefined,{maximumFractionDigits:d})}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function refresh(){
 const [m,p,a,h,u,i,s]=await Promise.all([
   fetch('/api/market/latest').then(r=>r.json()),
   fetch('/api/portfolio').then(r=>r.json()),
   fetch('/api/alerts?limit=20').then(r=>r.json()),
   fetch('/api/health').then(r=>r.json()),
   fetch('/api/intelligence/upcoming?hours=168&min_importance=6').then(r=>r.json()),
   fetch('/api/intelligence/events?limit=30&min_importance=5').then(r=>r.json()),
   fetch('/api/market/sources/latest').then(r=>r.json())
 ]);
 document.getElementById('market').innerHTML=m.length?m.map(x=>`<div class='card'><div class='row'><span class='sym'>${x.symbol}</span><span class='pill ${x.source_conflict?'bad':x.source_confidence==='high'?'ok':'warn'}'>${x.source_conflict?'SOURCE CONFLICT':x.source_confidence.toUpperCase()}</span></div><div class='price'>${f(x.mark_price,6)}</div><div class='row'><span class='muted'>Consensus ref</span><span>${f(x.rest_price,6)}</span></div><div class='row'><span class='muted'>Funding</span><span>${x.funding_rate==null?'—':(x.funding_rate*100).toFixed(5)+'%'}</span></div><div class='row'><span class='muted'>OI</span><span>${f(x.open_interest,0)}</span></div><div class='row'><span class='muted'>Spread</span><span>${x.spread_bps==null?'—':x.spread_bps.toFixed(2)+' bps'}</span></div></div>`).join(''):`<div class='card muted'>Waiting for worker market stream…</div>`;
 document.getElementById('sources').innerHTML=s.length?`<table><tr><th>Asset</th><th>Source</th><th>Mark</th><th>Funding</th><th>OI USD</th><th>Updated</th></tr>${s.map(x=>`<tr><td>${esc(x.symbol)}</td><td>${esc(x.source.toUpperCase())}</td><td>${f(x.mark_price,6)}</td><td>${x.funding_rate==null?'—':(x.funding_rate*100).toFixed(5)+'%'}</td><td>${f(x.open_interest_usd,0)}</td><td>${esc(x.received_at)}</td></tr>`).join('')}</table>`:'<span class="muted">Waiting for OKX / Bybit validation snapshots…</span>';
 if(!portfolioLoaded){document.getElementById('portfolio').value=JSON.stringify(p,null,2);portfolioLoaded=true;}
 document.getElementById('upcoming').innerHTML=u.length?`<table><tr><th>Time</th><th>Event</th><th>Importance</th><th>Source</th></tr>${u.map(x=>`<tr><td>${esc(x.event_time??'—')}</td><td>${esc(x.title)}</td><td>${f(x.importance,1)}/10</td><td>${esc(x.source)}</td></tr>`).join('')}</table>`:'<span class="muted">No high-impact scheduled catalysts in the selected window.</span>';
 document.getElementById('intel').innerHTML=i.length?`<table><tr><th>Seen</th><th>Event</th><th>Impact</th><th>Confidence</th><th>Source</th></tr>${i.map(x=>`<tr><td>${esc(x.first_seen_at)}</td><td>${esc(x.title)}</td><td>${f(x.importance,1)}/10</td><td>${esc(x.confidence)}</td><td>${esc(x.source)}</td></tr>`).join('')}</table>`:'<span class="muted">Waiting for intelligence collectors…</span>';
 document.getElementById('alerts').innerHTML=a.length?`<table><tr><th>Time</th><th>Asset</th><th>Risk</th><th>Confidence</th><th>Why</th></tr>${a.map(x=>`<tr><td>${esc(x.created_at)}</td><td>${esc(x.symbol??'PORTFOLIO')}</td><td>${esc(x.severity)}</td><td>${esc(x.confidence)}</td><td>${esc(x.explanation)}</td></tr>`).join('')}</table>`:'<span class="muted">No material alerts.</span>';
 const alive=h.workers.filter(x=>x.age_seconds<120); document.getElementById('health').innerHTML=alive.length?`<span class='ok'>● worker live</span> · ${alive.length} heartbeat(s)`:`<span class='bad'>● worker offline/stale</span>`;
}
async function savePortfolio(){try{const data=JSON.parse(document.getElementById('portfolio').value);const r=await fetch('/api/portfolio',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({data})});if(!r.ok)throw new Error(await r.text());const out=await r.json();document.getElementById('saved').textContent='saved as version '+out.version;}catch(e){alert(e.message)}}
refresh(); setInterval(refresh,3000);
</script></body></html>"""
