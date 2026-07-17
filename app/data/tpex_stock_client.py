from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import requests


TPEX_TRADING_STOCK_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
TPEX_QFII_STAT_URL = "https://www.tpex.org.tw/www/zh-tw/insti/qfiiStat"
TPEX_SITC_STAT_URL = "https://www.tpex.org.tw/www/zh-tw/insti/sitcStat"
TPEX_DEALER_STAT_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dealerStat"
TPEX_ATTENTION_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/attention"
TPEX_DISPOSITION_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal"


class TpexStockClient:
    def __init__(
        self,
        base_url: str = TPEX_TRADING_STOCK_URL,
        timeout_sec: int = 15,
        daily_quotes_url: str = TPEX_DAILY_QUOTES_URL,
        qfii_url: str = TPEX_QFII_STAT_URL,
        sitc_url: str = TPEX_SITC_STAT_URL,
        dealer_url: str = TPEX_DEALER_STAT_URL,
        attention_url: str = TPEX_ATTENTION_URL,
        disposition_url: str = TPEX_DISPOSITION_URL,
    ):
        self.base_url = str(base_url).strip()
        self.timeout_sec = int(timeout_sec)
        self.daily_quotes_url = str(daily_quotes_url).strip()
        self.qfii_url = str(qfii_url).strip()
        self.sitc_url = str(sitc_url).strip()
        self.dealer_url = str(dealer_url).strip()
        self.attention_url = str(attention_url).strip()
        self.disposition_url = str(disposition_url).strip()
        self._institutional_payload_cache: dict[tuple[str, date, str], dict[str, Any]] = {}

    def fetch_daily_bars(self, stock_id: str, start_date: date | str | datetime, end_date: date | str | datetime) -> list[dict[str, Any]]:
        symbol = str(stock_id or "").strip()
        if not symbol:
            return []
        start = self._to_date(start_date)
        end = self._to_date(end_date)
        if start is None or end is None:
            return []
        rows_by_date: dict[date, dict[str, Any]] = {}
        for month in self._iter_months(start, end):
            response = requests.post(
                self.base_url,
                data={"code": symbol, "date": month.strftime("%Y/%m/%d"), "response": "json"},
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            payload = response.json()
            for row in self.parse_trading_stock_payload(payload, symbol=symbol):
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
            self.daily_quotes_url,
            params={"date": as_of.strftime("%Y/%m/%d"), "type": "EW", "response": "json"},
            headers={"accept": "application/json"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return self.parse_daily_quotes_payload(response.json(), trade_date=as_of, symbols=symbols)

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
                foreign = self._fetch_institutional_category("foreign", cursor, symbol)
                investment = self._fetch_institutional_category("investment", cursor, symbol)
                dealer = self._fetch_institutional_category("dealer", cursor, symbol)
                if foreign or investment or dealer:
                    rows_by_date[cursor] = {
                        "date": cursor,
                        "foreign_net_buy": float(foreign.get(symbol, 0.0)),
                        "investment_trust_net_buy": float(investment.get(symbol, 0.0)),
                        "dealer_net_buy": float(dealer.get(symbol, 0.0)),
                        "source": "tpex_institutional",
                        "symbol": symbol,
                    }
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
        foreign = self._fetch_institutional_category_by_date("foreign", as_of)
        investment = self._fetch_institutional_category_by_date("investment", as_of)
        dealer = self._fetch_institutional_category_by_date("dealer", as_of)
        all_symbols = sorted(set(foreign) | set(investment) | set(dealer))
        if allow:
            all_symbols = [symbol for symbol in all_symbols if symbol in allow]
        else:
            all_symbols = [symbol for symbol in all_symbols if re.fullmatch(r"\d{4}", symbol)]
        return [
            {
                "date": as_of,
                "foreign_net_buy": float(foreign.get(symbol, 0.0)),
                "investment_trust_net_buy": float(investment.get(symbol, 0.0)),
                "dealer_net_buy": float(dealer.get(symbol, 0.0)),
                "source": "tpex_institutional",
                "symbol": symbol,
            }
            for symbol in all_symbols
        ]

    def fetch_attention_stocks(self, trade_date: date | str | datetime) -> list[dict[str, Any]]:
        sync_date = self._to_date(trade_date)
        if sync_date is None:
            return []
        response = requests.get(
            self.attention_url,
            params={
                "startDate": sync_date.strftime("%Y/%m/%d"),
                "endDate": sync_date.strftime("%Y/%m/%d"),
                "type": "all",
                "order": "date",
                "response": "json",
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
                "startDate": min(start, end).strftime("%Y/%m/%d"),
                "endDate": max(start, end).strftime("%Y/%m/%d"),
                "type": "all",
                "reason": "-1",
                "measure": "-1",
                "order": "date",
                "response": "json",
            },
            headers={"accept": "application/json"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return self.parse_disposition_payload(response.json())

    @staticmethod
    def parse_trading_stock_payload(payload: dict[str, Any], *, symbol: str = "") -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
            return []
        rows: list[dict[str, Any]] = []
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [re.sub(r"\s+", "", str(item or "")) for item in table.get("fields") or []]
            data = table.get("data") or []
            if not fields or not isinstance(data, list):
                continue

            def idx(*names: str) -> int | None:
                normalized = {re.sub(r"\s+", "", name): name for name in names}
                for index, field in enumerate(fields):
                    if field in normalized:
                        return index
                return None

            date_idx = idx("日期")
            volume_idx = idx("成交仟股", "成交股數")
            open_idx = idx("開盤", "開盤價")
            high_idx = idx("最高", "最高價")
            low_idx = idx("最低", "最低價")
            close_idx = idx("收盤", "收盤價")
            if None in {date_idx, open_idx, high_idx, low_idx, close_idx}:
                continue
            volume_multiplier = 1000.0 if volume_idx is not None and "仟股" in fields[volume_idx] else 1.0
            for raw in data:
                if not isinstance(raw, list):
                    continue
                max_idx = max(index for index in (date_idx, volume_idx, open_idx, high_idx, low_idx, close_idx) if index is not None)
                if len(raw) <= max_idx:
                    continue
                bar_date = TpexStockClient._to_date(raw[date_idx])
                open_value = TpexStockClient._parse_number(raw[open_idx])
                high_value = TpexStockClient._parse_number(raw[high_idx])
                low_value = TpexStockClient._parse_number(raw[low_idx])
                close_value = TpexStockClient._parse_number(raw[close_idx])
                if bar_date is None or min(open_value, high_value, low_value, close_value) <= 0:
                    continue
                volume = TpexStockClient._parse_number(raw[volume_idx]) * volume_multiplier if volume_idx is not None else 0.0
                rows.append(
                    {
                        "date": bar_date,
                        "open": open_value,
                        "high": high_value,
                        "low": low_value,
                        "close": close_value,
                        "volume": volume,
                        "source": "tpex_trading_stock",
                        "symbol": str(symbol or ""),
                    }
                )
        rows.sort(key=lambda item: item["date"])
        return rows

    @staticmethod
    def parse_daily_quotes_payload(
        payload: dict[str, Any],
        *,
        trade_date: date | str | datetime | None = None,
        symbols: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
            return []
        row_date = TpexStockClient._to_date(payload.get("date")) or TpexStockClient._to_date(trade_date)
        if row_date is None:
            return []
        allow = {str(symbol or "").strip().upper() for symbol in (symbols or []) if str(symbol or "").strip()}
        rows: list[dict[str, Any]] = []
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [TpexStockClient._normalize_field(item) for item in table.get("fields") or []]
            data = table.get("data") or []
            if not fields or not isinstance(data, list):
                continue
            symbol_idx = TpexStockClient._field_index(fields, "代號")
            volume_idx = TpexStockClient._field_index(fields, "成交股數")
            open_idx = TpexStockClient._field_index(fields, "開盤", "開盤價")
            high_idx = TpexStockClient._field_index(fields, "最高", "最高價")
            low_idx = TpexStockClient._field_index(fields, "最低", "最低價")
            close_idx = TpexStockClient._field_index(fields, "收盤", "收盤價")
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
                open_value = TpexStockClient._parse_number(raw[open_idx])
                high_value = TpexStockClient._parse_number(raw[high_idx])
                low_value = TpexStockClient._parse_number(raw[low_idx])
                close_value = TpexStockClient._parse_number(raw[close_idx])
                if min(open_value, high_value, low_value, close_value) <= 0:
                    continue
                rows.append(
                    {
                        "date": row_date,
                        "open": open_value,
                        "high": high_value,
                        "low": low_value,
                        "close": close_value,
                        "volume": TpexStockClient._parse_number(raw[volume_idx]) if volume_idx is not None else 0.0,
                        "source": "tpex_daily_quotes",
                        "symbol": symbol,
                    }
                )
        rows.sort(key=lambda item: (item["date"], item["symbol"]))
        return rows

    @staticmethod
    def parse_institutional_payload(
        payload: dict[str, Any],
        *,
        symbol: str = "",
        as_of: date | str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
            return []
        requested_symbol = str(symbol or "").strip()
        payload_date = TpexStockClient._to_date(payload.get("date")) or TpexStockClient._to_date(as_of)
        if payload_date is None:
            return []
        rows_by_symbol: dict[str, dict[str, Any]] = {}
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [TpexStockClient._normalize_field(item) for item in table.get("fields") or []]
            data = table.get("data") or []
            if not fields or not isinstance(data, list):
                continue
            symbol_idx = TpexStockClient._field_index(fields, "代號")
            net_idx = TpexStockClient._field_index(fields, "買賣超(張數)", last=True)
            if symbol_idx is None or net_idx is None:
                continue
            max_idx = max(symbol_idx, net_idx)
            for raw in data:
                if not isinstance(raw, list) or len(raw) <= max_idx:
                    continue
                row_symbol = str(raw[symbol_idx] or "").strip()
                if requested_symbol and row_symbol != requested_symbol:
                    continue
                rows_by_symbol[row_symbol] = {
                    "date": payload_date,
                    "symbol": row_symbol,
                    "net_buy": TpexStockClient._parse_number(raw[net_idx]) * 1000.0,
                }
        return [rows_by_symbol[key] for key in sorted(rows_by_symbol)]

    @staticmethod
    def parse_attention_payload(
        payload: dict[str, Any],
        *,
        trade_date: date | str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
            return []
        fallback_date = TpexStockClient._to_date(trade_date)
        rows: list[dict[str, Any]] = []
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [TpexStockClient._normalize_field(item) for item in table.get("fields") or []]
            data = table.get("data") or []
            if not fields or not isinstance(data, list):
                continue
            symbol_idx = TpexStockClient._field_index(fields, "證券代號")
            reason_idx = TpexStockClient._field_index(fields, "注意交易資訊")
            date_idx = TpexStockClient._field_index(fields, "公告日期")
            close_idx = TpexStockClient._field_index(fields, "收盤價")
            if symbol_idx is None or reason_idx is None:
                continue
            max_idx = max(index for index in (symbol_idx, reason_idx, date_idx, close_idx) if index is not None)
            for raw in data:
                if not isinstance(raw, list) or len(raw) <= max_idx:
                    continue
                symbol = str(raw[symbol_idx] or "").strip().upper()
                if not symbol:
                    continue
                row_date = TpexStockClient._to_date(raw[date_idx]) if date_idx is not None else fallback_date
                row_date = row_date or fallback_date
                if row_date is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "trade_date": row_date,
                        "announced_date": row_date,
                        "close": TpexStockClient._parse_number(raw[close_idx]) if close_idx is not None else 0.0,
                        "reason": TpexStockClient._clean_text(raw[reason_idx]),
                        "source": "tpex_attention",
                    }
                )
        rows.sort(key=lambda item: (item["trade_date"], item["symbol"]))
        return rows

    @staticmethod
    def parse_disposition_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
            return []
        rows: list[dict[str, Any]] = []
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [TpexStockClient._normalize_field(item) for item in table.get("fields") or []]
            data = table.get("data") or []
            if not fields or not isinstance(data, list):
                continue
            symbol_idx = TpexStockClient._field_index(fields, "證券代號")
            period_idx = TpexStockClient._field_index(fields, "處置起訖時間", "處置起迄時間")
            reason_idx = TpexStockClient._field_index(fields, "處置原因")
            content_idx = TpexStockClient._field_index(fields, "處置內容")
            if symbol_idx is None or period_idx is None:
                continue
            max_idx = max(index for index in (symbol_idx, period_idx, reason_idx, content_idx) if index is not None)
            for raw in data:
                if not isinstance(raw, list) or len(raw) <= max_idx:
                    continue
                symbol = str(raw[symbol_idx] or "").strip().upper()
                start, end = TpexStockClient._parse_period_dates(raw[period_idx])
                if not symbol or start is None or end is None:
                    continue
                disposition_type = TpexStockClient._clean_text(raw[reason_idx]) if reason_idx is not None else "處置有價證券"
                content = TpexStockClient._clean_text(raw[content_idx]) if content_idx is not None else ""
                rows.append(
                    {
                        "symbol": symbol,
                        "start_date": start,
                        "end_date": end,
                        "disposition_type": disposition_type,
                        "reason": content,
                        "source": "tpex_disposal",
                    }
                )
        rows.sort(key=lambda item: (item["start_date"], item["symbol"]))
        return rows

    def _fetch_institutional_category(self, category: str, trade_date: date, symbol: str) -> dict[str, float]:
        return {
            row_symbol: net_buy
            for row_symbol, net_buy in self._fetch_institutional_category_by_date(category, trade_date).items()
            if row_symbol == symbol
        }

    def _fetch_institutional_category_by_date(self, category: str, trade_date: date) -> dict[str, float]:
        specs = {
            "foreign": (self.qfii_url, "searchType"),
            "investment": (self.sitc_url, "searchType"),
            "dealer": (self.dealer_url, "stype"),
        }
        if category not in specs:
            return {}
        url, side_key = specs[category]
        by_symbol: dict[str, float] = {}
        for side in ("buy", "sell"):
            payload = self._fetch_institutional_payload(category, url, side_key, trade_date, side)
            for row in self.parse_institutional_payload(payload, as_of=trade_date):
                row_symbol = str(row.get("symbol") or "").strip()
                if row_symbol:
                    by_symbol[row_symbol] = float(row.get("net_buy") or 0.0)
        return by_symbol

    def _fetch_institutional_payload(self, category: str, url: str, side_key: str, trade_date: date, side: str) -> dict[str, Any]:
        cache_key = (category, trade_date, side)
        if cache_key not in self._institutional_payload_cache:
            params = {
                "date": trade_date.strftime("%Y/%m/%d"),
                "type": "Daily",
                side_key: side,
                "response": "json",
            }
            response = requests.get(
                url,
                params=params,
                headers={"accept": "application/json"},
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            self._institutional_payload_cache[cache_key] = response.json()
        return self._institutional_payload_cache[cache_key]

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
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except Exception:
                continue
        roc_match = re.search(r"(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})", text)
        if roc_match:
            return date(int(roc_match.group(1)) + 1911, int(roc_match.group(2)), int(roc_match.group(3)))
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
        normalized = [TpexStockClient._normalize_field(name) for name in names]
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
        text = TpexStockClient._clean_text(value)
        matches = re.findall(r"\d{2,4}[./-]\d{1,2}[./-]\d{1,2}", text)
        if len(matches) >= 2:
            return TpexStockClient._to_date(matches[0]), TpexStockClient._to_date(matches[1])
        parts = re.split(r"[~～]", text)
        if len(parts) >= 2:
            return TpexStockClient._to_date(parts[0]), TpexStockClient._to_date(parts[1])
        single = TpexStockClient._to_date(text)
        return single, single
