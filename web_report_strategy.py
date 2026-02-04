"""
網頁報表專用策略分析器：解決維度不匹配問題，專門用於產生網頁報表的分析結果
"""
import pandas as pd
import numpy as np
from strategies.momentum.simplified_swing import SimplifiedSwingStrategy
from data.market_data import MarketData
from data.providers.yfinance_provider import YFinanceProvider
import yfinance as yf
from tqdm import tqdm

class WebReportAnalyzer:
    """
    網頁報表分析器類別
    
    專門處理股票分析並產生適合網頁顯示的結果，
    同時避免維度不匹配問題
    """
    
    def __init__(self):
        """初始化分析器"""
        # 使用 SimplifiedSwingStrategy 策略，它已經處理了維度不匹配問題
        self.strategy = SimplifiedSwingStrategy()
        
        # 初始化資料提供者
        self.provider = YFinanceProvider()
        self.market_data = MarketData(self.provider)
        
    def analyze_stock_list(self, stock_list, start_date, end_date, params=None):
        """
        分析股票列表，產生適合網頁報表的結果
        
        :param stock_list: 股票代號列表
        :param start_date: 開始日期
        :param end_date: 結束日期
        :param params: 策略參數字典
        :return: 分析結果DataFrame
        """
        # 如果有提供參數，更新策略參數
        if params:
            self.strategy.parameters.update(params)
            
        results = []
        
        # 分析每個股票
        for symbol in tqdm(stock_list, desc="分析股票"):
            try:
                # 載入股票資料
                data = self.market_data.load_data(symbol, start_date, end_date)
                
                if data.empty or len(data) < 100:  # 確保有足夠的資料
                    print(f"警告: {symbol} 無足夠資料進行分析")
                    continue
                
                # 自行執行策略分析，避免維度不匹配問題
                # 計算技術指標
                df = data.copy().sort_index()
                
                # 計算 RSI
                delta = df['Close'].diff()
                gain = delta.where(delta > 0, 0)
                loss = -delta.where(delta < 0, 0)
                avg_gain = gain.rolling(window=14).mean()
                avg_loss = loss.rolling(window=14).mean()
                rs = avg_gain / avg_loss
                df['RSI'] = 100 - (100 / (1 + rs))
                
                # 計算 MACD
                ema12 = df['Close'].ewm(span=12, adjust=False).mean()
                ema26 = df['Close'].ewm(span=26, adjust=False).mean()
                df['MACD'] = ema12 - ema26
                df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
                
                # 計算布林帶 (修正維度問題)
                sma20 = df['Close'].rolling(window=20).mean()
                std20 = df['Close'].rolling(window=20).std()
                df['SMA20'] = sma20
                df['UpperBand'] = sma20 + (std20 * 2)
                df['LowerBand'] = sma20 - (std20 * 2)
                
                # 計算波動率 (15日平均)
                df['DailyReturn'] = df['Close'].pct_change()
                df['Volatility'] = df['DailyReturn'].rolling(window=15).std() * (252**0.5)  # 年化
                
                # 填充 NaN 值
                df = df.fillna(0)
                
                # 簡單的適合度評分 (使用多個指標)
                # 1. 波動率在適當範圍 (不會太低或太高)
                volatility_score = df['Volatility'].tail(30).mean()
                
                # 2. 計算平均成交量相對於過去的比率
                avg_volume = df['Volume'].tail(30).mean()
                past_avg_volume = df['Volume'].tail(90).head(60).mean()
                volume_ratio = avg_volume / past_avg_volume if past_avg_volume > 0 else 0
                
                # 3. 計算技術指標綜合分數
                # RSI 不會長時間處於極端區間
                rsi_extremes = (df['RSI'] > 70) | (df['RSI'] < 30)
                rsi_score = 1 - rsi_extremes.mean()  # 極端值比例越低越好
                
                # MACD 交叉頻率適中
                macd_cross = ((df['MACD'] > df['Signal']) & (df['MACD'].shift(1) < df['Signal'].shift(1))) | \
                            ((df['MACD'] < df['Signal']) & (df['MACD'].shift(1) > df['Signal'].shift(1)))
                macd_score = macd_cross.sum() / len(df) * 10  # 標準化
                if isinstance(macd_score, pd.Series):
                    macd_score = macd_score.mean()
                
                # 價格相對於布林帶的位置
                bb_den = (df['UpperBand'] - df['LowerBand']).replace(0, np.nan)
                bb_position = (df['Close'] - df['LowerBand']) / bb_den
                bb_position = bb_position.fillna(0.5)
                bb_swing = bb_position.rolling(window=20).std()
                bb_score = bb_swing.mean() * 5  # 標準化
                if isinstance(bb_score, pd.Series):
                    bb_score = bb_score.mean()
                
                # 4. 回測簡易策略，計算勝率和總回報
                returns = []
                win_count = 0
                trades = 0
                
                i = 50
                while i < len(df) - 15:
                    # 進場條件 (RSI 超賣 + MACD 即將交叉向上)
                    if df['RSI'].iloc[i] < 30 and df['MACD'].iloc[i] > df['MACD'].iloc[i-1]:
                        entry_price = df['Close'].iloc[i+1]  # 下一個收盤價進場
                        exit_idx = None
                        
                        # 尋找出場點 (RSI > 70 或者經過15個交易日)
                        for j in range(i+2, min(i+16, len(df))):
                            if df['RSI'].iloc[j] > 70:
                                exit_idx = j
                                break
                        
                        if exit_idx is None:
                            exit_idx = min(i + 15, len(df) - 1)
                            
                        exit_price = df['Close'].iloc[exit_idx]
                                
                        # 計算這次交易的回報率
                        trade_return = (exit_price / entry_price) - 1
                        returns.append(trade_return)
                        
                        if trade_return > 0:
                            win_count += 1
                            
                        trades += 1
                        i = exit_idx + 1
                        continue
                    i += 1
                
                # 整合所有評分要素
                if trades > 0:
                    win_rate = win_count / trades
                    total_return = np.prod([1 + r for r in returns]) - 1 if returns else 0
                    sharpe_ratio = (np.mean(returns) / np.std(returns)) * np.sqrt(252/15) if returns and np.std(returns) > 0 else 0
                    
                    # 計算每個波動的平均波幅
                    avg_swing_pct = np.mean(np.abs(np.diff(bb_position.fillna(0).values))) * 100 if len(bb_position) > 1 else 0
                    
                    # 綜合評分
                    score = (
                        (volatility_score * 10 if 0.15 <= volatility_score <= 0.4 else volatility_score * 5) +
                        (volume_ratio * 2 if volume_ratio > 1 else volume_ratio) +
                        rsi_score * 2 +
                        macd_score +
                        bb_score +
                        (win_rate * 3 if win_rate > 0.5 else win_rate) +
                        (sharpe_ratio * 2 if sharpe_ratio > 0 else 0)
                    )
                    
                    # 判斷是否適合波段操作
                    suitable = score > 10 and win_rate > 0.5 and volatility_score > 0.15
                    
                    # 取得股票名稱
                    try:
                        stock_info = yf.Ticker(symbol).info
                        name = stock_info.get('shortName', symbol)
                    except:
                        name = symbol
                    
                    # 彙總結果
                    result = {
                        'symbol': symbol,
                        'name': name,
                        'suitable': bool(suitable),  # 確保為布林值
                        'score': float(score),  # 確保為浮點數
                        'volatility': float(volatility_score),
                        'swing_percent': float(avg_swing_pct),
                        'volume_ratio': float(volume_ratio),
                        'total_return': float(total_return),
                        'sharpe_ratio': float(sharpe_ratio if not np.isnan(sharpe_ratio) else 0),
                        'num_trades': int(trades),
                        'win_rate': float(win_rate)
                    }
                    
                    results.append(result)
                    
            except Exception as e:
                print(f"分析 {symbol} 時發生錯誤: {str(e)}")
        
        # 轉換為DataFrame
        if results:
            results_df = pd.DataFrame(results)
            # 依照得分排序
            results_df = results_df.sort_values('score', ascending=False)
            return results_df
        else:
            return pd.DataFrame()
            
    def analyze_industry_groups(self, industry_groups, start_date, end_date, params=None):
        """
        分析各產業群組的股票適合度
        
        :param industry_groups: 產業股票分組字典，格式：{'產業名稱': [股票代號列表]}
        :param start_date: 開始日期
        :param end_date: 結束日期
        :param params: 策略參數字典
        :return: 各產業分析結果字典
        """
        industry_results = {}
        
        for category, stocks in industry_groups.items():
            print(f"\n=== 分析{category} ===")
            results = self.analyze_stock_list(stocks, start_date, end_date, params)
            industry_results[category] = results
            
            # 顯示前三名適合波段操作的股票
            if not results.empty:
                top_stocks = results.head(3)
                print(f"{category}中最適合波段操作的前三名股票:")
                for _, row in top_stocks.iterrows():
                    print(f"{row['symbol']} ({row['name']}): 評分 {row['score']:.1f}")
        
        return industry_results
