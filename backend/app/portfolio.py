import json
from datetime import datetime, timezone
from pathlib import Path
import yaml
from sqlalchemy import select
from .config import PORTFOLIO_PATH
from .db import SessionLocal, PortfolioConfig


def load_seed_portfolio(path: Path = PORTFOLIO_PATH) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_portfolio_seeded() -> None:
    with SessionLocal() as db:
        existing = db.scalar(select(PortfolioConfig).order_by(PortfolioConfig.version.desc()).limit(1))
        if existing:
            return
        seed = load_seed_portfolio()
        db.add(PortfolioConfig(version=1, data_json=json.dumps(seed), updated_at=datetime.now(timezone.utc)))
        db.commit()


def load_portfolio() -> dict:
    ensure_portfolio_seeded()
    with SessionLocal() as db:
        row = db.scalar(select(PortfolioConfig).order_by(PortfolioConfig.version.desc()).limit(1))
        return json.loads(row.data_json) if row else load_seed_portfolio()


def save_portfolio(data: dict) -> int:
    ensure_portfolio_seeded()
    with SessionLocal() as db:
        current = db.scalar(select(PortfolioConfig).order_by(PortfolioConfig.version.desc()).limit(1))
        version = (current.version if current else 0) + 1
        db.add(PortfolioConfig(version=version, data_json=json.dumps(data), updated_at=datetime.now(timezone.utc)))
        db.commit()
        return version
