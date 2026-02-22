from __future__ import annotations

import pandas as pd

from .base import BaseMultiStrategy
from .indicators import with_common_indicators
from .types import StrategyContext


class MeanReversionStrategy(BaseMultiStrategy):
    """
    Strategy B: Mean Reversion.
    Looks for oversold rebounds inside long-term uptrend.
    """

    name = "mean_reversion"

    def __init__(self, enabled: bool = True, params: dict | None = None, score: float = 1.0):
        defaults = {"rsi_threshold": 30}
        merged = defaults | (params or {})
        super().__init__(enabled=enabled, params=merged, score=score)

    def generate_signals(self, ctx: StrategyContext) -> pd.DataFrame:
        if not self.enabled:
            return self.disabled_output(ctx.df.index)

        df = with_common_indicators(ctx.df)
        rsi_threshold = float(self.params.get("rsi_threshold", 30))

        # Touch lower band on previous bar, then next day closes above open.
        touched_lower_prev = df["Close"].shift(1) <= df["BB_Lower"].shift(1)
        rebound_red_to_green = df["Close"] > df["Open"]

        signal = (
            (df["RSI14"] < rsi_threshold)
            & touched_lower_prev
            & rebound_red_to_green
            & (df["Close"] > df["MA60"])
        )
        out = pd.DataFrame({"signal": signal.astype(int)}, index=df.index)
        return self._finalize(
            out,
            reason_true="RSI oversold + Bollinger lower rebound under long-term uptrend",
            reason_false="no mean-reversion setup",
        )

