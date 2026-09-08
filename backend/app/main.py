from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from .db import init_db, SessionLocal, MarketSnapshot, AlertRecord
from .portfolio import load_portfolio, save_portfolio

app = FastAPI(title="ThesisGuard", version="0.1.0")


class PortfolioPayload(BaseModel):
    data: dict


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "thesisguard"}


@app.get("/api/portfolio")
def get_portfolio() -> dict:
    return load_portfolio()


@app.put("/api/portfolio")
def put_portfolio(payload: PortfolioPayload) -> dict:
    if "positions" not in payload.data:
        raise HTTPException(400, "positions is required")
    save_portfolio(payload.data)
    return {"ok": True, "portfolio": payload.data}


@app.get("/api/snapshots")
def snapshots(limit: int = 100) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(MarketSnapshot).order_by(MarketSnapshot.observed_at.desc()).limit(limit)).all()
    return [
        {"symbol": r.symbol, "price": r.price, "funding_rate": r.funding_rate, "open_interest": r.open_interest,
         "source": r.source, "observed_at": r.observed_at.isoformat()}
        for r in rows
    ]


@app.get("/api/alerts")
def alerts(limit: int = 50) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(AlertRecord).order_by(AlertRecord.created_at.desc()).limit(limit)).all()
    return [
        {"severity": r.severity, "confidence": r.confidence, "symbol": r.symbol, "title": r.title,
         "explanation": r.explanation, "evidence_json": r.evidence_json, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """
<!doctype html><html><head><meta charset='utf-8'><title>ThesisGuard</title>
<style>body{font-family:system-ui;margin:32px;max-width:1200px}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}.muted{color:#666}.pill{padding:2px 8px;border-radius:12px;background:#eee}pre{background:#f6f6f6;padding:12px;overflow:auto}</style></head>
<body><h1>ThesisGuard</h1><p class='muted'>Thesis-aware real-time market monitor</p>
<h2>Latest snapshots</h2><div id='snapshots'>Loading…</div>
<h2>Portfolio rules</h2><textarea id='portfolio' style='width:100%;height:420px'></textarea><br><button onclick='savePortfolio()'>Save rules</button>
<h2>Alerts</h2><div id='alerts'>Loading…</div>
<script>
async function refresh(){
 const [s,p,a]=await Promise.all([fetch('/api/snapshots?limit=30').then(r=>r.json()),fetch('/api/portfolio').then(r=>r.json()),fetch('/api/alerts?limit=20').then(r=>r.json())]);
 const latest={}; for(const x of s){if(!latest[x.symbol]) latest[x.symbol]=x;}
 document.getElementById('snapshots').innerHTML='<table><tr><th>Symbol</th><th>Price</th><th>Funding</th><th>OI</th><th>Observed</th></tr>'+Object.values(latest).map(x=>`<tr><td>${x.symbol}</td><td>${x.price}</td><td>${x.funding_rate??''}</td><td>${x.open_interest??''}</td><td>${x.observed_at}</td></tr>`).join('')+'</table>';
 if(!document.getElementById('portfolio').dataset.loaded){document.getElementById('portfolio').value=JSON.stringify(p,null,2);document.getElementById('portfolio').dataset.loaded='1';}
 document.getElementById('alerts').innerHTML=a.length?a.map(x=>`<p><span class='pill'>${x.severity}/${x.confidence}</span> <b>${x.title}</b><br>${x.explanation}</p>`).join(''):'No material alerts.';
}
async function savePortfolio(){const data=JSON.parse(document.getElementById('portfolio').value);await fetch('/api/portfolio',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({data})});alert('Saved');}
refresh(); setInterval(refresh,15000);
</script></body></html>"""
