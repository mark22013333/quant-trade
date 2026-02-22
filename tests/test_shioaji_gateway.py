from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from web.services.shioaji_gateway import ShioajiGateway


def test_normalize_stock_code():
    assert ShioajiGateway.normalize_stock_code("2330") == "2330"
    assert ShioajiGateway.normalize_stock_code("2330.tw") == "2330"
    assert ShioajiGateway.normalize_stock_code("8069.two") == "8069"


def test_pick_reference_price_prefers_reference():
    contract = SimpleNamespace(reference=52.5, close=51.0)
    assert ShioajiGateway.pick_reference_price(contract, fallback=1) == 52.5


def test_pick_reference_price_fallback_when_missing():
    contract = SimpleNamespace(reference=None, close=None)
    assert ShioajiGateway.pick_reference_price(contract, fallback=3.2) == 3.2


def test_extract_trade_status_reads_nested_status():
    trade = SimpleNamespace(status=SimpleNamespace(status="Submitted"))
    assert ShioajiGateway.extract_trade_status(trade) == "Submitted"


def test_serialize_supports_nested_objects():
    gateway = ShioajiGateway(Path(".env"))
    payload = {"item": SimpleNamespace(code="2330", qty=1)}
    data = gateway.serialize(payload)
    assert data["item"]["code"] == "2330"
    assert data["item"]["qty"] == 1


def test_to_current_month_alias_for_product_code():
    assert ShioajiGateway._to_current_month_alias("TXF") == "TXFC0"
    assert ShioajiGateway._to_current_month_alias("TXFC0") == "TXFC0"


def test_is_orderable_futures_contract_filters_stream_contract():
    stream_contract = type("StreamMultiContract", (), {"code": "TXFC0", "symbol": "TXFC0"})()
    real_contract = type("Future", (), {"code": "TXFC0", "symbol": "TXFC0"})()
    assert ShioajiGateway._is_orderable_futures_contract(stream_contract) is False
    assert ShioajiGateway._is_orderable_futures_contract(real_contract) is True
