"""
特徵工程模組：為股票資料建立進階特徵
"""
import pandas as pd
import numpy as np
from talib import abstract

class FeatureEngineering:
    """
    特徵工程工具：建立、計算和管理股票資料的技術指標和其他特徵
    """
    @staticmethod
    def add_price_features(df):
        """
        新增價格相關特徵
        :param df: 包含 OHLCV 資料的 DataFrame
        """
        # 確保欄位名稱符合 TA-Lib 需求
        df_copy = df.copy()
        
        # 價格變動率 (%)
        df_copy['Returns'] = df_copy['Close'].pct_change() * 100
        
        # 波動率 (使用 N 日標準差)
        for window in [5, 10, 20]:
            df_copy[f'Volatility_{window}d'] = df_copy['Returns'].rolling(window=window).std()
            
        # 價格區間 (%)
        df_copy['PriceRange'] = (df_copy['High'] - df_copy['Low']) / df_copy['Close'] * 100
        
        # 成交量變動率 (%)
        df_copy['VolumeChange'] = df_copy['Volume'].pct_change() * 100
        
        # 成交量加權平均價格 (VWAP)
        df_copy['VWAP'] = (df_copy['Close'] * df_copy['Volume']).rolling(window=5).sum() / df_copy['Volume'].rolling(window=5).sum()
        
        return df_copy
    
    @staticmethod
    def add_trend_features(df):
        """
        新增趨勢相關特徵
        :param df: 包含 OHLCV 資料的 DataFrame
        """
        df_copy = df.copy()
        
        # 移動平均線
        for window in [5, 10, 20, 60]:
            df_copy[f'SMA_{window}'] = df_copy['Close'].rolling(window=window).mean()
            
        # 指數移動平均線
        for window in [5, 10, 20, 60]:
            df_copy[f'EMA_{window}'] = df_copy['Close'].ewm(span=window, adjust=False).mean()
        
        # 價格相對於移動平均線的位置 (%)
        for window in [10, 20, 60]:
            df_copy[f'CloseToSMA_{window}_Ratio'] = df_copy['Close'] / df_copy[f'SMA_{window}'] - 1
        
        # 短期與長期移動平均線差距 (%)
        df_copy['SMA_5_20_Spread'] = (df_copy['SMA_5'] / df_copy['SMA_20'] - 1) * 100
        df_copy['SMA_10_60_Spread'] = (df_copy['SMA_10'] / df_copy['SMA_60'] - 1) * 100
        
        return df_copy
    
    @staticmethod
    def add_momentum_features(df):
        """
        新增動能相關特徵
        :param df: 包含 OHLCV 資料的 DataFrame
        """
        df_copy = df.copy()
        
        # 標準化的名稱
        ohlc_dict = {
            'open': 'Open', 
            'high': 'High', 
            'low': 'Low', 
            'close': 'Close', 
            'volume': 'Volume'
        }
        
        # 計算 RSI
        for period in [6, 14]:
            rsi = abstract.RSI(df_copy, timeperiod=period, price='Close')
            df_copy[f'RSI_{period}'] = rsi
            
            # RSI 變化率
            df_copy[f'RSI_{period}_Change'] = rsi.diff()
        
        # 計算 MACD
        macd_result = abstract.MACD(
            df_copy, 
            fastperiod=12, 
            slowperiod=26, 
            signalperiod=9
        )
        df_copy['MACD'] = macd_result['macd']
        df_copy['MACD_Signal'] = macd_result['macdsignal']
        df_copy['MACD_Hist'] = macd_result['macdhist']
        
        # 計算隨機指標 Stochastic
        stoch_result = abstract.STOCH(
            df_copy,
            fastk_period=14,
            slowk_period=3,
            slowk_matype=0,
            slowd_period=3,
            slowd_matype=0
        )
        df_copy['STOCH_K'] = stoch_result['slowk']
        df_copy['STOCH_D'] = stoch_result['slowd']
        
        # 計算威廉指標 Williams %R
        df_copy['WILLR_14'] = abstract.WILLR(
            df_copy, 
            timeperiod=14
        )
        
        # 計算動量指標 Momentum
        df_copy['MOM_10'] = abstract.MOM(
            df_copy, 
            timeperiod=10, 
            price='Close'
        )
        
        # 計算變動率指標 ROC (Rate of Change)
        df_copy['ROC_10'] = abstract.ROC(
            df_copy, 
            timeperiod=10, 
            price='Close'
        )
        
        return df_copy
    
    @staticmethod
    def add_volatility_features(df):
        """
        新增波動與壓力相關特徵
        :param df: 包含 OHLCV 資料的 DataFrame
        """
        df_copy = df.copy()
        
        # 計算布林通道
        for period in [10, 20]:
            upper, middle, lower = abstract.BBANDS(
                df_copy, 
                timeperiod=period,
                nbdevup=2,
                nbdevdn=2,
                matype=0
            )
            df_copy[f'BB_Upper_{period}'] = upper
            df_copy[f'BB_Middle_{period}'] = middle
            df_copy[f'BB_Lower_{period}'] = lower
            
            # 計算布林通道寬度 (%)
            df_copy[f'BB_Width_{period}'] = (upper - lower) / middle * 100
            
            # 價格相對布林通道位置 (-1 到 1，0 為中軸)
            df_copy[f'BB_Position_{period}'] = (df_copy['Close'] - middle) / (upper - middle) * 2 - 1
        
        # 計算平均真實範圍 ATR
        for period in [7, 14]:
            df_copy[f'ATR_{period}'] = abstract.ATR(
                df_copy, 
                timeperiod=period
            )
            
            # ATR 佔收盤價的百分比
            df_copy[f'ATR_Percent_{period}'] = df_copy[f'ATR_{period}'] / df_copy['Close'] * 100
        
        # 計算震盪指標 CCI (Commodity Channel Index)
        df_copy['CCI_14'] = abstract.CCI(
            df_copy, 
            timeperiod=14
        )
        
        return df_copy
    
    @staticmethod
    def add_volume_features(df):
        """
        新增成交量相關特徵
        :param df: 包含 OHLCV 資料的 DataFrame
        """
        df_copy = df.copy()
        
        # 計算成交量移動平均
        for window in [5, 10, 20]:
            df_copy[f'Volume_SMA_{window}'] = df_copy['Volume'].rolling(window=window).mean()
            
            # 成交量相對於移動平均的比例
            df_copy[f'Volume_SMA_Ratio_{window}'] = df_copy['Volume'] / df_copy[f'Volume_SMA_{window}']
        
        # 計算成交量與價格變動的相關性
        for window in [10, 20]:
            # 計算價格變動率
            price_change = df_copy['Close'].pct_change()
            
            # 計算成交量變動率
            volume_change = df_copy['Volume'].pct_change()
            
            # 計算相關係數
            df_copy[f'Price_Volume_Corr_{window}'] = pd.Series(
                [price_change.iloc[i-window+1:i+1].corr(volume_change.iloc[i-window+1:i+1]) 
                 if i >= window else np.nan 
                 for i in range(len(df_copy))], 
                index=df_copy.index
            )
        
        # 計算成交量力道 (Volume Force) = 收盤價變動 * 成交量
        df_copy['Volume_Force'] = df_copy['Close'].pct_change() * df_copy['Volume']
        
        # 計算成交量加權移動平均線 (VWMA)
        for window in [10, 20]:
            df_copy[f'VWMA_{window}'] = (df_copy['Close'] * df_copy['Volume']).rolling(window=window).sum() / df_copy['Volume'].rolling(window=window).sum()
        
        # 計算能量潮指標 OBV (On Balance Volume)
        obv = [0]
        for i in range(1, len(df_copy)):
            if df_copy['Close'].iloc[i] > df_copy['Close'].iloc[i-1]:
                obv.append(obv[-1] + df_copy['Volume'].iloc[i])
            elif df_copy['Close'].iloc[i] < df_copy['Close'].iloc[i-1]:
                obv.append(obv[-1] - df_copy['Volume'].iloc[i])
            else:
                obv.append(obv[-1])
        df_copy['OBV'] = obv
        
        return df_copy
    
    @staticmethod
    def add_all_features(df):
        """
        新增所有特徵
        :param df: 包含 OHLCV 資料的 DataFrame
        :return: 包含所有特徵的 DataFrame
        """
        df_result = df.copy()
        df_result = FeatureEngineering.add_price_features(df_result)
        df_result = FeatureEngineering.add_trend_features(df_result)
        df_result = FeatureEngineering.add_momentum_features(df_result)
        df_result = FeatureEngineering.add_volatility_features(df_result)
        df_result = FeatureEngineering.add_volume_features(df_result)
        
        # 移除 NaN 值
        # df_result = df_result.dropna()
        
        return df_result
