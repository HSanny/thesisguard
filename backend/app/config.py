from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./thesisguard.db"
    binance_fapi_base: str = "https://fapi.binance.com"
    poll_seconds: int = 15
    price_conflict_bps: float = 35.0
    key_level_confirm_samples: int = 3
    stale_after_seconds: int = 90

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO_PATH = PROJECT_ROOT / "config" / "portfolio.yaml"
