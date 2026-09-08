from backend.app.services.exchanges import (
    from_okx_inst_id,
    to_okx_inst_id,
)


def test_okx_symbol_mapping():
    assert to_okx_inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert to_okx_inst_id("LINKUSDT") == "LINK-USDT-SWAP"
    assert from_okx_inst_id("BTC-USDT-SWAP") == "BTCUSDT"
    assert from_okx_inst_id("ETH-USD-SWAP") is None
