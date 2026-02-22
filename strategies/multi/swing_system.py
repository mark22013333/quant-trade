from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd

from .base import BaseMultiStrategy
from .chip_flow import ChipFlowStrategy
from .ensemble import EnsembleDecision
from .indicators import with_common_indicators
from .mean_reversion import MeanReversionStrategy
from .momentum_trend import MomentumTrendStrategy
from .types import StrategyContext


@dataclass
class MultiStrategyConfig:
    symbol: str
    market: str = "TW"
    enabled: Dict[str, bool] | None = None
    weights: Dict[str, float] | None = None
    threshold: float = 0.6
    params: Dict[str, dict] | None = None


class MultiStrategySwingSystem:
    """
    Build strategy outputs and final entry signal from A/B/C strategies.
    """

    def __init__(self, config: MultiStrategyConfig):
        enabled = config.enabled or {}
        params = config.params or {}
        self.config = config
        self.strategies: Dict[str, BaseMultiStrategy] = {
            "momentum_trend": MomentumTrendStrategy(
                enabled=enabled.get("momentum_trend", True),
                params=params.get("momentum_trend"),
            ),
            "mean_reversion": MeanReversionStrategy(
                enabled=enabled.get("mean_reversion", True),
                params=params.get("mean_reversion"),
            ),
            "chip_flow": ChipFlowStrategy(
                enabled=enabled.get("chip_flow", True),
                params=params.get("chip_flow"),
            ),
        }
        self.ensemble = EnsembleDecision(weights=config.weights, threshold=config.threshold)

    def generate_signals(self, df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
        base = with_common_indicators(df)
        ctx = StrategyContext(
            symbol=self.config.symbol,
            market=self.config.market,
            df=base,
            params={},
        )

        outputs: Dict[str, pd.DataFrame] = {}
        for name, strategy in self.strategies.items():
            outputs[name] = strategy.generate_signals(ctx)

        combined = self.ensemble.combine(outputs, index=base.index)
        final_df = base.join(combined, how="left")
        final_df["entry_signal"] = final_df["entry_signal"].fillna(0).astype(int)
        final_df["ensemble_score"] = final_df["ensemble_score"].fillna(0.0)
        return final_df, outputs

