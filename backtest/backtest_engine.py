import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Heiti TC', 'Arial Unicode MS', 'Microsoft JhengHei', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

from config.settings import DEFAULT_SETTINGS
from utils.signals import add_signal_from_position

class BacktestEngine:
    """
    回測引擎：執行策略、模擬下單、計算績效
    """
    def __init__(self, df, strategy, broker, settings=None):
        self.df = df
        self.strategy = strategy
        self.broker = broker
        self.result = None
        self.settings = settings or DEFAULT_SETTINGS.get('BACKTEST', {})

    def run(self):
        df = self.strategy.generate_signals(self.df)
        if 'signal' not in df.columns:
            df = add_signal_from_position(df)
        if hasattr(self.broker, 'simulate'):
            self.result = self.broker.simulate(df, settings=self.settings)
        else:
            raise NotImplementedError("Broker does not support simulate() for backtesting")

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
        self.result['equity_curve'].plot(title="資金曲線")
        plt.show()
