import pandas as pd
import numpy as np

from strategies.ma_cross import MACrossStrategy
from strategies.momentum.macd import MACDStrategy
from strategies.momentum.rsi import RSIStrategy
from strategies.momentum.simplified_swing import SimplifiedSwingStrategy
from strategies.momentum.swing_trader import SwingTradingStrategy
from strategies.mean_reversion.bollinger_bands import BollingerBandsStrategy
from strategies.portfolio.equal_weight import EqualWeightStrategy


def make_ohlcv_df(rows=200):
    dates = pd.date_range(start="2022-01-01", periods=rows, freq="B")
    prices = np.linspace(100, 120, rows) + np.random.normal(0, 1, rows)
    df = pd.DataFrame({
        "Open": prices,
        "High": prices * 1.01,
        "Low": prices * 0.99,
        "Close": prices,
        "Volume": np.random.randint(1000, 5000, rows)
    }, index=dates)
    return df


def assert_signal_columns(df):
    assert "position" in df.columns
    assert "signal" in df.columns
    pos_vals = set(df["position"].dropna().unique())
    sig_vals = set(df["signal"].dropna().unique())
    assert pos_vals.issubset({-1, 0, 1})
    assert sig_vals.issubset({-1, 0, 1})


def test_strategies_output_signal_and_position():
    df = make_ohlcv_df()

    strategies = [
        MACrossStrategy(),
        MACDStrategy(),
        RSIStrategy(),
        SimplifiedSwingStrategy(),
        SwingTradingStrategy(),
        BollingerBandsStrategy(),
        EqualWeightStrategy()
    ]

    for strat in strategies:
        out = strat.generate_signals(df)
        assert_signal_columns(out)
