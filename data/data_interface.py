from abc import ABC, abstractmethod
import pandas as pd
from typing import List, Optional, Union, Dict, Any

class DataSourceInterface(ABC):
    """
    資料來源介面抽象類別，所有資料來源都要繼承這個介面
    """
    
    @abstractmethod
    def get_historical_data(
        self, 
        symbol: str, 
        start_date: str, 
        end_date: Optional[str] = None, 
        interval: str = "1d"
    ) -> pd.DataFrame:
        """
        取得歷史資料
        
        :param symbol: 股票代號
        :param start_date: 開始日期 (YYYY-MM-DD)
        :param end_date: 結束日期 (YYYY-MM-DD)，預設為今天
        :param interval: 資料間隔，例如 "1d" 表示日資料，"1h" 表示小時資料
        :return: 包含歷史資料的 DataFrame，至少包含 Open, High, Low, Close, Volume 欄位
        """
        pass
    
    @abstractmethod
    def get_latest_price(self, symbol: str) -> float:
        """
        取得最新價格
        
        :param symbol: 股票代號
        :return: 最新價格
        """
        pass
    
    @abstractmethod
    def search_symbols(self, keyword: str) -> List[Dict[str, Any]]:
        """
        搜尋股票代號
        
        :param keyword: 關鍵字
        :return: 符合關鍵字的股票資訊列表
        """
        pass


class DataManagerInterface(ABC):
    """
    資料管理介面抽象類別，負責資料的儲存、讀取和處理
    """
    
    @abstractmethod
    def save_data(self, df: pd.DataFrame, symbol: str, interval: str = "1d") -> None:
        """
        儲存資料
        
        :param df: 要儲存的資料
        :param symbol: 股票代號
        :param interval: 資料間隔
        """
        pass
    
    @abstractmethod
    def load_data(self, symbol: str, interval: str = "1d") -> pd.DataFrame:
        """
        讀取資料
        
        :param symbol: 股票代號
        :param interval: 資料間隔
        :return: 讀取的資料
        """
        pass
    
    @abstractmethod
    def update_data(self, symbol: str, interval: str = "1d") -> pd.DataFrame:
        """
        更新資料（從上次儲存的時間點到現在）
        
        :param symbol: 股票代號
        :param interval: 資料間隔
        :return: 更新後的完整資料
        """
        pass