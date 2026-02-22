from __future__ import annotations

import pandas as pd

from .base import BaseMultiStrategy
from .types import StrategyContext


class ChipFlowStrategy(BaseMultiStrategy):
    """
    Strategy C: Chip Flow.
    Tracks institutional net-buy and concentration proxy.
    """

    name = "chip_flow"

    REQUIRED_COLUMNS = (
        "Foreign_Net_Buy",
        "InvestmentTrust_Net_Buy",
        "Chip_Concentration_Proxy",
    )

    def __init__(self, enabled: bool = True, params: dict | None = None, score: float = 1.0):
        defaults = {"net_buy_threshold": 1000}
        merged = defaults | (params or {})
        super().__init__(enabled=enabled, params=merged, score=score)

    def generate_signals(self, ctx: StrategyContext) -> pd.DataFrame:
        if not self.enabled:
            return self.disabled_output(ctx.df.index)

        if str(ctx.market).upper() != "TW":
            out = pd.DataFrame({"signal": 0, "score": 0.0, "reason": "chip flow only for TW market"}, index=ctx.df.index)
            return out

        missing = [name for name in self.REQUIRED_COLUMNS if name not in ctx.df.columns]
        if missing:
            out = pd.DataFrame({"signal": 0, "score": 0.0, "reason": f"missing chip columns: {', '.join(missing)}"}, index=ctx.df.index)
            return out

        df = ctx.df.copy()
        threshold = float(self.params.get("net_buy_threshold", 1000))

        net_buy = (df["Foreign_Net_Buy"].fillna(0) + df["InvestmentTrust_Net_Buy"].fillna(0)).rolling(window=5, min_periods=5).sum()
        proxy_diff = df["Chip_Concentration_Proxy"].diff()
        concentration_up_3d = proxy_diff.gt(0).rolling(window=3, min_periods=3).sum() == 3

        signal = (net_buy > threshold) & concentration_up_3d
        out = pd.DataFrame({"signal": signal.astype(int)}, index=df.index)
        return self._finalize(
            out,
            reason_true="5d institutional net-buy and 3d concentration increase",
            reason_false="no chip-flow confirmation",
        )

