"""
簡化版波段交易策略：結合基本技術指標，避免維度不匹配問題
"""
import pandas as pd
import numpy as np
from utils.signals import add_signal_from_position
from ..base_strategy import BaseStrategy

class SimplifiedSwingStrategy(BaseStrategy):
    """
    簡化版波段交易策略
    
    專注於基本指標組合，確保回測計算正確
    """
    
    def __init__(self, parameters=None):
        default_params = {
            'rsi_period': 14,
            'rsi_overbought': 70,
            'rsi_oversold': 30,
            'bb_period': 20,
            'bb_std': 2.0,
            'profit_target': 0.05,
            'stop_loss': 0.03
        }
        
        if parameters:
            default_params.update(parameters)
            
        super().__init__(default_params)
        
    def generate_signals(self, data):
        """
        產生波段交易訊號
        
        簡化版策略使用 RSI 和布林通道
        """
        if data is None or data.empty:
            return pd.DataFrame()
            
        df = data.copy()
        
        # 確保資料包含必要欄位
        required_columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"資料必須包含 '{col}' 欄位")
        
        # 計算 RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=self.parameters['rsi_period']).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=self.parameters['rsi_period']).mean()
        
        # 避免除以零
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
        
        # 填充 NaN 值
        df['RSI'] = df['RSI'].fillna(50)
        
        # 計算布林通道
        df['BB_Mid'] = df['Close'].rolling(window=self.parameters['bb_period']).mean()
        df['BB_Std'] = df['Close'].rolling(window=self.parameters['bb_period']).std()
        df['BB_Upper'] = df['BB_Mid'] + (df['BB_Std'] * self.parameters['bb_std'])
        df['BB_Lower'] = df['BB_Mid'] - (df['BB_Std'] * self.parameters['bb_std'])
        
        # 計算日回報率
        df['Daily_Return'] = df['Close'].pct_change()
        
        # 產生交易訊號
        df['position'] = 0  # 初始化倉位
        df['entry_price'] = np.nan
        
        # 確保有足夠資料進行計算
        min_periods = max(self.parameters['rsi_period'], self.parameters['bb_period']) + 5
        
        if len(df) <= min_periods:
            return df
        
        # 使用單一迴圈處理訊號生成
        for i in range(min_periods, len(df)):
            # 買入條件：RSI 低於超賣且價格位於布林下軌附近
            rsi_buy = df['RSI'].iloc[i] < self.parameters['rsi_oversold']
            bb_buy = df['Close'].iloc[i] < df['BB_Lower'].iloc[i] * 1.01
            
            # 賣出條件：RSI 高於超買且價格位於布林上軌附近
            rsi_sell = df['RSI'].iloc[i] > self.parameters['rsi_overbought']
            bb_sell = df['Close'].iloc[i] > df['BB_Upper'].iloc[i] * 0.99
            
            # 獲取前一天倉位
            prev_position = df['position'].iloc[i-1]
            
            # 維持前一天倉位
            position = prev_position
            
            if prev_position == 0 and rsi_buy and bb_buy:  # 買入信號
                position = 1
                df.loc[df.index[i], 'entry_price'] = df['Close'].iloc[i]
                
            elif prev_position == 1:  # 檢查賣出信號
                entry_price = df['entry_price'].iloc[i-1]
                if not np.isnan(entry_price):
                    # 計算當前報酬率
                    current_return = df['Close'].iloc[i] / entry_price - 1
                    
                    # 止盈止損或技術指標賣出信號
                    if (current_return >= self.parameters['profit_target'] or
                        current_return <= -self.parameters['stop_loss'] or
                        (rsi_sell and bb_sell)):
                        position = 0
            
            # 更新當前倉位
            df.loc[df.index[i], 'position'] = position
            
            # 如果有持倉，保持進場價格
            if position == 1 and pd.isna(df.loc[df.index[i], 'entry_price']):
                df.loc[df.index[i], 'entry_price'] = df['entry_price'].iloc[i-1]
            
        df = add_signal_from_position(df)
        return df

    def analyze_stock_suitability(self, data, threshold=0.05):
        """
        分析股票是否適合波段操作
        """
        try:
            if data is None or data.empty or len(data) < 100:
                return {
                    'suitable': False,
                    'reason': '資料不足',
                    'score': 0,
                    'metrics': {}
                }
                
            # 生成訊號
            signals = self.generate_signals(data)
            
            if len(signals) < 100:  # 確保有足夠的資料進行分析
                return {
                    'suitable': False,
                    'reason': '訊號資料不足',
                    'score': 0,
                    'metrics': {}
                }
            
            # 計算策略收益
            positions = np.array(signals['position'])
            returns = np.array(signals['Daily_Return'].fillna(0))
            
            # 確保陣列維度相同
            min_len = min(len(positions), len(returns))
            positions = positions[:min_len]
            returns = returns[:min_len]
            
            # 計算策略收益 (使用向量化操作避免迴圈)
            strategy_returns = np.zeros_like(returns)
            strategy_returns[1:] = positions[:-1] * returns[1:]  # 前一天的倉位 * 今天的報酬
            
            # 過濾有效資料
            valid_returns = strategy_returns[~np.isnan(strategy_returns) & ~np.isinf(strategy_returns)]
            
            # 策略指標計算
            if len(valid_returns) < 10:
                return {
                    'suitable': False,
                    'reason': '有效交易資料不足',
                    'score': 0,
                    'metrics': {}
                }
            
            # 計算關鍵指標
            total_return = np.prod(1 + valid_returns) - 1
            volatility = np.std(valid_returns) * np.sqrt(252)  # 年化波動率
            sharpe = np.mean(valid_returns) * 252 / volatility if volatility > 0 else 0  # 年化夏普比率
            
            # 交易次數和勝率計算
            transactions = []
            buy_price = 0
            in_position = False
            
            for i in range(len(signals)):
                if signals['position'].iloc[i] == 1 and not in_position:
                    buy_price = signals['Close'].iloc[i]
                    in_position = True
                elif signals['position'].iloc[i] == 0 and in_position:
                    sell_price = signals['Close'].iloc[i]
                    profit_pct = (sell_price / buy_price) - 1
                    transactions.append(profit_pct)
                    in_position = False
            
            num_trades = len(transactions)
            win_rate = sum(1 for r in transactions if r > 0) / max(1, num_trades)
            
            # 波動特性
            close_prices = signals['Close'].values
            daily_returns = np.diff(close_prices) / close_prices[:-1]
            volatility = np.std(daily_returns)
            
            # 價格與均線關係
            price_vs_ma = np.mean(close_prices[-20:]) / np.mean(close_prices[-50:]) - 1
            
            # 交易活躍度
            traded_days = np.sum(positions > 0) / len(positions)
            
            # 綜合評分計算
            score = 5.0  # 基礎分數
            
            # 調整分數
            score += min(2.0, total_return * 10)  # 根據回報調整 (最多加2分)
            score += min(1.0, sharpe / 2)  # 根據夏普比率調整 (最多加1分)
            score += min(1.0, win_rate)  # 根據勝率調整 (最多加1分)
            score += min(1.0, min(num_trades, 10) / 10)  # 根據交易次數調整 (最多加1分)
            score -= max(0, min(2.0, volatility * 20))  # 根據波動性扣分 (最多扣2分)
            
            # 限制在1-10分之間
            score = max(1, min(10, score))
            
            # 判斷是否適合波段交易
            suitable = (
                total_return > threshold and
                sharpe > 0.8 and
                win_rate > 0.4 and
                num_trades >= 3
            )
            
            return {
                'suitable': suitable,
                'score': score,
                'metrics': {
                    'total_return': total_return,
                    'sharpe_ratio': sharpe,
                    'volatility': volatility,
                    'num_trades': num_trades,
                    'win_rate': win_rate,
                    'avg_swing_percent': price_vs_ma,
                    'avg_volume_ratio': traded_days
                }
            }
            
        except Exception as e:
            print(f"分析適合度時發生錯誤: {str(e)}")
            return {
                'suitable': False,
                'reason': f'分析過程中發生錯誤: {str(e)}',
                'score': 0,
                'metrics': {}
            }
