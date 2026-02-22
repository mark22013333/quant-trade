from __future__ import annotations

import pandas as pd

from .base import BaseMultiStrategy
from .indicators import with_common_indicators
from .types import StrategyContext


class MomentumTrendStrategy(BaseMultiStrategy):
    """
    Strategy A: Momentum & Trend.
    Captures breakouts with trend and volume confirmation.
    """

    name = "momentum_trend"

    def __init__(self, enabled: bool = True, params: dict | None = None, score: float = 1.0):
        defaults = {"volume_multiplier": 1.5}
        merged = defaults | (params or {})
        super().__init__(enabled=enabled, params=merged, score=score)

    def generate_signals(self, ctx: StrategyContext) -> pd.DataFrame:
        if not self.enabled:
            return self.disabled_output(ctx.df.index)

        df = with_common_indicators(ctx.df)
        volume_multiplier = float(self.params.get("volume_multiplier", 1.5))

        signal = (
            (df["Close"] > df["MA20"])
            & (df["MA20"] > df["MA60"])
            & (df["Close"] > df["Donchian_High_20"])
            & (df["Volume"] > df["Volume_MA5"] * volume_multiplier)
        )
        out = pd.DataFrame({"signal": signal.astype(int)}, index=df.index)
        return self._finalize(
            out,
            reason_true="close>MA20>MA60 with Donchian breakout and volume expansion",
            reason_false="no momentum breakout",
        )

