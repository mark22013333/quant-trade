"""
短期投資 Dashboard 產生器（靜態 HTML）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import plotly.express as px


def _format_percent(x):
    try:
        return f"{x:.2%}"
    except Exception:
        return "-"


def _format_float(x, digits=2):
    try:
        return f"{x:.{digits}f}"
    except Exception:
        return "-"


def _build_table(df_top: pd.DataFrame) -> str:
    # 指定欄位
    columns = [
        ("symbol", "代號"),
        ("name", "名稱"),
        ("industry", "產業"),
        ("total_score", "Score"),
        ("ret_5d", "5日報酬"),
        ("ret_10d", "10日報酬"),
        ("ret_20d", "20日報酬"),
        ("rsi_14", "RSI"),
        ("atr_pct", "ATR%"),
        ("avg_turnover_20d", "20日成交額")
    ]

    rows_html = []
    for _, row in df_top.iterrows():
        row_cells = []
        for key, _ in columns:
            val = row.get(key, "")
            if key.startswith("ret_"):
                display = _format_percent(val)
                sort_value = float(val) if pd.notnull(val) else -999
            elif key in ("total_score", "rsi_14", "atr_pct"):
                display = _format_float(val, 2)
                sort_value = float(val) if pd.notnull(val) else -999
            elif key == "avg_turnover_20d":
                display = _format_float(val, 0)
                sort_value = float(val) if pd.notnull(val) else -999
            else:
                display = str(val)
                sort_value = display
            row_cells.append(f"<td data-sort='{sort_value}'>{display}</td>")
        rows_html.append(f"<tr>{''.join(row_cells)}</tr>")

    header_html = "".join([f"<th>{label}</th>" for _, label in columns])
    return f"""
    <div class="table-wrap">
      <input class="table-search" placeholder="搜尋代號 / 名稱 / 產業" oninput="filterTable(this)" />
      <table id="top-table">
        <thead><tr>{header_html}</tr></thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    """


def generate_dashboard(full_df: pd.DataFrame, top20_df: pd.DataFrame, output_dir: str = "reports") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"short_term_dashboard_{timestamp}.html"

    # 圖表 1：波動 vs 短期報酬
    scatter = px.scatter(
        full_df,
        x="atr_pct",
        y="ret_20d",
        size="avg_turnover_20d",
        color="industry",
        hover_name="symbol",
        title="波動（ATR%）vs 20日報酬",
        labels={"atr_pct": "ATR%", "ret_20d": "20日報酬", "avg_turnover_20d": "成交額"},
        height=520
    )

    # 圖表 2：分數分布
    hist = px.histogram(
        full_df,
        x="total_score",
        nbins=30,
        title="短期投資分數分布",
        height=360
    )

    # 圖表 3：Top20 產業分布
    industry_counts = top20_df["industry"].value_counts().reset_index()
    industry_counts.columns = ["industry", "count"]
    industry_bar = px.bar(
        industry_counts,
        x="industry",
        y="count",
        title="Top20 產業分布",
        height=360
    )

    table_html = _build_table(top20_df)

    html = f"""
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>短期投資 Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@500;700&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --ink: #0b0f19;
      --paper: #f7f4ef;
      --muted: #8b93a7;
      --accent: #ff6b35;
      --accent-2: #2ec4b6;
      --shadow: rgba(0,0,0,0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: 'IBM Plex Sans', sans-serif;
      color: var(--ink);
      background: radial-gradient(1200px 800px at 10% 0%, #fff5e7 0%, #f7f4ef 60%, #f0efe9 100%);
    }}
    .hero {{
      padding: 36px 32px 20px;
      position: relative;
      overflow: hidden;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      right: -120px;
      top: -140px;
      width: 380px;
      height: 380px;
      background: radial-gradient(circle, rgba(255,107,53,0.22), transparent 65%);
      filter: blur(2px);
    }}
    .title {{
      font-family: 'Fraunces', serif;
      font-size: 36px;
      letter-spacing: 0.5px;
      margin: 0 0 4px;
    }}
    .subtitle {{ color: var(--muted); margin: 0 0 12px; }}
    .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 18px; }}
    .card {{
      background: white;
      border-radius: 14px;
      padding: 16px 18px;
      box-shadow: 0 8px 24px var(--shadow);
      border: 1px solid rgba(0,0,0,0.04);
    }}
    .card h4 {{ margin: 0 0 6px; font-size: 13px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.08em; }}
    .card p {{ margin: 0; font-size: 18px; font-weight: 600; }}

    .grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 18px; padding: 0 32px 32px; }}
    .panel {{ background: white; border-radius: 16px; padding: 18px; box-shadow: 0 8px 24px var(--shadow); }}
    .panel h3 {{ font-family: 'Fraunces', serif; margin: 0 0 10px; }}

    .full {{ padding: 0 32px 32px; }}
    .table-wrap {{ background: white; border-radius: 16px; padding: 18px; box-shadow: 0 8px 24px var(--shadow); }}
    .table-search {{
      width: 100%;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid #ddd;
      margin-bottom: 12px;
      font-size: 14px;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 8px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }}
    th {{ cursor: pointer; position: sticky; top: 0; background: #fafafa; }}
    tr:hover {{ background: #fff7f1; }}

    .footer {{ text-align: center; color: var(--muted); font-size: 12px; padding: 12px 0 24px; }}

    @media (max-width: 980px) {{
      .stats {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
      .hero, .grid, .full {{ padding-left: 16px; padding-right: 16px; }}
    }}
  </style>
</head>
<body>
  <section class="hero">
    <h1 class="title">短期投資 Dashboard</h1>
    <p class="subtitle">每次執行更新｜10–20 交易日短期評分｜Top20 高流動性候選</p>
    <div class="stats">
      <div class="card"><h4>樣本數</h4><p>{len(full_df)} 檔</p></div>
      <div class="card"><h4>Top20 平均分數</h4><p>{_format_float(top20_df['total_score'].mean(), 1)}</p></div>
      <div class="card"><h4>更新時間</h4><p>{datetime.now().strftime('%Y-%m-%d %H:%M')}</p></div>
    </div>
  </section>

  <section class="grid">
    <div class="panel">
      <h3>波動 vs 短期報酬</h3>
      {scatter.to_html(full_html=False, include_plotlyjs='cdn')}
    </div>
    <div class="panel">
      <h3>分數分布</h3>
      {hist.to_html(full_html=False, include_plotlyjs='cdn')}
    </div>
  </section>

  <section class="grid">
    <div class="panel">
      <h3>Top20 產業分布</h3>
      {industry_bar.to_html(full_html=False, include_plotlyjs='cdn')}
    </div>
    <div class="panel">
      <h3>評分說明</h3>
      <p style="font-size:13px;color:#4b5563;line-height:1.6;">
        分數基於短期動能（5/10/20 日）、流動性（成交額）、
        RSI 相對強弱與 ATR 波動風險的加權組合。
        分數越高代表短期動能與風險平衡越好。
      </p>
    </div>
  </section>

  <section class="full">
    <h3 style="font-family:'Fraunces',serif;">Top 20 短期投資名單</h3>
    {table_html}
  </section>

  <div class="footer">Generated by quant-trade</div>

  <script>
    function filterTable(input) {{
      const filter = input.value.toLowerCase();
      const rows = document.querySelectorAll('#top-table tbody tr');
      rows.forEach(row => {{
        const text = row.innerText.toLowerCase();
        row.style.display = text.includes(filter) ? '' : 'none';
      }});
    }}

    // Simple sort on header click
    document.querySelectorAll('#top-table th').forEach((th, idx) => {{
      th.addEventListener('click', () => {{
        const tbody = document.querySelector('#top-table tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const asc = !th.classList.contains('asc');
        rows.sort((a, b) => {{
          const aCell = a.children[idx];
          const bCell = b.children[idx];
          const aVal = aCell.getAttribute('data-sort') ?? aCell.innerText;
          const bVal = bCell.getAttribute('data-sort') ?? bCell.innerText;
          const aNum = parseFloat(aVal); const bNum = parseFloat(bVal);
          if (!isNaN(aNum) && !isNaN(bNum)) return asc ? aNum - bNum : bNum - aNum;
          return asc ? String(aVal).localeCompare(String(bVal)) : String(bVal).localeCompare(String(aVal));
        }});
        tbody.innerHTML = '';
        rows.forEach(r => tbody.appendChild(r));
        document.querySelectorAll('#top-table th').forEach(h => h.classList.remove('asc','desc'));
        th.classList.add(asc ? 'asc' : 'desc');
      }});
    }});
  </script>
</body>
</html>
"""

    out_path.write_text(html, encoding="utf-8")
    return out_path
