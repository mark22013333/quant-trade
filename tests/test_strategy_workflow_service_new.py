import pandas as pd

from web.services.strategy_workflow import StrategyRunConfig, StrategyWorkflowService


def make_service_df(rows: int = 170) -> pd.DataFrame:
    idx = pd.date_range("2023-01-02", periods=rows, freq="B")
    close = pd.Series([80 + i * 0.28 for i in range(rows)], index=idx, dtype=float)
    frame = pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.002,
            "Low": close * 0.997,
            "Close": close,
            "Volume": pd.Series(1500, index=idx, dtype=float),
            "Foreign_Net_Buy": pd.Series(260, index=idx, dtype=float),
            "InvestmentTrust_Net_Buy": pd.Series(220, index=idx, dtype=float),
            "Dealer_Net_Buy": pd.Series(50, index=idx, dtype=float),
            "Chip_Concentration_Proxy": pd.Series(range(rows), index=idx, dtype=float),
        },
        index=idx,
    )
    frame.loc[frame.index[-40:], "Volume"] = 4200
    return frame


class StubDataFacade:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def load_for_strategy(self, **kwargs):  # noqa: ARG002
        return self.df.copy()


def make_strategy_service(df: pd.DataFrame) -> StrategyWorkflowService:
    industries = {
        "1101": "水泥工業",
        "2317": "電子零組件業",
        "2881": "金融保險業",
    }
    context = {
        "水泥工業": {"status": "ok", "industry": "水泥工業", "average_return": 0.01, "breadth": 0.6, "symbol_count": 5, "label": "neutral"},
        "電子零組件業": {"status": "ok", "industry": "電子零組件業", "average_return": 0.02, "breadth": 0.7, "symbol_count": 8, "label": "tailwind"},
        "金融保險業": {"status": "ok", "industry": "金融保險業", "average_return": 0.01, "breadth": 0.5, "symbol_count": 6, "label": "mixed"},
    }
    return StrategyWorkflowService(
        data_facade=StubDataFacade(df),
        instrument_industry_lookup=lambda symbol: industries.get(str(symbol or "").replace(".TW", ""), ""),
        industry_context_provider=lambda **kwargs: context.get(kwargs.get("industry"), {"status": "insufficient", "reason": "missing"}),
    )


def test_strategy_workflow_uses_industry_stratified_default_peers() -> None:
    captured: dict = {}

    def _provider(**kwargs):
        captured.update(kwargs)
        return [
            {"symbol": "1101", "industry": "水泥工業", "avg_turnover": 300_000_000},
            {"symbol": "2881", "industry": "金融保險業", "avg_turnover": 250_000_000},
            {"symbol": "2317", "industry": "電子零組件業", "avg_turnover": 200_000_000},
        ]

    service = StrategyWorkflowService(
        data_facade=StubDataFacade(make_service_df()),
        instrument_industry_lookup=lambda _symbol: "",
        industry_symbol_provider=_provider,
        industry_context_provider=lambda **kwargs: {
            "status": "ok",
            "industry": kwargs.get("industry"),
            "average_return": 0.01,
            "breadth": 0.6,
            "symbol_count": 5,
            "label": "neutral",
        },
    )
    cfg = StrategyRunConfig(
        symbol="2330.TW",
        market="TW",
        validation_config={
            "max_cross_symbols": 3,
            "min_cross_symbol_rows": 80,
            "cross_industry_per_group": 1,
            "cross_symbol_lookback_days": 120,
        },
    )

    result = service.run_multi_strategy_backtest(cfg)
    sensitivity = result["validation_splits"]["cross_symbol_sensitivity"]

    assert captured["markets"] == ["TWSE", "TPEX"]
    assert captured["exclude_symbols"] == ["2330"]
    assert captured["limit"] == 3
    assert captured["per_industry"] == 1
    assert captured["lookback_days"] == 120
    assert sensitivity["requested_symbols"] == ["1101.TW", "2881.TW", "2317.TW"]
    assert sensitivity["group_source_summary"] == {"instrument_industry": 3}
    assert sensitivity["group_stress"]["status"] == "ok"
    assert sensitivity["group_stress"]["group_count"] == 3
    assert sensitivity["group_stress"]["context_group_count"] == 3
    assert {row["group"] for row in sensitivity["rows"] if row["status"] == "ok"} == {"水泥工業", "金融保險業", "電子零組件業"}


