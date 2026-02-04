import pandas as pd
import numpy as np

from web_report_strategy2 import SimpleWebReportAnalyzer


class DummyTicker:
    def __init__(self, *_args, **_kwargs):
        self.info = {"shortName": "Dummy"}


def make_df(rows=120):
    dates = pd.date_range(start="2022-01-01", periods=rows, freq="B")
    prices = np.linspace(50, 60, rows) + np.random.normal(0, 0.5, rows)
    df = pd.DataFrame({
        "Open": prices,
        "High": prices * 1.01,
        "Low": prices * 0.99,
        "Close": prices,
        "Volume": np.random.randint(1000, 5000, rows)
    }, index=dates)
    return df


def test_simple_web_report_analyzer_no_exceptions(monkeypatch):
    analyzer = SimpleWebReportAnalyzer()
    df = make_df()

    # Avoid network calls
    monkeypatch.setattr(analyzer.market_data, "load_data", lambda *args, **kwargs: df)
    monkeypatch.setattr("web_report_strategy2.yf.Ticker", lambda *args, **kwargs: DummyTicker())

    results = analyzer.analyze_stock_list(["2330.TW"], "2022-01-01", "2022-12-31")
    assert not results.empty
    for col in ["symbol", "name", "score", "volatility", "avg_return", "win_rate", "num_trades", "total_return"]:
        assert col in results.columns
