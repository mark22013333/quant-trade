from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    AdvisorObservationRecord,
    Base,
    OrderPreviewRecord,
    PromotionGateRecord,
    ReconciliationRecord,
    TradingExecutionRecord,
)
from app.db.repository import TradingRepository
from app.execution import OrderIntent
from app.execution.order_preview import OrderPreviewService
from app.execution.promotion_gate import PromotionGate
from app.portfolio.reconciliation import ReconciliationService


def test_repository_persists_trading_execution_record():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        repo = TradingRepository(session)
        repo.add_trading_execution_record(
            intent={
                "intent_id": "intent-1",
                "source": "test",
                "environment": "simulation",
                "symbol": "2330",
                "side": "buy",
                "price": 100.0,
            },
            result={
                "intent_id": "intent-1",
                "environment": "simulation",
                "symbol": "2330",
                "side": "buy",
                "price": 100.0,
                "quantity": 10,
                "accepted": True,
                "executed": True,
                "status": "submitted",
                "pretrade": {"reason": "ok"},
            },
        )
        session.commit()

        row = session.query(TradingExecutionRecord).filter_by(intent_id="intent-1").one()

    assert row.symbol == "2330"
    assert row.accepted is True
    assert row.executed is True
    assert row.status == "submitted"


def test_repository_persists_order_preview_and_decision():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        repo = TradingRepository(session)
        service = OrderPreviewService(repository=repo)
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
        service.approve(preview_id=preview.preview_id, intent=intent, manual_confirmed=True)
        session.commit()

        row = session.query(OrderPreviewRecord).filter_by(preview_id=preview.preview_id).one()
        recent = repo.list_recent_order_preview_records(limit=5)

    assert row.status == "accepted"
    assert row.reason == "ok"
    assert recent[0]["preview_id"] == preview.preview_id


def test_repository_persists_promotion_gate_and_reconciliation_records():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        repo = TradingRepository(session)
        gate = PromotionGate(repository=repo)
        gate.evaluate(
            strategy_name="manual",
            strategy_version="v1",
            paper_days=0,
            paper_trades=0,
            max_drawdown=0,
            slippage_report={},
            reconciliation_matched=False,
        )
        ReconciliationService(repository=repo).reconcile(
            expected_cash=1000,
            actual_cash=990,
            expected_positions={"2330": 1},
            actual_positions={"2330": 2},
        )
        session.commit()

        gate_row = session.query(PromotionGateRecord).one()
        reconciliation_row = session.query(ReconciliationRecord).one()
        gate_recent = repo.list_recent_promotion_gate_records(limit=5)
        reconciliation_recent = repo.list_recent_reconciliation_records(limit=5)

    assert gate_row.accepted is False
    assert gate_recent[0]["reason"] == "paper_days_insufficient"
    assert reconciliation_row.matched is False
    assert "position_diff" in reconciliation_recent[0]["blocking_reasons"]


def test_repository_tracks_advisor_observation_period_readiness():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        repo = TradingRepository(session)
        repo.add_advisor_observation_record(
            record={
                "observation_date": "2026-06-10",
                "symbol": "2330",
                "advisor_name": "stub",
                "preview_created": True,
                "human_action": "rejected",
                "simulation_result": "skipped",
                "data_quality": "fresh",
                "passed": True,
            }
        )
        repo.add_advisor_observation_record(
            record={
                "observation_date": "2026-06-11",
                "symbol": "2317",
                "advisor_name": "stub",
                "preview_created": True,
                "human_action": "confirmed",
                "simulation_result": "submitted",
                "data_quality": "fresh",
                "passed": True,
            }
        )
        repo.add_advisor_observation_record(
            record={
                "observation_date": "2026-06-12",
                "symbol": "2454",
                "advisor_name": "stub",
                "preview_created": False,
                "human_action": "skipped",
                "simulation_result": "skipped",
                "data_quality": "missing",
                "passed": False,
                "blocker_reasons": ["data_quality_missing"],
            }
        )
        session.commit()

        rows = session.query(AdvisorObservationRecord).all()
        recent = repo.list_recent_advisor_observation_records(limit=2)
        summary = repo.summarize_advisor_observation_period(advisor_name="stub", min_days=3)

    assert len(rows) == 3
    assert recent[0]["observation_date"] == "2026-06-12"
    assert summary["observed_days"] == 3
    assert summary["ready_for_next_phase"] is False
    assert summary["blocking_reasons"] == ["data_quality_missing"]


def test_repository_marks_advisor_observation_ready_after_clean_minimum_days():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        repo = TradingRepository(session)
        for idx, day in enumerate(["2026-06-10", "2026-06-11", "2026-06-12"], start=1):
            repo.add_advisor_observation_record(
                record={
                    "observation_date": day,
                    "symbol": f"233{idx}",
                    "advisor_name": "stub",
                    "preview_created": True,
                    "human_action": "rejected",
                    "simulation_result": "skipped",
                    "data_quality": "fresh",
                    "passed": True,
                }
            )
        session.commit()

        summary = repo.summarize_advisor_observation_period(advisor_name="stub", min_days=3)

    assert summary["ready_for_next_phase"] is True
    assert summary["blocking_reasons"] == []
