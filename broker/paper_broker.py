import pandas as pd
from .broker_interface import BrokerInterface

class PaperBroker(BrokerInterface):
    """
    紙上交易（模擬下單）券商，實作標準券商介面
    """
    def __init__(self, init_cash=1_000_000):
        self.init_cash = init_cash
        self.cash = init_cash
        self.positions = {}  # symbol: 持股數

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

    def simulate(self, df, symbol='2330.TW'):
        """
        給回測引擎用的模擬下單流程
        """
        # 重置狀態
        self.cash = self.init_cash
        self.positions = {}
        position = 0
        equity_curve = []
        trade_count = 0
        for idx, row in df.iterrows():
            # 買進
            if row['position'] == 1:
                buy_qty = int(self.cash // row['Close'])
                if buy_qty > 0:
                    self.place_order(idx, row['Close'], buy_qty, 'buy', symbol)
                    trade_count += 1
            # 賣出
            elif row['position'] == -1 and self.get_position(symbol) > 0:
                sell_qty = self.get_position(symbol)
                self.place_order(idx, row['Close'], sell_qty, 'sell', symbol)
                trade_count += 1
            equity_curve.append(self.cash + self.get_position(symbol) * row['Close'])
        total_return = (equity_curve[-1] - self.init_cash) / self.init_cash
        max_drawdown = self._max_drawdown(equity_curve)
        return {
            'equity_curve': pd.Series(equity_curve, index=df.index),
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'trade_count': trade_count
        }

    def _max_drawdown(self, curve):
        curve = pd.Series(curve)
        roll_max = curve.cummax()
        drawdown = (curve - roll_max) / roll_max
        return drawdown.min()
