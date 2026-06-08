# Backtest Assumptions

Version: `v2026.02-realworld-1`

This document defines shared assumptions for all backtest paths (`backtest/backtest_engine.py`, `broker/paper_broker.py`, `app/paper/ledger.py`).

## Execution Model

- Signal generated on day `t`.
- Main multi-strategy backtest fills at day `t+1` open (with slippage).
- Legacy broker simulation fills at close of current bar (legacy behavior).

## Cost Model

- Commission rate: default `0.1425%`.
- Minimum commission fee per order: default `20`.
- Sell-side tax: default `0.3%`.
- Fee rounding: ceil (conservative).
- Tax rounding: ceil (conservative).

## Slippage

- Default slippage is `0.1%` (`SLIPPAGE=0.001`).
- Slippage is applied to both buy and sell fills.

## Stress Fill Rejections (Multi-Strategy Backtest)

- Exit order may be rejected (delayed) when market is approximated as limit-down.
- Entry order may be delayed if opening gap exceeds configured threshold.
- Fill can be rejected when liquidity is below `MIN_DAILY_VOLUME_FOR_FILL`.
- Pending entry expires after `MAX_ENTRY_DELAY_DAYS`.

## Regression Coverage

The multi-strategy execution model is covered by `tests/test_backtest_execution_model.py`.

- T+1 entry fills use next-day open with buy-side slippage.
- Entry can be delayed by excessive opening gap, then filled before expiry.
- Entry can be rejected by limit-up or insufficient liquidity.
- Stop exits can be delayed by limit-down conditions.
- Higher slippage must reduce return and increase recorded slippage costs.

## Data Quality

- `chip_data_status` is attached to strategy input:
  - `ok`: chip data loaded and aligned.
  - `degraded`: chip source failed or partially unavailable.
  - `missing`: no chip data was returned.
  - `not_applicable`: non-TW market or chip disabled.

Backtest outputs should include:

- `cost_breakdown`
- `fill_rejects`
- `data_quality_summary`
