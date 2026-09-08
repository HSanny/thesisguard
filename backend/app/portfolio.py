from pathlib import Path
import yaml
from .config import PORTFOLIO_PATH


def load_portfolio(path: Path = PORTFOLIO_PATH) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_portfolio(data: dict, path: Path = PORTFOLIO_PATH) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
