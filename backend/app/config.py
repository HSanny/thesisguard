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

    multi_source_enabled: bool = True
    exchange_validation_seconds: int = 30
    okx_rest_base: str = "https://www.okx.com"
    bybit_rest_base: str = "https://api.bybit.com"

    telegram_alerts_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_send_startup: bool = False
    telegram_language: str = "bilingual"
    telegram_event_only: bool = True
    telegram_feed_health_enabled: bool = False
    telegram_health_heartbeat_seconds: int = 0
    telegram_critical_system_alerts_enabled: bool = True
    telegram_queries_enabled: bool = True
    telegram_query_default_hours: int = 6

    intelligence_enabled: bool = True
    intelligence_poll_seconds: int = 120
    intelligence_calendar_poll_seconds: int = 3600
    crypto_calendar_poll_seconds: int = 900
    intelligence_store_min_importance: float = 5.0
    intelligence_push_min_importance: float = 7.0
    gdelt_enabled: bool = True
    gdelt_timespan: str = "30min"
    gdelt_max_records: int = 50
    google_news_fallback_enabled: bool = True
    coindesk_rss_enabled: bool = True
    source_block_backoff_seconds: int = 900
    coinmarketcal_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO_PATH = PROJECT_ROOT / "config" / "portfolio.yaml"
