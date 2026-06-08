from app.broker.pre_trade_check import enforce_absolute_capital_guard


def test_pre_trade_guard_accepts_valid_order():
    result = enforce_absolute_capital_guard(available_cash=5000, current_price=100)
    assert result.accepted is True
    assert result.estimated_total_cost <= 5000
    assert result.qty > 0


def test_pre_trade_guard_rejects_invalid_order():
    result = enforce_absolute_capital_guard(available_cash=1000, current_price=100)
    assert result.accepted is False
    assert result.qty == 0
