from __future__ import annotations

import pandas as pd

from data.providers.finmind_provider import FinMindProvider


class ChipDataProvider:
    """Facade for TW chip-related features."""

    def __init__(self, provider: FinMindProvider | None = None):
        self.provider = provider or FinMindProvider()

    @staticmethod
    def normalize_stock_id(symbol: str) -> str:
        code = (symbol or "").upper().strip()
        for suffix in (".TW", ".TWO"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
        return code

    def load_chip_features(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        stock_id = self.normalize_stock_id(symbol)
        inst = self.provider.get_institutional_net_buy(stock_id, start_date, end_date)
        concentration = self.provider.get_chip_concentration_proxy(stock_id, start_date, end_date)

        if inst.empty and concentration.empty:
            return pd.DataFrame()

        merged = inst.join(concentration, how="outer")
        merged = merged.sort_index()
        for col in ("Foreign_Net_Buy", "InvestmentTrust_Net_Buy", "Dealer_Net_Buy"):
            if col not in merged.columns:
                merged[col] = 0.0
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)
        if "Chip_Concentration_Proxy" not in merged.columns:
            merged["Chip_Concentration_Proxy"] = 0.0
        merged["Chip_Concentration_Proxy"] = pd.to_numeric(merged["Chip_Concentration_Proxy"], errors="coerce").ffill().fillna(0.0)
        return merged

