from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repository import TradingRepository
from app.execution.promotion_gate import PromotionGate
from app.execution.promotion_readiness import PromotionReadinessService


def _build_repo() -> tuple[TradingRepository, object]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, future=True)()
    return TradingRepository(session), session


def _seed_clean_observation(repo: TradingRepository) -> None:
    for idx, day in enumerate(["2026-06-10", "2026-06-11", "2026-06-12"], start=1):
        repo.add_advisor_observation_record(
            record={
                "observation_date": day,
                "symbol": f"233{idx}",
                "advisor_name": "stub",
                "preview_created": True,
                "human_action": "confirmed",
                "simulation_result": "submitted",
                "data_quality": "fresh",
                "passed": True,
            }
        )


def test_promotion_readiness_allows_paper_after_clean_observation_period() -> None:
    repo, session = _build_repo()
    _seed_clean_observation(repo)
    session.commit()

    decision = PromotionReadinessService().evaluate(
        repository=repo,
        advisor_name="stub",
        strategy_name="manual",
        strategy_version="manual",
        target_phase="paper",
    )

    assert decision.accepted is True
    assert decision.readiness == "paper_ready"
    assert decision.blocking_reasons == []
    assert decision.observation_summary["observed_days"] == 3


def test_promotion_readiness_blocks_live_without_promotion_gate() -> None:
    repo, session = _build_repo()
    _seed_clean_observation(repo)
    session.commit()

    decision = PromotionReadinessService().evaluate(
        repository=repo,
        advisor_name="stub",
        strategy_name="manual",
        strategy_version="manual",
        target_phase="small_live",
    )

    assert decision.accepted is False
    assert decision.readiness == "live_blocked"
    assert decision.blocking_reasons == ["promotion_gate_missing"]
    assert decision.latest_promotion_gate is None


def test_promotion_readiness_allows_small_live_after_accepted_gate() -> None:
    repo, session = _build_repo()
    _seed_clean_observation(repo)
    PromotionGate(repository=repo).evaluate(
        strategy_name="manual",
        strategy_version="manual",
        paper_days=20,
        paper_trades=6,
        max_drawdown=0.02,
        slippage_report={"avg_slippage_bps": 3},
        reconciliation_matched=True,
        single_order_value=5_000,
        daily_order_value=10_000,
        daily_order_count=1,
    )
    session.commit()

    decision = PromotionReadinessService().evaluate(
        repository=repo,
        advisor_name="stub",
        strategy_name="manual",
        strategy_version="manual",
        target_phase="live",
    )

    assert decision.accepted is True
    assert decision.readiness == "small_live_ready"
    assert decision.blocking_reasons == []
    assert decision.latest_promotion_gate is not None
    assert decision.latest_promotion_gate["accepted"] is True


def test_promotion_readiness_carries_latest_gate_blockers() -> None:
    repo, session = _build_repo()
    _seed_clean_observation(repo)
    PromotionGate(repository=repo).evaluate(
        strategy_name="manual",
        strategy_version="manual",
        paper_days=1,
        paper_trades=1,
        max_drawdown=0.02,
        slippage_report={},
        reconciliation_matched=False,
    )
    session.commit()

    decision = PromotionReadinessService().evaluate(
        repository=repo,
        advisor_name="stub",
        strategy_name="manual",
        strategy_version="manual",
        target_phase="small_live",
    )

    assert decision.accepted is False
    assert decision.readiness == "live_blocked"
    assert "paper_days_insufficient" in decision.blocking_reasons
    assert "slippage_report_missing" in decision.blocking_reasons
    assert "reconciliation_not_matched" in decision.blocking_reasons
