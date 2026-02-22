import pandas as pd

from strategies.multi import (
    ChipFlowStrategy,
    EnsembleDecision,
    MeanReversionStrategy,
    MomentumTrendStrategy,
    PositionState,
    RiskManager,
    StrategyContext,
)


def make_base_df(rows: int = 140) -> pd.DataFrame:
    index = pd.date_range("2024-01-02", periods=rows, freq="B")
    close = pd.Series([100 + i * 0.35 for i in range(rows)], index=index, dtype=float)
    open_ = close * 0.999
    high = close * 1.0005
    low = close * 0.9995
    volume = pd.Series(1200, index=index, dtype=float)
    volume.iloc[-20:] = 3600

    df = pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )
    return df


def test_momentum_strategy_produces_binary_signal():
    df = make_base_df()
    strategy = MomentumTrendStrategy(enabled=True, params={"volume_multiplier": 1.2})
    out = strategy.generate_signals(StrategyContext(symbol="2330.TW", market="TW", df=df))

    assert {"signal", "score", "reason"}.issubset(out.columns)
    assert set(out["signal"].dropna().unique()).issubset({0, 1})
    assert int(out["signal"].sum()) > 0


def test_mean_reversion_strategy_schema():
    df = make_base_df()
    # inject a pullback + rebound block
    df.loc[df.index[-8], "Close"] *= 0.92
    df.loc[df.index[-7], "Open"] = df.loc[df.index[-7], "Close"] * 0.985
    df.loc[df.index[-7], "Close"] = df.loc[df.index[-7], "Close"] * 1.015
    strategy = MeanReversionStrategy(enabled=True, params={"rsi_threshold": 80})
    out = strategy.generate_signals(StrategyContext(symbol="2330.TW", market="TW", df=df))

    assert {"signal", "score", "reason"}.issubset(out.columns)
    assert set(out["signal"].dropna().unique()).issubset({0, 1})


def test_chip_flow_strategy_with_tw_columns():
    df = make_base_df()
    df["Foreign_Net_Buy"] = 350
    df["InvestmentTrust_Net_Buy"] = 280
    df["Chip_Concentration_Proxy"] = pd.Series(range(len(df)), index=df.index, dtype=float)

    strategy = ChipFlowStrategy(enabled=True, params={"net_buy_threshold": 1000})
    out = strategy.generate_signals(StrategyContext(symbol="2330.TW", market="TW", df=df))

    assert {"signal", "score", "reason"}.issubset(out.columns)
    assert int(out["signal"].sum()) > 0


def test_ensemble_weighted_threshold():
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    outputs = {
        "momentum_trend": pd.DataFrame({"signal": [1, 0, 1], "score": [1.0, 0.0, 1.0], "reason": ["a", "", "a"]}, index=idx),
        "mean_reversion": pd.DataFrame({"signal": [0, 1, 1], "score": [0.0, 1.0, 1.0], "reason": ["", "b", "b"]}, index=idx),
        "chip_flow": pd.DataFrame({"signal": [0, 0, 1], "score": [0.0, 0.0, 1.0], "reason": ["", "", "c"]}, index=idx),
    }
    ensemble = EnsembleDecision(weights={"momentum_trend": 0.5, "mean_reversion": 0.3, "chip_flow": 0.2}, threshold=0.6)
    combined = ensemble.combine(outputs)
    assert list(combined["entry_signal"]) == [0, 0, 1]


def test_risk_manager_exit_rules():
    risk = RiskManager(
        {
            "stop_loss_pct": 0.05,
            "atr_multiplier": 2.0,
            "trail_activate_return": 0.1,
            "trail_drawdown_pct": 0.03,
            "max_holding_days": 10,
            "min_return_for_hold": 0.02,
        }
    )

    stop_state = PositionState(
        entry_price=100,
        entry_atr=2,
        highest_price=103,
        holding_days=2,
        current_return=-0.06,
        max_return=0.02,
    )
    assert risk.check_exit(stop_state, close_price=94).reason == "stop_loss"

    trail_state = PositionState(
        entry_price=100,
        entry_atr=2,
        highest_price=116,
        holding_days=6,
        current_return=0.11,
        max_return=0.16,
    )
    assert risk.check_exit(trail_state, close_price=111).reason == "trailing_stop"

    time_state = PositionState(
        entry_price=100,
        entry_atr=2,
        highest_price=102,
        holding_days=11,
        current_return=0.01,
        max_return=0.03,
    )
    assert risk.check_exit(time_state, close_price=101).reason == "time_exit"

