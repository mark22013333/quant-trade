from __future__ import annotations

import math


class FixedRiskPositionSizer:
    """
    Position size by fixed account risk per trade.
    """

    def __init__(self, risk_fraction: float = 0.01):
        self.risk_fraction = float(risk_fraction)

    def size(self, equity: float, entry_price: float, stop_distance: float, cash_limit: float | None = None) -> int:
        if equity <= 0 or entry_price <= 0 or stop_distance <= 0:
            return 0
        risk_budget = equity * self.risk_fraction
        qty = int(math.floor(risk_budget / stop_distance))
        if qty <= 0:
            return 0
        if cash_limit is not None and cash_limit > 0:
            affordable = int(math.floor(cash_limit / entry_price))
            qty = min(qty, affordable)
        return max(qty, 0)

