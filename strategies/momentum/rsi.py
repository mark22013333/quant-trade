"""
RSI 相對強弱指標交易策略
"""
import pandas as pd
import numpy as np
from utils.signals import add_signal_from_position
from ..base_strategy import BaseStrategy

class RSIStrategy(BaseStrategy):
    """
    RSI 相對強弱指標策略
    
    參數說明：
    - period: RSI 計算週期，預設 14
    - overbought: 超買閾值，預設 70
    - oversold: 超賣閾值，預設 30
    - use_divergence: 是否使用背離確認，預設 False
    """
    def __init__(self, parameters=None):
        default_params = {
            'period': 14,
            'overbought': 70,
            'oversold': 30,
            'use_divergence': False
        }
        # 更新預設參數
        if parameters:
            default_params.update(parameters)
            
        super().__init__(default_params)
        
    def generate_signals(self, data):
        """
        產生交易訊號
        
        策略邏輯：
        - RSI 低於超賣閾值時，產生買入訊號
        - RSI 高於超買閾值時，產生賣出訊號
        - 若啟用背離確認，則同時檢查價格與 RSI 是否出現背離
        """
        df = data.copy()
        
        # 確保 data 包含 Close 欄位
        if 'Close' not in df.columns:
            raise ValueError("資料必須包含 'Close' 欄位")
        
        period = self.parameters['period']
        overbought = self.parameters['overbought']
        oversold = self.parameters['oversold']
        use_divergence = self.parameters['use_divergence']
        
        # 計算 RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
        
        # 避免除以零
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            
        # 填充可能的 NaN 值
        df['RSI'] = df['RSI'].fillna(50)
        
        # 初始化部位欄位
        df['position'] = 0
        
        # 交易訊號邏輯
        for i in range(period + 10, len(df)):  # 額外的 10 是為了計算背離
            position = df['position'].iloc[i-1]  # 維持前一個部位
            
            # 檢查 RSI 是否處於超買/超賣區域
            rsi_oversold = df['RSI'].iloc[i] < oversold
            rsi_overbought = df['RSI'].iloc[i] > overbought
            
            # 如果啟用背離確認
            if use_divergence:
                # 檢查前 10 天的資料
                lookback = 10
                # 找出區間內的價格高點和低點
                price_high = df['Close'].iloc[i-lookback:i+1].max()
                price_low = df['Close'].iloc[i-lookback:i+1].min()
                price_high_idx = df['Close'].iloc[i-lookback:i+1].idxmax()
                price_low_idx = df['Close'].iloc[i-lookback:i+1].idxmin()
                
                # 相應的 RSI 值
                rsi_at_price_high = df.loc[price_high_idx, 'RSI']
                rsi_at_price_low = df.loc[price_low_idx, 'RSI']
                
                # 尋找 RSI 的高點和低點
                rsi_high = df['RSI'].iloc[i-lookback:i+1].max()
                rsi_low = df['RSI'].iloc[i-lookback:i+1].min()
                rsi_high_idx = df['RSI'].iloc[i-lookback:i+1].idxmax()
                rsi_low_idx = df['RSI'].iloc[i-lookback:i+1].idxmin()
                
                # 判斷是否有背離
                bullish_divergence = (price_low_idx < rsi_low_idx) and (df['Close'].iloc[i] < price_low) and (df['RSI'].iloc[i] > rsi_low)
                bearish_divergence = (price_high_idx < rsi_high_idx) and (df['Close'].iloc[i] > price_high) and (df['RSI'].iloc[i] < rsi_high)
                
                # 交易邏輯：僅在出現背離時交易
                if rsi_oversold and bullish_divergence:
                    position = 1  # 買入
                elif rsi_overbought and bearish_divergence:
                    position = -1  # 賣出
            else:
                # 不考慮背離，直接根據 RSI 超買超賣交易
                if rsi_oversold and position <= 0:
                    position = 1  # 買入
                elif rsi_overbought and position >= 0:
                    position = -1  # 賣出
                    
                # 加入回歸區間的平倉邏輯 (當 RSI 回到 40-60 之間)
                if position != 0 and 40 <= df['RSI'].iloc[i] <= 60:
                    position = 0  # 平倉
                    
            df.loc[df.index[i], 'position'] = position
                
        df = add_signal_from_position(df)
        return df
