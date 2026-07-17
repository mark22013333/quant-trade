"""
網頁報表產生器：將股票分析結果轉換成 HTML 格式的報表
不使用視窗介面，完全透過網頁方式呈現，避免維度不匹配問題
"""
import os
from html import escape
import pandas as pd
import numpy as np
from datetime import datetime


def _text(value, default="未分類"):
    if value is None or pd.isna(value):
        return default
    return str(value)


def _format_chart_number(value, digits=2, suffix=""):
    try:
        if pd.isna(value):
            return "-"
        return f"{float(value):.{digits}f}{suffix}"
    except Exception:
        return "-"


def _chart_card(title, body):
    return f"""
        <section class="chart-card">
            <h3>{escape(title)}</h3>
            {body}
        </section>
    """


def _empty_chart(message="無資料"):
    return f'<div class="empty-chart">{escape(message)}</div>'


def _scatter_chart_html(
    df,
    *,
    x_col,
    y_col,
    title,
    x_label,
    y_label,
    x_reference=None,
    y_reference=None,
):
    plot_df = df.copy()
    plot_df[x_col] = pd.to_numeric(plot_df[x_col], errors="coerce")
    plot_df[y_col] = pd.to_numeric(plot_df[y_col], errors="coerce")
    plot_df = plot_df.dropna(subset=[x_col, y_col])
    if plot_df.empty:
        return _chart_card(title, _empty_chart())

    x_values = plot_df[x_col]
    y_values = plot_df[y_col]
    x_min, x_max = float(x_values.min()), float(x_values.max())
    y_min, y_max = float(y_values.min()), float(y_values.max())
    if x_reference is not None:
        x_min, x_max = min(x_min, float(x_reference)), max(x_max, float(x_reference))
    if y_reference is not None:
        y_min, y_max = min(y_min, float(y_reference)), max(y_max, float(y_reference))
    if x_min == x_max:
        x_min -= 1.0
        x_max += 1.0
    if y_min == y_max:
        y_min -= 1.0
        y_max += 1.0

    left, top, width, height = 56.0, 28.0, 540.0, 280.0
    x_span = x_max - x_min
    y_span = y_max - y_min
    score_values = pd.to_numeric(plot_df.get("score", 0), errors="coerce").abs().fillna(0.0)
    max_score = float(score_values.max()) if not score_values.empty else 0.0
    colors = ["#0d6efd", "#20c997", "#fd7e14", "#6f42c1", "#198754", "#dc3545"]
    category_index = {
        category: idx
        for idx, category in enumerate(sorted({_text(item) for item in plot_df.get("category", pd.Series(dtype=object))}))
    }

    reference_lines = []
    if y_reference is not None and y_min <= float(y_reference) <= y_max:
        cy = top + height - (((float(y_reference) - y_min) / y_span) * height)
        reference_lines.append(
            f'<line x1="{left:.1f}" y1="{cy:.1f}" x2="{left + width:.1f}" y2="{cy:.1f}" stroke="#dc3545" stroke-dasharray="6 4" opacity="0.45" />'
        )
    if x_reference is not None and x_min <= float(x_reference) <= x_max:
        cx = left + (((float(x_reference) - x_min) / x_span) * width)
        reference_lines.append(
            f'<line x1="{cx:.1f}" y1="{top:.1f}" x2="{cx:.1f}" y2="{top + height:.1f}" stroke="#dc3545" stroke-dasharray="6 4" opacity="0.45" />'
        )

    circles = []
    for idx, (_, row) in enumerate(plot_df.iterrows()):
        cx = left + (((float(row[x_col]) - x_min) / x_span) * width)
        cy = top + height - (((float(row[y_col]) - y_min) / y_span) * height)
        radius = 5.0 + ((float(score_values.iloc[idx]) / max_score) * 11.0) if max_score > 0 else 7.0
        category = _text(row.get("category"))
        color = colors[category_index.get(category, 0) % len(colors)]
        symbol = _text(row.get("symbol"), "-")
        name = _text(row.get("name"), "")
        tooltip = (
            f"{symbol} {name} | {x_label}: {_format_chart_number(row[x_col])} | "
            f"{y_label}: {_format_chart_number(row[y_col])} | 評分: {_format_chart_number(row.get('score'))}"
        )
        circles.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}" fill="{color}" opacity="0.78">'
            f"<title>{escape(tooltip)}</title></circle>"
        )

    axis_labels = f"""
        <text x="{left:.1f}" y="20" font-size="12" fill="#6c757d">{escape(y_label)}</text>
        <text x="{left + width - 64:.1f}" y="335" font-size="12" fill="#6c757d">{escape(x_label)}</text>
        <text x="{left:.1f}" y="326" font-size="11" fill="#6c757d">{escape(_format_chart_number(x_min))}</text>
        <text x="{left + width - 42:.1f}" y="326" font-size="11" fill="#6c757d">{escape(_format_chart_number(x_max))}</text>
    """
    body = f"""
        <svg class="simple-scatter" viewBox="0 0 640 350" role="img" aria-label="{escape(title)}">
            <rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" fill="#fbfcfe" stroke="#dee2e6" />
            <line x1="{left:.1f}" y1="{top + height:.1f}" x2="{left + width:.1f}" y2="{top + height:.1f}" stroke="#adb5bd" />
            <line x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{top + height:.1f}" stroke="#adb5bd" />
            {''.join(reference_lines)}
            {''.join(circles)}
            {axis_labels}
        </svg>
    """
    return _chart_card(title, body)

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

        rows_html = []
        for category, row in industry_counts.iterrows():
            suitable_count = int(row.loc[True]) if True in row.index else 0
            unsuitable_count = int(row.loc[False]) if False in row.index else 0
            total = max(suitable_count + unsuitable_count, 1)
            suitable_width = suitable_count / total * 100.0
            unsuitable_width = unsuitable_count / total * 100.0
            rows_html.append(f"""
                <div class="bar-row stacked">
                    <span class="bar-label">{escape(_text(category))}</span>
                    <div class="bar-track stacked-track" title="適合 {suitable_count}，不適合 {unsuitable_count}">
                        <i class="bar-ok" style="width:{suitable_width:.1f}%"></i>
                        <i class="bar-muted" style="width:{unsuitable_width:.1f}%"></i>
                    </div>
                    <b>{suitable_count}/{total}</b>
                </div>
            """)

        legend = """
            <div class="chart-legend">
                <span><i class="legend-ok"></i>適合</span>
                <span><i class="legend-muted"></i>不適合</span>
            </div>
        """
        return _chart_card("各產業適合波段操作的股票數量", legend + f'<div class="simple-bars">{"".join(rows_html)}</div>')
        
    def _create_score_boxplot(self, all_results):
        """建立評分分布摘要"""
        if all_results.empty:
            return None

        score_summary = (
            all_results.groupby("category", dropna=False)["score"]
            .agg(["count", "min", "median", "mean", "max"])
            .reset_index()
        )
        rows_html = []
        for _, row in score_summary.iterrows():
            rows_html.append(f"""
                <tr>
                    <td>{escape(_text(row["category"]))}</td>
                    <td>{int(row["count"])}</td>
                    <td>{_format_chart_number(row["median"], 1)}</td>
                    <td>{_format_chart_number(row["mean"], 1)}</td>
                    <td>{_format_chart_number(row["min"], 1)}</td>
                    <td>{_format_chart_number(row["max"], 1)}</td>
                </tr>
            """)
        body = f"""
            <table class="metric-table">
                <thead>
                    <tr><th>產業</th><th>樣本</th><th>中位數</th><th>平均</th><th>最低</th><th>最高</th></tr>
                </thead>
                <tbody>{''.join(rows_html)}</tbody>
            </table>
        """
        return _chart_card("各產業波段適合度評分分布", body)
    
    def _create_volatility_scatter(self, all_results):
        """建立波動性與評分散點圖"""
        if all_results.empty:
            return None

        return _scatter_chart_html(
            all_results,
            x_col="volatility",
            y_col="score",
            title="波動率與適合度評分關係",
            x_label="波動率",
            y_label="適合度評分",
        )
        
    def _create_return_vs_winrate_scatter(self, all_results):
        """建立獲利與勝率散點圖"""
        if all_results.empty:
            return None

        y_col = 'avg_return' if 'avg_return' in all_results.columns else 'total_return'
        y_label = '平均回報 (%)' if y_col == 'avg_return' else '總回報'
        return _scatter_chart_html(
            all_results,
            x_col="win_rate",
            y_col=y_col,
            title="勝率與平均回報關係",
            x_label="勝率",
            y_label=y_label,
            x_reference=0.5,
            y_reference=0,
        )
        
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
        charts_html = "".join(
            chart for chart in (
                industry_chart,
                score_boxplot,
                volatility_scatter,
                return_winrate_scatter,
            )
            if chart
        )
        
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
        .charts-container {{
            display: grid;
            gap: 20px;
        }}
        .chart-card {{
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 16px;
            background: #fff;
        }}
        .chart-card h3 {{
            font-size: 1.15rem;
            margin-bottom: 12px;
        }}
        .chart-legend {{
            display: flex;
            gap: 16px;
            font-size: 0.9rem;
            color: #6c757d;
            margin-bottom: 10px;
        }}
        .chart-legend span {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .chart-legend i {{
            width: 10px;
            height: 10px;
            border-radius: 999px;
            display: inline-block;
        }}
        .legend-ok, .bar-ok {{
            background: #198754;
        }}
        .legend-muted, .bar-muted {{
            background: #adb5bd;
        }}
        .simple-bars {{
            display: grid;
            gap: 10px;
        }}
        .bar-row {{
            display: grid;
            grid-template-columns: minmax(120px, 180px) 1fr 64px;
            gap: 10px;
            align-items: center;
            font-size: 0.92rem;
        }}
        .bar-label {{
            overflow-wrap: anywhere;
        }}
        .bar-track {{
            height: 12px;
            border-radius: 999px;
            background: #eef2f7;
            overflow: hidden;
        }}
        .stacked-track {{
            display: flex;
        }}
        .stacked-track i {{
            display: block;
            height: 100%;
        }}
        .metric-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .metric-table th, .metric-table td {{
            padding: 9px 8px;
            border-bottom: 1px solid #edf0f2;
            text-align: left;
        }}
        .simple-scatter {{
            width: 100%;
            max-height: 420px;
            background: #fbfcfe;
            border-radius: 8px;
        }}
        .empty-chart {{
            padding: 18px;
            border: 1px dashed #ced4da;
            border-radius: 8px;
            color: #6c757d;
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
