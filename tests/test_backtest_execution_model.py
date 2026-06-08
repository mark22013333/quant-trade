from __future__ import annotations

import pandas as pd

from backtest.backtest_engine import BacktestEngine
from strategies.multi.position_sizer import FixedRiskPositionSizer
from strategies.multi.risk import RiskManager


def _frame(
    *,
    opens: list[float],
    closes: list[float],
    volumes: list[float] | None = None,
    entry_signals: list[int] | None = None,
) -> pd.DataFrame:
    size = len(opens)
    idx = pd.date_range("2026-01-02", periods=size, freq="B")
    volumes = volumes or [10_000.0] * size
    entry_signals = entry_signals or [0] * size
    return pd.DataFrame(
        {
            "Open": opens,
            "High": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
            "Low": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
            "Close": closes,
            "Volume": volumes,
            "entry_signal": entry_signals,
            "ensemble_score": [0.9 if item else 0.0 for item in entry_signals],
            "active_strategies": ["fixture" if item else "" for item in entry_signals],
            "ATR14": [1.0] * size,
        },
        index=idx,
    )


def _run(df: pd.DataFrame, settings: dict | None = None) -> dict:
    default_settings = {
        "INITIAL_CAPITAL": 100_000.0,
        "COMMISSION_RATE": 0.001425,
        "MIN_COMMISSION_FEE": 20.0,
        "TAX_RATE": 0.003,
        "SLIPPAGE": 0.001,
        "MAX_GAP_FOR_ENTRY": 0.07,
        "MIN_DAILY_VOLUME_FOR_FILL": 0.0,
        "LIMIT_DOWN_FACTOR": 0.90,
        "LIMIT_UP_FACTOR": 1.10,
        "MAX_ENTRY_DELAY_DAYS": 3,
        "RISK_FRACTION": 0.10,
    }
    default_settings.update(settings or {})
    return BacktestEngine(df=df, strategy=None, broker=None, settings=default_settings)._simulate_t1_open_fill(
        df,
        symbol="2330",
        risk_manager=RiskManager(
            {
                "stop_loss_pct": 0.05,
                "atr_multiplier": 2.0,
                "trail_activate_return": 0.50,
                "trail_drawdown_pct": 0.20,
                "max_holding_days": 99,
                "min_return_for_hold": 0.0,
            }
        ),
        position_sizer=FixedRiskPositionSizer(risk_fraction=float(default_settings["RISK_FRACTION"])),
        settings=default_settings,
    )


def test_multi_strategy_backtest_fills_entry_at_t_plus_1_open_with_slippage():
    df = _frame(
        opens=[100.0, 101.0, 102.0],
        closes=[100.0, 102.0, 103.0],
        entry_signals=[1, 0, 0],
    )

    result = _run(df, {"SLIPPAGE": 0.01})
    trades = result["trade_log"]

    buy = trades[trades["side"] == "buy"].iloc[0]
    assert buy["signal_date"] == df.index[0]
    assert buy["date"] == df.index[1]
    assert buy["price"] == 101.0 * 1.01


def test_entry_gap_rejection_delays_then_fills_before_expiry():
    df = _frame(
        opens=[100.0, 120.0, 101.0, 102.0],
        closes=[100.0, 119.0, 102.0, 103.0],
        entry_signals=[1, 0, 0, 0],
    )

    result = _run(df, {"MAX_GAP_FOR_ENTRY": 0.05, "MAX_ENTRY_DELAY_DAYS": 3})
    rejects = result["fill_rejects"]
    trades = result["trade_log"]

    assert "entry_gap_exceed" in set(rejects["reason"])
    buy = trades[trades["side"] == "buy"].iloc[0]
    assert buy["date"] == df.index[2]


def test_entry_limit_up_and_liquidity_blocks_are_reported():
    limit_up_df = _frame(
        opens=[100.0, 111.0, 101.0],
        closes=[100.0, 109.0, 102.0],
        entry_signals=[1, 0, 0],
    )
    liquidity_df = _frame(
        opens=[100.0, 101.0, 102.0],
        closes=[100.0, 102.0, 103.0],
        volumes=[10_000.0, 100.0, 10_000.0],
        entry_signals=[1, 0, 0],
    )

    limit_up = _run(limit_up_df, {"MAX_GAP_FOR_ENTRY": 0.50})
    liquidity = _run(liquidity_df, {"MIN_DAILY_VOLUME_FOR_FILL": 1000.0})

    assert "limit_up_locked" in set(limit_up["fill_rejects"]["reason"])
    assert "insufficient_liquidity" in set(liquidity["fill_rejects"]["reason"])


def test_exit_limit_down_block_is_reported_for_pending_stop_exit():
    df = _frame(
        opens=[100.0, 100.0, 84.0, 86.0],
        closes=[100.0, 94.0, 85.0, 87.0],
        entry_signals=[1, 0, 0, 0],
    )

    result = _run(df)
    rejects = result["fill_rejects"]

    sell_rejects = rejects[rejects["side"] == "sell"]
    assert "limit_down_locked" in set(sell_rejects["reason"])


def test_higher_slippage_reduces_return_and_records_slippage_costs():
    df = _frame(
        opens=[100.0, 100.0, 105.0],
        closes=[100.0, 104.0, 108.0],
        entry_signals=[1, 0, 0],
    )

    no_slippage = _run(df, {"SLIPPAGE": 0.0})
    high_slippage = _run(df, {"SLIPPAGE": 0.02})

    assert high_slippage["total_return"] < no_slippage["total_return"]
    assert high_slippage["cost_breakdown"]["slippage_buy_total"] > 0
    assert high_slippage["cost_breakdown"]["slippage_sell_total"] > 0