def test_strategy_workflow_service_runs_backtest():
    service = make_strategy_service(make_service_df())
    cfg = StrategyRunConfig(
        symbol="2330.TW",
        market="TW",
        enabled={"momentum_trend": True, "mean_reversion": True, "chip_flow": True},
        weights={"momentum_trend": 0.4, "mean_reversion": 0.3, "chip_flow": 0.3},
        threshold=0.6,
        validation_config={
            "cross_symbol_symbols": ["1101.TW", "2881.TW", "2317.TW"],
            "max_cross_symbols": 3,
            "min_cross_symbol_rows": 100,
        },
    )

    result = service.run_multi_strategy_backtest(cfg)

    assert result["message"]
    assert "metrics" in result
    assert "trade_count" in result["metrics"]
    assert isinstance(result["recent_signals"], list)
    assert isinstance(result["trade_log"], list)
    assert isinstance(result["equity_curve"], list)
    assert "cost_breakdown" in result
    assert "fill_rejects" in result
    assert "data_quality_summary" in result
    assert result["validation_splits"]["status"] == "ok"
    assert "out_of_sample" in result["validation_splits"]
    assert result["validation_splits"]["parameter_sensitivity"]["status"] == "ok"
    assert len(result["validation_splits"]["parameter_sensitivity"]["rows"]) >= 3
    assert result["validation_splits"]["walk_forward"]["status"] == "ok"
    assert result["validation_splits"]["walk_forward"]["window_count"] >= 2
    assert isinstance(result["validation_splits"]["walk_forward"]["windows"], list)
    assert result["validation_splits"]["cross_symbol_sensitivity"]["status"] == "ok"
    assert result["validation_splits"]["cross_symbol_sensitivity"]["symbol_count"] >= 2
    assert result["validation_splits"]["cross_symbol_sensitivity"]["requested_symbols"] == ["1101.TW", "2881.TW", "2317.TW"]
    assert result["validation_splits"]["cross_symbol_sensitivity"]["group_count"] >= 2
    assert isinstance(result["validation_splits"]["cross_symbol_sensitivity"]["group_summary"], list)
    assert result["validation_splits"]["cross_symbol_sensitivity"]["group_source_summary"] == {"instrument_industry": 3}
    assert result["validation_splits"]["cross_symbol_sensitivity"]["group_stress"]["status"] == "ok"
    assert result["validation_splits"]["cross_symbol_sensitivity"]["group_stress"]["context_group_count"] == 3
    assert all(
        item["market_context"]["status"] == "ok"
        for item in result["validation_splits"]["cross_symbol_sensitivity"]["group_stress"]["groups"]
    )
    assert {
        row["group"] for row in result["validation_splits"]["cross_symbol_sensitivity"]["rows"] if row["status"] == "ok"
    } == {"水泥工業", "金融保險業", "電子零組件業"}
    assert all(
        row["group_source"] == "instrument_industry"
        for row in result["validation_splits"]["cross_symbol_sensitivity"]["rows"]
        if row["status"] == "ok"
    )
    assert "research_spec" in result
    assert result["research_spec"]["validation_plan"]["walk_forward"]["status"] == "ok"
    assert result["research_spec"]["validation_plan"]["cross_symbol_sensitivity"]["status"] == "ok"
    assert result["research_spec"]["validation_plan"]["cross_symbol_sensitivity"]["symbols"] == ["1101.TW", "2881.TW", "2317.TW"]
    assert "validation_report" in result
    assert result["validation_report"]["readiness"] in {"paper_ready", "needs_review", "blocked"}
    assert isinstance(result["validation_report"]["checks"], list)
    assert any(check["code"] == "oos_split" for check in result["validation_report"]["checks"])
    assert any(check["code"] == "threshold_sensitivity" for check in result["validation_report"]["checks"])
    assert any(check["code"] == "walk_forward" for check in result["validation_report"]["checks"])
    assert any(check["code"] == "cross_symbol_sensitivity" for check in result["validation_report"]["checks"])
    assert any(check["code"] == "cross_group_stress" for check in result["validation_report"]["checks"])
    assert result["strategy_comparison"]["schema_version"] == "2026-06-25.strategy-comparison.v1"
    assert result["strategy_comparison"]["baseline"]["symbol"] == "2330.TW"
    assert len(result["strategy_comparison"]["threshold_variants"]) >= 3
    assert result["strategy_comparison"]["best_threshold"]["verdict"] in {"candidate", "mixed", "fragile", "no_trades"}
    assert result["parameter_robustness"]["schema_version"] == "2026-06-30.parameter-robustness.v1"
    assert result["parameter_robustness"]["status"] == "ok"
    assert result["parameter_robustness"]["verdict"] in {"robust", "candidate", "sensitive", "fragile"}
    assert result["parameter_robustness"]["variant_count"] >= 3
    assert "next_actions" in result["parameter_robustness"]
    assert {row["group"] for row in result["strategy_comparison"]["group_pressure"]} == {"水泥工業", "金融保險業", "電子零組件業"}
    assert result["metrics"]["trade_count"] >= 1


def test_strategy_workflow_service_no_data():
    service = make_strategy_service(pd.DataFrame())
    cfg = StrategyRunConfig(symbol="2330.TW", market="TW")
    result = service.run_multi_strategy_backtest(cfg)
    assert result["passed"] is False
    assert result["error"] == "no_data"


def test_strategy_workflow_service_invalid_config():
    service = make_strategy_service(make_service_df())
    cfg = StrategyRunConfig(symbol="2330.TW", market="TW", enabled={"bad_strategy": True})

    result = service.run_multi_strategy_backtest(cfg)

    assert result["passed"] is False
    assert result["error"] == "invalid_config"


def test_strategy_workflow_service_export_artifacts(tmp_path):
    service = make_strategy_service(make_service_df())
    cfg = StrategyRunConfig(symbol="2330.TW", market="TW")

    result = service.run_multi_strategy_backtest_export(cfg, output_dir=tmp_path)

    assert result["passed"] in {True, False}
    export = result["export"]
    assert "files" in export
    for filename in export["files"].values():
        path = tmp_path / filename
        assert path.exists()
        assert path.is_file()
    assert "fill_rejects_csv" in export["files"]
    assert "validation_report" in result["backtest"]
    assert "research_spec" in result["backtest"]
    assert "strategy_comparison" in result["backtest"]
