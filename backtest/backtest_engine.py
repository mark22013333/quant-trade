import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Heiti TC', 'Arial Unicode MS', 'Microsoft JhengHei', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

class BacktestEngine:
    """
    回測引擎：執行策略、模擬下單、計算績效
    """
    def __init__(self, df, strategy, broker):
        self.df = df
        self.strategy = strategy
        self.broker = broker
        self.result = None

    def run(self):
        df = self.strategy.generate_signals(self.df)
        self.result = self.broker.simulate(df)

    def report(self):
        if self.result is None:
            print("尚未執行回測")
            return
        print("\n=== 回測績效 ===")
        print(f"總報酬率: {self.result['total_return']:.2%}")
        print(f"最大回撤: {self.result['max_drawdown']:.2%}")
        print(f"交易次數: {self.result['trade_count']}")
        self.result['equity_curve'].plot(title="資金曲線")
        plt.show()
