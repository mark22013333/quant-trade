from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class PositionState:
    entry_price: float
    entry_atr: float
    highest_price: float
    holding_days: int
    current_return: float
    max_return: float


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str = ""
    trigger_price: float | None = None


class RiskManager:
    """
    Shared exit rules for all entry strategies.
    """

    def __init__(self, config: Dict[str, float] | None = None):
        defaults = {
            "stop_loss_pct": 0.05,
            "atr_multiplier": 2.0,
            "trail_activate_return": 0.10,
            "trail_drawdown_pct": 0.03,
            "max_holding_days": 10,
            "min_return_for_hold": 0.02,
        }
        self.config = defaults | (config or {})

    def check_exit(self, state: PositionState, close_price: float) -> ExitDecision:
        stop_loss_pct = float(self.config["stop_loss_pct"])
        atr_multiplier = float(self.config["atr_multiplier"])
        trail_activate = float(self.config["trail_activate_return"])
        trail_drawdown = float(self.config["trail_drawdown_pct"])
        max_holding_days = int(self.config["max_holding_days"])
        min_return_for_hold = float(self.config["min_return_for_hold"])

        # 1) Hard stop loss by percentage and ATR channel.
        pct_stop_price = state.entry_price * (1 - stop_loss_pct)
        atr_stop_price = state.entry_price - (atr_multiplier * max(state.entry_atr, 0.0))
        hard_stop_price = max(pct_stop_price, atr_stop_price)
        if close_price <= hard_stop_price:
            return ExitDecision(True, "stop_loss", hard_stop_price)

        # 2) Trailing stop after sufficient profit.
        if state.max_return >= trail_activate:
            peak_price = state.highest_price
            if peak_price > 0:
                drawdown = (peak_price - close_price) / peak_price
                if drawdown >= trail_drawdown:
                    return ExitDecision(True, "trailing_stop", close_price)

        # 3) Time-based exit for stagnant trades.
        if state.holding_days > max_holding_days and state.current_return < min_return_for_hold:
            return ExitDecision(True, "time_exit", close_price)

        return ExitDecision(False)

