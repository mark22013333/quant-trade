from __future__ import annotations

from datetime import date
from typing import Any

from app.advisor.interface import TradingAdvisor
from app.advisor.models import AdvisorRequest
from app.advisor.stub import StubTradingAdvisor
from app.backtest.costs import estimate_buy_total_cost
from app.execution import OrderIntent
from app.execution.order_preview import OrderPreviewService


class AdvisorWorkflowService:
    def __init__(
        self,
        *,
        repo: Any,
        advisor: TradingAdvisor | None = None,
        preview_service: OrderPreviewService | None = None,
    ):
        self.repo = repo
        self.advisor = advisor or StubTradingAdvisor()
        self.preview_service = preview_service or OrderPreviewService()

    def create_proposal(
        self,
        *,
        symbol: str,
        trade_date: date,
        available_cash: float,
        position_qty: int = 0,
        create_preview: bool = True,
        environment: str = "simulation",
    ) -> dict[str, Any]:
        request = self._build_request(
            symbol=symbol,
            trade_date=trade_date,
            available_cash=available_cash,
            position_qty=position_qty,
        )
        decision = self.advisor.advise(request)
        preview_payload: dict[str, Any] | None = None
        preview_id = ""

        if bool(create_preview) and decision.actionable and decision.proposal is not None:
            proposal = decision.proposal
            intent = OrderIntent(
                source="web",
                environment="simulation" if environment == "simulation" else "live",
                symbol=proposal.symbol,
                side=proposal.action,  # type: ignore[arg-type]
                price=float(proposal.price or 0.0),
                quantity=int(proposal.quantity or 0),
                strategy_name=f"advisor:{decision.advisor_name}",
                signal_id=decision.decision_id,
                metadata={"strategy_version": decision.advisor_version, "advisor_decision_id": decision.decision_id},
            )
            estimated_total_cost = (
                estimate_buy_total_cost(float(intent.price) * int(intent.quantity or 0))
                if intent.side == "buy"
                else 0.0
            )
            preview = self.preview_service.create_preview(
                intent=intent,
                estimated_total_cost=estimated_total_cost,
                available_cash=float(available_cash),
                position_before=int(position_qty),
                checks=[{"name": "advisor_schema", "passed": True}],
                strategy_version=decision.advisor_version,
                signal_id=decision.decision_id,
                repository=self.repo,
            )
            preview_payload = preview.to_dict()
            preview_id = preview.preview_id

        payload = decision.model_dump(mode="json")
        payload["preview_id"] = preview_id
        if hasattr(self.repo, "add_advisor_decision_record"):
            self.repo.add_advisor_decision_record(decision=payload, preview_id=preview_id)
        return {"decision": payload, "preview": preview_payload, "message": "advisor_proposal_created"}

    def reject_decision(self, *, decision_id: str, reason: str = "manual_rejected") -> dict[str, Any]:
        updated = False
        if hasattr(self.repo, "update_advisor_decision_status"):
            updated = self.repo.update_advisor_decision_status(
                decision_id=decision_id,
                status="manual_rejected",
                rejected_reason=reason,
            )
        return {"updated": bool(updated), "decision_id": decision_id, "status": "manual_rejected", "reason": reason}

    def _build_request(self, *, symbol: str, trade_date: date, available_cash: float, position_qty: int) -> AdvisorRequest:
        bars = []
        if hasattr(self.repo, "get_daily_bars"):
            for row in self.repo.get_daily_bars(symbol=symbol, end_date=trade_date)[-80:]:
                bars.append(
                    {
                        "date": row.date.isoformat() if row.date else None,
                        "open": float(row.open),
                        "high": float(row.high),
                        "low": float(row.low),
                        "close": float(row.close),
                        "volume": float(row.volume),
                    }
                )
        radar_item = {}
        if hasattr(self.repo, "get_latest_daily_radar"):
            wanted = str(symbol).strip().upper().replace(".TW", "").replace(".TWO", "")
            for item in self.repo.get_latest_daily_radar(count=50):
                if str(item.get("symbol", "")).upper() == wanted:
                    radar_item = item
                    break
        return AdvisorRequest(
            symbol=symbol,
            trade_date=trade_date,
            available_cash=float(available_cash),
            position_qty=int(position_qty),
            market_context={"source": "advisor_workflow"},
            bars=bars,
            radar_item=radar_item,
            constraints={"human_confirmation_required": True},
        )
