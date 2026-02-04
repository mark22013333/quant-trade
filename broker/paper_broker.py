import pandas as pd
import numpy as np
from config.settings import DEFAULT_SETTINGS
from utils.signals import add_signal_from_position, sanitize_position
from .broker_interface import BrokerInterface

class PaperBroker(BrokerInterface):
    """
    紙上交易（模擬下單）券商，實作標準券商介面
    """
    def __init__(self, init_cash=1_000_000):
        self.init_cash = init_cash
        self.cash = init_cash
        self.positions = {}  # symbol: 持股數
        self.last_trade_log = pd.DataFrame()

    def place_order(self, date, price, quantity, side, symbol):
        # 假設無手續費、無滑價，直接成交
        if side == 'buy':
            cost = price * quantity
            if self.cash >= cost:
                self.cash -= cost
                self.positions[symbol] = self.positions.get(symbol, 0) + quantity
                return True
            else:
                return False
        elif side == 'sell':
            if self.positions.get(symbol, 0) >= quantity:
                self.cash += price * quantity
                self.positions[symbol] -= quantity
                return True
            else:
                return False
        else:
            raise ValueError('side 必須是 buy 或 sell')

    def get_balance(self):
        return self.cash

    def get_position(self, symbol):
        return self.positions.get(symbol, 0)

    def simulate(self, df, symbol='2330.TW', settings=None):
        """
        給回測引擎用的模擬下單流程
        """
        settings = settings or DEFAULT_SETTINGS.get('BACKTEST', {})
        commission_rate = settings.get('COMMISSION_RATE', 0.0)
        tax_rate = settings.get('TAX_RATE', 0.0)
        slippage = settings.get('SLIPPAGE', 0.0)

        if df is None or df.empty:
            return {
                'equity_curve': pd.Series(dtype=float),
                'trade_log': pd.DataFrame(),
                'total_return': 0.0,
                'max_drawdown': 0.0,
                'sharpe': 0.0,
                'cagr': 0.0,
                'win_rate': 0.0,
                'trade_count': 0
            }

        df = df.copy()
        if 'signal' not in df.columns:
            df = add_signal_from_position(df)
        if 'position' in df.columns:
            df['position'] = sanitize_position(df['position'])
        df['signal'] = sanitize_position(df['signal'])

        # 重置狀態
        self.cash = self.init_cash
        self.positions = {}
        position_qty = 0
        entry_price = None
        entry_value = 0.0
        entry_fees = 0.0
        entry_date = None

        equity_curve = []
        trade_log = []

        for idx, row in df.iterrows():
            price = float(row['Close'])
            signal = int(row.get('signal', 0))

            # 只支援做多；負向訊號視為清倉
            if signal > 0 and position_qty == 0:
                buy_price = price * (1 + slippage)
                effective_buy_price = buy_price * (1 + commission_rate)
                buy_qty = int(self.cash // effective_buy_price)
                if buy_qty > 0:
                    gross = buy_price * buy_qty
                    fees = gross * commission_rate
                    total_cost = gross + fees
                    if self.cash >= total_cost:
                        self.cash -= total_cost
                        position_qty += buy_qty
                        self.positions[symbol] = position_qty
                        entry_price = buy_price
                        entry_value = gross
                        entry_fees = fees
                        entry_date = idx
                        trade_log.append({
                            'date': idx,
                            'symbol': symbol,
                            'side': 'buy',
                            'price': buy_price,
                            'quantity': buy_qty,
                            'fees': fees,
                            'tax': 0.0,
                            'cash_after': self.cash
                        })

            elif signal < 0 and position_qty > 0:
                sell_price = price * (1 - slippage)
                gross = sell_price * position_qty
                fees = gross * commission_rate
                tax = gross * tax_rate
                self.cash += gross - fees - tax
                pnl = gross - fees - tax - entry_value - entry_fees
                pnl_pct = pnl / (entry_value + entry_fees) if (entry_value + entry_fees) > 0 else 0.0
                holding_days = (idx - entry_date).days if entry_date is not None else None

                trade_log.append({
                    'date': idx,
                    'symbol': symbol,
                    'side': 'sell',
                    'price': sell_price,
                    'quantity': position_qty,
                    'fees': fees,
                    'tax': tax,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'holding_days': holding_days,
                    'cash_after': self.cash
                })

                position_qty = 0
                self.positions[symbol] = 0
                entry_price = None
                entry_value = 0.0
                entry_fees = 0.0
                entry_date = None

            equity_curve.append(self.cash + position_qty * price)

        equity_series = pd.Series(equity_curve, index=df.index)
        total_return = (equity_series.iloc[-1] - self.init_cash) / self.init_cash if not equity_series.empty else 0.0
        max_drawdown = self._max_drawdown(equity_series)
        daily_returns = equity_series.pct_change().replace([np.inf, -np.inf], 0).fillna(0.0)
        sharpe = 0.0
        if daily_returns.std() > 0:
            sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

        equity_index = equity_series.index
        if not isinstance(equity_index, pd.DatetimeIndex):
            equity_index = pd.to_datetime(equity_index, errors="coerce")
        valid_index = equity_index.dropna() if hasattr(equity_index, "dropna") else equity_index
        days = (valid_index[-1] - valid_index[0]).days if len(valid_index) > 1 else 0
        years = days / 365.0 if days > 0 else 0.0
        cagr = (equity_series.iloc[-1] / self.init_cash) ** (1 / years) - 1 if years > 0 else 0.0

        trade_log_df = pd.DataFrame(trade_log)
        self.last_trade_log = trade_log_df
        closed_trades = trade_log_df[trade_log_df.get('side') == 'sell'] if not trade_log_df.empty else pd.DataFrame()
        win_rate = 0.0
        if not closed_trades.empty and 'pnl' in closed_trades.columns:
            wins = (closed_trades['pnl'] > 0).sum()
            win_rate = wins / len(closed_trades)

        return {
            'equity_curve': equity_series,
            'trade_log': trade_log_df,
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'sharpe': sharpe,
            'cagr': cagr,
            'win_rate': win_rate,
            'trade_count': int(len(closed_trades))
        }

    def get_trade_log(self):
        return self.last_trade_log.copy()

    def _max_drawdown(self, curve):
        curve = pd.Series(curve).replace([np.inf, -np.inf], np.nan).dropna()
        if curve.empty:
            return 0.0
        roll_max = curve.cummax()
        drawdown = (curve - roll_max) / roll_max
        return drawdown.min()
