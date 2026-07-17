from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.security import redact_sensitive


@dataclass(frozen=True)
class PromotionReadinessDecision:
    advisor_name: str
    strategy_name: str
    strategy_version: str
    target_phase: str
    readiness: str
    accepted: bool
    blocking_reasons: list[str]
    observation_summary: dict[str, Any]
    latest_promotion_gate: dict[str, Any] | None
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(asdict(self))


class PromotionReadinessService:
    def __init__(
        self,
        *,
        min_observation_days: int = 3,
        promotion_gate_limit: int = 100,
    ):
        self.min_observation_days = int(min_observation_days)
        self.promotion_gate_limit = int(promotion_gate_limit)

    def evaluate(
        self,
        *,
        repository: Any,
        advisor_name: str = "stub",
        strategy_name: str = "manual",
        strategy_version: str = "manual",
        target_phase: str = "paper",
        min_observation_days: int | None = None,
    ) -> PromotionReadinessDecision:
        phase = self._normalize_target_phase(target_phase)
        required_observation_days = int(min_observation_days or self.min_observation_days)
        observation_summary = repository.summarize_advisor_observation_period(
            advisor_name=str(advisor_name or "stub"),
            min_days=required_observation_days,
            limit=max(required_observation_days, 100),
        )
        blockers = list(observation_summary.get("blocking_reasons") or [])
        latest_gate = self._latest_gate_record(
            repository=repository,
            strategy_name=str(strategy_name or "manual"),
            strategy_version=str(strategy_version or "manual"),
        )

        if phase == "paper":
            accepted = not blockers
            readiness = "paper_ready" if accepted else "observe_blocked"
            recommendation = (
                "可進入 paper 觀察期，仍需保留固定停用條件。"
                if accepted
                else "尚未符合 paper 觀察期門檻，請先補齊 observation 紀錄或處理阻擋原因。"
            )
        else:
            if latest_gate is None:
                blockers.append("promotion_gate_missing")
            elif not bool(latest_gate.get("accepted")):
                gate_blockers = list(latest_gate.get("blocking_reasons") or [])
                blockers.extend(gate_blockers or [str(latest_gate.get("reason") or "promotion_gate_rejected")])
            accepted = not blockers
            readiness = "small_live_ready" if accepted else "live_blocked"
            recommendation = (
                "可進入小額 live，但仍需套用單筆、單日與人工確認限制。"
                if accepted
                else "尚未符合小額 live 門檻，請先完成 paper gate、對帳與交易限制檢查。"
            )

        unique_blockers = list(dict.fromkeys(str(item) for item in blockers if str(item).strip()))
        if unique_blockers:
            accepted = False
            readiness = "observe_blocked" if phase == "paper" else "live_blocked"

        return PromotionReadinessDecision(
            advisor_name=str(advisor_name or "stub"),
            strategy_name=str(strategy_name or "manual"),
            strategy_version=str(strategy_version or "manual"),
            target_phase=phase,
            readiness=readiness,
            accepted=accepted,
            blocking_reasons=unique_blockers,
            observation_summary=observation_summary,
            latest_promotion_gate=latest_gate,
            recommendation=recommendation,
        )

    def _latest_gate_record(
        self,
        *,
        repository: Any,
        strategy_name: str,
        strategy_version: str,
    ) -> dict[str, Any] | None:
        if not hasattr(repository, "list_recent_promotion_gate_records"):
            return None
        rows = repository.list_recent_promotion_gate_records(limit=max(1, self.promotion_gate_limit))
        for row in rows:
            if str(row.get("strategy_name") or "") != strategy_name:
                continue
            if str(row.get("strategy_version") or "") != strategy_version:
                continue
            return row
        return None

    @staticmethod
    def _normalize_target_phase(value: str) -> str:
        text = str(value or "paper").strip().lower().replace("-", "_")
        if text in {"live", "small_live", "smalllive"}:
            return "small_live"
        return "paper"
