import pandas as pd
from dashboard_generator import generate_dashboard


def test_dashboard_generator(tmp_path):
    df = pd.DataFrame({
        "symbol": ["2330.TW", "2317.TW"],
        "name": ["TSMC", "FOXCONN"],
        "industry": ["半導體", "電子"],
        "ret_5d": [0.02, 0.01],
        "ret_10d": [0.03, 0.02],
        "ret_20d": [0.05, 0.04],
        "rsi_14": [55, 60],
        "atr_pct": [2.1, 2.8],
        "avg_turnover_20d": [1e8, 8e7],
        "total_score": [78.5, 72.0]
    })

    out = generate_dashboard(df, df, output_dir=tmp_path)
    assert out.exists()
