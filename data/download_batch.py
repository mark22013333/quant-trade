"""
批次下載多檔台股歷史資料（用 yfinance）
用法：python download_batch.py stock_list.txt 2018-01-01 2024-12-31
stock_list.txt 每行一個股票代號，例如：
2330.TW
2317.TW
0050.TW
"""
import sys
import os
from download_data import download_stock

def read_symbols(file_path):
    with open(file_path, 'r') as f:
        symbols = [line.strip() for line in f if line.strip()]
    return symbols

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法：python download_batch.py <股票清單檔案> <起始日> <結束日>")
        print("範例：python download_batch.py stock_list.txt 2018-01-01 2024-12-31")
        sys.exit(1)
    stock_list_path = sys.argv[1]
    start = sys.argv[2]
    end = sys.argv[3]
    symbols = read_symbols(stock_list_path)
    for symbol in symbols:
        print(f"下載 {symbol} ...")
        download_stock(symbol, start, end, save_dir=".")
