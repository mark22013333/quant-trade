from __future__ import annotations

import pandas as pd

from data.data_facade import DataFacade


class DummyMarketData:
    def load_data(self, symbol, start_date, end_date, interval="1d"):  # noqa: ANN001, ARG002
        idx = pd.date_range("2024-01-02", periods=80, freq="B")
        close = pd.Series([100 + i * 0.2 for i in range(len(idx))], index=idx, dtype=float)
        return pd.DataFrame(
            {
                "Open": close * 0.999,
                "High": close * 1.002,
                "Low": close * 0.998,
                "Close": close,
                "Volume": 2000.0,
            },
            index=idx,
        )


class FailingChipProvider:
    def load_chip_features(self, symbol, start_date, end_date):  # noqa: ANN001, ARG002
        raise RuntimeError("chip provider down")


def test_data_facade_marks_chip_data_degraded_when_chip_provider_fails():
    facade = DataFacade(market_data=DummyMarketData(), chip_provider=FailingChipProvider())
    df = facade.load_for_strategy(symbol="2330.TW", start_date="2024-01-01", end_date="2024-06-01", market="TW")

    assert not df.empty
    assert "chip_data_status" in df.columns
    assert set(df["chip_data_status"].unique()) == {"degraded"}
    assert "Foreign_Net_Buy" in df.columns
    assert "InvestmentTrust_Net_Buy" in df.columns
    assert "Chip_Concentration_Proxy" in df.columns
