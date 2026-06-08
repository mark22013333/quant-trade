from __future__ import annotations

from datetime import date

import pandas as pd

from app.backtest.commission_tw import TaiwanStockCommissionInfo
from app.backtest.execution_model import ExecutionModelConfig
from app.backtest.sizer_absolute_capital import AbsoluteCapitalSizer
from app.backtest.strategy_tw_swing import TwSwingStrategy
from app.db.repository import TradingRepository


def _to_dataframe(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(
        [
            {
                "datetime": row.date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            for row in rows
        ]
    )
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame = frame.set_index("datetime").sort_index()
    return frame


def run_backtest(repo: TradingRepository, symbol: str, start_date: date, end_date: date, cash: float = 10_000) -> dict:
    try:
        import backtrader as bt
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("backtrader is not installed. Please `pip install backtrader`.") from exc

    rows = repo.get_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
    df = _to_dataframe(rows)
    if df.empty:
        return {"passed": False, "message": "no_data", "symbol": symbol}

    data_feed = bt.feeds.PandasData(dataname=df)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(data_feed)
    cerebro.addstrategy(TwSwingStrategy)
    cerebro.broker.setcash(float(cash))
    cerebro.broker.addcommissioninfo(TaiwanStockCommissionInfo())
    cerebro.addsizer(AbsoluteCapitalSizer)
    cerebro.run()

    final_value = float(cerebro.broker.getvalue())
    return {
        "passed": True,
        "symbol": symbol,
        "initial_cash": float(cash),
        "final_value": final_value,
        "pnl": final_value - float(cash),
        "return_pct": (final_value / float(cash) - 1.0) if cash > 0 else 0.0,
        "execution_model": {
            "name": "backtrader_alternative",
            "config": ExecutionModelConfig().to_dict(),
        },
    }
