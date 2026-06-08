from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.security import redact_sensitive


@dataclass(frozen=True)
class PromotionGateDecision:
    strategy_name: str
    strategy_version: str
    paper_days: int
    paper_trades: int
    max_drawdown: float
    slippage_report: dict[str, Any]
    accepted: bool
    reason: str
    blocking_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(asdict(self))


class PromotionGate:
    def __init__(
        self,
        *,
        min_paper_days: int = 20,
        max_drawdown_limit: float = 0.08,
        max_single_live_order_twd: float = 10_000,
        max_daily_live_order_twd: float = 30_000,
        max_daily_live_orders: int = 3,
        repository: Any | None = None,
    ):
        self.min_paper_days = int(min_paper_days)
        self.max_drawdown_limit = float(max_drawdown_limit)
        self.max_single_live_order_twd = float(max_single_live_order_twd)
        self.max_daily_live_order_twd = float(max_daily_live_order_twd)
        self.max_daily_live_orders = int(max_daily_live_orders)
        self.repository = repository

    def evaluate(
        self,
        *,
        strategy_name: str,
        strategy_version: str,
        paper_days: int,
        paper_trades: int,
        max_drawdown: float,
        slippage_report: dict[str, Any] | None = None,
        data_quality_blocked: bool = False,
        reconciliation_matched: bool = False,
        single_order_value: float = 0.0,
        daily_order_value: float = 0.0,
        daily_order_count: int = 0,
        repository: Any | None = None,
    ) -> PromotionGateDecision:
        blockers: list[str] = []
        if int(paper_days) < self.min_paper_days:
            blockers.append("paper_days_insufficient")
        if bool(data_quality_blocked):
            blockers.append("paper_data_quality_blocked")
        if abs(float(max_drawdown)) > self.max_drawdown_limit:
            blockers.append("max_drawdown_exceeded")
        if not bool(slippage_report):
            blockers.append("slippage_report_missing")
        if not bool(reconciliation_matched):
            blockers.append("reconciliation_not_matched")
        if float(single_order_value) > self.max_single_live_order_twd:
            blockers.append("single_live_order_limit_exceeded")
        if float(daily_order_value) > self.max_daily_live_order_twd:
            blockers.append("daily_live_order_value_exceeded")
        if int(daily_order_count) > self.max_daily_live_orders:
            blockers.append("daily_live_order_count_exceeded")
        accepted = not blockers
        decision = PromotionGateDecision(
            strategy_name=str(strategy_name),
            strategy_version=str(strategy_version),
            paper_days=int(paper_days),
            paper_trades=int(paper_trades),
            max_drawdown=float(max_drawdown),
            slippage_report=dict(slippage_report or {}),
            accepted=accepted,
            reason="ok" if accepted else blockers[0],
            blocking_reasons=blockers,
        )
        repo = repository or self.repository
        if repo is not None and hasattr(repo, "add_promotion_gate_record"):
            repo.add_promotion_gate_record(decision=decision.to_dict())
        return decision
