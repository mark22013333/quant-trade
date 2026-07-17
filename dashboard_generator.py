"""
短期投資 Dashboard 產生器（靜態 HTML）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import pandas as pd


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


def _marker_sizes(values: pd.Series, *, minimum: float = 8.0, maximum: float = 28.0) -> list[float]:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0)
    if numeric.empty or float(numeric.max()) <= 0:
        return [minimum] * len(numeric)
    scaled = minimum + ((numeric / float(numeric.max())) * (maximum - minimum))
    return [float(item) for item in scaled]


def _simple_scatter_html(df: pd.DataFrame) -> str:
    if df.empty:
        return "<div class='empty-chart'>無資料</div>"
    x = pd.to_numeric(df["atr_pct"], errors="coerce").fillna(0.0)
    y = pd.to_numeric(df["ret_20d"], errors="coerce").fillna(0.0)
    x_min, x_max = float(x.min()), float(x.max())
    y_min, y_max = float(y.min()), float(y.max())
    x_span = max(x_max - x_min, 1e-9)
    y_span = max(y_max - y_min, 1e-9)
    sizes = _marker_sizes(df["avg_turnover_20d"], minimum=5.0, maximum=13.0)
    colors = ["#18a384", "#1d6ef2", "#ff6b35", "#8b5cf6", "#64748b"]
    industries = {name: idx for idx, name in enumerate(sorted({str(item or "未分類") for item in df["industry"]}))}
    circles = []
    for idx, (_, row) in enumerate(df.iterrows()):
        cx = 40 + ((float(x.iloc[idx]) - x_min) / x_span * 500)
        cy = 250 - ((float(y.iloc[idx]) - y_min) / y_span * 210)
        industry = str(row.get("industry") or "未分類")
        color = colors[industries.get(industry, 0) % len(colors)]
        title = f"{row.get('symbol', '-')}: ATR {float(x.iloc[idx]):.2f}, 20日報酬 {float(y.iloc[idx]):.2%}"
        circles.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{sizes[idx]:.1f}' fill='{color}' opacity='0.78'><title>{title}</title></circle>")
    return f"""
      <svg class="simple-chart" viewBox="0 0 580 280" role="img" aria-label="波動與報酬散點圖">
        <line x1="40" y1="250" x2="550" y2="250" stroke="#d0d5dd" />
        <line x1="40" y1="32" x2="40" y2="250" stroke="#d0d5dd" />
        {''.join(circles)}
        <text x="44" y="24" font-size="11" fill="#667085">20日報酬</text>
        <text x="490" y="270" font-size="11" fill="#667085">ATR%</text>
      </svg>
    """


def _simple_bar_chart_html(labels: list[str], values: list[float], *, value_suffix: str = "") -> str:
    if not labels:
        return "<div class='empty-chart'>無資料</div>"
    max_value = max([abs(float(item)) for item in values] or [1.0]) or 1.0
    rows = []
    for label, value in zip(labels, values):
        width = min(100.0, abs(float(value)) / max_value * 100.0)
        rows.append(
            f"<div class='bar-row'><span>{label}</span><div class='bar-track'><i style='width:{width:.1f}%'></i></div><b>{_format_float(value, 1)}{value_suffix}</b></div>"
        )
    return f"<div class='simple-bars'>{''.join(rows)}</div>"


def _score_histogram_html(df: pd.DataFrame) -> str:
    values = pd.to_numeric(df["total_score"], errors="coerce").dropna()
    if values.empty:
        return "<div class='empty-chart'>無資料</div>"
    bins = 8
    min_v, max_v = float(values.min()), float(values.max())
    step = max((max_v - min_v) / bins, 1.0)
    labels: list[str] = []
    counts: list[float] = []
    for idx in range(bins):
        start = min_v + (step * idx)
        end = start + step
        if idx == bins - 1:
            count = int(((values >= start) & (values <= end)).sum())
        else:
            count = int(((values >= start) & (values < end)).sum())
        labels.append(f"{start:.0f}-{end:.0f}")
        counts.append(float(count))
    return _simple_bar_chart_html(labels, counts)


def generate_dashboard(full_df: pd.DataFrame, top20_df: pd.DataFrame, output_dir: str = "reports") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"short_term_dashboard_{timestamp}.html"

    scatter_html = _simple_scatter_html(full_df)
    hist_html = _score_histogram_html(full_df)

    # 圖表 3：Top20 產業分布
    industry_counts = top20_df["industry"].value_counts().reset_index()
    industry_counts.columns = ["industry", "count"]
    industry_bar_html = _simple_bar_chart_html(industry_counts["industry"].tolist(), [float(item) for item in industry_counts["count"].tolist()])

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
    .simple-chart {{ width: 100%; height: 320px; background: #fbfcfe; border-radius: 10px; border: 1px solid #edf0f5; }}
    .simple-bars {{ display: grid; gap: 10px; margin-top: 8px; }}
    .bar-row {{ display: grid; grid-template-columns: 90px 1fr 54px; gap: 8px; align-items: center; font-size: 12px; }}
    .bar-row span {{ overflow-wrap: anywhere; }}
    .bar-track {{ height: 10px; border-radius: 999px; background: #eef2f7; overflow: hidden; }}
    .bar-track i {{ display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--accent-2), #1d6ef2); }}
    .bar-row b {{ text-align: right; font-family: monospace; }}
    .empty-chart {{ padding: 18px; color: var(--muted); border: 1px dashed #d0d5dd; border-radius: 10px; }}

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
      {scatter_html}
    </div>
    <div class="panel">
      <h3>分數分布</h3>
      {hist_html}
    </div>
  </section>

  <section class="grid">
    <div class="panel">
      <h3>Top20 產業分布</h3>
      {industry_bar_html}
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
