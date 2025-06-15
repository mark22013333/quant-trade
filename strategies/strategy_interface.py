from abc import ABC, abstractmethod
import pandas as pd

class StrategyInterface(ABC):
    """
    策略介面抽象類別，所有交易策略都要繼承這個介面
    """
    
    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        根據歷史資料生成交易訊號
        
        :param df: 歷史價格資料，包含 Open, High, Low, Close, Volume 等欄位
        :return: 添加了交易訊號的 DataFrame，必須包含 'signal' 和 'position' 欄位
                 'signal': 0 表示不持有，1 表示持有
                 'position': 1 表示買入，-1 表示賣出，0 表示不動作
        """
        pass
    
    @abstractmethod
    def get_parameters(self) -> dict:
        """
        取得策略參數
        
        :return: 包含策略參數的字典
        """
        pass
    
    @abstractmethod
    def set_parameters(self, parameters: dict) -> None:
        """
        設定策略參數
        
        :param parameters: 包含策略參數的字典
        """
        pass