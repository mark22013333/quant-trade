from __future__ import annotations

import math


def estimate_fee(order_value: float, fee_rate: float = 0.001425, min_fee: float = 20.0) -> float:
    raw = float(order_value) * float(fee_rate)
    # Keep conservative estimate (round up to avoid under-estimation).
    return float(max(min_fee, math.ceil(raw)))


def estimate_tax(order_value: float, tax_rate: float = 0.003) -> float:
    # Keep conservative estimate (round up to avoid under-estimation).
    raw = float(order_value) * float(tax_rate)
    return float(max(0.0, math.ceil(raw)))


def estimate_buy_total_cost(order_value: float, fee_rate: float = 0.001425, min_fee: float = 20.0) -> float:
    fee = estimate_fee(order_value=order_value, fee_rate=fee_rate, min_fee=min_fee)
    return float(order_value + fee)


def estimate_sell_total_cost(
    order_value: float,
    fee_rate: float = 0.001425,
    min_fee: float = 20.0,
    tax_rate: float = 0.003,
) -> float:
    fee = estimate_fee(order_value=order_value, fee_rate=fee_rate, min_fee=min_fee)
    tax = estimate_tax(order_value=order_value, tax_rate=tax_rate)
    return float(order_value + fee + tax)


def estimate_buy_cost_breakdown(order_value: float, fee_rate: float = 0.001425, min_fee: float = 20.0) -> dict[str, float]:
    fee = estimate_fee(order_value=order_value, fee_rate=fee_rate, min_fee=min_fee)
    return {
        "order_value": float(order_value),
        "fee": float(fee),
        "tax": 0.0,
        "total_cost": float(order_value + fee),
    }


def estimate_sell_proceeds_breakdown(
    order_value: float,
    fee_rate: float = 0.001425,
    min_fee: float = 20.0,
    tax_rate: float = 0.003,
) -> dict[str, float]:
    fee = estimate_fee(order_value=order_value, fee_rate=fee_rate, min_fee=min_fee)
    tax = estimate_tax(order_value=order_value, tax_rate=tax_rate)
    net = float(order_value - fee - tax)
    return {
        "order_value": float(order_value),
        "fee": float(fee),
        "tax": float(tax),
        "net_proceeds": net,
    }
