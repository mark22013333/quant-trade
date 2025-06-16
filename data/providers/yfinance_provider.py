"""
Yahoo Finance 資料提供者
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from .base_provider import DataProvider

class YFinanceProvider(DataProvider):
    """
    使用 Yahoo Finance API 取得股市資料
    """
    def __init__(self):
        self.cache = {}  # 簡單的資料快取
    
    def get_historical_data(self, symbol, start_date, end_date=None, interval='1d'):
        """
        取得歷史資料
        :param symbol: 股票代號，如 '2330.TW'
        :param start_date: 開始日期，如 '2020-01-01' 或 datetime 物件
        :param end_date: 結束日期，預設為今日
        :param interval: 資料週期，如 '1d', '1h', '1wk'
        :return: DataFrame 包含 OHLCV 資料
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
            
        cache_key = f"{symbol}_{start_date}_{end_date}_{interval}"
        
        if cache_key in self.cache:
            return self.cache[cache_key]
            
        try:
            data = yf.download(
                symbol, 
                start=start_date, 
                end=end_date, 
                interval=interval,
                auto_adjust=True  # 自動調整股價（考慮除權息）
            )
            
            # 標準化欄位名稱
            if not data.empty:
                # 修正處理元組型態的欄位名稱
                new_columns = {}
                for col in data.columns:
                    if isinstance(col, tuple):
                        # 對於元組型態的欄位，使用第一個元素作為欄位名稱
                        new_name = col[0].capitalize() if isinstance(col[0], str) else str(col[0])
                        new_columns[col] = new_name
                    else:
                        new_columns[col] = col.capitalize() if isinstance(col, str) else str(col)
                
                data = data.rename(columns=new_columns)
                
                # 檢查並處理缺失值
                data = data.ffill()  # 使用前一個有效值填充
                
                # 新增日期欄位（方便後續處理）
                data['Date'] = data.index
                
                # 快取結果
                self.cache[cache_key] = data
                
                return data
            else:
                print(f"警告：無法取得 {symbol} 的資料")
                return pd.DataFrame()
                
        except Exception as e:
            print(f"取得 {symbol} 歷史資料時發生錯誤: {str(e)}")
            return pd.DataFrame()
    
    def get_realtime_data(self, symbols):
        """
        取得即時資料 (注意：Yahoo Finance 非真正即時資料，通常有 15-20 分鐘延遲)
        :param symbols: 股票代號列表
        :return: Dict 包含即時報價資訊
        """
        if not isinstance(symbols, list):
            symbols = [symbols]
            
        result = {}
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                quote = ticker.history(period="1d")
                
                if not quote.empty:
                    last_row = quote.iloc[-1]
                    result[symbol] = {
                        'price': last_row['Close'] if 'Close' in last_row else None,
                        'change': last_row['Close'] - last_row['Open'] if 'Close' in last_row and 'Open' in last_row else None,
                        'change_percent': (last_row['Close'] / last_row['Open'] - 1) * 100 if 'Close' in last_row and 'Open' in last_row and last_row['Open'] != 0 else None,
                        'volume': last_row['Volume'] if 'Volume' in last_row else None,
                        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'name': info.get('shortName', symbol),
                    }
                else:
                    result[symbol] = {
                        'error': '無資料'
                    }
                    
            except Exception as e:
                result[symbol] = {
                    'error': str(e)
                }
                
        return result
    
    def get_fundamental_data(self, symbol):
        """
        取得基本面資料
        :param symbol: 股票代號
        :return: Dict 包含基本面資訊
        """
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            # 提取常用基本面指標
            fundamental = {
                'name': info.get('shortName', ''),
                'industry': info.get('industry', ''),
                'sector': info.get('sector', ''),
                'market_cap': info.get('marketCap', 0),
                'pe_ratio': info.get('trailingPE', 0),
                'eps': info.get('trailingEps', 0),
                'dividend_yield': info.get('dividendYield', 0) * 100 if info.get('dividendYield') else 0,
                'book_value': info.get('bookValue', 0),
                'pb_ratio': info.get('priceToBook', 0),
                'beta': info.get('beta', 0),
                'revenue': info.get('totalRevenue', 0),
                'net_income': info.get('netIncomeToCommon', 0)
            }
            
            # 取得最近幾季財報數據
            try:
                financials = ticker.financials
                if not financials.empty:
                    fundamental['recent_financials'] = financials.to_dict()
            except:
                pass
                
            return fundamental
                
        except Exception as e:
            print(f"取得 {symbol} 基本面資料時發生錯誤: {str(e)}")
            return {}
