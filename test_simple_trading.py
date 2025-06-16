"""
簡易實盤交易系統測試
使用模擬資料和模擬券商，不需要外部 API 連接
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from strategies.ma_cross import MACrossStrategy
from broker.paper_broker import PaperBroker

class MockDataProvider:
    """模擬資料提供者"""
    def __init__(self):
        self.data = {}
    
    def get_historical_data(self, symbol, start_date, end_date=None):
        """產生模擬歷史資料"""
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date) if end_date else datetime.now()
        
        # 產生日期範圍
        dates = pd.date_range(start=start, end=end, freq='B')
        
        # 產生模擬價格
        np.random.seed(42)  # 固定隨機種子以便重複測試
        price = 100
        prices = []
        for i in range(len(dates)):
            change = np.random.normal(0, 1) * 2  # 每日變動約 +-2%
            price = max(price + change, 50)  # 確保價格不會太低
            prices.append(price)
        
        # 建立 DataFrame
        df = pd.DataFrame({
            'Open': prices,
            'High': [p * (1 + np.random.uniform(0, 0.02)) for p in prices],
            'Low': [p * (1 - np.random.uniform(0, 0.02)) for p in prices],
            'Close': prices,
            'Volume': np.random.randint(1000, 10000, len(dates))
        }, index=dates)
        
        return df
    
    def get_realtime_data(self, symbols):
        """取得模擬即時資料"""
        result = {}
        for symbol in symbols:
            result[symbol] = {
                'price': np.random.uniform(90, 110),
                'change': np.random.uniform(-2, 2),
                'volume': np.random.randint(1000, 10000),
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        return result

class SimpleLiveTrader:
    """簡易實盤交易系統"""
    def __init__(self, strategy, broker, data_provider, symbols):
        self.strategy = strategy
        self.broker = broker
        self.data_provider = data_provider
        self.symbols = symbols if isinstance(symbols, list) else [symbols]
        self.market_data = {}
        self.signals = {}
        
    def update_market_data(self):
        """更新市場資料"""
        print("更新市場資料...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        for symbol in self.symbols:
            data = self.data_provider.get_historical_data(
                symbol, 
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d')
            )
            self.market_data[symbol] = data
            print(f"已取得 {symbol} 的歷史資料，共 {len(data)} 個交易日")
        
    def generate_signals(self):
        """產生交易訊號"""
        print("產生交易訊號...")
        for symbol, data in self.market_data.items():
            signals_df = self.strategy.generate_signals(data)
            latest_signal = signals_df.iloc[-1]
            
            # 取得訊號
            position = 0
            if latest_signal['position'] > 0:
                position = 1  # 買入訊號
            elif latest_signal['position'] < 0:
                position = -1  # 賣出訊號
            
            self.signals[symbol] = {
                'position': position,
                'price': latest_signal['Close'],
                'short_ma': latest_signal['short_ma'],
                'long_ma': latest_signal['long_ma']
            }
            
            signal_type = "無" if position == 0 else "買入" if position > 0 else "賣出"
            print(f"{symbol} 的最新訊號為: {signal_type}，收盤價: {latest_signal['Close']:.2f}")
            print(f"  短期均線: {latest_signal['short_ma']:.2f}, 長期均線: {latest_signal['long_ma']:.2f}")
    
    def execute_trades(self):
        """執行交易"""
        print("執行交易...")
        for symbol, signal in self.signals.items():
            current_position = self.broker.get_position(symbol)
            target_position = signal['position']
            price = signal['price']
            
            # 無交易訊號或已持有相同部位
            if target_position == 0 or (target_position > 0 and current_position > 0) or \
               (target_position < 0 and current_position < 0):
                print(f"{symbol}: 無交易訊號或已持有相同部位，跳過執行")
                continue
            
            # 執行買入
            if target_position > 0 and current_position <= 0:
                quantity = 1000  # 固定交易1000股
                print(f"{symbol}: 執行買入 {quantity} 股，價格 {price:.2f}")
                self.broker.place_order(datetime.now(), price, quantity, 'buy', symbol)
            
            # 執行賣出
            elif target_position < 0 and current_position > 0:
                quantity = current_position
                print(f"{symbol}: 執行賣出 {quantity} 股，價格 {price:.2f}")
                self.broker.place_order(datetime.now(), price, quantity, 'sell', symbol)
    
    def run_cycle(self):
        """執行一個完整的交易循環"""
        self.update_market_data()
        self.generate_signals()
        self.execute_trades()
        
        # 顯示目前部位和資金
        positions = {}
        for symbol in self.symbols:
            pos = self.broker.get_position(symbol)
            if pos > 0:
                positions[symbol] = pos
        
        cash = self.broker.get_balance()
        print("\n=== 目前狀態 ===")
        print(f"現金餘額: {cash:,.0f} 元")
        print(f"持倉: {positions}")
        print(f"訊號: {self.signals}")
        print("================\n")

if __name__ == "__main__":
    # 步驟 1：建立交易策略
    print("建立交易策略...")
    strategy = MACrossStrategy(short_window=5, long_window=20)
    
    # 步驟 2：建立模擬券商
    print("建立模擬券商...")
    broker = PaperBroker(init_cash=1000000)  # 設定初始資金 100 萬
    
    # 步驟 3：建立模擬資料提供者
    print("建立模擬資料提供者...")
    data_provider = MockDataProvider()
    
    # 步驟 4：建立交易標的列表
    symbols = ['2330', '2317', '2454']  # 台積電、鴻海、聯發科
    
    # 步驟 5：建立簡易實盤交易系統
    print("建立簡易實盤交易系統...")
    trader = SimpleLiveTrader(
        strategy=strategy,
        broker=broker,
        data_provider=data_provider,
        symbols=symbols
    )
    
    # 步驟 6：執行交易循環
    print("開始執行交易循環...")
    for i in range(3):  # 執行 3 個循環
        print(f"\n=== 交易循環 {i+1} ===")
        trader.run_cycle()
