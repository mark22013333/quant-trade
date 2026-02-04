"""
波段交易策略：結合多種技術指標，適合捕捉股價波段漲跌
"""
import pandas as pd
import numpy as np
from utils.signals import add_signal_from_position
from ..base_strategy import BaseStrategy

class SwingTradingStrategy(BaseStrategy):
    """
    波段交易策略
    
    特點：
    - 同時使用多種指標進行多重確認
    - 重視價格波動區間和交易量確認
    - 提供靈活的參數調整
    
    參數說明：
    - rsi_period: RSI 計算週期
    - rsi_overbought: RSI 超買閾值
    - rsi_oversold: RSI 超賣閾值
    - macd_fast: MACD 快線期數
    - macd_slow: MACD 慢線期數
    - macd_signal: MACD 訊號線期數
    - bb_period: 布林通道計算週期
    - bb_std: 布林通道標準差倍數
    - volume_factor: 交易量增幅因子
    - swing_lookback: 計算波幅的回顧期數
    """
    
    def __init__(self, parameters=None):
        default_params = {
            'rsi_period': 14,
            'rsi_overbought': 70,
            'rsi_oversold': 30,
            'macd_fast': 12,
            'macd_slow': 26,
            'macd_signal': 9,
            'bb_period': 20,
            'bb_std': 2.0,
            'volume_factor': 1.5,  # 交易量增加比例
            'swing_lookback': 10,  # 計算波幅的回顧期數
            'profit_target': 0.05,  # 目標獲利比例
            'stop_loss': 0.03      # 停損比例
        }
        
        if parameters:
            default_params.update(parameters)
            
        super().__init__(default_params)
        
    def generate_signals(self, data):
        """
        產生波段交易訊號
        
        策略邏輯：
        1. 買入條件：
           - RSI 從超賣區向上突破
           - MACD 柱狀向上且 MACD 由負轉正
           - 價格位於布林通道下軌附近
           - 交易量增加
           
        2. 賣出條件：
           - RSI 從超買區向下突破
           - MACD 柱狀向下且 MACD 由正轉負
           - 價格位於布林通道上軌附近
           - 達到目標獲利或停損
        """
        df = data.copy()
        
        # 確保資料包含必要欄位
        required_columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"資料必須包含 '{col}' 欄位")
                
        # 1. 計算 RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=self.parameters['rsi_period']).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=self.parameters['rsi_period']).mean()
        
        # 避免除以零
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            
        # 填充 NaN 值 (避免 SettingWithCopyWarning)
        df = df.copy()
        df['RSI'] = df['RSI'].fillna(50)
        
        # RSI 向上和向下穿越的信號
        df['RSI_Up_Cross'] = (df['RSI'] > self.parameters['rsi_oversold']) & (df['RSI'].shift(1) <= self.parameters['rsi_oversold'])
        df['RSI_Down_Cross'] = (df['RSI'] < self.parameters['rsi_overbought']) & (df['RSI'].shift(1) >= self.parameters['rsi_overbought'])
        
        # 2. 計算 MACD
        fast_ema = df['Close'].ewm(span=self.parameters['macd_fast'], adjust=False).mean()
        slow_ema = df['Close'].ewm(span=self.parameters['macd_slow'], adjust=False).mean()
        df['MACD'] = fast_ema - slow_ema
        df['MACD_Signal'] = df['MACD'].ewm(span=self.parameters['macd_signal'], adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
        
        # MACD 相關訊號
        df['MACD_Up_Cross'] = (df['MACD'] > df['MACD_Signal']) & (df['MACD'].shift(1) <= df['MACD_Signal'].shift(1))
        df['MACD_Down_Cross'] = (df['MACD'] < df['MACD_Signal']) & (df['MACD'].shift(1) >= df['MACD_Signal'].shift(1))
        df['MACD_Positive'] = df['MACD'] > 0
        
        # 3. 計算布林通道
        df['BB_Mid'] = df['Close'].rolling(window=self.parameters['bb_period']).mean()
        df['BB_Std'] = df['Close'].rolling(window=self.parameters['bb_period']).std()
        df['BB_Upper'] = df['BB_Mid'] + (df['BB_Std'] * self.parameters['bb_std'])
        df['BB_Lower'] = df['BB_Mid'] - (df['BB_Std'] * self.parameters['bb_std'])
        
        # 布林通道位置比例 (0=下軌，0.5=中軌，1=上軌)
        # 修正: 確保只有在 BB_Upper 與 BB_Lower 有差異時才計算位置
        bb_range = df['BB_Upper'] - df['BB_Lower']
        df['BB_Position'] = np.where(
            bb_range > 0,
            (df['Close'] - df['BB_Lower']) / bb_range,
            0.5  # 當上下軌重合時，視為中間位置
        )
        
        # 4. 計算交易量特徵
        df['Volume_Ratio'] = df['Volume'] / df['Volume'].rolling(window=20).mean()
        df['Volume_Increase'] = df['Volume_Ratio'] > self.parameters['volume_factor']
        
        # 5. 計算漲跌幅和波動率
        df['Daily_Return'] = df['Close'].pct_change()
        df['Volatility'] = df['Daily_Return'].rolling(window=20).std()
        
        # 6. 波段特性分析
        lookback = self.parameters['swing_lookback']
        
        # 計算波段高低點
        df['Local_High'] = df['Close'].rolling(window=lookback).max()
        df['Local_Low'] = df['Close'].rolling(window=lookback).min()
        df['Swing_Range'] = df['Local_High'] - df['Local_Low']
        df['Swing_Percent'] = np.where(
            df['Local_Low'] > 0,
            df['Swing_Range'] / df['Local_Low'],
            0
        )
        
        # 7. 產生交易訊號
        df['position'] = 0
        df['entry_price'] = np.nan
        
        for i in range(max(self.parameters['rsi_period'], self.parameters['macd_slow'], self.parameters['bb_period']) + lookback, len(df)):
            position = df['position'].iloc[i-1]  # 維持前一個部位
            
            # 檢查是否已持倉
            if position == 0:  # 尚未持倉，檢查買入訊號
                # 買入訊號 - 多重條件確認
                rsi_buy = df['RSI_Up_Cross'].iloc[i]
                macd_buy = df['MACD_Up_Cross'].iloc[i] or (df['MACD_Hist'].iloc[i] > 0 and df['MACD_Hist'].iloc[i-1] < 0)
                bb_buy = df['BB_Position'].iloc[i] < 0.3  # 接近布林下軌
                volume_buy = df['Volume_Increase'].iloc[i]
                
                # 綜合判斷買入
                if (rsi_buy or macd_buy) and bb_buy and volume_buy:
                    position = 1  # 買入
                    df.loc[df.index[i], 'entry_price'] = df['Close'].iloc[i]
                    
            elif position == 1:  # 已持倉，檢查賣出訊號
                entry_price = df['entry_price'].iloc[i-1]
                current_return = (df['Close'].iloc[i] / entry_price - 1)
                
                # 檢查止盈止損
                hit_profit = current_return >= self.parameters['profit_target']
                hit_stop_loss = current_return <= -self.parameters['stop_loss']
                
                # 賣出訊號 - 多重條件確認
                rsi_sell = df['RSI_Down_Cross'].iloc[i]
                macd_sell = df['MACD_Down_Cross'].iloc[i] or (df['MACD_Hist'].iloc[i] < 0 and df['MACD_Hist'].iloc[i-1] > 0)
                bb_sell = df['BB_Position'].iloc[i] > 0.7  # 接近布林上軌
                
                # 綜合判斷賣出
                if hit_profit or hit_stop_loss or (rsi_sell and macd_sell) or bb_sell:
                    position = 0  # 賣出
                    df.loc[df.index[i], 'entry_price'] = np.nan
            
            df.loc[df.index[i], 'position'] = position
                
            if position == 1 and pd.isna(df.loc[df.index[i], 'entry_price']):
                df.loc[df.index[i], 'entry_price'] = entry_price
            
        df = add_signal_from_position(df)
        return df
    
    def analyze_stock_suitability(self, data, threshold=0.05):
        """
        分析股票是否適合波段操作
        
        評估指標：
        1. 波動率：足夠的波動才有波段操作空間
        2. 交易量：有一定的交易活躍度
        3. 趨勢明確度：能形成明確的上漲下跌趨勢
        4. 波段表現：歷史波段操作的回測績效
        
        :param data: 股票歷史資料
        :param threshold: 最小獲利閾值
        :return: 評估結果字典
        """
        # 計算技術指標
        try:
            signals = self.generate_signals(data)
            
            # 1. 評估波動率
            avg_volatility = signals['Volatility'].mean()
            avg_swing_percent = signals['Swing_Percent'].mean() 
            
            # 2. 評估交易量
            avg_volume_ratio = signals['Volume_Ratio'].mean()
            
            # 3. 計算回測績效
            # 重要修正：確保位置和收益的維度匹配
            positions = signals['position'].values
            returns = signals['Daily_Return'].values
            
            # 策略收益 - 確保維度匹配
            if len(positions) != len(returns):
                # 如果維度不匹配，取兩者共同的長度
                min_length = min(len(positions), len(returns))
                positions = positions[:min_length]
                returns = returns[:min_length]
            
            # 使用 shift 操作的替代方案
            strategy_returns = []
            for i in range(1, len(positions)):
                strategy_returns.append(positions[i-1] * returns[i])
                
            strategy_returns = np.array(strategy_returns)
            
            # 移除 NaN
            strategy_returns = strategy_returns[~np.isnan(strategy_returns)]
            
            if len(strategy_returns) == 0:
                return {
                    'suitable': False,
                    'reason': '資料不足',
                    'metrics': {}
                }
                
            # 計算指標
            total_return = np.prod(1 + strategy_returns) - 1
            sharpe = (strategy_returns.mean() / strategy_returns.std()) * np.sqrt(252) if strategy_returns.std() > 0 else 0
            
            # 計算勝率
            trade_returns = []
            in_position = False
            entry_price = 0
            
            for i, row in signals.iterrows():
                if row['position'] == 1 and not in_position:  # 進場
                    in_position = True
                    entry_price = row['Close']
                elif row['position'] == 0 and in_position:  # 出場
                    exit_price = row['Close']
                    trade_return = exit_price / entry_price - 1
                    trade_returns.append(trade_return)
                    in_position = False
            
            # 交易次數和勝率
            num_trades = len(trade_returns)
            if num_trades > 0:
                win_rate = len([r for r in trade_returns if r > 0]) / num_trades
            else:
                win_rate = 0
            
            # 判斷是否適合波段操作
            suitable = (
                avg_volatility > 0.01 and       # 有一定程度的波動性
                avg_volume_ratio > 0.8 and      # 交易活躍度足夠
                avg_swing_percent > 0.03 and    # 有足夠的波段空間
                total_return > threshold and    # 回測有正收益
                sharpe > 1.0 and                # 風險調整後收益
                (num_trades >= 5 or num_trades == 0)  # 有足夠的交易次數或特別穩定
            )
            
            # 綜合評分 (1-10分)
            score = 5  # 基礎分數
            score += min(2, avg_volatility * 100)  # 波動率加分 (最多2分)
            score += min(1, avg_volume_ratio - 0.7)  # 交易量加分 (最多1分)
            score += min(2, total_return * 10)  # 收益加分 (最多2分)
            score = max(1, min(10, score))  # 限制在1-10之間
            
            return {
                'suitable': suitable,
                'score': score,
                'metrics': {
                    'avg_volatility': avg_volatility,
                    'avg_swing_percent': avg_swing_percent,
                    'avg_volume_ratio': avg_volume_ratio,
                    'total_return': total_return,
                    'sharpe_ratio': sharpe,
                    'num_trades': num_trades,
                    'win_rate': win_rate
                }
            }
        except Exception as e:
            # 捕捉並返回錯誤
            print(f"分析適合度時發生錯誤: {str(e)}")
            return {
                'suitable': False,
                'reason': f'分析過程中發生錯誤: {str(e)}',
                'metrics': {}
            }
