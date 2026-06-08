from __future__ import annotations

import pytest

from app.services.strategy_config import StrategyConfigError, StrategyRunConfig


def test_strategy_config_normalizes_known_partial_payload():
    cfg = StrategyRunConfig(
        symbol="2330.tw",
        market="tw",
        start_date="2024-01-01",
        end_date="2024-12-31",
        enabled={"momentum_trend": True},
        weights={"momentum_trend": 0.7},
        strategy_params={
            "momentum_trend": {"volume_multiplier": "2.0"},
            "chip_flow": {"net_buy_threshold_mode": "zscore", "net_buy_zscore_window": "80"},
        },
        risk_config={"stop_loss_pct": "0.05", "max_holding_days": "12"},
        backtest_config={"INITIAL_CAPITAL": "1000000", "RISK_FRACTION": "0.02"},
    )

    normalized = cfg.validated()

    assert normalized.market == "TW"
    assert normalized.enabled["mean_reversion"] is True
    assert normalized.weights["momentum_trend"] == 0.7
    assert normalized.strategy_params["momentum_trend"]["volume_multiplier"] == 2.0
    assert normalized.strategy_params["chip_flow"]["net_buy_zscore_window"] == 80
    assert normalized.risk_config["max_holding_days"] == 12
    assert normalized.backtest_config["INITIAL_CAPITAL"] == 1_000_000


def test_strategy_config_rejects_unknown_strategy_key():
    cfg = StrategyRunConfig(enabled={"moonshot": True})

    with pytest.raises(StrategyConfigError, match="unknown enabled strategy"):
        cfg.validated()


def test_strategy_config_rejects_invalid_threshold_and_dates():
    with pytest.raises(StrategyConfigError, match="threshold"):
        StrategyRunConfig(threshold=1.5).validated()

    with pytest.raises(StrategyConfigError, match="start_date"):
        StrategyRunConfig(start_date="2025-01-02", end_date="2025-01-01").validated()


def test_strategy_config_rejects_unknown_param_key():
    cfg = StrategyRunConfig(strategy_params={"chip_flow": {"mystery": 1}})

    with pytest.raises(StrategyConfigError, match="unknown chip_flow param"):
        cfg.validated()
