from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Any

import requests


TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

TWSE_INDUSTRY_INDEX_MAP = {
    "水泥工業": "水泥類指數",
    "食品工業": "食品類指數",
    "塑膠工業": "塑膠類指數",
    "紡織纖維": "紡織纖維類指數",
    "電機機械": "電機機械類指數",
    "電器電纜": "電器電纜類指數",
    "化學生技醫療": "化學生技醫療類指數",
    "化學工業": "化學類指數",
    "生技醫療業": "生技醫療類指數",
    "玻璃陶瓷": "玻璃陶瓷類指數",
    "造紙工業": "造紙類指數",
    "鋼鐵工業": "鋼鐵類指數",
    "橡膠工業": "橡膠類指數",
    "汽車工業": "汽車類指數",
    "電子工業": "電子工業類指數",
    "半導體業": "半導體類指數",
    "電腦及週邊設備業": "電腦及週邊設備類指數",
    "光電業": "光電類指數",
    "通信網路業": "通信網路類指數",
    "電子零組件業": "電子零組件類指數",
    "電子通路業": "電子通路類指數",
    "資訊服務業": "資訊服務類指數",
    "其他電子業": "其他電子類指數",
    "建材營造": "建材營造類指數",
    "航運業": "航運類指數",
    "觀光餐旅": "觀光餐旅類指數",
    "金融保險": "金融保險類指數",
    "金融保險業": "金融保險類指數",
    "貿易百貨": "貿易百貨類指數",
    "油電燃氣業": "油電燃氣類指數",
    "綠能環保": "綠能環保類指數",
    "數位雲端": "數位雲端類指數",
    "運動休閒": "運動休閒類指數",
    "居家生活": "居家生活類指數",
    "其他": "其他類指數",
}

TWSE_INDEX_INDUSTRY_MAP = {index_name: industry for industry, index_name in TWSE_INDUSTRY_INDEX_MAP.items()}


def twse_index_name_for_industry(industry: str) -> str:
    text = str(industry or "").strip()
    if not text:
        return ""
    if text in TWSE_INDUSTRY_INDEX_MAP:
        return TWSE_INDUSTRY_INDEX_MAP[text]
    compact = text.replace("工業", "").replace("業", "")
    for key, index_name in TWSE_INDUSTRY_INDEX_MAP.items():
        if key.replace("工業", "").replace("業", "") == compact:
            return index_name
    return f"{compact}類指數" if compact else ""


def industry_for_twse_index_name(index_name: str) -> str:
    text = str(index_name or "").strip()
    if not text:
        return ""
    if text in TWSE_INDEX_INDUSTRY_MAP:
        return TWSE_INDEX_INDUSTRY_MAP[text]
    if text.endswith("類指數"):
        return text[: -len("類指數")]
    return ""


class TwseIndexClient:
    def __init__(self, base_url: str = TWSE_MI_INDEX_URL, timeout_sec: int = 15, retry_count: int = 2, retry_sleep_sec: float = 0.2):
        self.base_url = str(base_url).strip()
        self.timeout_sec = int(timeout_sec)
        self.retry_count = max(0, int(retry_count))
        self.retry_sleep_sec = max(0.0, float(retry_sleep_sec))

    def fetch_industry_index_bars(self, trade_date: date | str | datetime) -> list[dict[str, Any]]:
        day = self._to_date(trade_date)
        payload = self._fetch_json(
            params={"date": day.strftime("%Y%m%d"), "type": "IND", "response": "json"},
            context=f"TWSE MI_INDEX IND {day.isoformat()}",
        )
        if str(payload.get("stat", "")).upper() != "OK":
            return []
        return self.parse_industry_index_payload(payload, trade_date=day)

    def _fetch_json(self, *, params: dict[str, str], context: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            response = requests.get(
                self.base_url,
                params=params,
                headers={
                    "accept": "application/json,text/plain,*/*",
                    "user-agent": "Mozilla/5.0 quant-trade/1.0 (+https://www.twse.com.tw/)",
                },
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                last_error = exc
                if attempt < self.retry_count:
                    time.sleep(self.retry_sleep_sec)
                    continue
                snippet = re.sub(r"\s+", " ", str(getattr(response, "text", "") or ""))[:160]
                raise RuntimeError(f"{context} returned non-JSON response: {snippet}") from exc
            if isinstance(payload, dict):
                return payload
            last_error = TypeError(f"{context} returned {type(payload).__name__}, expected dict")
            if attempt < self.retry_count:
                time.sleep(self.retry_sleep_sec)
                continue
        raise RuntimeError(str(last_error or f"{context} returned no payload"))

    @staticmethod
    def parse_industry_index_payload(payload: dict[str, Any], *, trade_date: date | str | datetime) -> list[dict[str, Any]]:
        day = TwseIndexClient._to_date(trade_date)
        rows: list[dict[str, Any]] = []
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            title = str(table.get("title") or "")
            fields = [str(item) for item in table.get("fields") or []]
            if "價格指數(臺灣證券交易所)" not in title:
                continue
            if "指數" not in fields or "收盤指數" not in fields:
                continue
            name_idx = fields.index("指數")
            close_idx = fields.index("收盤指數")
            change_idx = fields.index("漲跌點數") if "漲跌點數" in fields else None
            pct_idx = fields.index("漲跌百分比(%)") if "漲跌百分比(%)" in fields else None
            for raw in table.get("data") or []:
                if not isinstance(raw, list) or len(raw) <= max(name_idx, close_idx):
                    continue
                index_name = str(raw[name_idx] or "").strip()
                industry = industry_for_twse_index_name(index_name)
                if not industry:
                    continue
                close = TwseIndexClient._parse_number(raw[close_idx])
                if close <= 0:
                    continue
                rows.append(
                    {
                        "date": day,
                        "index_name": index_name,
                        "industry": industry,
                        "close": close,
                        "change": TwseIndexClient._parse_number(raw[change_idx]) if change_idx is not None and len(raw) > change_idx else 0.0,
                        "change_pct": TwseIndexClient._parse_number(raw[pct_idx]) / 100.0 if pct_idx is not None and len(raw) > pct_idx else 0.0,
                        "source": "twse_mi_index",
                    }
                )
        return rows

    @staticmethod
    def _to_date(value: date | str | datetime) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except Exception:
                continue
        raise ValueError(f"invalid date: {value}")

    @staticmethod
    def _parse_number(value: Any) -> float:
        text = re.sub(r"<[^>]+>", "", str(value if value is not None else "")).replace(",", "").strip()
        if text in {"", "--", "-"}:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0
