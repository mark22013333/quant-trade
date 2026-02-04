"""
布林通道交易策略
"""
import pandas as pd
import numpy as np
from utils.signals import add_signal_from_position
from ..base_strategy import BaseStrategy

class BollingerBandsStrategy(BaseStrategy):
    """
    布林通道均值回歸策略
    
    參數說明：
    - window: 布林通道的移動平均窗口，預設 20
    - std_dev: 標準差倍數，預設 2
    - exit_middle: 是否在價格回到中軌時平倉，預設 True
    """
    def __init__(self, parameters=None):
        default_params = {
            'window': 20,
            'std_dev': 2.0,
            'exit_middle': True
        }
        # 更新預設參數
        if parameters:
            default_params.update(parameters)
            
        super().__init__(default_params)
        
    def generate_signals(self, data):
        """
        產生交易訊號
        
        策略邏輯：
        - 價格突破上軌時，產生賣出訊號 (預期回歸平均)
        - 價格突破下軌時，產生買入訊號 (預期回歸平均)
        - 若 exit_middle=True，則在價格回到中軌時平倉
        """
        df = data.copy()
        
        # 確保 data 包含 Close 欄位
        if 'Close' not in df.columns:
            raise ValueError("資料必須包含 'Close' 欄位")
        
        window = self.parameters['window']
        std_dev = self.parameters['std_dev']
        exit_middle = self.parameters['exit_middle']
        
        # 計算布林通道
        df['Middle'] = df['Close'].rolling(window=window).mean()
        df['Std'] = df['Close'].rolling(window=window).std()
        df['Upper'] = df['Middle'] + (df['Std'] * std_dev)
        df['Lower'] = df['Middle'] - (df['Std'] * std_dev)
        
        # 初始化部位欄位
        df['position'] = 0
        
        # 是否持有部位的輔助指標
        position = 0
        
        # 交易訊號邏輯
        for i in range(window, len(df)):
            # 超買情況：價格突破上軌
            if df['Close'].iloc[i] > df['Upper'].iloc[i]:
                # 如果尚未有空頭部位，則建立
                if position >= 0:
                    position = -1
                    
            # 超賣情況：價格突破下軌
            elif df['Close'].iloc[i] < df['Lower'].iloc[i]:
                # 如果尚未有多頭部位，則建立
                if position <= 0:
                    position = 1
                    
            # 回歸平均：價格回到中軌附近
            elif exit_middle and abs(df['Close'].iloc[i] - df['Middle'].iloc[i]) < 0.5 * df['Std'].iloc[i]:
                # 如果有部位且啟用了中軌平倉，則平倉
                if position != 0:
                    position = 0
                    
            df.loc[df.index[i], 'position'] = position
        
        df = add_signal_from_position(df)
        return df
