from __future__ import annotations

from datetime import datetime, timedelta

from app.execution import OrderIntent
from app.execution.order_preview import OrderPreviewService
from app.execution.promotion_gate import PromotionGate
from app.portfolio.reconciliation import ReconciliationService


def test_live_preview_requires_manual_confirmation_and_metadata():
    service = OrderPreviewService(ttl_seconds=120)
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=100,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={"strategy_version": "v1"},
    )
    preview = service.create_preview(
        intent=intent,
        estimated_total_cost=100,
        available_cash=1000,
        position_before=0,
        strategy_version="v1",
        signal_id="S1",
    )

    rejected = service.approve(preview_id=preview.preview_id, intent=intent, manual_confirmed=False)
    accepted = service.approve(preview_id=preview.preview_id, intent=intent, manual_confirmed=True)

    assert rejected.accepted is False
    assert rejected.reason == "manual_confirmation_required"
    assert accepted.accepted is True


def test_preview_expired_rejects_order():
    service = OrderPreviewService(ttl_seconds=1)
    intent = OrderIntent(source="web", environment="simulation", symbol="2330", side="buy", price=100, quantity=1)
    preview = service.create_preview(intent=intent, estimated_total_cost=100, available_cash=1000, position_before=0)

    decision = service.approve(
        preview_id=preview.preview_id,
        intent=intent,
        manual_confirmed=True,
        now=datetime.utcnow() + timedelta(seconds=5),
    )

    assert decision.accepted is False
    assert decision.reason == "preview_expired"


def test_promotion_gate_blocks_when_reconciliation_not_matched():
    decision = PromotionGate().evaluate(
        strategy_name="s",
        strategy_version="v1",
        paper_days=20,
        paper_trades=5,
        max_drawdown=0.01,
        slippage_report={"avg": 0.1},
        reconciliation_matched=False,
    )

    assert decision.accepted is False
    assert "reconciliation_not_matched" in decision.blocking_reasons


def test_reconciliation_reports_position_diff():
    result = ReconciliationService().reconcile(
        expected_cash=1000,
        actual_cash=1000,
        expected_positions={"2330": 1},
        actual_positions={"2330": 2},
    )

    assert result.matched is False
    assert result.blocking_reasons == ["position_diff"]
