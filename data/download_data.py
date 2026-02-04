"""
自動下載台股歷史資料（用 yfinance）
用法：python download_data.py 2330.TW 2018-01-01 2024-12-31
輸出：data/stock_data/<symbol>_1d.csv
"""
import sys
import yfinance as yf
import os
from pathlib import Path

def download_stock(symbol, start, end, save_dir=None):
    if save_dir is None:
        save_dir = Path(__file__).parent / "stock_data"
    os.makedirs(save_dir, exist_ok=True)
    df = yf.download(symbol, start=start, end=end)
    if df.empty:
        print(f"查無資料：{symbol}")
        return
    save_path = os.path.join(str(save_dir), f"{symbol}_1d.csv")
    df.to_csv(save_path)
    print(f"已儲存 {symbol} 資料到 {save_path}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法：python download_data.py <股票代號> <起始日> <結束日>")
        print("範例：python download_data.py 2330.TW 2018-01-01 2024-12-31")
        sys.exit(1)
    symbol = sys.argv[1]
    start = sys.argv[2]
    end = sys.argv[3]
    download_stock(symbol, start, end, save_dir=".")
