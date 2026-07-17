from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import requests


TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_INSTITUTIONAL_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_ATTENTION_URL = "https://www.twse.com.tw/zh/announcement/notice"
TWSE_DISPOSITION_URL = "https://www.twse.com.tw/zh/announcement/punish"


class TwseStockClient:
    def __init__(
        self,
        base_url: str = TWSE_STOCK_DAY_URL,
        timeout_sec: int = 15,
        market_daily_url: str = TWSE_MI_INDEX_URL,
        institutional_url: str = TWSE_INSTITUTIONAL_T86_URL,
        attention_url: str = TWSE_ATTENTION_URL,
        disposition_url: str = TWSE_DISPOSITION_URL,
    ):
        self.base_url = str(base_url).strip()
        self.timeout_sec = int(timeout_sec)
        self.market_daily_url = str(market_daily_url).strip()
        self.institutional_url = str(institutional_url).strip()
        self.attention_url = str(attention_url).strip()
        self.disposition_url = str(disposition_url).strip()
        self._institutional_payload_cache: dict[date, dict[str, Any]] = {}

    def fetch_daily_bars(self, stock_id: str, start_date: date | str | datetime, end_date: date | str | datetime) -> list[dict[str, Any]]:
        symbol = str(stock_id or "").strip()
        if not symbol:
            return []
        start = self._to_date(start_date)
        end = self._to_date(end_date)
        rows_by_date: dict[date, dict[str, Any]] = {}
        for month in self._iter_months(start, end):
            response = requests.get(
                self.base_url,
                params={"date": month.strftime("%Y%m%d"), "stockNo": symbol, "response": "json"},
                headers={"accept": "application/json"},
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            payload = response.json()
            for row in self.parse_stock_day_payload(payload, symbol=symbol):
                row_date = row.get("date")
                if isinstance(row_date, date) and start <= row_date <= end:
                    rows_by_date[row_date] = row
        return [rows_by_date[key] for key in sorted(rows_by_date)]

    def fetch_daily_bars_by_date(
        self,
        trade_date: date | str | datetime,
        symbols: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        as_of = self._to_date(trade_date)
        if as_of is None:
            return []
        response = requests.get(
            self.market_daily_url,
            params={"date": as_of.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
            headers={"accept": "application/json"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return self.parse_mi_index_daily_payload(response.json(), trade_date=as_of, symbols=symbols)

    def fetch_institutional_chip(self, stock_id: str, start_date: date | str | datetime, end_date: date | str | datetime) -> list[dict[str, Any]]:
        symbol = str(stock_id or "").strip()
        if not symbol:
            return []
        start = self._to_date(start_date)
        end = self._to_date(end_date)
        if start is None or end is None:
            return []
        rows_by_date: dict[date, dict[str, Any]] = {}
        cursor = min(start, end)
        limit = max(start, end)
        while cursor <= limit:
            if cursor.weekday() < 5:
                payload = self._fetch_institutional_payload(cursor)
                for row in self.parse_t86_payload(payload, symbol=symbol, as_of=cursor):
                    row_date = row.get("date")
                    if isinstance(row_date, date) and start <= row_date <= end:
                        rows_by_date[row_date] = row
            cursor = date.fromordinal(cursor.toordinal() + 1)
        return [rows_by_date[key] for key in sorted(rows_by_date)]

    def fetch_institutional_chip_by_date(
        self,
        trade_date: date | str | datetime,
        symbols: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        as_of = self._to_date(trade_date)
        if as_of is None or as_of.weekday() >= 5:
            return []
        allow = {str(symbol or "").strip().upper() for symbol in (symbols or []) if str(symbol or "").strip()}
        payload = self._fetch_institutional_payload(as_of)
        rows = self.parse_t86_payload(payload, as_of=as_of)
        if allow:
            rows = [row for row in rows if str(row.get("symbol") or "").strip().upper() in allow]
        return rows

    def fetch_attention_stocks(self, trade_date: date | str | datetime) -> list[dict[str, Any]]:
        sync_date = self._to_date(trade_date)
        if sync_date is None:
            return []
        response = requests.get(
            self.attention_url,
            params={
                "response": "json",
                "startDate": sync_date.strftime("%Y%m%d"),
                "endDate": sync_date.strftime("%Y%m%d"),
                "stockNo": "",
                "sortKind": "STKNO",
                "querytype": "1",
                "selectType": "",
            },
            headers={"accept": "application/json"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return self.parse_attention_payload(response.json(), trade_date=sync_date)

    def fetch_disposition_periods(
        self,
        start_date: date | str | datetime,
        end_date: date | str | datetime,
    ) -> list[dict[str, Any]]:
        start = self._to_date(start_date)
        end = self._to_date(end_date)
        if start is None or end is None:
            return []
        response = requests.get(
            self.disposition_url,
            params={
                "response": "json",
                "startDate": min(start, end).strftime("%Y%m%d"),
                "endDate": max(start, end).strftime("%Y%m%d"),
            },
            headers={"accept": "application/json"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return self.parse_disposition_payload(response.json())

    @staticmethod
    def parse_stock_day_payload(payload: dict[str, Any], *, symbol: str = "") -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        stat = str(payload.get("stat") or "").strip().upper()
        if stat and stat != "OK":
            return []
        fields = [str(item).strip() for item in payload.get("fields") or []]
        data = payload.get("data") or []
        if not fields or not isinstance(data, list):
            return []

        def idx(*names: str) -> int | None:
            for name in names:
                if name in fields:
                    return fields.index(name)
            return None

        date_idx = idx("日期")
        volume_idx = idx("成交股數")
        open_idx = idx("開盤價")
        high_idx = idx("最高價")
        low_idx = idx("最低價")
        close_idx = idx("收盤價")
        if None in {date_idx, open_idx, high_idx, low_idx, close_idx}:
            return []

        rows: list[dict[str, Any]] = []
        for raw in data:
            if not isinstance(raw, list):
                continue
            max_idx = max(index for index in (date_idx, volume_idx, open_idx, high_idx, low_idx, close_idx) if index is not None)
            if len(raw) <= max_idx:
                continue
            bar_date = TwseStockClient._to_date(raw[date_idx])
            open_value = TwseStockClient._parse_number(raw[open_idx])
            high_value = TwseStockClient._parse_number(raw[high_idx])
            low_value = TwseStockClient._parse_number(raw[low_idx])
            close_value = TwseStockClient._parse_number(raw[close_idx])
            if bar_date is None or min(open_value, high_value, low_value, close_value) <= 0:
                continue
            rows.append(
                {
                    "date": bar_date,
                    "open": open_value,
                    "high": high_value,
                    "low": low_value,
                    "close": close_value,
                    "volume": TwseStockClient._parse_number(raw[volume_idx]) if volume_idx is not None and len(raw) > volume_idx else 0.0,
                    "source": "twse_stock_day",
                    "symbol": str(symbol or ""),
                }
            )
        rows.sort(key=lambda item: item["date"])
        return rows

    @staticmethod
    def parse_mi_index_daily_payload(
        payload: dict[str, Any],
        *,
        trade_date: date | str | datetime | None = None,
        symbols: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        stat = str(payload.get("stat") or "").strip().upper()
        if stat and stat != "OK":
            return []
        row_date = TwseStockClient._to_date(payload.get("date")) or TwseStockClient._to_date(trade_date)
        if row_date is None:
            return []
        allow = {str(symbol or "").strip().upper() for symbol in (symbols or []) if str(symbol or "").strip()}
        rows: list[dict[str, Any]] = []
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [TwseStockClient._normalize_field(item) for item in table.get("fields") or []]
            data = table.get("data") or []
            if not fields or not isinstance(data, list):
                continue
            symbol_idx = TwseStockClient._field_index(fields, "證券代號")
            volume_idx = TwseStockClient._field_index(fields, "成交股數")
            open_idx = TwseStockClient._field_index(fields, "開盤價", "開盤")
            high_idx = TwseStockClient._field_index(fields, "最高價", "最高")
            low_idx = TwseStockClient._field_index(fields, "最低價", "最低")
            close_idx = TwseStockClient._field_index(fields, "收盤價", "收盤")
            if None in {symbol_idx, open_idx, high_idx, low_idx, close_idx}:
                continue
            max_idx = max(index for index in (symbol_idx, volume_idx, open_idx, high_idx, low_idx, close_idx) if index is not None)
            for raw in data:
                if not isinstance(raw, list) or len(raw) <= max_idx:
                    continue
                symbol = str(raw[symbol_idx] or "").strip().upper()
                if not symbol:
                    continue
                if allow:
                    if symbol not in allow:
                        continue
                elif not re.fullmatch(r"\d{4}", symbol):
                    continue
                open_value = TwseStockClient._parse_number(raw[open_idx])
                high_value = TwseStockClient._parse_number(raw[high_idx])
                low_value = TwseStockClient._parse_number(raw[low_idx])
                close_value = TwseStockClient._parse_number(raw[close_idx])
                if min(open_value, high_value, low_value, close_value) <= 0:
                    continue
                rows.append(
                    {
                        "date": row_date,
                        "open": open_value,
                        "high": high_value,
                        "low": low_value,
                        "close": close_value,
                        "volume": TwseStockClient._parse_number(raw[volume_idx]) if volume_idx is not None else 0.0,
                        "source": "twse_mi_index",
                        "symbol": symbol,
                    }
                )
        rows.sort(key=lambda item: (item["date"], item["symbol"]))
        return rows

    @staticmethod
    def parse_t86_payload(payload: dict[str, Any], *, symbol: str = "", as_of: date | str | datetime | None = None) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        stat = str(payload.get("stat") or "").strip().upper()
        if stat and stat != "OK":
            return []
        fields = [TwseStockClient._normalize_field(item) for item in payload.get("fields") or []]
        data = payload.get("data") or []
        if not fields or not isinstance(data, list):
            return []

        def idx(*names: str, last: bool = False) -> int | None:
            normalized = [TwseStockClient._normalize_field(name) for name in names]
            matches = [index for index, field in enumerate(fields) if field in normalized]
            if matches:
                return matches[-1] if last else matches[0]
            for index, field in enumerate(fields):
                if any(name and name in field for name in normalized):
                    return index
            return None

        symbol_idx = idx("證券代號")
        foreign_idx = idx(
            "外陸資買賣超股數(不含外資自營商)",
            "外資及陸資買賣超股數(不含外資自營商)",
            "外資買賣超股數(不含外資自營商)",
        )
        foreign_dealer_idx = idx("外資自營商買賣超股數")
        investment_idx = idx("投信買賣超股數")
        dealer_idx = idx("自營商買賣超股數")
        if symbol_idx is None or foreign_idx is None or investment_idx is None or dealer_idx is None:
            return []

        row_date = TwseStockClient._to_date(payload.get("date")) or TwseStockClient._to_date(as_of)
        if row_date is None:
            return []
        requested_symbol = str(symbol or "").strip()
        rows: list[dict[str, Any]] = []
        max_idx = max(index for index in (symbol_idx, foreign_idx, foreign_dealer_idx, investment_idx, dealer_idx) if index is not None)
        for raw in data:
            if not isinstance(raw, list) or len(raw) <= max_idx:
                continue
            row_symbol = str(raw[symbol_idx] or "").strip()
            if requested_symbol and row_symbol != requested_symbol:
                continue
            foreign_net = TwseStockClient._parse_number(raw[foreign_idx])
            if foreign_dealer_idx is not None and len(raw) > foreign_dealer_idx:
                foreign_net += TwseStockClient._parse_number(raw[foreign_dealer_idx])
            rows.append(
                {
                    "date": row_date,
                    "foreign_net_buy": foreign_net,
                    "investment_trust_net_buy": TwseStockClient._parse_number(raw[investment_idx]),
                    "dealer_net_buy": TwseStockClient._parse_number(raw[dealer_idx]),
                    "source": "twse_t86",
                    "symbol": row_symbol,
                }
            )
        rows.sort(key=lambda item: (item["date"], item["symbol"]))
        return rows

    @staticmethod
    def parse_attention_payload(
        payload: dict[str, Any],
        *,
        trade_date: date | str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        stat = str(payload.get("stat") or "").strip().upper()
        if stat and stat != "OK":
            return []
        fields = [TwseStockClient._normalize_field(item) for item in payload.get("fields") or []]
        data = payload.get("data") or []
        if not fields or not isinstance(data, list):
            return []
        symbol_idx = TwseStockClient._field_index(fields, "證券代號")
        reason_idx = TwseStockClient._field_index(fields, "注意交易資訊")
        date_idx = TwseStockClient._field_index(fields, "日期")
        close_idx = TwseStockClient._field_index(fields, "收盤價")
        if symbol_idx is None or reason_idx is None:
            return []
        max_idx = max(index for index in (symbol_idx, reason_idx, date_idx, close_idx) if index is not None)
        fallback_date = TwseStockClient._to_date(trade_date)
        rows: list[dict[str, Any]] = []
        for raw in data:
            if not isinstance(raw, list) or len(raw) <= max_idx:
                continue
            symbol = str(raw[symbol_idx] or "").strip().upper()
            if not symbol:
                continue
            row_date = TwseStockClient._to_date(raw[date_idx]) if date_idx is not None else fallback_date
            row_date = row_date or fallback_date
            if row_date is None:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": row_date,
                    "announced_date": row_date,
                    "close": TwseStockClient._parse_number(raw[close_idx]) if close_idx is not None else 0.0,
                    "reason": TwseStockClient._clean_text(raw[reason_idx]),
                    "source": "twse_notice",
                }
            )
        rows.sort(key=lambda item: (item["trade_date"], item["symbol"]))
        return rows

    @staticmethod
    def parse_disposition_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        stat = str(payload.get("stat") or "").strip().upper()
        if stat and stat != "OK":
            return []
        fields = [TwseStockClient._normalize_field(item) for item in payload.get("fields") or []]
        data = payload.get("data") or []
        if not fields or not isinstance(data, list):
            return []
        symbol_idx = TwseStockClient._field_index(fields, "證券代號")
        period_idx = TwseStockClient._field_index(fields, "處置起迄時間", "處置起訖時間")
        measure_idx = TwseStockClient._field_index(fields, "處置措施")
        condition_idx = TwseStockClient._field_index(fields, "處置條件")
        content_idx = TwseStockClient._field_index(fields, "處置內容")
        if symbol_idx is None or period_idx is None:
            return []
        max_idx = max(index for index in (symbol_idx, period_idx, measure_idx, condition_idx, content_idx) if index is not None)
        rows: list[dict[str, Any]] = []
        for raw in data:
            if not isinstance(raw, list) or len(raw) <= max_idx:
                continue
            symbol = str(raw[symbol_idx] or "").strip().upper()
            start, end = TwseStockClient._parse_period_dates(raw[period_idx])
            if not symbol or start is None or end is None:
                continue
            disposition_type = TwseStockClient._clean_text(raw[measure_idx]) if measure_idx is not None else "處置有價證券"
            condition = TwseStockClient._clean_text(raw[condition_idx]) if condition_idx is not None else ""
            content = TwseStockClient._clean_text(raw[content_idx]) if content_idx is not None else ""
            reason = "；".join(part for part in (condition, content) if part)
            rows.append(
                {
                    "symbol": symbol,
                    "start_date": start,
                    "end_date": end,
                    "disposition_type": disposition_type,
                    "reason": reason,
                    "source": "twse_punish",
                }
            )
        rows.sort(key=lambda item: (item["start_date"], item["symbol"]))
        return rows

    def _fetch_institutional_payload(self, trade_date: date) -> dict[str, Any]:
        if trade_date not in self._institutional_payload_cache:
            response = requests.get(
                self.institutional_url,
                params={"date": trade_date.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"},
                headers={"accept": "application/json"},
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            self._institutional_payload_cache[trade_date] = response.json()
        return self._institutional_payload_cache[trade_date]

    @staticmethod
    def _iter_months(start: date, end: date):
        cursor = date(start.year, start.month, 1)
        limit = date(end.year, end.month, 1)
        while cursor <= limit:
            yield cursor
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

    @staticmethod
    def _to_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = re.sub(r"<[^>]+>", "", str(value or "")).strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except Exception:
                continue
        roc_match = re.search(r"(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})", text)
        if roc_match:
            year = int(roc_match.group(1)) + 1911
            return date(year, int(roc_match.group(2)), int(roc_match.group(3)))
        return None

    @staticmethod
    def _parse_number(value: Any) -> float:
        text = re.sub(r"<[^>]+>", "", str(value if value is not None else "")).replace(",", "").strip()
        if text in {"", "-", "--", "X", "x"}:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    @staticmethod
    def _normalize_field(value: Any) -> str:
        return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", str(value or ""))).strip()

    @staticmethod
    def _field_index(fields: list[str], *names: str, last: bool = False) -> int | None:
        normalized = [TwseStockClient._normalize_field(name) for name in names]
        matches = [index for index, field in enumerate(fields) if field in normalized]
        if matches:
            return matches[-1] if last else matches[0]
        matches = [index for index, field in enumerate(fields) if any(name and name in field for name in normalized)]
        if matches:
            return matches[-1] if last else matches[0]
        return None

    @staticmethod
    def _clean_text(value: Any) -> str:
        text = re.sub(r"<br\s*/?>", "\n", str(value if value is not None else ""), flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _parse_period_dates(value: Any) -> tuple[date | None, date | None]:
        text = TwseStockClient._clean_text(value)
        matches = re.findall(r"\d{2,4}[./-]\d{1,2}[./-]\d{1,2}", text)
        if len(matches) >= 2:
            return TwseStockClient._to_date(matches[0]), TwseStockClient._to_date(matches[1])
        parts = re.split(r"[~～]", text)
        if len(parts) >= 2:
            return TwseStockClient._to_date(parts[0]), TwseStockClient._to_date(parts[1])
        single = TwseStockClient._to_date(text)
        return single, single
