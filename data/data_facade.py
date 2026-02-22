from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from data.chip_data_provider import ChipDataProvider
from data.market_data import MarketData
from data.providers.yfinance_provider import YFinanceProvider


class DataFacade:
    """
    Unified data loader:
    - OHLCV from MarketData (local cache + yfinance)
    - TW chip features from FinMind-based provider
    """

    def __init__(self, market_data: MarketData | None = None, chip_provider: ChipDataProvider | None = None):
        self.market_data = market_data or MarketData(provider=YFinanceProvider())
        self.chip_provider = chip_provider or ChipDataProvider()

    @staticmethod
    def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index, errors="coerce")
        out = out[~out.index.isna()]
        out = out.sort_index()
        out.index = out.index.tz_localize(None) if out.index.tz is not None else out.index
        return out

    def load_for_strategy(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        market: str = "TW",
        interval: str = "1d",
        include_chip: bool = True,
    ) -> pd.DataFrame:
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        ohlcv = self.market_data.load_data(symbol=symbol, start_date=start_date, end_date=end_date, interval=interval)
        if ohlcv is None or ohlcv.empty:
            return pd.DataFrame()

        ohlcv = self._normalize_index(ohlcv)
        required = ["Open", "High", "Low", "Close", "Volume"]
        missing = [col for col in required if col not in ohlcv.columns]
        if missing:
            raise ValueError(f"OHLCV missing columns: {', '.join(missing)}")

        output = ohlcv.copy()

        if include_chip and str(market).upper() == "TW":
            try:
                chip = self.chip_provider.load_chip_features(symbol, start_date, end_date)
                if chip is not None and not chip.empty:
                    chip = self._normalize_index(chip)
                    output = output.join(chip, how="left")
            except Exception:
                # Keep strategy pipeline available even when external chip API is unavailable.
                pass

        for col in ("Foreign_Net_Buy", "InvestmentTrust_Net_Buy", "Dealer_Net_Buy"):
            if col not in output.columns:
                output[col] = 0.0
            output[col] = pd.to_numeric(output[col], errors="coerce").fillna(0.0)
        if "Chip_Concentration_Proxy" not in output.columns:
            output["Chip_Concentration_Proxy"] = 0.0
        output["Chip_Concentration_Proxy"] = pd.to_numeric(output["Chip_Concentration_Proxy"], errors="coerce").ffill().fillna(0.0)

        return output

