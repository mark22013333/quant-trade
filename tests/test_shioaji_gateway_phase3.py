from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.broker.pre_trade_check import PreTradeCheckError
from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway
from app.execution import OrderIntent
from app.market_data import DataQualityStatus


class DummyAPI:
    def __init__(self, balances: list[float], contract: SimpleNamespace | None = None):
        self._balances = list(balances)
        self.stock_account = SimpleNamespace(account_id="STOCK-ACC")
        self.Contracts = SimpleNamespace(
            Stocks={"2330": contract or SimpleNamespace(code="2330", reference=100.0, limit_down=90.0, limit_up=110.0)}
        )
        self.placed = None
        self.last_order_kwargs = None

    def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
        value = self._balances.pop(0) if self._balances else 0.0
        return SimpleNamespace(acc_balance=float(value))

    def Order(self, **kwargs):
        self.last_order_kwargs = kwargs
        return SimpleNamespace(**kwargs)

    def place_order(self, contract, order):
        self.placed = (contract, order)
        return SimpleNamespace(
            status=SimpleNamespace(status="Submitted"),
            account_id="1234567890",
            token="secret-token-value",
        )


class BalanceFailingAPI(DummyAPI):
    def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("balance unavailable")


def test_safe_place_stock_order_places_order_after_double_cash_check():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert result["passed"] is True
    assert result["qty"] > 0
    assert result["estimated_total_cost"] <= 5000.0
    assert result["status"] == "Submitted"
    assert isinstance(result.get("pretrade_checks"), list)
    assert any(item.get("name") == "capital_guard_first" for item in result["pretrade_checks"])
    assert result["trade"]["account_id"] != "1234567890"
    assert result["trade"]["token"] != "secret-token-value"
    assert gateway.api.placed is not None
    names = {item.get("name") for item in result["pretrade_checks"]}
    assert {
        "live_order_unlock",
        "data_freshness",
        "data_quality",
        "tick_size",
        "price_band",
        "capital_guard_first",
        "capital_guard_second",
        "duplicate_order",
        "daily_order_limit",
        "position_limit",
        "max_portfolio_exposure",
        "disposition_stock_block",
        "liquidity_threshold",
    }.issubset(names)


def test_safe_place_stock_order_rejects_when_pre_trade_guard_fails():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([1000.0, 1000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_when_second_cash_check_fails():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 1000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_uses_simulation_cash_fallback_when_balance_unavailable(monkeypatch):
    monkeypatch.setenv("SHIOAJI_SIMULATION_CASH_FALLBACK", "20000")
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = BalanceFailingAPI([0.0, 0.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, quantity=1, use_odd_lot=True)

    assert result["passed"] is True
    checks = {item["name"]: item for item in result["pretrade_checks"]}
    assert checks["capital_guard_first"]["cash_source"].startswith("simulation_cash_fallback")
    assert checks["capital_guard_second"]["cash_source"].startswith("simulation_cash_fallback")


def test_safe_place_stock_order_does_not_fallback_live_balance(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = BalanceFailingAPI([0.0, 0.0])

    with pytest.raises(RuntimeError, match="unable to read account balance"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, quantity=1, use_odd_lot=True)

    assert gateway.api.placed is None


def test_execute_stock_order_in_thread_aborts_failed_order_thread():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([1000.0, 1000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.execute_stock_order_in_thread(symbol="2330", current_price=100.0, timeout_sec=3)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_invalid_tick_size():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.03, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_live_without_backend_unlock(monkeypatch):
    monkeypatch.delenv("SHIOAJI_ENABLE_LIVE_ORDERS", raising=False)
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="live_order_env_locked"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_allows_live_with_backend_unlock(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    monkeypatch.setenv("SHIOAJI_LIVE_ORDER_NONCE", "confirm-live")
    gateway = ShioajiGateway(
        ShioajiConfig(simulation=False, allow_live_order=True, live_order_nonce="confirm-live")
    )
    gateway.api = DummyAPI([5000.0, 5000.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert result["passed"] is True
    assert gateway.api.placed is not None


def test_safe_place_stock_order_rejects_live_nonce_mismatch(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    monkeypatch.setenv("SHIOAJI_LIVE_ORDER_NONCE", "confirm-live")
    gateway = ShioajiGateway(
        ShioajiConfig(simulation=False, allow_live_order=True, live_order_nonce="wrong-nonce")
    )
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="live_order_nonce_mismatch"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_daily_order_limit():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True, max_daily_orders=1))
    gateway.api = DummyAPI([5000.0, 5000.0])
    gateway._increase_daily_order_count()

    with pytest.raises(PreTradeCheckError, match="daily_order_limit"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_duplicate_order():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True, duplicate_window_sec=60))
    gateway.api = DummyAPI([5000.0, 5000.0, 5000.0, 5000.0])

    first = gateway.safe_place_stock_order(symbol="2330.TW", current_price=100.0, use_odd_lot=True)

    with pytest.raises(PreTradeCheckError, match="duplicate_order"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert first["passed"] is True
    assert gateway.api.placed is not None


def test_safe_place_stock_order_rejects_price_out_of_band():
    contract = SimpleNamespace(code="2330", reference=100.0, limit_down=90.0, limit_up=105.0)
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0], contract=contract)

    with pytest.raises(PreTradeCheckError, match="price_out_of_band"):
        gateway.safe_place_stock_order(symbol="2330", current_price=106.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_price_deviation_guard():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="price_deviation_guard"):
        gateway.safe_place_stock_order(symbol="2330", current_price=105.0, reference_price=100.0, quantity=1)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_sell_more_than_position():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="position_quantity_for_sell"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, side="sell", quantity=3, position_quantity=2)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_live_disposition_and_low_liquidity(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="disposition_attention_guard"):
        gateway.safe_place_stock_order(
            symbol="2330",
            current_price=100.0,
            quantity=1,
            is_disposition_or_attention=True,
        )

    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])
    with pytest.raises(PreTradeCheckError, match="liquidity_guard"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, quantity=1, avg_volume_20=100.0)


def test_execute_intent_returns_execution_result():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])
    intent = OrderIntent(source="cli", environment="simulation", symbol="2330.TW", side="buy", price=100.0)

    result = gateway.execute_intent(intent)

    assert result.intent_id == intent.intent_id
    assert result.accepted is True
    assert result.executed is True
    assert result.symbol == "2330"
    assert result.pretrade.accepted is True
    assert result.raw_trade["token"] != "secret-token-value"


def test_safe_place_stock_order_supports_sell_side_with_requested_quantity():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, side="sell", quantity=3)

    assert result["passed"] is True
    assert result["side"] == "sell"
    assert result["qty"] == 3
    assert gateway.api.last_order_kwargs["action"] != "Buy"


def test_safe_place_stock_sell_does_not_require_cash_lookup():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = BalanceFailingAPI([])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, side="sell", quantity=3)

    assert result["passed"] is True
    assert result["side"] == "sell"
    assert result["available_cash_before"] is None
    assert result["available_cash_before_order"] is None
    assert gateway.api.placed is not None


def test_execute_intent_rejects_live_insecure_data_quality(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])
    intent = OrderIntent(source="cli", environment="live", symbol="2330", side="buy", price=100.0)
    quality = DataQualityStatus(freshness_status="ok", insecure_transport=True)

    result = gateway.execute_intent(intent, data_quality=quality)

    assert result.accepted is False
    assert result.executed is False
    assert "insecure_transport" in result.pretrade.reason
    assert gateway.api.placed is None
