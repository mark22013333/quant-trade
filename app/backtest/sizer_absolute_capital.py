from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.backtest.costs import estimate_buy_total_cost


@dataclass
class AbsoluteSizerConfig:
    min_trade_value: float = 2000.0
    max_allocation_per_trade: float = 5000.0
    fee_rate: float = 0.001425
    min_fee: float = 20.0


def compute_order_size(available_cash: float, current_price: float, config: AbsoluteSizerConfig | None = None) -> dict[str, Any]:
    cfg = config or AbsoluteSizerConfig()
    cash = float(max(available_cash, 0))
    price = float(current_price)
    if price <= 0:
        return {"accepted": False, "qty": 0, "reason": "invalid_price"}

    target_value = min(cash, float(cfg.max_allocation_per_trade))
    if target_value < float(cfg.min_trade_value):
        return {"accepted": False, "qty": 0, "reason": "target_value_below_min_trade_value"}

    qty = int(target_value // price)
    if qty <= 0:
        return {"accepted": False, "qty": 0, "reason": "qty_below_one_share"}

    while qty > 0:
        order_value = qty * price
        total_cost = estimate_buy_total_cost(order_value, fee_rate=cfg.fee_rate, min_fee=cfg.min_fee)
        if total_cost <= cash and order_value >= cfg.min_trade_value:
            return {
                "accepted": True,
                "qty": qty,
                "target_value": target_value,
                "order_value": order_value,
                "estimated_total_cost": total_cost,
                "fee_rate": cfg.fee_rate,
                "min_fee": cfg.min_fee,
            }
        qty -= 1

    return {"accepted": False, "qty": 0, "reason": "capital_guard_rejected"}


try:
    import backtrader as bt
except Exception:  # noqa: BLE001
    bt = None


if bt is not None:
    class AbsoluteCapitalSizer(bt.Sizer):  # type: ignore[misc]
        params = dict(
            min_trade_value=2000.0,
            max_allocation_per_trade=5000.0,
            fee_rate=0.001425,
            min_fee=20.0,
        )

        def _getsizing(self, comminfo, cash, data, isbuy):  # noqa: ARG002
            if not isbuy:
                position = self.broker.getposition(data)
                return int(max(position.size, 0))
            info = compute_order_size(
                available_cash=float(cash),
                current_price=float(data.close[0]),
                config=AbsoluteSizerConfig(
                    min_trade_value=float(self.p.min_trade_value),
                    max_allocation_per_trade=float(self.p.max_allocation_per_trade),
                    fee_rate=float(self.p.fee_rate),
                    min_fee=float(self.p.min_fee),
                ),
            )
            return int(info.get("qty", 0))
else:
    class AbsoluteCapitalSizer:  # fallback for environments without backtrader
        def __init__(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            raise RuntimeError("backtrader is required for AbsoluteCapitalSizer class")
