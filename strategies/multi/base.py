from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict

import pandas as pd

from .types import StrategyContext


@dataclass
class StrategyOutput:
    name: str
    data: pd.DataFrame


class BaseMultiStrategy(ABC):
    name = "base"

    def __init__(self, enabled: bool = True, params: dict | None = None, score: float = 1.0):
        self.enabled = enabled
        self.params = params or {}
        self.default_score = float(score)

    @abstractmethod
    def generate_signals(self, ctx: StrategyContext) -> pd.DataFrame:
        """
        Return dataframe with index aligned to ctx.df and columns:
        - signal: 1 for entry candidate, 0 otherwise
        - score: confidence weight
        - reason: readable explanation
        """

    def disabled_output(self, index: pd.Index) -> pd.DataFrame:
        return pd.DataFrame({"signal": 0, "score": 0.0, "reason": "disabled"}, index=index)

    def _finalize(self, frame: pd.DataFrame, reason_true: str, reason_false: str = "") -> pd.DataFrame:
        out = frame.copy()
        out["signal"] = out["signal"].fillna(0).astype(int)
        out["score"] = out["signal"] * self.default_score
        out["reason"] = out["signal"].map(lambda x: reason_true if x > 0 else reason_false)
        return out[["signal", "score", "reason"]]

    def get_config(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "score": self.default_score,
            "params": self.params,
        }

