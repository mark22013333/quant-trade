"""
測試實盤交易系統
"""
from strategies.ma_cross import MACrossStrategy
from broker.shioaji_broker import ShioajiBroker
from data.providers.shioaji_provider import ShioajiProvider
from live_trading.trader import LiveTrader
from live_trading.risk_manager import RiskManager
import time

if __name__ == "__main__":
    # 步驟 1：建立資料提供者
    print("建立資料提供者...")
    provider = ShioajiProvider()
    
    # 步驟 2：建立券商介面
    print("建立券商介面...")
    broker = ShioajiBroker(simulation=True)  # 使用模擬環境
    
    # 步驟 3：建立風險管理器
    print("建立風險管理器...")
    risk_config = {
        'max_position_percent': 0.1,  # 單一部位最大資金比例 10%
        'stop_loss_percent': 0.03     # 單一部位停損比例 3%
    }
    risk_manager = RiskManager(config=risk_config)
    
    # 步驟 4：建立交易策略
    print("建立交易策略...")
    strategy = MACrossStrategy(short_window=5, long_window=20)
    
    # 步驟 5：建立交易標的列表
    symbols = ['2330', '2317', '2454']  # 台積電、鴻海、聯發科
    
    # 步驟 6：整合為實盤交易系統
    print("建立實盤交易系統...")
    trader = LiveTrader(
        strategy=strategy,
        broker=broker,
        data_provider=provider,
        symbols=symbols,
        risk_manager=risk_manager
    )
    
    try:
        # 步驟 7：啟動交易系統
        print("啟動交易系統...")
        trader.start()
        
        # 查看系統狀態
        time.sleep(2)  # 等待系統啟動完成
        status = trader.get_status()
        print("\n交易系統狀態:")
        print(f"運行狀態: {'運行中' if status['is_running'] else '未運行'}")
        print(f"市場狀態: {'開市中' if status['is_market_open'] else '已收市'}")
        print(f"交易標的: {status['symbols']}")
        print(f"持倉狀態: {status['positions']}")
        
        # 持續執行 60 秒
        print("\n系統將運行 60 秒後停止...")
        for i in range(60):
            time.sleep(1)
            if i % 10 == 0:
                print(f"已運行 {i} 秒...")
        
    except KeyboardInterrupt:
        print("\n使用者中斷程式")
    finally:
        # 步驟 8：停止交易系統
        print("停止交易系統...")
        trader.stop()
        print("交易系統已完全停止")
