from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ExecutionModelConfig:
    commission_rate: float = 0.001425
    min_commission_fee: float = 20.0
    tax_rate: float = 0.003
    slippage: float = 0.001
    settlement_days: int = 2
    t_plus_entry_days: int = 1
    use_odd_lot: bool = True
    limit_down_factor: float = 0.90
    limit_up_factor: float = 1.10
    min_daily_volume_for_fill: float = 0.0
    max_gap_for_entry: float = 0.07
    max_entry_delay_days: int = 3

    def to_dict(self) -> dict:
        return asdict(self)


class BaseExecutionModel:
    name = "base"

    def __init__(self, config: ExecutionModelConfig | None = None):
        self.config = config or ExecutionModelConfig()

    def describe(self) -> dict:
        return {"name": self.name, "config": self.config.to_dict()}


class ResearchExecutionModel(BaseExecutionModel):
    name = "research_t1_open_fill"


class PaperLedgerExecutionModel(BaseExecutionModel):
    name = "paper_ledger_t2_settlement"


class LiveExecutionModel(BaseExecutionModel):
    name = "live_pretrade_guarded"
