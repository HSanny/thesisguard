import json
from backend.app.services.binance import BinanceMarkPriceStream, parse_mark_price_message


def test_parse_combined_mark_price_message():
    payload = {
        "stream": "linkusdt@markPrice@1s",
        "data": {
            "e": "markPriceUpdate",
            "E": 1788831000000,
            "s": "LINKUSDT",
            "p": "12.76300000",
            "i": "12.75800000",
            "r": "0.00001234",
            "T": 1788844800000,
        },
    }
    event = parse_mark_price_message(json.dumps(payload))
    assert event is not None
    assert event.symbol == "LINKUSDT"
    assert event.mark_price == 12.763
    assert event.index_price == 12.758
    assert event.funding_rate == 0.00001234


def test_stream_url_uses_current_usdm_market_endpoint():
    stream = BinanceMarkPriceStream(["BTCUSDT", "LINKUSDT"])
    assert stream.url.startswith("wss://fstream.binance.com/market/stream?streams=")
    assert "btcusdt@markPrice@1s" in stream.url
    assert "linkusdt@markPrice@1s" in stream.url
