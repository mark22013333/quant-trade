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
        defaults = {
            "net_buy_threshold": 1000,
            "net_buy_threshold_mode": "absolute",
            "net_buy_zscore_window": 60,
            "net_buy_zscore_threshold": 0.5,
        }
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
        threshold_mode = str(self.params.get("net_buy_threshold_mode", "absolute")).strip().lower()
        z_window = max(5, int(self.params.get("net_buy_zscore_window", 60)))
        z_threshold = float(self.params.get("net_buy_zscore_threshold", 0.5))

        net_buy = (df["Foreign_Net_Buy"].fillna(0) + df["InvestmentTrust_Net_Buy"].fillna(0)).rolling(window=5, min_periods=5).sum()
        proxy_diff = df["Chip_Concentration_Proxy"].diff()
        concentration_up_3d = proxy_diff.gt(0).rolling(window=3, min_periods=3).sum() == 3

        if threshold_mode == "zscore":
            roll_mean = net_buy.rolling(window=z_window, min_periods=z_window).mean()
            roll_std = net_buy.rolling(window=z_window, min_periods=z_window).std()
            zscore = ((net_buy - roll_mean) / roll_std.replace(0.0, pd.NA)).fillna(0.0)
            threshold_ok = zscore > z_threshold
            threshold_reason = f"zscore>{z_threshold:g}"
        elif threshold_mode == "relative_volume":
            rel = net_buy / df["Volume"].replace(0.0, pd.NA)
            threshold_ok = rel.fillna(0.0) > float(self.params.get("net_buy_volume_ratio_threshold", 0.005))
            threshold_reason = "net_buy/volume above threshold"
        else:
            threshold_ok = net_buy > threshold
            threshold_reason = f"5d net-buy>{threshold:g}"

        signal = threshold_ok & concentration_up_3d
        out = pd.DataFrame({"signal": signal.astype(int)}, index=df.index)
        return self._finalize(
            out,
            reason_true=f"{threshold_reason} and 3d concentration increase",
            reason_false="no chip-flow confirmation",
        )
