import pandas as pd
from utils.signals import add_signal_from_position
from .strategy_interface import StrategyInterface

class MACrossStrategy(StrategyInterface):
    """
    簡單均線交叉策略：短天期均線上穿長天期均線時買進，下穿時賣出。
    """
    def __init__(self, short_window=5, long_window=20):
        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['short_ma'] = df['Close'].rolling(self.short_window).mean()
        df['long_ma'] = df['Close'].rolling(self.long_window).mean()
        df['position'] = 0
        start_idx = max(self.short_window, self.long_window)
        if start_idx < len(df):
            df.loc[df.index[start_idx:], 'position'] = (
                (df['short_ma'][start_idx:] > df['long_ma'][start_idx:]).astype(int)
            )
        df = add_signal_from_position(df)
        return df

    def get_parameters(self) -> dict:
        """
        取得策略參數
        """
        return {
            'short_window': self.short_window,
            'long_window': self.long_window
        }

    def set_parameters(self, parameters: dict) -> None:
        """
        設定策略參數
        """
        if 'short_window' in parameters:
            self.short_window = parameters['short_window']
        if 'long_window' in parameters:
            self.long_window = parameters['long_window']
