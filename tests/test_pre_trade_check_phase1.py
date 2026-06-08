from app.broker.pre_trade_check import enforce_absolute_capital_guard
from app.execution import OrderIntent
from app.risk.pre_trade_service import PreTradeCheckService


def test_pre_trade_guard_accepts_valid_order():
    result = enforce_absolute_capital_guard(available_cash=5000, current_price=100)
    assert result.accepted is True
    assert result.estimated_total_cost <= 5000
    assert result.qty > 0


def test_pre_trade_guard_rejects_invalid_order():
    result = enforce_absolute_capital_guard(available_cash=1000, current_price=100)
    assert result.accepted is False
    assert result.qty == 0


def test_pre_trade_service_accepts_sell_without_cash_requirement():
    service = PreTradeCheckService()
    intent = OrderIntent(source="test", environment="simulation", symbol="2330", side="sell", price=100, quantity=3)

    decision = service.evaluate(intent, available_cash=0)

    assert decision.accepted is True
    assert decision.approved_quantity == 3
    assert decision.estimated_total_cost == 0
    assert any(item["name"] == "capital_guard_first" for item in decision.checks)
