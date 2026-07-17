from __future__ import annotations

from app.backtest.research_spec import build_strategy_research_spec
from app.services.strategy_config import StrategyRunConfig


def test_strategy_research_spec_captures_hypothesis_and_validation_plan() -> None:
    cfg = StrategyRunConfig(
        symbol="2330.TW",
        market="TW",
        enabled={"momentum_trend": True, "mean_reversion": False, "chip_flow": True},
        weights={"momentum_trend": 0.6, "mean_reversion": 0.0, "chip_flow": 0.4},
        threshold=0.62,
        risk_config={"stop_loss_pct": 0.08},
        validation_config={"cross_symbol_symbols": "1101.TW,2881.TW,2317.TW", "max_cross_symbols": 3},
    ).validated()
    spec = build_strategy_research_spec(
        cfg,
        start_date="2024-01-01",
        end_date="2024-12-31",
        data_rows=240,
        validation_splits={
            "status": "ok",
            "method": "chronological_70_30_with_oos_warmup",
            "parameter_sensitivity": {"status": "ok", "method": "threshold_neighborhood_oos"},
            "walk_forward": {"status": "ok", "method": "rolling_walk_forward_fixed_parameters"},
            "cross_symbol_sensitivity": {"status": "ok", "method": "representative_symbol_basket"},
        },
    )

    assert spec["schema_version"] == "2026-06-25.strategy-research-spec.v1"
    assert spec["spec_id"].startswith("TW:2330.TW:2024-01-01:2024-12-31:")
    assert spec["universe"]["scope"] == "single_symbol_backtest"
    assert spec["data_requirements"]["actual_rows"] == 240
    assert spec["strategy"]["active_strategies"] == ["momentum_trend", "chip_flow"]
    assert spec["strategy"]["threshold"] == 0.62
    assert spec["risk"]["config"]["stop_loss_pct"] == 0.08
    assert spec["validation_plan"]["oos_split"]["status"] == "ok"
    assert spec["validation_plan"]["walk_forward"]["status"] == "ok"
    assert spec["validation_plan"]["cross_symbol_sensitivity"]["status"] == "ok"
    assert spec["validation_plan"]["cross_symbol_sensitivity"]["symbols"] == ["1101.TW", "2881.TW", "2317.TW"]
    assert "validation_report" in spec["artifacts"]["included_in_payload"]
