"""
網頁報表產生器：將股票分析結果轉換成 HTML 格式的報表
不使用視窗介面，完全透過網頁方式呈現，避免維度不匹配問題
"""
import os
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

class HtmlReportGenerator:
    """
    HTML 報表產生器
    
    將股票分析結果轉換成互動式網頁報表，包含：
    1. 資料表格
    2. 互動式圖表
    3. 具有篩選和排序功能的分析結果
    """
    
    def __init__(self, output_dir="./report"):
        """
        初始化報表產生器
        
        :param output_dir: 輸出目錄
        """
        self.output_dir = output_dir
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 建立輸出目錄
        os.makedirs(output_dir, exist_ok=True)
        
    def _create_industry_distribution_chart(self, all_results):
        """建立產業分布圖"""
        # 計算各產業的適合股票數量
        industry_counts = all_results.groupby(['category', 'suitable']).size().unstack(fill_value=0)
        
        if industry_counts.empty:
            return None
            
        # 轉換為長格式數據
        industry_data = []
        for idx, row in industry_counts.iterrows():
            for col in industry_counts.columns:
                industry_data.append({
                    '產業類別': idx,
                    '適合度': '適合' if col else '不適合',
                    '股票數量': row[col]
                })
        
        df_industry = pd.DataFrame(industry_data)
        
        # 繪製堆疊柱狀圖
        fig = px.bar(
            df_industry, 
            x='產業類別', 
            y='股票數量', 
            color='適合度',
            title='各產業適合波段操作的股票數量',
            color_discrete_map={'適合': 'green', '不適合': 'gray'},
            barmode='stack',
            height=500
        )
        
        return fig
        
    def _create_score_boxplot(self, all_results):
        """建立評分盒狀圖"""
        if all_results.empty:
            return None
            
        fig = px.box(
            all_results, 
            x='category', 
            y='score',
            title='各產業波段適合度評分分布',
            labels={'category': '產業類別', 'score': '適合度評分'},
            height=500,
            color='category'
        )
        
        return fig
    
    def _create_volatility_scatter(self, all_results):
        """建立波動性與評分散點圖"""
        if all_results.empty:
            return None
            
        # 確保使用正值作為尺寸
        size_values = np.abs(all_results['score']) + 3  # 加上基礎值，確保點足夠大
            
        fig = px.scatter(
            all_results, 
            x='volatility', 
            y='score',
            color='category',
            size=size_values,  # 使用絕對值評分作為大小
            size_max=25,
            hover_name='symbol',
            hover_data=['name', 'win_rate', 'num_trades'],
            title='波動率與適合度評分關係',
            labels={
                'volatility': '波動率', 
                'score': '適合度評分',
                'category': '產業類別'
            },
            height=600
        )
        
        return fig
        
    def _create_return_vs_winrate_scatter(self, all_results):
        """建立獲利與勝率散點圖"""
        if all_results.empty:
            return None
            
        # 確保使用正值作為尺寸
        size_values = np.abs(all_results['score']) + 3  # 加上基礎值，確保點足夠大
            
        y_col = 'avg_return' if 'avg_return' in all_results.columns else 'total_return'
        y_label = '平均回報 (%)' if y_col == 'avg_return' else '總回報'
        fig = px.scatter(
            all_results,
            x='win_rate',
            y=y_col,  # 使用平均回報或總回報
            color='category',
            size=size_values,  # 使用評分絕對值作為大小
            size_max=25,
            hover_name='symbol',
            hover_data=['name', 'num_trades', 'volatility'],
            title='勝率與平均回報關係',
            labels={
                'win_rate': '勝率',
                y_col: y_label,
                'category': '產業類別'
            },
            height=600
        )
        
        # 添加參考線
        fig.add_hline(y=0, line_dash='dash', line_color='red', opacity=0.4)
        fig.add_vline(x=0.5, line_dash='dash', line_color='red', opacity=0.4)
        
        return fig
        
    def _create_top_stocks_table(self, all_results, top_n=10):
        """建立前 N 名股票表格"""
        if all_results.empty:
            return ""
            
        # 選擇前 N 名股票
        top_stocks = all_results.sort_values('score', ascending=False).head(top_n)
        
        # 格式化數字
        formatted_data = top_stocks.copy()
        formatted_data['score'] = formatted_data['score'].apply(lambda x: f"{x:.1f}")
        
        # 檢查是否有相關欄位，有則進行格式化
        if 'total_return' in formatted_data.columns:
            formatted_data['total_return'] = formatted_data['total_return'].apply(lambda x: f"{x:.2%}")
        if 'avg_return' in formatted_data.columns:
            formatted_data['avg_return'] = formatted_data['avg_return'].apply(lambda x: f"{x:.2f}%")
        if 'win_rate' in formatted_data.columns:
            formatted_data['win_rate'] = formatted_data['win_rate'].apply(lambda x: f"{x:.1%}")
        if 'sharpe_ratio' in formatted_data.columns:
            formatted_data['sharpe_ratio'] = formatted_data['sharpe_ratio'].apply(lambda x: f"{x:.2f}" if pd.notnull(x) else "-")
        
        # 選擇顯示的欄位 (確保欄位存在)
        available_columns = formatted_data.columns.tolist()
        desired_columns = ['symbol', 'name', 'category', 'score', 'win_rate', 'avg_return', 'total_return', 'num_trades', 'volatility']
        display_columns = [col for col in desired_columns if col in available_columns]
        
        table_df = formatted_data[display_columns]
        
        # 列標題對應
        column_names = {
            'symbol': '代號',
            'name': '名稱',
            'category': '產業',
            'score': '評分',
            'total_return': '總回報',
            'avg_return': '平均回報',
            'win_rate': '勝率',
            'num_trades': '交易次數',
            'volatility': '波動率',
            'sharpe_ratio': '夏普比率'
        }
        
        # 只重命名存在的欄位
        rename_cols = {k: v for k, v in column_names.items() if k in table_df.columns}
        table_df = table_df.rename(columns=rename_cols)
        
        # 轉換為 HTML 表格
        table_html = table_df.to_html(
            index=False,
            classes=['table', 'table-striped', 'table-hover']
        )
        
        return table_html
        
    def _create_industry_summary_table(self, industry_results):
        """建立產業摘要表格"""
        if not industry_results:
            return ""
        
        industry_summary = []
        
        for category, df in industry_results.items():
            if df.empty:
                continue
                
            suitable_stocks = df[df['suitable'] == True]
            
            summary = {
                '產業': category,
                '股票數': len(df),
                '適合數量': len(suitable_stocks),
                '適合比例': f"{len(suitable_stocks) / len(df):.1%}" if len(df) > 0 else "0.0%",
                '平均評分': f"{df['score'].mean():.1f}",
                '最高評分': f"{df['score'].max():.1f}" if len(df) > 0 else "0.0"
            }
            
            if not suitable_stocks.empty:
                top_stock = suitable_stocks.loc[suitable_stocks['score'].idxmax()]
                summary['最佳股票'] = f"{top_stock['symbol']} ({top_stock['name']})"
            else:
                summary['最佳股票'] = "-"
                
            industry_summary.append(summary)
            
        # 轉為 DataFrame
        summary_df = pd.DataFrame(industry_summary)
        
        # 轉換為 HTML 表格
        summary_html = summary_df.to_html(
            index=False,
            classes=['table', 'table-striped', 'table-hover']
        )
        
        return summary_html
        
    def generate_report(self, industry_results, analysis_params, start_date, end_date):
        """
        產生完整的 HTML 報表
        
        :param industry_results: 各產業分析結果字典
        :param analysis_params: 分析參數
        :param start_date: 分析開始日期
        :param end_date: 分析結束日期
        :return: HTML 報表檔案路徑
        """
        # 合併所有結果
        all_results = pd.DataFrame()
        for category, df in industry_results.items():
            if df.empty:
                continue
                
            df = df.copy()
            df['category'] = category
            all_results = pd.concat([all_results, df])
        
        if all_results.empty:
            print("沒有足夠的分析結果可供報表生成")
            return None

        if 'avg_return' not in all_results.columns and 'total_return' in all_results.columns:
            if 'num_trades' in all_results.columns:
                denom = all_results['num_trades'].replace(0, np.nan)
                all_results['avg_return'] = all_results['total_return'] / denom
                all_results['avg_return'] = all_results['avg_return'].fillna(all_results['total_return'])
            else:
                all_results['avg_return'] = all_results['total_return']
        
        # 儲存原始資料為 CSV
        csv_file = os.path.join(self.output_dir, f"swing_analysis_data_{self.timestamp}.csv")
        all_results.to_csv(csv_file, index=False, encoding='utf-8-sig')
        print(f"原始資料已儲存至: {csv_file}")
            
        # 產生圖表
        industry_chart = self._create_industry_distribution_chart(all_results)
        score_boxplot = self._create_score_boxplot(all_results)
        volatility_scatter = self._create_volatility_scatter(all_results)
        return_winrate_scatter = self._create_return_vs_winrate_scatter(all_results)
        
        # 產生表格
        top_stocks_table = self._create_top_stocks_table(all_results)
        industry_summary_table = self._create_industry_summary_table(industry_results)
        
        # 產生圖表 HTML
        charts_html = ""
        
        if industry_chart:
            charts_html += industry_chart.to_html(full_html=False, include_plotlyjs='cdn')
            
        if score_boxplot:
            charts_html += score_boxplot.to_html(full_html=False, include_plotlyjs='cdn')
            
        if volatility_scatter:
            charts_html += volatility_scatter.to_html(full_html=False, include_plotlyjs='cdn')
            
        if return_winrate_scatter:
            charts_html += return_winrate_scatter.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 格式化參數
        params_html = "<h3>分析參數</h3><ul>"
        for param, value in analysis_params.items():
            params_html += f"<li><strong>{param}:</strong> {value}</li>"
        params_html += "</ul>"
        
        # 建立 HTML 報表
        html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>台股波段適合度分析報告</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/css/bootstrap.min.css">
    <style>
        body {{
            font-family: Arial, "Microsoft JhengHei", sans-serif;
            line-height: 1.6;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1, h2, h3 {{
            color: #333;
            margin-bottom: 20px;
        }}
        .stats-card {{
            background-color: #f8f9fa;
            border-left: 4px solid #007bff;
            padding: 15px;
            margin-bottom: 20px;
            border-radius: 4px;
        }}
        .table {{
            margin-bottom: 30px;
        }}
        .footer {{
            text-align: center;
            margin-top: 30px;
            color: #777;
            font-size: 0.9rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-4">台股波段交易適合度分析報告</h1>
        <div class="row mb-4">
            <div class="col-md-6">
                <div class="stats-card">
                    <h5>分析區間</h5>
                    <p>從 {start_date} 至 {end_date}</p>
                </div>
            </div>
            <div class="col-md-6">
                <div class="stats-card">
                    <h5>分析股票數量</h5>
                    <p>共 {len(all_results)} 檔股票，分別來自 {len(industry_results)} 個產業類別</p>
                </div>
            </div>
        </div>
        
        <h2>產業摘要</h2>
        {industry_summary_table}
        
        <h2>前幾名最適合波段的股票</h2>
        {top_stocks_table}
        
        <h2>視覺化分析</h2>
        <div class="charts-container">
            {charts_html}
        </div>
        
        <div class="parameter-section">
            {params_html}
        </div>
        
        <div class="footer">
            <p>報表生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""
        
        # 儲存 HTML 報表
        html_file = os.path.join(self.output_dir, f"swing_analysis_report_{self.timestamp}.html")
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        print(f"HTML 報表已儲存至: {html_file}")
        return html_file
