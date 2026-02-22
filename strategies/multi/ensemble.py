from __future__ import annotations

from typing import Dict

import pandas as pd


class EnsembleDecision:
    """
    Weighted voting combiner for entry candidates.
    """

    def __init__(self, weights: Dict[str, float] | None = None, threshold: float = 0.6):
        self.weights = weights or {}
        self.threshold = float(threshold)

    def combine(self, outputs: Dict[str, pd.DataFrame], index: pd.Index | None = None) -> pd.DataFrame:
        if not outputs:
            base = pd.DataFrame(index=index or pd.Index([]))
            base["ensemble_score"] = 0.0
            base["entry_signal"] = 0
            base["active_strategies"] = ""
            return base

        base_index = index if index is not None else next(iter(outputs.values())).index
        score_sum = pd.Series(0.0, index=base_index)
        weight_total = 0.0
        active_names = []
        combined = pd.DataFrame(index=base_index)

        for name, frame in outputs.items():
            aligned = frame.reindex(base_index).copy()
            signal = aligned.get("signal", pd.Series(0, index=base_index)).fillna(0).astype(int)
            score = aligned.get("score", pd.Series(0.0, index=base_index)).fillna(0.0).astype(float)
            weight = float(self.weights.get(name, 1.0))
            if weight < 0:
                weight = 0.0
            weight_total += weight
            score_sum = score_sum + score * weight
            combined[f"{name}_signal"] = signal
            combined[f"{name}_reason"] = aligned.get("reason", "").fillna("")
            if weight > 0:
                active_names.append(name)

        normalizer = weight_total if weight_total > 0 else max(len(outputs), 1)
        combined["ensemble_score"] = score_sum / normalizer
        combined["entry_signal"] = (combined["ensemble_score"] >= self.threshold).astype(int)

        def pick_active(row: pd.Series) -> str:
            names = [name for name in active_names if int(row.get(f"{name}_signal", 0)) > 0]
            return ",".join(names)

        combined["active_strategies"] = combined.apply(pick_active, axis=1)
        return combined

