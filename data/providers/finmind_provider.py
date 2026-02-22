from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable

import pandas as pd
import requests


def _normalize_date(date_value: str | datetime) -> str:
    if isinstance(date_value, datetime):
        return date_value.strftime("%Y-%m-%d")
    return str(date_value)


@dataclass
class FinMindConfig:
    token: str | None = None
    base_url: str = "https://api.finmindtrade.com/api/v4/data"
    timeout_sec: int = 12


class FinMindProvider:
    """
    Lightweight FinMind adapter for TW institutional/chip-related datasets.
    """

    FOREIGN_KEYS = ("Foreign", "外資")
    TRUST_KEYS = ("Investment_Trust", "投信")
    DEALER_KEYS = ("Dealer", "自營商")

    def __init__(self, config: FinMindConfig | None = None):
        self.config = config or FinMindConfig(token=os.getenv("FINMIND_TOKEN"))

    def _request_dataset(self, dataset: str, data_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        params = {
            "dataset": dataset,
            "data_id": data_id,
            "start_date": _normalize_date(start_date),
            "end_date": _normalize_date(end_date),
        }
        if self.config.token:
            params["token"] = self.config.token

        resp = requests.get(self.config.base_url, params=params, timeout=self.config.timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            return pd.DataFrame()
        data = payload.get("data")
        if not isinstance(data, list):
            return pd.DataFrame()
        if not data:
            return pd.DataFrame()
        frame = pd.DataFrame(data)
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"])
        return frame

    @staticmethod
    def _contains_any(text: str, keys: Iterable[str]) -> bool:
        return any(key in text for key in keys)

    def get_institutional_net_buy(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        raw = self._request_dataset(
            dataset="TaiwanStockInstitutionalInvestorsBuySell",
            data_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
        if raw.empty:
            return pd.DataFrame(
                columns=["Foreign_Net_Buy", "InvestmentTrust_Net_Buy", "Dealer_Net_Buy"],
                dtype=float,
            )

        investor_col = "name" if "name" in raw.columns else "investor"
        buy_col = "buy" if "buy" in raw.columns else None
        sell_col = "sell" if "sell" in raw.columns else None
        if investor_col not in raw.columns:
            return pd.DataFrame(index=raw["date"].drop_duplicates().sort_values())
        if buy_col is None or sell_col is None:
            if "buy_sell" in raw.columns:
                raw["net"] = pd.to_numeric(raw["buy_sell"], errors="coerce").fillna(0.0)
            else:
                return pd.DataFrame(index=raw["date"].drop_duplicates().sort_values())
        else:
            raw["net"] = pd.to_numeric(raw[buy_col], errors="coerce").fillna(0.0) - pd.to_numeric(raw[sell_col], errors="coerce").fillna(0.0)

        raw["investor_text"] = raw[investor_col].astype(str)
        raw["Foreign_Net_Buy"] = raw.apply(
            lambda row: row["net"] if self._contains_any(row["investor_text"], self.FOREIGN_KEYS) else 0.0,
            axis=1,
        )
        raw["InvestmentTrust_Net_Buy"] = raw.apply(
            lambda row: row["net"] if self._contains_any(row["investor_text"], self.TRUST_KEYS) else 0.0,
            axis=1,
        )
        raw["Dealer_Net_Buy"] = raw.apply(
            lambda row: row["net"] if self._contains_any(row["investor_text"], self.DEALER_KEYS) else 0.0,
            axis=1,
        )

        grouped = (
            raw.groupby("date")[["Foreign_Net_Buy", "InvestmentTrust_Net_Buy", "Dealer_Net_Buy"]]
            .sum(min_count=1)
            .sort_index()
        )
        grouped.index.name = "Date"
        return grouped

    @staticmethod
    def _parse_holding_level_weight(level: str) -> float:
        text = str(level).replace(",", "").strip()
        if not text:
            return 1.0
        if "以上" in text:
            digits = "".join(ch for ch in text if ch.isdigit())
            return float(digits) if digits else 2000.0
        if "-" in text:
            left, right = text.split("-", 1)
            left_digits = "".join(ch for ch in left if ch.isdigit())
            right_digits = "".join(ch for ch in right if ch.isdigit())
            if left_digits and right_digits:
                return (float(left_digits) + float(right_digits)) / 2.0
        digits = "".join(ch for ch in text if ch.isdigit())
        return float(digits) if digits else 1.0

    def get_chip_concentration_proxy(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        raw = self._request_dataset(
            dataset="TaiwanStockHoldingSharesPer",
            data_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
        if raw.empty:
            return pd.DataFrame(columns=["Chip_Concentration_Proxy"], dtype=float)

        level_col = "HoldingSharesLevel" if "HoldingSharesLevel" in raw.columns else "holding_shares_level"
        share_col = None
        for candidate in ("people", "percent", "ratio", "unit"):
            if candidate in raw.columns:
                share_col = candidate
                break
        if level_col not in raw.columns or share_col is None:
            return pd.DataFrame(index=raw["date"].drop_duplicates().sort_values(), columns=["Chip_Concentration_Proxy"]).fillna(0.0)

        raw["weight"] = raw[level_col].astype(str).map(self._parse_holding_level_weight)
        raw["share"] = pd.to_numeric(raw[share_col], errors="coerce").fillna(0.0)
        grouped = raw.groupby("date").apply(
            lambda sub: float((sub["weight"] * sub["share"]).sum() / sub["share"].sum())
            if sub["share"].sum() > 0
            else 0.0
        )
        frame = grouped.to_frame(name="Chip_Concentration_Proxy")
        frame.index.name = "Date"
        return frame.sort_index()
