"""
永豐金證券 Shioaji 資料提供者
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import shioaji as sj
from .base_provider import DataProvider

class ShioajiProvider(DataProvider):
    """
    使用永豐金證券 Shioaji API 取得股市資料
    """
    def __init__(self, api=None):
        """
        初始化 Shioaji 資料提供者
        :param api: 現有的 Shioaji API 實例，若無則內部建立
        """
        self.api = api
        self._connected = False
        if self.api is not None:
            self._connected = True
    
    def connect(self, api_key, api_secret, simulation=False):
        """
        連接 Shioaji API
        :param api_key: API Key
        :param api_secret: API Secret
        :param simulation: 是否使用模擬環境
        """
        try:
            self.api = sj.Shioaji(simulation=simulation)
            self.api.login(api_key, api_secret)
            self._connected = True
            return True
        except Exception as e:
            print(f"連接 Shioaji API 發生錯誤: {str(e)}")
            self._connected = False
            return False
    
    def _ensure_connected(self):
        """
        確保已連接 Shioaji API
        """
        if not self._connected or self.api is None:
            raise ConnectionError("尚未連接 Shioaji API，請先呼叫 connect() 方法")
    
    def get_historical_data(self, symbol, start_date, end_date=None, interval='1d'):
        """
        取得歷史資料
        :param symbol: 股票代號，如 '2330'
        :param start_date: 開始日期，如 '2020-01-01' 或 datetime 物件
        :param end_date: 結束日期，預設為今日
        :param interval: 資料週期，如 '1d', 'tick'
        :return: DataFrame 包含 OHLCV 資料
        """
        self._ensure_connected()
        
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
        if end_date is None:
            end_date = datetime.now()
        elif isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
            
        # 移除 symbol 尾端的 .TW
        if '.TW' in symbol:
            symbol = symbol.replace('.TW', '')
            
        try:
            contract = self.api.Contracts.Stocks[symbol]
            
            if interval == '1d':
                # 取得日K資料
                kbars = self.api.kbars(
                    contract=contract,
                    start=start_date.strftime('%Y-%m-%d'),
                    end=end_date.strftime('%Y-%m-%d')
                )
                df = pd.DataFrame({
                    'Open': kbars.open,
                    'High': kbars.high,
                    'Low': kbars.low,
                    'Close': kbars.close,
                    'Volume': kbars.volume
                })
                df.index = pd.to_datetime(kbars.ts)
                
            elif interval == 'tick':
                # 取得 Tick 資料（僅當天）
                ticks = self.api.ticks(
                    contract=contract,
                    date=end_date.strftime('%Y-%m-%d')
                )
                df = pd.DataFrame({
                    'Close': ticks.close,
                    'Volume': ticks.volume,
                    'TickType': ticks.tick_type
                })
                df.index = pd.to_datetime(ticks.ts)
                
            return df
                
        except Exception as e:
            print(f"取得 {symbol} 歷史資料時發生錯誤: {str(e)}")
            return pd.DataFrame()
    
    def get_realtime_data(self, symbols):
        """
        取得即時資料
        :param symbols: 股票代號列表
        :return: Dict 包含即時報價資訊
        """
        self._ensure_connected()
        
        if not isinstance(symbols, list):
            symbols = [symbols]
            
        result = {}
        for symbol in symbols:
            try:
                # 移除 symbol 尾端的 .TW
                if '.TW' in symbol:
                    clean_symbol = symbol.replace('.TW', '')
                else:
                    clean_symbol = symbol
                    
                contract = self.api.Contracts.Stocks[clean_symbol]
                
                # 訂閱 Tick 資料
                self.api.quote.subscribe(
                    contract,
                    quote_type='tick',
                    version='v1'
                )
                
                # 取得即時報價
                snapshots = self.api.snapshots([contract])
                if snapshots:
                    snapshot = snapshots[0]
                    result[symbol] = {
                        'price': snapshot.close,
                        'change': snapshot.change,
                        'change_percent': snapshot.change_rate,
                        'volume': snapshot.total_volume,
                        'time': datetime.fromtimestamp(snapshot.ts / 1000000000).strftime('%Y-%m-%d %H:%M:%S'),
                        'name': contract.name
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
        取得基本面資料 (Shioaji API 不直接提供完整基本面資料，需另外實作)
        :param symbol: 股票代號
        :return: Dict 包含基本面資訊
        """
        self._ensure_connected()
        
        # 移除 symbol 尾端的 .TW
        if '.TW' in symbol:
            clean_symbol = symbol.replace('.TW', '')
        else:
            clean_symbol = symbol
        
        try:
            contract = self.api.Contracts.Stocks[clean_symbol]
            
            # Shioaji 提供的基本資訊有限，僅返回合約基本資訊
            fundamental = {
                'name': contract.name,
                'exchange_code': contract.exchange,
                'market': contract.market,
                'security_type': contract.security_type,
                'currency': contract.currency,
                'limit_up': contract.limit_up,
                'limit_down': contract.limit_down,
                'reference': contract.reference,
                'lot_size': contract.lot_size
            }
                
            return fundamental
                
        except Exception as e:
            print(f"取得 {symbol} 基本面資料時發生錯誤: {str(e)}")
            return {}
