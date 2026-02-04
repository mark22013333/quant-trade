"""
市場資料類別：用於統一和標準化不同來源的市場資料
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import importlib.util
import os


class MarketData:
    """
    市場資料管理器：標準化不同資料來源的股市資料，提供一致的資料處理介面
    """
    def __init__(self, provider=None, data_dir=None):
        """
        初始化市場資料管理器
        :param provider: 資料提供者實例
        :param data_dir: 本地資料儲存目錄
        """
        self.provider = provider
        self.data_dir = data_dir or Path(__file__).parent.parent / 'data' / 'stock_data'
        self.data_cache = {}  # 記憶體快取
        self.storage_format = self._detect_storage_format()
        
        # 建立資料儲存目錄（如果不存在）
        os.makedirs(self.data_dir, exist_ok=True)

    def _detect_storage_format(self):
        """根據依賴情況決定使用 parquet 或 csv"""
        parquet_available = (
            importlib.util.find_spec("pyarrow") is not None or
            importlib.util.find_spec("fastparquet") is not None
        )
        return "parquet" if parquet_available else "csv"
    
    def set_provider(self, provider):
        """
        設定資料提供者
        """
        self.provider = provider
    
    def _get_local_data_path(self, symbol, interval):
        """
        取得本地資料檔案路徑
        """
        ext = "parquet" if self.storage_format == "parquet" else "csv"
        return self.data_dir / f"{symbol}_{interval}.{ext}"

    def _read_local_data(self, path):
        if self.storage_format == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path, index_col=0, parse_dates=True)

    def _write_local_data(self, df, path):
        if self.storage_format == "parquet":
            df.to_parquet(path)
        else:
            df.to_csv(path)

    def _coverage_ok(self, df, start_date, end_date, min_ratio=0.9):
        if df.empty:
            return False
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        expected = pd.date_range(start=start, end=end, freq='B')
        if len(expected) == 0:
            return True
        actual = df.index.unique()
        overlap = actual.intersection(expected)
        ratio = len(overlap) / len(expected)
        return ratio >= min_ratio
    
    def load_data(self, symbol, start_date, end_date=None, interval='1d', use_cache=True, force_download=False):
        """
        載入股票資料，先檢查本地檔案和快取，若無才從遠端載入
        :param symbol: 股票代號
        :param start_date: 開始日期
        :param end_date: 結束日期，預設為今日
        :param interval: 資料頻率，如 '1d'
        :param use_cache: 是否使用記憶體快取
        :param force_download: 強制重新下載
        :return: 股票資料 DataFrame
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
            
        # 檢查記憶體快取
        cache_key = f"{symbol}_{start_date}_{end_date}_{interval}"
        if use_cache and cache_key in self.data_cache and not force_download:
            return self.data_cache[cache_key]
            
        # 檢查資料是否在本地檔案
        local_path = self._get_local_data_path(symbol, interval)
        existing_df = None
        
        if os.path.exists(local_path) and not force_download:
            try:
                full_df = self._read_local_data(local_path)
                
                # 轉換日期索引
                if not pd.api.types.is_datetime64_ns_dtype(full_df.index):
                    full_df.index = pd.to_datetime(full_df.index)
                
                # 過濾日期範圍
                df = full_df.loc[(full_df.index >= pd.to_datetime(start_date)) & 
                                 (full_df.index <= pd.to_datetime(end_date))]
                
                # 若本地有完整資料，直接返回
                if self._coverage_ok(df, start_date, end_date):
                    # 儲存到快取
                    if use_cache:
                        self.data_cache[cache_key] = df
                    return df
                existing_df = full_df
                    
            except Exception as e:
                print(f"讀取本地資料時發生錯誤: {str(e)}")
                # 本地資料讀取失敗，重新下載
        
        # 從資料提供者下載資料
        if self.provider is None:
            raise ValueError("未設定資料提供者")
            
        df = self.provider.get_historical_data(symbol, start_date, end_date, interval)
        
        if not df.empty:
            # 儲存資料到本地
            try:
                # 檢查是否已有本地資料
                if os.path.exists(local_path) and not force_download:
                    if existing_df is None:
                        existing_df = self._read_local_data(local_path)
                    combined = pd.concat([existing_df, df])
                    combined = combined[~combined.index.duplicated(keep='last')]
                    combined = combined.sort_index()
                    self._write_local_data(combined, local_path)
                else:
                    self._write_local_data(df, local_path)
            except Exception as e:
                print(f"儲存資料到本地時發生錯誤: {str(e)}")
            
            # 儲存到快取
            if use_cache:
                self.data_cache[cache_key] = df
                
        return df
    
    def add_technical_indicators(self, df, indicators=None):
        """
        新增技術指標到股價資料
        :param df: 股價 DataFrame
        :param indicators: 指標設定列表，例如 [('SMA', 20), ('RSI', 14)]
        :return: 加入指標後的 DataFrame
        """
        if indicators is None:
            return df
            
        result = df.copy()
        
        for indicator_type, *params in indicators:
            if indicator_type == 'SMA':  # 簡單移動平均線
                window = params[0]
                result[f'SMA_{window}'] = result['Close'].rolling(window=window).mean()
                
            elif indicator_type == 'EMA':  # 指數移動平均線
                window = params[0]
                result[f'EMA_{window}'] = result['Close'].ewm(span=window, adjust=False).mean()
                
            elif indicator_type == 'RSI':  # 相對強弱指標
                window = params[0]
                delta = result['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
                rs = gain / loss
                result[f'RSI_{window}'] = 100 - (100 / (1 + rs))
                
            elif indicator_type == 'MACD':  # 移動平均收斂擴散
                fast = params[0] if len(params) > 0 else 12
                slow = params[1] if len(params) > 1 else 26
                signal = params[2] if len(params) > 2 else 9
                
                fast_ema = result['Close'].ewm(span=fast, adjust=False).mean()
                slow_ema = result['Close'].ewm(span=slow, adjust=False).mean()
                result['MACD'] = fast_ema - slow_ema
                result['MACD_Signal'] = result['MACD'].ewm(span=signal, adjust=False).mean()
                result['MACD_Hist'] = result['MACD'] - result['MACD_Signal']
                
            elif indicator_type == 'BB':  # 布林通道
                window = params[0] if len(params) > 0 else 20
                std_dev = params[1] if len(params) > 1 else 2
                
                result[f'BB_Mid_{window}'] = result['Close'].rolling(window=window).mean()
                result[f'BB_Std_{window}'] = result['Close'].rolling(window=window).std()
                result[f'BB_Upper_{window}'] = result[f'BB_Mid_{window}'] + std_dev * result[f'BB_Std_{window}']
                result[f'BB_Lower_{window}'] = result[f'BB_Mid_{window}'] - std_dev * result[f'BB_Std_{window}']
                
        return result
    
    def get_multiple_symbols(self, symbols, start_date, end_date=None, interval='1d', column='Close'):
        """
        取得多個股票的特定欄位資料（適合比較和投資組合分析）
        :param symbols: 股票代號列表
        :param start_date: 開始日期
        :param end_date: 結束日期
        :param interval: 資料頻率
        :param column: 要抽取的欄位，例如 'Close'
        :return: 包含多個股票資料的 DataFrame
        """
        result = pd.DataFrame()
        
        for symbol in symbols:
            df = self.load_data(symbol, start_date, end_date, interval)
            if not df.empty:
                result[symbol] = df[column]
        
        return result
    
    def get_market_calendar(self, year=None):
        """
        取得市場交易日歷
        由於這需要特定市場資料，這裡僅返回工作日做為近似值
        實際使用時應該根據市場規則或從交易所API獲取
        """
        if year is None:
            year = datetime.now().year
            
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"
        
        # 使用台積電作為參照物取得實際交易日
        # 台股的標的通常每日都有交易紀錄
        try:
            df = self.load_data("2330.TW", start_date, end_date)
            return df.index.tolist()
        except:
            # 如果失敗，返回一個可能的工作日列表（週一到週五）
            all_days = pd.date_range(start=start_date, end=end_date, freq='D')
            trading_days = [d for d in all_days if d.weekday() < 5]
            return trading_days
