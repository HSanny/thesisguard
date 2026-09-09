import backend.app.services.telegram_query as tq


def test_parse_hours_chinese_and_english():
    assert tq._parse_hours("最近6小时发生了什么") == 6
    assert tq._parse_hours("recent 12h events") == 12
    assert tq._parse_hours("upcoming 3 days") == 72


def test_symbol_detection():
    assert tq._symbol_from_text("LINK 最近有什么新闻") == "LINKUSDT"
    assert tq._symbol_from_text("how is ethereum doing?") == "ETHUSDT"
    assert tq._symbol_from_text("XRPUSDT recent events") == "XRPUSDT"


def test_natural_language_market_intent(monkeypatch):
    monkeypatch.setattr(tq, "_market_overview", lambda hours: f"MARKET:{hours}")
    assert tq.build_query_response("how's the market going?") == "MARKET:6"
    assert tq.build_query_response("市场怎么样") == "MARKET:6"


def test_recent_events_intent(monkeypatch):
    monkeypatch.setattr(tq, "_recent_overview", lambda hours: f"RECENT:{hours}")
    assert tq.build_query_response("最近12小时发生了什么") == "RECENT:12"
    assert tq.build_query_response("/recent 8h") == "RECENT:8"


def test_portfolio_correlation_intent(monkeypatch):
    monkeypatch.setattr(tq, "_portfolio_correlation", lambda hours: f"PORTFOLIO:{hours}")
    assert tq.build_query_response("最近事件对我的持仓有什么影响") == "PORTFOLIO:6"
    assert tq.build_query_response("/portfolio 24h") == "PORTFOLIO:24"


def test_asset_intent(monkeypatch):
    monkeypatch.setattr(tq, "_asset_overview", lambda symbol, hours: f"{symbol}:{hours}")
    assert tq.build_query_response("LINK 最近有什么事") == "LINKUSDT:6"
    assert tq.build_query_response("/asset ETH 12h") == "ETHUSDT:12"


def test_upcoming_intent(monkeypatch):
    monkeypatch.setattr(tq, "_upcoming_overview", lambda hours: f"UPCOMING:{hours}")
    assert tq.build_query_response("未来7天有什么重大事件") == "UPCOMING:168"
