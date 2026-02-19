import pandas as pd
import numpy as np

from analysis.short_term_ranker import compute_features, score_stocks


def make_ohlcv(rows=80):
    dates = pd.date_range("2023-01-01", periods=rows, freq="B")
    prices = np.linspace(50, 60, rows) + np.random.normal(0, 0.5, rows)
    return pd.DataFrame({
        "Open": prices,
        "High": prices * 1.01,
        "Low": prices * 0.99,
        "Close": prices,
        "Volume": np.random.randint(1000, 5000, rows)
    }, index=dates)


def test_compute_features():
    df = make_ohlcv()
    feats = compute_features(df)
    assert "ret_5d" in feats
    assert "ret_10d" in feats
    assert "ret_20d" in feats
    assert "rsi_14" in feats
    assert "atr_pct" in feats
    assert "avg_turnover_20d" in feats


def test_score_stocks_output():
    rows = []
    for i in range(30):
        rows.append({
            "symbol": f"{i:04d}.TW",
            "ret_5d": np.random.randn() / 100,
            "ret_10d": np.random.randn() / 100,
            "ret_20d": np.random.randn() / 100,
            "rsi_14": np.random.uniform(30, 70),
            "atr_pct": np.random.uniform(1, 5),
            "avg_turnover_20d": np.random.uniform(1e7, 5e8)
        })

    df = pd.DataFrame(rows)
    scored = score_stocks(df)
    assert "total_score" in scored.columns
    assert scored["total_score"].between(0, 100).all()
