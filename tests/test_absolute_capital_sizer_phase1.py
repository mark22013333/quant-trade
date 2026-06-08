from app.backtest.costs import estimate_buy_total_cost, estimate_fee, estimate_tax
from app.backtest.sizer_absolute_capital import AbsoluteSizerConfig, compute_order_size


def test_fee_has_minimum_floor():
    assert estimate_fee(order_value=1000, fee_rate=0.001425, min_fee=20) == 20


def test_compute_order_size_respects_available_cash():
    config = AbsoluteSizerConfig(min_trade_value=2000, max_allocation_per_trade=5000, fee_rate=0.001425, min_fee=20)
    result = compute_order_size(available_cash=3000, current_price=590, config=config)
    assert result["accepted"] is True
    assert result["estimated_total_cost"] <= 3000


def test_compute_order_size_rejects_when_cash_too_low():
    config = AbsoluteSizerConfig(min_trade_value=2000, max_allocation_per_trade=5000, fee_rate=0.001425, min_fee=20)
    result = compute_order_size(available_cash=1500, current_price=50, config=config)
    assert result["accepted"] is False


def test_buy_total_cost_matches_fee_plus_value():
    value = 4800
    expected = value + estimate_fee(value)
    assert estimate_buy_total_cost(order_value=value) == expected


def test_tax_uses_conservative_rounding():
    # 0.3% of 3333.33 = 9.99999 -> round up to 10
    assert estimate_tax(order_value=3333.33, tax_rate=0.003) == 10
