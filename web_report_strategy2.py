"""
簡化版網頁報表策略 - 專注於產生可用於網頁報表的資料，避免複雜的維度不匹配問題
"""
import pandas as pd
import numpy as np
from data.market_data import MarketData
from data.providers.yfinance_provider import YFinanceProvider
import yfinance as yf
from tqdm import tqdm

class SimpleWebReportAnalyzer:
    """
    簡化的網頁報表分析器
    
    專注於生成基本分析資料，避免複雜的維度不匹配問題
    """
    
    def __init__(self):
        """初始化分析器"""
        self.provider = YFinanceProvider()
        self.market_data = MarketData(self.provider)
    
    def analyze_stock_list(self, stock_list, start_date, end_date):
        """
        分析股票列表，產生基本分析資料
        
        :param stock_list: 股票代號列表
        :param start_date: 開始日期
        :param end_date: 結束日期
        :return: 分析結果DataFrame
        """
        results = []
        
        for symbol in tqdm(stock_list, desc="分析股票"):
            try:
                # 載入股票資料
                data = self.market_data.load_data(symbol, start_date, end_date)
                
                if data.empty or len(data) < 60:
                    print(f"警告: {symbol} 無足夠資料進行分析")
                    continue
                    
                # 使用不會造成維度不匹配的安全方式計算指標
                df = data.copy()
                
                # 1. 波動率 (避免使用 np.diff)
                # 使用 pct_change 計算日報酬率
                df['DailyReturn'] = df['Close'].pct_change()
                # 移除 NaN 值
                returns = df['DailyReturn'].dropna().values
                # 基本波動率計算
                volatility = float(np.std(returns) * np.sqrt(252)) if len(returns) > 0 else 0.0
                
                # 2. 成交量變化 (確保索引有效)
                # 如果資料不足30天或90天，相應調整
                recent_vol = df['Volume'].tail(min(30, len(df)))
                past_vol = df['Volume'].iloc[max(0, len(df)-90):max(0, len(df)-30)]
                
                avg_volume = float(recent_vol.mean().iloc[0]) if not recent_vol.empty else 0.0
                past_avg_volume = float(past_vol.mean().iloc[0]) if not past_vol.empty else 1.0  # 避免除以0
                volume_ratio = avg_volume / past_avg_volume if past_avg_volume > 0 else 1.0
                
                # 3. 趨勢分析 (純量計算，避免維度問題)
                if len(df) >= 20:
                    current_price = float(df['Close'].iloc[-1].iloc[0])
                    price_20d_ago = float(df['Close'].iloc[-min(20, len(df))].iloc[0])
                    trend_20d = ((current_price / price_20d_ago) - 1) * 100
                else:
                    trend_20d = 0.0
                    
                if len(df) >= 60:
                    price_60d_ago = float(df['Close'].iloc[-min(60, len(df))].iloc[0])
                    trend_60d = ((current_price / price_60d_ago) - 1) * 100
                else:
                    trend_60d = 0.0
                
                # 4. 簡單回測 (安全實現)
                win_count = 0
                trade_returns = []
                total_trades = 0
                
                # 確保至少有足夠的資料進行回測
                if len(df) > 20:
                    prices = df['Close'].values
                    
                    # 按10天周期進行簡易回測，避免維度不匹配問題
                    for i in range(0, len(prices) - 10, 10):
                        if i+10 < len(prices):  # 確保索引有效
                            entry_price = float(prices[i])
                            exit_price = float(prices[i+10])
                            
                            # 計算單次交易報酬率
                            trade_return = (exit_price / entry_price - 1) * 100
                            trade_returns.append(trade_return)
                            
                            if trade_return > 0:
                                win_count += 1
                                
                            total_trades += 1
                
                # 計算勝率與平均回報
                win_rate = win_count / total_trades if total_trades > 0 else 0.0
                avg_return = sum(trade_returns) / len(trade_returns) if trade_returns else 0.0
                
                # 股票名稱
                try:
                    stock_info = yf.Ticker(symbol).info
                    name = stock_info.get('shortName', symbol)
                except:
                    name = symbol
                
                # 計算綜合評分 (簡單版)
                # - 波動率適中 (0.15-0.4之間最好)
                volatility_score = 5 if 0.15 <= volatility <= 0.4 else (
                    volatility * 10 if volatility < 0.15 else (0.4 / volatility) * 10
                )
                
                # - 成交量增加有利
                volume_score = volume_ratio * 2 if volume_ratio > 1.0 else volume_ratio
                
                # - 趨勢分數 (短期與長期趨勢方向一致且不過於劇烈為佳)
                trend_score = 3 if (trend_20d * trend_60d > 0) else 1
                trend_score += 2 if abs(trend_20d) < 20 else 0  # 波動不要太劇烈
                
                # - 回測勝率與報酬
                backtest_score = win_rate * 5 + avg_return * 0.5
                
                # 總分與適合度判斷
                total_score = volatility_score + volume_score + trend_score + backtest_score
                is_suitable = total_score > 10 and win_rate > 0.5
                
                # 彙總結果
                result = {
                    'symbol': symbol,
                    'name': name,
                    'suitable': bool(is_suitable),
                    'score': float(total_score),
                    'volatility': float(volatility),
                    'volume_ratio': float(volume_ratio),
                    'trend_20d': float(trend_20d),
                    'trend_60d': float(trend_60d),
                    'win_rate': float(win_rate),
                    'avg_return': float(avg_return),
                    'num_trades': int(total_trades),
                    'total_return': float(sum(trade_returns)) if trade_returns else 0.0
                }
                
                results.append(result)
                
            except Exception as e:
                print(f"分析 {symbol} 時發生錯誤: {e}")
                continue
        
        return pd.DataFrame(results) if results else pd.DataFrame()
    
    def analyze_industry_groups(self, industry_groups, start_date, end_date):
        """
        分析各產業群組的股票
        
        :param industry_groups: 產業股票分組字典，格式：{'產業名稱': [股票代號列表]}
        :param start_date: 開始日期
        :param end_date: 結束日期
        :return: 各產業分析結果字典
        """
        industry_results = {}
        
        for category, stocks in industry_groups.items():
            print(f"\n=== 分析{category} ===")
            results = self.analyze_stock_list(stocks, start_date, end_date)
            industry_results[category] = results
            
            # 顯示前三名適合波段操作的股票
            if not results.empty and len(results) >= 3:
                top_stocks = results.head(3)
                print(f"{category}中最適合波段操作的前三名股票:")
                for _, row in top_stocks.iterrows():
                    print(f"{row['symbol']} ({row['name']}): 評分 {row['score']:.1f}")
            elif not results.empty:
                top_stocks = results
                print(f"{category}中適合波段操作的股票:")
                for _, row in top_stocks.iterrows():
                    print(f"{row['symbol']} ({row['name']}): 評分 {row['score']:.1f}")
        
        return industry_results
