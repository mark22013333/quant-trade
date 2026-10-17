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

    @classmethod
    def from_confirmed(cls, config, product):
        fees=config['fees'][product]
        return cls(commission_rate=fees['commission_rate'],min_commission_fee=__import__('decimal').Decimal(fees['minimum_fee_e4'])/10000,
                   tax_rate=fees['sell_tax_rate'],slippage=str(__import__('decimal').Decimal(str(config['execution']['slippage_bps']))/10000),
                   settlement_days=config['execution']['settlement_sessions'],t_plus_entry_days=1,use_odd_lot=True)

    def quote_e4(self, opening, quantity, side):
        from decimal import Decimal, ROUND_HALF_UP
        price=int((Decimal(str(opening))*(1+Decimal(str(self.slippage)) if side=='buy' else 1-Decimal(str(self.slippage)))*10000).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        gross=price*quantity
        fee=max(int(Decimal(str(self.min_commission_fee))*10000),int((Decimal(gross)*Decimal(str(self.commission_rate))).quantize(Decimal(1),rounding=ROUND_HALF_UP)))
        tax=int((Decimal(gross)*Decimal(str(self.tax_rate))).quantize(Decimal(1),rounding=ROUND_HALF_UP)) if side=='sell' else 0
        return price,fee,tax

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
