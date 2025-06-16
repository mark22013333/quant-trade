"""
MACD 指標交易策略
"""
import pandas as pd
import numpy as np
from ..base_strategy import BaseStrategy

class MACDStrategy(BaseStrategy):
    """
    MACD 交叉交易策略
    
    參數說明：
    - fast_period: 短週期 EMA 天數，預設 12
    - slow_period: 長週期 EMA 天數，預設 26
    - signal_period: 訊號線天數，預設 9
    """
    def __init__(self, parameters=None):
        default_params = {
            'fast_period': 12,
            'slow_period': 26,
            'signal_period': 9
        }
        # 更新預設參數
        if parameters:
            default_params.update(parameters)
            
        super().__init__(default_params)
        
    def generate_signals(self, data):
        """
        產生交易訊號
        
        策略邏輯：
        - MACD 線向上穿越訊號線時，產生買入訊號
        - MACD 線向下穿越訊號線時，產生賣出訊號
        - 其他時候，保持前一個部位
        """
        df = data.copy()
        
        # 確保 data 包含 Close 欄位
        if 'Close' not in df.columns:
            raise ValueError("資料必須包含 'Close' 欄位")
        
        fast = self.parameters['fast_period']
        slow = self.parameters['slow_period']
        signal = self.parameters['signal_period']
        
        # 計算 MACD
        df['EMA_fast'] = df['Close'].ewm(span=fast, adjust=False).mean()
        df['EMA_slow'] = df['Close'].ewm(span=slow, adjust=False).mean()
        df['MACD'] = df['EMA_fast'] - df['EMA_slow']
        df['Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
        df['Histogram'] = df['MACD'] - df['Signal']
        
        # 計算 MACD 和訊號線的交叉
        df['MACD_prev'] = df['MACD'].shift(1)
        df['Signal_prev'] = df['Signal'].shift(1)
        
        # 初始化部位欄位
        df['position'] = 0
        
        # 交易訊號邏輯
        for i in range(1, len(df)):
            # 前一日是下穿或上穿的交叉點，今日保持前一天的部位
            if df['position'].iloc[i-1] != 0:
                df.loc[df.index[i], 'position'] = df['position'].iloc[i-1]
            
            # MACD 向上穿越訊號線 (黃金交叉)
            elif (df['MACD_prev'].iloc[i] < df['Signal_prev'].iloc[i] and 
                  df['MACD'].iloc[i] > df['Signal'].iloc[i]):
                df.loc[df.index[i], 'position'] = 1  # 買入
                
            # MACD 向下穿越訊號線 (死亡交叉)
            elif (df['MACD_prev'].iloc[i] > df['Signal_prev'].iloc[i] and 
                  df['MACD'].iloc[i] < df['Signal'].iloc[i]):
                df.loc[df.index[i], 'position'] = -1  # 賣出
                
            # 沒有新交叉，維持前一個部位
            else:
                df.loc[df.index[i], 'position'] = df['position'].iloc[i-1]
        
        return df
