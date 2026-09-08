from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./thesisguard.db"
    binance_fapi_base: str = "https://fapi.binance.com"
    binance_ws_base: str = "wss://fstream.binance.com/market/stream?streams="
    rest_validation_seconds: int = 30
    oi_refresh_seconds: int = 60
    persist_interval_seconds: int = 5
    portfolio_refresh_seconds: int = 5
    price_conflict_bps: float = 35.0
    stale_after_seconds: int = 90

    telegram_alerts_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_send_startup: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO_PATH = PROJECT_ROOT / "config" / "portfolio.yaml"
