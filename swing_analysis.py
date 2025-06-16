"""
波段交易適合度分析工具
分析不同產業股票中，哪些適合進行波段操作
"""
import os
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from tqdm import tqdm
import yfinance as yf
from datetime import datetime, timedelta
import webbrowser

# 引入專案模組
from data.market_data import MarketData
from data.providers.yfinance_provider import YFinanceProvider
from strategies.momentum.swing_trader import SwingTradingStrategy
from data.tw_industry_stocks import ALL_CATEGORY_STOCKS, get_all_stocks
from html_report_generator import HtmlReportGenerator  # 引入 HTML 報表產生器
from web_report_strategy2 import SimpleWebReportAnalyzer  # 引入簡化版網頁報表分析器

# 設定繪圖風格
plt.style.use('ggplot')
sns.set(font_scale=1.2)
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei']  # 中文顯示
plt.rcParams['axes.unicode_minus'] = False  # 負號顯示

# 載入環境變數
dotenv_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path, override=True)

def analyze_stock_swing_suitability(stock_list, start_date, end_date, params=None):
    """
    分析股票的波段交易適合度
    
    :param stock_list: 股票代號列表
    :param start_date: 開始日期
    :param end_date: 結束日期
    :param params: 策略參數字典
    :return: 分析結果DataFrame
    """
    # 初始化資料提供者和市場資料管理器
    provider = YFinanceProvider()
    market_data = MarketData(provider)
    
    # 初始化策略
    strategy = SwingTradingStrategy(params)
    
    results = []
    
    # 分析每個股票
    for symbol in tqdm(stock_list, desc="分析股票"):
        try:
            # 載入股票資料
            data = market_data.load_data(symbol, start_date, end_date)
            
            if data.empty or len(data) < 100:  # 確保有足夠的資料
                print(f"警告: {symbol} 無足夠資料進行分析")
                continue
                
            # 分析適合度
            analysis = strategy.analyze_stock_suitability(data)
            
            # 股票名稱
            try:
                stock_info = yf.Ticker(symbol).info
                name = stock_info.get('shortName', symbol)
            except:
                name = symbol
                
            # 彙總結果
            result = {
                'symbol': symbol,
                'name': name,
                'suitable': analysis['suitable'],
                'score': analysis['score'],
                'volatility': analysis['metrics']['avg_volatility'],
                'swing_percent': analysis['metrics']['avg_swing_percent'],
                'volume_ratio': analysis['metrics']['avg_volume_ratio'],
                'total_return': analysis['metrics']['total_return'],
                'sharpe_ratio': analysis['metrics']['sharpe_ratio'],
                'num_trades': analysis['metrics']['num_trades'],
                'win_rate': analysis['metrics']['win_rate']
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

def analyze_industry_groups(start_date, end_date, params=None):
    """
    分析各產業群組的股票適合度
    
    :param start_date: 開始日期
    :param end_date: 結束日期
    :param params: 策略參數字典
    :return: 各產業分析結果字典
    """
    industry_results = {}
    
    for category, stocks in ALL_CATEGORY_STOCKS.items():
        print(f"\n=== 分析{category} ===")
        results = analyze_stock_swing_suitability(stocks, start_date, end_date, params)
        industry_results[category] = results
        
        # 顯示前三名適合波段操作的股票
        if not results.empty:
            top_stocks = results.head(3)
            print(f"{category}中最適合波段操作的前三名股票:")
            for _, row in top_stocks.iterrows():
                print(f"{row['symbol']} ({row['name']}): 評分 {row['score']:.1f}，獲利率 {row['total_return']:.2%}，勝率 {row['win_rate']:.2%}")
    
    return industry_results

def analyze_industry_groups_simple(start_date, end_date):
    """
    使用簡化版網頁報表分析器分析各產業群組的股票適合度
    專門解決維度不匹配問題
    
    :param start_date: 開始日期
    :param end_date: 結束日期
    :return: 各產業分析結果字典
    """
    # 建立簡化版網頁報表分析器
    analyzer = SimpleWebReportAnalyzer()
    
    # 使用分析器分析所有產業
    return analyzer.analyze_industry_groups(ALL_CATEGORY_STOCKS, start_date, end_date)

def visualize_results(industry_results, output_dir="./results"):
    """
    視覺化分析結果 (舊版，使用 matplotlib 產生靜態圖表)
    
    :param industry_results: 產業分析結果字典
    :param output_dir: 輸出圖表目錄
    """
    # 建立輸出目錄
    os.makedirs(output_dir, exist_ok=True)
    
    # 合併所有結果
    all_results = pd.DataFrame()
    for category, df in industry_results.items():
        if not df.empty:
            df['category'] = category
            all_results = pd.concat([all_results, df])
    
    if all_results.empty:
        print("沒有足夠的分析結果可供視覺化")
        return
    
    # 1. 各產業適合度分布
    plt.figure(figsize=(12, 8))
    sns.boxplot(x='category', y='score', data=all_results)
    plt.title('各產業波段適合度評分分布')
    plt.xlabel('產業類別')
    plt.ylabel('適合度評分')
    plt.savefig(f"{output_dir}/industry_suitability_scores.png", dpi=300, bbox_inches='tight')
    
    # 2. 波動率與適合度評分關係
    plt.figure(figsize=(12, 8))
    sns.scatterplot(x='volatility', y='score', hue='category', size='total_return', 
                    sizes=(20, 200), data=all_results)
    plt.title('波動率與適合度評分關係')
    plt.xlabel('波動率')
    plt.ylabel('適合度評分')
    plt.legend(title='產業類別')
    plt.savefig(f"{output_dir}/volatility_vs_score.png", dpi=300, bbox_inches='tight')
    
    # 3. 勝率與獲利率散點圖
    plt.figure(figsize=(12, 8))
    sns.scatterplot(x='win_rate', y='total_return', hue='category', size='score', 
                    sizes=(20, 200), data=all_results)
    plt.title('勝率與獲利率關係')
    plt.xlabel('勝率')
    plt.ylabel('總獲利率')
    plt.axhline(y=0, color='r', linestyle='-', alpha=0.3)
    plt.axvline(x=0.5, color='r', linestyle='-', alpha=0.3)
    plt.legend(title='產業類別')
    plt.savefig(f"{output_dir}/winrate_vs_return.png", dpi=300, bbox_inches='tight')
    
    # 4. 各產業適合波段的股票比例
    suitable_counts = all_results.groupby(['category', 'suitable']).size().unstack(fill_value=0)
    if not 'suitable_counts' in locals() or suitable_counts.empty:
        print("無法計算適合度比例")
        return
    
    plt.figure(figsize=(10, 6))
    suitable_counts.plot(kind='bar', stacked=True)
    plt.title('各產業適合波段操作的股票數量')
    plt.xlabel('產業類別')
    plt.ylabel('股票數量')
    plt.legend(['不適合', '適合'])
    plt.savefig(f"{output_dir}/industry_suitability_counts.png", dpi=300, bbox_inches='tight')
    
    # 5. 產出總結報告
    report = all_results.sort_values(['category', 'score'], ascending=[True, False])
    report.to_csv(f"{output_dir}/swing_trading_analysis.csv", index=False, encoding='utf-8-sig')
    
    print(f"\n分析結果已儲存至 {output_dir} 目錄")

def main():
    """
    主程式
    """
    print("=== 台股波段交易適合度分析 ===")
    
    # 設定分析時間範圍 (預設近一年)
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    print(f"分析時間範圍: {start_date} 至 {end_date}")
    
    # 波段策略參數設定
    strategy_params = {
        'rsi_period': 14,
        'rsi_overbought': 70,
        'rsi_oversold': 30,
        'macd_fast': 12,
        'macd_slow': 26,
        'macd_signal': 9,
        'bb_period': 20,
        'bb_std': 2.0,
        'volume_factor': 1.5,
        'swing_lookback': 15,
        'profit_target': 0.08,
        'stop_loss': 0.05
    }
    
    # 使用簡化版網頁報表分析器進行分析，避免維度不匹配問題
    industry_results = analyze_industry_groups_simple(start_date, end_date)
    
    # 使用網頁報表呈現分析結果 (取代舊版視窗介面視覺化)
    output_dir = "./reports"
    
    # 建立 HTML 報表產生器
    report_generator = HtmlReportGenerator(output_dir)
    
    # 生成網頁報表
    report_path = report_generator.generate_report(
        industry_results,
        strategy_params,
        start_date,
        end_date
    )
    
    # 自動在瀏覽器中開啟報表
    if report_path:
        webbrowser.open('file://' + os.path.abspath(report_path))
    
    # 同時也輸出傳統報表 (可選)
    # visualize_results(industry_results)

if __name__ == "__main__":
    main()
