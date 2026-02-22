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


def test_strategy_workflow_service_runs_backtest():
    facade = StubDataFacade(make_service_df())
    service = StrategyWorkflowService(data_facade=facade)
    cfg = StrategyRunConfig(
        symbol="2330.TW",
        market="TW",
        enabled={"momentum_trend": True, "mean_reversion": True, "chip_flow": True},
        weights={"momentum_trend": 0.4, "mean_reversion": 0.3, "chip_flow": 0.3},
        threshold=0.6,
    )

    result = service.run_multi_strategy_backtest(cfg)

    assert result["message"]
    assert "metrics" in result
    assert "trade_count" in result["metrics"]
    assert isinstance(result["recent_signals"], list)
    assert isinstance(result["trade_log"], list)
    assert isinstance(result["equity_curve"], list)
    assert result["metrics"]["trade_count"] >= 1


def test_strategy_workflow_service_no_data():
    facade = StubDataFacade(pd.DataFrame())
    service = StrategyWorkflowService(data_facade=facade)
    cfg = StrategyRunConfig(symbol="2330.TW", market="TW")
    result = service.run_multi_strategy_backtest(cfg)
    assert result["passed"] is False
    assert result["error"] == "no_data"


def test_strategy_workflow_service_export_artifacts(tmp_path):
    facade = StubDataFacade(make_service_df())
    service = StrategyWorkflowService(data_facade=facade)
    cfg = StrategyRunConfig(symbol="2330.TW", market="TW")

    result = service.run_multi_strategy_backtest_export(cfg, output_dir=tmp_path)

    assert result["passed"] in {True, False}
    export = result["export"]
    assert "files" in export
    for filename in export["files"].values():
        path = tmp_path / filename
        assert path.exists()
        assert path.is_file()
