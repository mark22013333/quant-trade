"""
主程式：整合資料下載、策略、回測、模擬下單
"""
from strategies.ma_cross import MACrossStrategy
from backtest.backtest_engine import BacktestEngine
from broker.paper_broker import PaperBroker
import pandas as pd
import os

if __name__ == "__main__":
    # 步驟1：讀取歷史資料
    data_path = os.path.join("data", "2330.TW.csv")
    if not os.path.exists(data_path):
        print("請先下載歷史資料到 data/ 目錄，檔名如 2330.TW.csv")
        exit(1)
    df = pd.read_csv(data_path, index_col=0, parse_dates=True)
    # 強制轉換 Close 欄位為 float，並排除非數字資料
    df = df[pd.to_numeric(df['Close'], errors='coerce').notnull()]
    df['Close'] = df['Close'].astype(float)

    # 步驟2：建立策略
    strategy = MACrossStrategy(short_window=5, long_window=20)

    # 步驟3：建立模擬券商
    broker = PaperBroker()

    # 步驟4：回測
    engine = BacktestEngine(df, strategy, broker)
    engine.run()
    engine.report()
