from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import pandas as pd


@dataclass
class StrategyContext:
    """Execution context passed to each strategy."""

    symbol: str
    market: str
    df: pd.DataFrame
    params: Dict[str, Any] = field(default_factory=dict)

