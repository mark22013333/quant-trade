"""
資料提供者基礎介面
所有資料提供者都需繼承此類別
"""
from abc import ABC, abstractmethod

class DataProvider(ABC):
    """
    資料提供者抽象類別
    """
    @abstractmethod
    def get_historical_data(self, symbol, start_date, end_date, interval='1d'):
        """
        取得歷史資料
        :param symbol: 股票代號
        :param start_date: 開始日期
        :param end_date: 結束日期
        :param interval: 資料頻率，如 '1d', '1h'
        :return: DataFrame 包含 OHLCV (開高低收量)
        """
        pass
    
    @abstractmethod
    def get_realtime_data(self, symbols):
        """
        取得即時資料
        :param symbols: 股票代號列表
        :return: Dict 包含即時報價資訊
        """
        pass
    
    @abstractmethod
    def get_fundamental_data(self, symbol):
        """
        取得基本面資料
        :param symbol: 股票代號
        :return: Dict 包含基本面資訊
        """
        pass
