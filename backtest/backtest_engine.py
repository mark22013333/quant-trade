from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from app.backtest.costs import estimate_buy_cost_breakdown, estimate_sell_proceeds_breakdown
from app.backtest.execution_model import ExecutionModelConfig, ResearchExecutionModel
from config.settings import DEFAULT_SETTINGS
from strategies.multi.risk import PositionState, RiskManager
from strategies.multi.swing_system import MultiStrategyConfig, MultiStrategySwingSystem
from strategies.multi.position_sizer import FixedRiskPositionSizer
from utils.signals import add_signal_from_position

plt.rcParams["font.sans-serif"] = ["Heiti TC", "Arial Unicode MS", "Microsoft JhengHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


@dataclass
class PendingEntry:
    signal_date: pd.Timestamp
    score: float
    active_strategies: str
    signal_close: float
    wait_days: int = 0


class BacktestEngine:
    """
    回測引擎：支援舊版單策略流程與新版多策略流程。
    """

    def __init__(self, df, strategy, broker, settings=None):
        self.df = df
        self.strategy = strategy
        self.broker = broker
        self.result = None
        self.settings = settings or DEFAULT_SETTINGS.get("BACKTEST", {})

    def run(self):
        """
        Legacy path: strategy.generate_signals + broker.simulate
        """
        df = self.strategy.generate_signals(self.df)
        if "signal" not in df.columns:
            df = add_signal_from_position(df)
        if hasattr(self.broker, "simulate"):
            self.result = self.broker.simulate(df, settings=self.settings)
        else:
            raise NotImplementedError("Broker does not support simulate() for backtesting")
        return self.result

    def run_multi(
        self,
        df: pd.DataFrame,
        *,
        symbol: str,
        market: str = "TW",
        strategy_config: dict | None = None,
        risk_config: dict | None = None,
        backtest_config: dict | None = None,
    ) -> dict:
        """
        New path: A/B/C multi-strategy + weighted voting + shared risk rules.
        Fill model: signal at t, fill at t+1 open with slippage/fees/tax.
        """
        strategy_config = strategy_config or {}
        cfg = MultiStrategyConfig(
            symbol=symbol,
            market=market,
            enabled=strategy_config.get("enabled"),
            weights=strategy_config.get("weights"),
            threshold=float(strategy_config.get("threshold", 0.6)),
            params=strategy_config.get("params"),
        )
        system = MultiStrategySwingSystem(cfg)
        feature_df, strategy_outputs = system.generate_signals(df)

        merged_backtest_cfg = dict(self.settings)
        merged_backtest_cfg.update(backtest_config or {})
        risk_manager = RiskManager(config=risk_config)
        position_sizer = FixedRiskPositionSizer(risk_fraction=float(merged_backtest_cfg.get("RISK_FRACTION", 0.01)))

        self.result = self._simulate_t1_open_fill(
            feature_df,
            symbol=symbol,
            risk_manager=risk_manager,
            position_sizer=position_sizer,
            settings=merged_backtest_cfg,
        )
        self.result["strategy_outputs"] = strategy_outputs
        self.result["signals"] = feature_df
        return self.result

    def _simulate_t1_open_fill(
        self,
        df: pd.DataFrame,
        *,
        symbol: str,
        risk_manager: RiskManager,
        position_sizer: FixedRiskPositionSizer,
        settings: dict,
    ) -> dict:
        if df is None or df.empty:
            return self._empty_result()

        data = df.copy().sort_index()
        initial_capital = float(settings.get("INITIAL_CAPITAL", 1_000_000))
        commission_rate = float(settings.get("COMMISSION_RATE", 0.001425))
        min_commission_fee = float(settings.get("MIN_COMMISSION_FEE", 20.0))
        tax_rate = float(settings.get("TAX_RATE", 0.003))
        slippage = float(settings.get("SLIPPAGE", 0.001))
        max_gap_for_entry = float(settings.get("MAX_GAP_FOR_ENTRY", 0.07))
        min_daily_volume_for_fill = float(settings.get("MIN_DAILY_VOLUME_FOR_FILL", 0.0))
        limit_down_factor = float(settings.get("LIMIT_DOWN_FACTOR", 0.90))
        limit_up_factor = float(settings.get("LIMIT_UP_FACTOR", 1.10))
        max_entry_delay_days = int(settings.get("MAX_ENTRY_DELAY_DAYS", 3))
        stop_loss_pct = float(risk_manager.config.get("stop_loss_pct", 0.05))
        atr_multiplier = float(risk_manager.config.get("atr_multiplier", 2.0))

        cash = initial_capital
        position_qty = 0
        entry_price = 0.0
        entry_cost_total = 0.0
        entry_atr = 0.0
        entry_date = None
        highest_price = 0.0
        max_return = 0.0
        pending_entry: PendingEntry | None = None
        pending_exit_reason = ""

        equity_curve = []
        trade_log = []
        fill_rejects = []
        cost_breakdown = {
            "buy_fee_total": 0.0,
            "sell_fee_total": 0.0,
            "sell_tax_total": 0.0,
            "slippage_buy_total": 0.0,
            "slippage_sell_total": 0.0,
            "gross_buy_value": 0.0,
            "gross_sell_value": 0.0,
        }

        for i, (idx, row) in enumerate(data.iterrows()):
            open_price = float(row["Open"])
            close_price = float(row["Close"])
            high_price = float(row["High"])
            volume = float(row.get("Volume", 0.0) or 0.0)
            prev_close = float(data["Close"].iloc[i - 1]) if i > 0 else close_price

            # Fill pending exit at today's open.
            if pending_exit_reason and position_qty > 0:
                limit_down_locked = open_price <= (prev_close * limit_down_factor)
                liquidity_blocked = min_daily_volume_for_fill > 0 and volume < min_daily_volume_for_fill
                if limit_down_locked or liquidity_blocked:
                    fill_rejects.append(
                        {
                            "date": idx,
                            "symbol": symbol,
                            "side": "sell",
                            "reason": "limit_down_locked" if limit_down_locked else "insufficient_liquidity",
                            "price": open_price,
                            "prev_close": prev_close,
                            "volume": volume,
                            "pending_exit_reason": pending_exit_reason,
                        }
                    )
                else:
                    sell_price = open_price * (1 - slippage)
                    gross = sell_price * position_qty
                    sell_cost = estimate_sell_proceeds_breakdown(
                        order_value=gross,
                        fee_rate=commission_rate,
                        min_fee=min_commission_fee,
                        tax_rate=tax_rate,
                    )
                    fees = float(sell_cost["fee"])
                    tax = float(sell_cost["tax"])
                    net_proceeds = float(sell_cost["net_proceeds"])
                    cash += net_proceeds
                    pnl = net_proceeds - entry_cost_total
                    pnl_pct = pnl / entry_cost_total if entry_cost_total > 0 else 0.0
                    holding_days = (idx - entry_date).days if entry_date is not None else 0
                    trade_log.append(
                        {
                            "date": idx,
                            "symbol": symbol,
                            "side": "sell",
                            "price": sell_price,
                            "quantity": position_qty,
                            "fees": fees,
                            "tax": tax,
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "holding_days": holding_days,
                            "reason": pending_exit_reason,
                            "cash_after": cash,
                        }
                    )
                    cost_breakdown["sell_fee_total"] += fees
                    cost_breakdown["sell_tax_total"] += tax
                    cost_breakdown["gross_sell_value"] += gross
                    cost_breakdown["slippage_sell_total"] += max((open_price - sell_price) * position_qty, 0.0)
                    position_qty = 0
                    entry_price = 0.0
                    entry_cost_total = 0.0
                    entry_atr = 0.0
                    entry_date = None
                    highest_price = 0.0
                    max_return = 0.0
                    pending_exit_reason = ""

            # Fill pending entry at today's open.
            if pending_entry and position_qty == 0:
                gap_ratio = abs(open_price - pending_entry.signal_close) / pending_entry.signal_close if pending_entry.signal_close > 0 else 0.0
                limit_up_locked = open_price >= (prev_close * limit_up_factor)
                liquidity_blocked = min_daily_volume_for_fill > 0 and volume < min_daily_volume_for_fill
                if gap_ratio > max_gap_for_entry or limit_up_locked or liquidity_blocked:
                    pending_entry.wait_days += 1
                    fill_rejects.append(
                        {
                            "date": idx,
                            "symbol": symbol,
                            "side": "buy",
                            "reason": (
                                "entry_gap_exceed"
                                if gap_ratio > max_gap_for_entry
                                else ("limit_up_locked" if limit_up_locked else "insufficient_liquidity")
                            ),
                            "price": open_price,
                            "signal_close": pending_entry.signal_close,
                            "gap_ratio": gap_ratio,
                            "volume": volume,
                            "wait_days": pending_entry.wait_days,
                        }
                    )
                    if pending_entry.wait_days > max_entry_delay_days:
                        fill_rejects.append(
                            {
                                "date": idx,
                                "symbol": symbol,
                                "side": "buy",
                                "reason": "entry_expired",
                                "wait_days": pending_entry.wait_days,
                                "signal_date": pending_entry.signal_date,
                            }
                        )
                        pending_entry = None
                else:
                    buy_price = open_price * (1 + slippage)
                    stop_distance = max(buy_price * stop_loss_pct, atr_multiplier * max(entry_atr, 0.0))
                    if stop_distance <= 0:
                        stop_distance = buy_price * stop_loss_pct
                    qty = position_sizer.size(
                        equity=cash,
                        entry_price=buy_price,
                        stop_distance=stop_distance,
                        cash_limit=(cash / (1 + commission_rate)),
                    )
                    if qty > 0:
                        gross = buy_price * qty
                        buy_cost = estimate_buy_cost_breakdown(
                            order_value=gross,
                            fee_rate=commission_rate,
                            min_fee=min_commission_fee,
                        )
                        fees = float(buy_cost["fee"])
                        total_cost = float(buy_cost["total_cost"])
                        if total_cost <= cash:
                            cash -= total_cost
                            position_qty = qty
                            entry_price = buy_price
                            entry_cost_total = total_cost
                            entry_date = idx
                            highest_price = max(high_price, close_price, buy_price)
                            max_return = (close_price / entry_price) - 1 if entry_price > 0 else 0.0
                            trade_log.append(
                                {
                                    "date": idx,
                                    "signal_date": pending_entry.signal_date,
                                    "symbol": symbol,
                                    "side": "buy",
                                    "price": buy_price,
                                    "quantity": qty,
                                    "fees": fees,
                                    "tax": 0.0,
                                    "signal_score": pending_entry.score,
                                    "active_strategies": pending_entry.active_strategies,
                                    "cash_after": cash,
                                }
                            )
                            cost_breakdown["buy_fee_total"] += fees
                            cost_breakdown["gross_buy_value"] += gross
                            cost_breakdown["slippage_buy_total"] += max((buy_price - open_price) * qty, 0.0)
                            pending_entry = None
                        else:
                            fill_rejects.append(
                                {
                                    "date": idx,
                                    "symbol": symbol,
                                    "side": "buy",
                                    "reason": "insufficient_cash_after_cost",
                                    "qty": int(qty),
                                    "total_cost": float(total_cost),
                                    "cash": float(cash),
                                }
                            )
                            pending_entry = None
                    else:
                        fill_rejects.append(
                            {
                                "date": idx,
                                "symbol": symbol,
                                "side": "buy",
                                "reason": "position_sizer_qty_zero",
                            }
                        )
                        pending_entry = None

            if position_qty > 0 and entry_price > 0:
                highest_price = max(highest_price, high_price, close_price)
                current_return = (close_price / entry_price) - 1
                max_return = max(max_return, current_return)
                holding_days = (idx - entry_date).days if entry_date is not None else 0
                state = PositionState(
                    entry_price=entry_price,
                    entry_atr=entry_atr,
                    highest_price=highest_price,
                    holding_days=holding_days,
                    current_return=current_return,
                    max_return=max_return,
                )
                decision = risk_manager.check_exit(state, close_price)
                if decision.should_exit and i < len(data.index) - 1:
                    pending_exit_reason = decision.reason

            # Schedule next-day entry if flat and signal is triggered.
            if (
                position_qty == 0
                and pending_entry is None
                and i < len(data.index) - 1
                and int(row.get("entry_signal", 0)) > 0
            ):
                entry_atr = float(row.get("ATR14", 0.0) or 0.0)
                pending_entry = PendingEntry(
                    signal_date=idx,
                    score=float(row.get("ensemble_score", 0.0) or 0.0),
                    active_strategies=str(row.get("active_strategies", "")),
                    signal_close=close_price,
                )

            equity_curve.append(cash + position_qty * close_price)

        # Force close open position at final close for accounting consistency.
        if position_qty > 0 and entry_price > 0:
            idx = data.index[-1]
            close_price = float(data["Close"].iloc[-1]) * (1 - slippage)
            gross = close_price * position_qty
            sell_cost = estimate_sell_proceeds_breakdown(
                order_value=gross,
                fee_rate=commission_rate,
                min_fee=min_commission_fee,
                tax_rate=tax_rate,
            )
            fees = float(sell_cost["fee"])
            tax = float(sell_cost["tax"])
            net_proceeds = float(sell_cost["net_proceeds"])
            cash += net_proceeds
            pnl = net_proceeds - entry_cost_total
            pnl_pct = pnl / entry_cost_total if entry_cost_total > 0 else 0.0
            holding_days = (idx - entry_date).days if entry_date is not None else 0
            trade_log.append(
                {
                    "date": idx,
                    "symbol": symbol,
                    "side": "sell",
                    "price": close_price,
                    "quantity": position_qty,
                    "fees": fees,
                    "tax": tax,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "holding_days": holding_days,
                    "reason": "end_of_data",
                    "cash_after": cash,
                }
            )
            final_open_price = float(data["Close"].iloc[-1])
            cost_breakdown["sell_fee_total"] += fees
            cost_breakdown["sell_tax_total"] += tax
            cost_breakdown["gross_sell_value"] += gross
            cost_breakdown["slippage_sell_total"] += max((final_open_price - close_price) * position_qty, 0.0)
            equity_curve[-1] = cash

        equity_series = pd.Series(equity_curve, index=data.index, dtype=float)
        trade_log_df = pd.DataFrame(trade_log)
        fill_rejects_df = pd.DataFrame(fill_rejects)
        result = self._build_result(initial_capital, equity_series, trade_log_df)
        result["cost_breakdown"] = {key: float(value) for key, value in cost_breakdown.items()}
        result["fill_rejects"] = fill_rejects_df
        result["data_quality_summary"] = self._build_data_quality_summary(data)
        result["execution_model"] = ResearchExecutionModel(
            ExecutionModelConfig(
                commission_rate=commission_rate,
                min_commission_fee=min_commission_fee,
                tax_rate=tax_rate,
                slippage=slippage,
                limit_down_factor=limit_down_factor,
                limit_up_factor=limit_up_factor,
                min_daily_volume_for_fill=min_daily_volume_for_fill,
                max_gap_for_entry=max_gap_for_entry,
                max_entry_delay_days=max_entry_delay_days,
            )
        ).describe()
        return result

    @staticmethod
    def _empty_result() -> dict:
        return {
            "equity_curve": pd.Series(dtype=float),
            "trade_log": pd.DataFrame(),
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "cagr": 0.0,
            "win_rate": 0.0,
            "trade_count": 0,
            "cost_breakdown": {
                "buy_fee_total": 0.0,
                "sell_fee_total": 0.0,
                "sell_tax_total": 0.0,
                "slippage_buy_total": 0.0,
                "slippage_sell_total": 0.0,
                "gross_buy_value": 0.0,
                "gross_sell_value": 0.0,
            },
            "fill_rejects": pd.DataFrame(),
            "data_quality_summary": {},
        }

    def _build_result(self, initial_capital: float, equity_series: pd.Series, trade_log_df: pd.DataFrame) -> dict:
        if equity_series.empty:
            return self._empty_result()

        total_return = (equity_series.iloc[-1] - initial_capital) / initial_capital
        max_drawdown = self._max_drawdown(equity_series)
        daily_returns = equity_series.pct_change().replace([np.inf, -np.inf], 0).fillna(0.0)
        sharpe = 0.0
        if daily_returns.std() > 0:
            sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(252))

        days = (equity_series.index[-1] - equity_series.index[0]).days if len(equity_series.index) > 1 else 0
        years = days / 365.0 if days > 0 else 0.0
        cagr = float((equity_series.iloc[-1] / initial_capital) ** (1 / years) - 1) if years > 0 else 0.0

        closed_trades = trade_log_df[trade_log_df.get("side") == "sell"] if not trade_log_df.empty else pd.DataFrame()
        win_rate = 0.0
        if not closed_trades.empty and "pnl" in closed_trades.columns:
            win_rate = float((closed_trades["pnl"] > 0).sum() / len(closed_trades))

        return {
            "equity_curve": equity_series,
            "trade_log": trade_log_df,
            "total_return": float(total_return),
            "max_drawdown": float(max_drawdown),
            "sharpe": sharpe,
            "cagr": cagr,
            "win_rate": win_rate,
            "trade_count": int(len(closed_trades)),
        }

    @staticmethod
    def _max_drawdown(curve):
        curve = pd.Series(curve).replace([np.inf, -np.inf], np.nan).dropna()
        if curve.empty:
            return 0.0
        roll_max = curve.cummax()
        drawdown = (curve - roll_max) / roll_max
        return float(drawdown.min())

    @staticmethod
    def _build_data_quality_summary(df: pd.DataFrame) -> dict:
        if df is None or df.empty or "chip_data_status" not in df.columns:
            return {}
        values = pd.Series(df["chip_data_status"].astype(str).fillna("unknown"))
        counts = values.value_counts(dropna=False).to_dict()
        return {str(key): int(value) for key, value in counts.items()}

    def report(self):
        if self.result is None:
            print("尚未執行回測")
            return
        print("\n=== 回測績效 ===")
        print(f"總報酬率: {self.result['total_return']:.2%}")
        print(f"最大回撤: {self.result['max_drawdown']:.2%}")
        print(f"夏普比率: {self.result.get('sharpe', 0):.2f}")
        print(f"CAGR: {self.result.get('cagr', 0):.2%}")
        print(f"勝率: {self.result.get('win_rate', 0):.2%}")
        print(f"交易次數: {self.result['trade_count']}")
        self.result["equity_curve"].plot(title="資金曲線")
        plt.show()
