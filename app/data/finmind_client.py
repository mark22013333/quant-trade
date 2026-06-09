from __future__ import annotations

import re
import json
from collections import defaultdict
from datetime import date, datetime

import requests

from app.config import load_config
from app.data.errors import FinMindRequestError


class FinMindClient:
    """
    Minimal FinMind API wrapper for Taiwan daily stock bars.
    Ref: https://finmind.github.io/tutor/TaiwanMarket/DataList/
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        user_info_url: str | None = None,
        timeout_sec: int = 15,
        retries: int = 1,
    ):
        cfg = load_config()
        self.api_key = (api_key if api_key is not None else cfg.finmind_api_key).strip()
        self.base_url = (base_url if base_url is not None else cfg.finmind_api_url).strip()
        self.api_root = self._derive_api_root(self.base_url)
        self.user_info_url = (user_info_url if user_info_url is not None else cfg.finmind_user_info_url).strip()
        self.timeout_sec = int(timeout_sec)
        self.retries = max(1, int(retries))

    @staticmethod
    def _derive_api_root(base_url: str) -> str:
        value = str(base_url or "").strip().rstrip("/")
        if value.endswith("/data"):
            return value[: -len("/data")]
        return value

    @staticmethod
    def _to_date_str(value: str | date | datetime) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        return str(value)

    def _headers(self) -> dict:
        headers = {"accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _pick(row: dict, *keys: str):
        for key in keys:
            if key in row and row[key] not in (None, ""):
                return row[key]
        return None

    @staticmethod
    def _to_date(value) -> date | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except Exception:
                continue
        return None

    @staticmethod
    def _normalize_symbol(value: str | None) -> str:
        text = str(value or "").strip().upper()
        for suffix in (".TW", ".TWO"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return text

    @staticmethod
    def _normalize_period(value, fallback_date: date | None = None, month_only: bool = False) -> str:
        text = str(value or "").strip()
        if text:
            match = re.search(r"(\d{4})[-/](\d{1,2})", text)
            if match:
                return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}" if month_only else f"{int(match.group(1)):04d}-Q{((int(match.group(2)) - 1) // 3) + 1}"
            quarter = re.search(r"(\d{4})\D*[Qq季](\d)", text)
            if quarter:
                return f"{int(quarter.group(1)):04d}-Q{int(quarter.group(2))}"
            if re.fullmatch(r"\d{6}", text):
                return f"{text[:4]}-{text[4:6]}" if month_only else f"{text[:4]}-Q{((int(text[4:6]) - 1) // 3) + 1}"
        if fallback_date is not None:
            if month_only:
                return fallback_date.strftime("%Y-%m")
            return f"{fallback_date.year:04d}-Q{((fallback_date.month - 1) // 3) + 1}"
        return ""

    @staticmethod
    def _default_announce_date(period: str, fallback: date | None = None) -> date | None:
        if fallback is not None:
            return fallback
        month_match = re.fullmatch(r"(\d{4})-(\d{2})", str(period or ""))
        if month_match:
            year = int(month_match.group(1))
            month = int(month_match.group(2))
            if month == 12:
                return date(year + 1, 1, 10)
            return date(year, month + 1, 10)
        quarter_match = re.fullmatch(r"(\d{4})-Q([1-4])", str(period or ""))
        if quarter_match:
            year = int(quarter_match.group(1))
            quarter = int(quarter_match.group(2))
            month = quarter * 3
            if quarter == 4:
                return date(year + 1, 3, 31)
            return date(year, month + 2, 15)
        return None

    def _row_symbol(self, row: dict, fallback: str | None = None) -> str:
        raw = self._pick(row, "stock_id", "data_id", "symbol", "code")
        symbol = self._normalize_symbol(raw if raw is not None else fallback)
        return symbol

    def check_auth(self) -> dict:
        params = {
            "dataset": "TaiwanStockInfo",
            "data_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
        }
        response = requests.get(self.base_url, params=params, headers=self._headers(), timeout=self.timeout_sec)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("FinMind response is not JSON object")
        return {
            "status": data.get("status", 0),
            "msg": data.get("msg", ""),
            "data_len": len(data.get("data") or []),
        }

    def _request_dataset(self, dataset: str, **params) -> list[dict]:
        query = {"dataset": dataset}
        for key, value in params.items():
            if value in (None, ""):
                continue
            if isinstance(value, (date, datetime)):
                query[key] = self._to_date_str(value)
            else:
                query[key] = str(value)
        payload = self._get_json(self.base_url, params=query)
        if not isinstance(payload, dict):
            raise FinMindRequestError("FinMind response format error")
        status = int(payload.get("status", 0))
        if status != 200:
            msg = payload.get("msg", "unknown")
            raise FinMindRequestError(f"FinMind request failed: {msg}")
        data = payload.get("data") or []
        return data if isinstance(data, list) else []

    def _request_endpoint(self, endpoint: str, **params) -> list[dict]:
        if not self.api_root:
            return []
        query = {}
        for key, value in params.items():
            if value in (None, ""):
                continue
            if isinstance(value, (date, datetime)):
                query[key] = self._to_date_str(value)
            else:
                query[key] = str(value)

        url = f"{self.api_root.rstrip('/')}/{str(endpoint).lstrip('/')}"
        payload = self._get_json(url, params=query)
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise FinMindRequestError("FinMind endpoint response format error")
        status = int(payload.get("status", 200))
        if status != 200:
            msg = payload.get("msg", "unknown")
            raise FinMindRequestError(f"FinMind endpoint failed: {msg}")
        data = payload.get("data") or []
        return data if isinstance(data, list) else []

    def _get_json(self, url: str, *, params: dict | None = None):
        last_error: Exception | None = None
        for _ in range(self.retries):
            try:
                response = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout_sec)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise FinMindRequestError(f"FinMind request failed after {self.retries} attempt(s): {last_error}") from last_error

    def fetch_daily_bars(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows = self._request_dataset(
            dataset="TaiwanStockPrice",
            data_id=str(stock_id),
            start_date=start_date,
            end_date=end_date,
        )
        rows = []
        for row in payload_rows:
            try:
                bar_date = self._to_date(self._pick(row, "date"))
                if bar_date is None:
                    continue
                open_value = self._pick(row, "open", "Open")
                high_value = self._pick(row, "max", "high", "High")
                low_value = self._pick(row, "min", "low", "Low")
                close_value = self._pick(row, "close", "Close")
                volume_value = self._pick(row, "Trading_Volume", "trading_volume", "volume", "Volume")
                if open_value is None or high_value is None or low_value is None or close_value is None:
                    continue
                rows.append(
                    {
                        "date": bar_date,
                        "open": float(open_value),
                        "high": float(high_value),
                        "low": float(low_value),
                        "close": float(close_value),
                        "volume": float(volume_value or 0.0),
                        "source": "finmind",
                    }
                )
            except Exception:
                continue
        return rows

    def fetch_stock_info(self) -> list[dict]:
        payload_rows = self._request_dataset(dataset="TaiwanStockInfo")
        result_map: dict[str, dict] = {}
        for row in payload_rows:
            symbol = self._row_symbol(row)
            if not symbol:
                continue
            market_raw = str(self._pick(row, "type", "market", "exchange", "stock_type") or "").strip().upper()
            if market_raw in {"TWSE", "TSE", "上市"}:
                market = "TWSE"
            elif market_raw in {"TPEX", "OTC", "上櫃"}:
                market = "TPEX"
            else:
                continue
            result_map[symbol] = {
                "symbol": symbol,
                "name": str(self._pick(row, "stock_name", "name", "company_name") or "").strip(),
                "market": market,
                "source": "finmind",
            }
        return [result_map[key] for key in sorted(result_map.keys())]

    def fetch_institutional_chip(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows: list[dict]
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockInstitutionalInvestorsBuySell",
                data_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_institutional_investors_buy_sell",
                stock_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        bucket: dict[date, dict[str, float]] = {}
        for row in payload_rows:
            row_date = self._to_date(self._pick(row, "date"))
            if row_date is None:
                continue
            name = str(
                self._pick(
                    row,
                    "name",
                    "institutional_investors",
                    "institutionalInvestors",
                    "institutional",
                    "investor_type",
                )
                or ""
            ).lower()
            buy = self._to_float(self._pick(row, "buy", "Buy", "buy_amount", "BuyAmount"))
            sell = self._to_float(self._pick(row, "sell", "Sell", "sell_amount", "SellAmount"))
            net = self._to_float(self._pick(row, "buy_sell", "net_buy_sell", "net"))
            if net is None:
                net = float((buy or 0.0) - (sell or 0.0))
            if row_date not in bucket:
                bucket[row_date] = {
                    "foreign_net_buy": 0.0,
                    "investment_trust_net_buy": 0.0,
                    "dealer_net_buy": 0.0,
                }

            if "外資" in name or "foreign" in name:
                bucket[row_date]["foreign_net_buy"] += float(net)
            elif "投信" in name or "investment" in name:
                bucket[row_date]["investment_trust_net_buy"] += float(net)
            elif "自營商" in name or "dealer" in name:
                bucket[row_date]["dealer_net_buy"] += float(net)
            else:
                # Unknown category: keep ignored to avoid accidental double count.
                continue

        rows: list[dict] = []
        for day in sorted(bucket.keys()):
            data = bucket[day]
            rows.append(
                {
                    "date": day,
                    "foreign_net_buy": float(data["foreign_net_buy"]),
                    "investment_trust_net_buy": float(data["investment_trust_net_buy"]),
                    "dealer_net_buy": float(data["dealer_net_buy"]),
                    "source": "finmind",
                }
            )
        return rows

    def fetch_broker_agg_chip(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows: list[dict]
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockTradingDailyReportSecIdAgg",
                data_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_trading_daily_report_secid_agg",
                stock_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        by_day: dict[date, list[float]] = {}
        for row in payload_rows:
            row_date = self._to_date(self._pick(row, "date"))
            if row_date is None:
                continue
            buy = self._to_float(self._pick(row, "buy", "Buy", "buy_volume", "buy_shares", "buy_amount"))
            sell = self._to_float(self._pick(row, "sell", "Sell", "sell_volume", "sell_shares", "sell_amount"))
            net = self._to_float(self._pick(row, "buy_sell", "net_buy_sell", "net"))
            if net is None:
                net = float((buy or 0.0) - (sell or 0.0))
            by_day.setdefault(row_date, []).append(float(net))

        rows: list[dict] = []
        for day in sorted(by_day.keys()):
            net_values = sorted(by_day[day], reverse=True)
            top5_sum = float(sum(net_values[:5]))
            rows.append(
                {
                    "date": day,
                    "concentration_proxy": top5_sum,
                    "top5_net_buy": top5_sum,
                    "source": "finmind",
                }
            )
        return rows

    def fetch_disposition_periods(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows: list[dict]
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockDispositionSecuritiesPeriod",
                data_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_disposition_securities_period",
                stock_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        rows: list[dict] = []
        for row in payload_rows:
            start = self._to_date(
                self._pick(
                    row,
                    "period_start",
                    "start_date",
                    "disposition_start_date",
                    "disposition_date_start",
                    "date",
                )
            )
            end = self._to_date(
                self._pick(
                    row,
                    "period_end",
                    "end_date",
                    "disposition_end_date",
                    "disposition_date_end",
                    "date",
                )
            )
            if start is None:
                continue
            if end is None:
                end = start
            disposition_type = str(
                self._pick(
                    row,
                    "measure",
                    "disposition_type",
                    "type",
                    "rule",
                    "announcement_type",
                )
                or ""
            ).strip()
            reason = str(
                self._pick(
                    row,
                    "condition",
                    "reason",
                    "note",
                    "remarks",
                    "information",
                )
                or ""
            ).strip()
            rows.append(
                {
                    "start_date": start,
                    "end_date": end,
                    "disposition_type": disposition_type,
                    "reason": reason,
                    "source": "finmind",
                }
            )
        return rows

    def fetch_monthly_revenue(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows = self._request_dataset(
            dataset="TaiwanStockMonthRevenue",
            data_id=str(stock_id),
            start_date=start_date,
            end_date=end_date,
        )
        rows: list[dict] = []
        for row in payload_rows:
            row_date = self._to_date(self._pick(row, "date", "revenue_month", "month"))
            period = self._normalize_period(
                self._pick(row, "period", "revenue_month", "month", "date"),
                fallback_date=row_date,
                month_only=True,
            )
            announce_date = self._to_date(self._pick(row, "announce_date", "publication_date", "date")) or self._default_announce_date(
                period,
                fallback=row_date,
            )
            revenue = self._to_float(
                self._pick(row, "revenue", "Revenue", "monthly_revenue", "當月營收", "current_month_revenue")
            )
            if not period or announce_date is None or revenue is None:
                continue
            rows.append(
                {
                    "period": period,
                    "announce_date": announce_date,
                    "revenue": float(revenue),
                    "revenue_yoy_pct": self._to_float(self._pick(row, "revenue_year", "yoy_growth_pct", "YoY", "yoy")),
                    "revenue_mom_pct": self._to_float(self._pick(row, "mom_growth_pct", "MoM", "mom", "month_growth_pct")),
                    "source": "finmind",
                }
            )
        return rows

    def fetch_financial_statement_summary(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        datasets = [
            "TaiwanStockFinancialStatements",
            "TaiwanStockBalanceSheet",
            "TaiwanStockCashFlowsStatement",
        ]
        by_period: dict[str, dict] = {}
        for dataset in datasets:
            try:
                payload_rows = self._request_dataset(
                    dataset=dataset,
                    data_id=str(stock_id),
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception:
                continue
            for row in payload_rows:
                row_date = self._to_date(self._pick(row, "date", "announce_date", "publication_date"))
                period = self._normalize_period(
                    self._pick(row, "period", "quarter", "season", "date"),
                    fallback_date=row_date,
                    month_only=False,
                )
                announce_date = self._to_date(self._pick(row, "announce_date", "publication_date", "date")) or self._default_announce_date(
                    period,
                    fallback=row_date,
                )
                if not period or announce_date is None:
                    continue
                bucket = by_period.setdefault(
                    period,
                    {
                        "period": period,
                        "announce_date": announce_date,
                        "eps": None,
                        "roe_pct": None,
                        "gross_margin_pct": None,
                        "operating_margin_pct": None,
                        "debt_ratio_pct": None,
                        "operating_cash_flow": None,
                        "source": "finmind",
                        "raw": [],
                    },
                )
                bucket["announce_date"] = max(bucket["announce_date"], announce_date)
                bucket["raw"].append(row)
                self._merge_financial_metric(bucket, row)

        rows: list[dict] = []
        for period in sorted(by_period.keys()):
            item = by_period[period]
            rows.append(
                {
                    "period": item["period"],
                    "announce_date": item["announce_date"],
                    "eps": item["eps"],
                    "roe_pct": item["roe_pct"],
                    "gross_margin_pct": item["gross_margin_pct"],
                    "operating_margin_pct": item["operating_margin_pct"],
                    "debt_ratio_pct": item["debt_ratio_pct"],
                    "operating_cash_flow": item["operating_cash_flow"],
                    "source": "finmind",
                    "raw_json": json.dumps(item["raw"], ensure_ascii=False, default=str),
                }
            )
        return rows

    def _merge_financial_metric(self, bucket: dict, row: dict) -> None:
        label = str(self._pick(row, "type", "name", "account", "label", "statement_item") or "").lower()
        value = self._to_float(self._pick(row, "value", "amount", "data", "ratio", "percent"))
        direct_map = {
            "eps": ("eps", "每股盈餘"),
            "roe_pct": ("roe", "權益報酬"),
            "gross_margin_pct": ("gross_margin", "毛利率"),
            "operating_margin_pct": ("operating_margin", "營益率", "營業利益率"),
            "debt_ratio_pct": ("debt_ratio", "負債比"),
            "operating_cash_flow": ("operating_cash_flow", "營業活動"),
        }
        for target, names in direct_map.items():
            direct = self._to_float(self._pick(row, target, *names))
            if direct is not None:
                bucket[target] = direct
        if value is None:
            return
        if "eps" in label or "每股盈餘" in label:
            bucket["eps"] = value
        elif "roe" in label or "權益報酬" in label:
            bucket["roe_pct"] = value
        elif "gross" in label or "毛利率" in label:
            bucket["gross_margin_pct"] = value
        elif "operating margin" in label or "營益率" in label or "營業利益率" in label:
            bucket["operating_margin_pct"] = value
        elif "debt" in label or "負債比" in label:
            bucket["debt_ratio_pct"] = value
        elif "operating cash" in label or "營業活動" in label:
            bucket["operating_cash_flow"] = value

    def fetch_stock_news(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows = self._request_dataset(
            dataset="TaiwanStockNews",
            data_id=str(stock_id),
            start_date=start_date,
            end_date=end_date,
        )
        rows: list[dict] = []
        for row in payload_rows:
            news_date = self._to_date(self._pick(row, "date", "publish_date", "news_date"))
            title = str(self._pick(row, "title", "headline", "name") or "").strip()
            if news_date is None or not title:
                continue
            risk_tags = self._infer_news_risk_tags(title)
            rows.append(
                {
                    "date": news_date,
                    "title": title,
                    "source_name": str(self._pick(row, "source_name", "source", "publisher") or "").strip(),
                    "url": str(self._pick(row, "url", "link") or "").strip(),
                    "llm_summary": str(self._pick(row, "llm_summary", "summary", "content") or "").strip()[:500],
                    "risk_tags": risk_tags,
                    "source": "finmind",
                }
            )
        return rows

    @staticmethod
    def _infer_news_risk_tags(title: str) -> list[str]:
        text = str(title or "")
        mapping = {
            "negative_event": ["下修", "虧損", "衰退", "裁員", "違約", "檢調", "重訊", "處分", "停工", "召回"],
            "positive_event": ["創高", "成長", "上修", "得標", "擴產", "配息", "轉盈", "新高"],
            "uncertainty": ["展望", "保守", "不確定", "調查", "訴訟", "匯損"],
        }
        tags: list[str] = []
        for tag, keywords in mapping.items():
            if any(keyword in text for keyword in keywords):
                tags.append(tag)
        return tags

    @staticmethod
    def _parse_bucket_bounds(label: str) -> tuple[float | None, float | None]:
        text = str(label or "").strip().lower()
        if not text:
            return (None, None)
        if "以上" in text or "+" in text:
            nums = re.findall(r"[\d,]+", text)
            if nums:
                lower = float(nums[0].replace(",", ""))
                return (lower, None)
        nums = re.findall(r"[\d,]+", text)
        if len(nums) >= 2:
            lower = float(nums[0].replace(",", ""))
            upper = float(nums[1].replace(",", ""))
            return (lower, upper)
        if len(nums) == 1:
            value = float(nums[0].replace(",", ""))
            return (value, value)
        return (None, None)

    @staticmethod
    def _is_major_bucket(level: str) -> bool:
        lower, _ = FinMindClient._parse_bucket_bounds(level)
        if lower is None:
            return "1000" in str(level).replace(",", "")
        return lower >= 1000

    @staticmethod
    def _is_retail_bucket(level: str) -> bool:
        lower, upper = FinMindClient._parse_bucket_bounds(level)
        if lower is None and upper is None:
            text = str(level or "")
            return "1-10" in text or "10" in text
        if upper is not None:
            return upper <= 10
        return lower is not None and lower <= 10

    def _normalize_shareholding_rows(self, payload_rows: list[dict], fallback_symbol: str | None = None) -> list[dict]:
        bucket: dict[tuple[str, date], dict[str, float]] = {}
        for row in payload_rows:
            symbol = self._row_symbol(row, fallback=fallback_symbol)
            row_date = self._to_date(self._pick(row, "date", "holding_date", "shareholding_date", "stock_holding_date"))
            if not symbol or row_date is None:
                continue
            key = (symbol, row_date)
            if key not in bucket:
                bucket[key] = {"major_holder_ratio": 0.0, "retail_holder_ratio": 0.0}

            major_direct = self._to_float(
                self._pick(
                    row,
                    "major_holder_ratio",
                    "major_ratio",
                    "large_holder_ratio",
                    "big_holder_ratio",
                    "holder_over_1000_ratio",
                    "more_than_1000_lots_ratio",
                )
            )
            retail_direct = self._to_float(
                self._pick(
                    row,
                    "retail_holder_ratio",
                    "retail_ratio",
                    "small_holder_ratio",
                    "holder_under_10_ratio",
                    "less_than_10_lots_ratio",
                )
            )
            if major_direct is not None:
                bucket[key]["major_holder_ratio"] = float(major_direct)
            if retail_direct is not None:
                bucket[key]["retail_holder_ratio"] = float(retail_direct)
            if major_direct is not None or retail_direct is not None:
                continue

            ratio = self._to_float(self._pick(row, "percent", "ratio", "holding_ratio", "shareholding_ratio", "percentage"))
            if ratio is None:
                continue
            level = str(self._pick(row, "holding_shares_level", "HoldingSharesLevel", "shares_level", "level") or "")
            if self._is_major_bucket(level):
                bucket[key]["major_holder_ratio"] += float(ratio)
            if self._is_retail_bucket(level):
                bucket[key]["retail_holder_ratio"] += float(ratio)

        rows: list[dict] = []
        for (symbol, row_date), value in sorted(bucket.items(), key=lambda item: (item[0][0], item[0][1])):
            rows.append(
                {
                    "symbol": symbol,
                    "date": row_date,
                    "major_holder_ratio": float(value["major_holder_ratio"]),
                    "retail_holder_ratio": float(value["retail_holder_ratio"]),
                    "source": "finmind",
                }
            )
        return rows

    def fetch_shareholding(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows: list[dict]
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockShareholding",
                data_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_shareholding",
                stock_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        return self._normalize_shareholding_rows(payload_rows, fallback_symbol=str(stock_id))

    def fetch_shareholding_by_date(self, as_of_date: str | date) -> list[dict]:
        payload_rows: list[dict]
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockShareholding",
                date=as_of_date,
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_shareholding",
                date=as_of_date,
            )
        return self._normalize_shareholding_rows(payload_rows)

    def _normalize_holding_shares_per_rows(self, payload_rows: list[dict], fallback_symbol: str | None = None) -> list[dict]:
        bucket: dict[tuple[str, date], dict[str, float]] = defaultdict(lambda: {"concentration_proxy": 0.0, "dispersion_proxy": 0.0})
        for row in payload_rows:
            symbol = self._row_symbol(row, fallback=fallback_symbol)
            row_date = self._to_date(self._pick(row, "date", "holding_date", "shareholding_date", "stock_holding_date"))
            if not symbol or row_date is None:
                continue
            key = (symbol, row_date)

            concentration_direct = self._to_float(
                self._pick(
                    row,
                    "concentration_proxy",
                    "concentration_ratio",
                    "major_holder_ratio",
                    "big_holder_ratio",
                )
            )
            dispersion_direct = self._to_float(
                self._pick(
                    row,
                    "dispersion_proxy",
                    "dispersion_ratio",
                    "retail_holder_ratio",
                    "small_holder_ratio",
                )
            )
            if concentration_direct is not None:
                bucket[key]["concentration_proxy"] = float(concentration_direct)
            if dispersion_direct is not None:
                bucket[key]["dispersion_proxy"] = float(dispersion_direct)
            if concentration_direct is not None or dispersion_direct is not None:
                continue

            ratio = self._to_float(self._pick(row, "percent", "ratio", "holding_ratio", "shareholding_ratio", "percentage"))
            if ratio is None:
                continue
            level = str(self._pick(row, "holding_shares_level", "HoldingSharesLevel", "shares_level", "level") or "")
            if self._is_major_bucket(level):
                bucket[key]["concentration_proxy"] += float(ratio)
            if self._is_retail_bucket(level):
                bucket[key]["dispersion_proxy"] += float(ratio)

        rows: list[dict] = []
        for (symbol, row_date), value in sorted(bucket.items(), key=lambda item: (item[0][0], item[0][1])):
            rows.append(
                {
                    "symbol": symbol,
                    "date": row_date,
                    "concentration_proxy": float(value["concentration_proxy"]),
                    "dispersion_proxy": float(value["dispersion_proxy"]),
                    "source": "finmind",
                }
            )
        return rows

    def fetch_holding_shares_per(self, stock_id: str, start_date: str | date, end_date: str | date) -> list[dict]:
        payload_rows: list[dict]
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockHoldingSharesPer",
                data_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_holding_shares_per",
                stock_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
            )
        return self._normalize_holding_shares_per_rows(payload_rows, fallback_symbol=str(stock_id))

    def fetch_holding_shares_per_by_date(self, as_of_date: str | date) -> list[dict]:
        payload_rows: list[dict]
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockHoldingSharesPer",
                date=as_of_date,
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_holding_shares_per",
                date=as_of_date,
            )
        return self._normalize_holding_shares_per_rows(payload_rows)

    def fetch_broker_agg_by_date(self, as_of_date: str | date, async_mode: bool = True) -> list[dict]:
        payload_rows: list[dict]
        async_value = "true" if bool(async_mode) else None
        try:
            payload_rows = self._request_dataset(
                dataset="TaiwanStockTradingDailyReportSecIdAgg",
                date=as_of_date,
                **{"async": async_value},
            )
        except Exception:
            payload_rows = self._request_endpoint(
                endpoint="taiwan_stock_trading_daily_report_secid_agg",
                date=as_of_date,
                **{"async": async_value},
            )

        by_symbol_day: dict[tuple[str, date], list[float]] = defaultdict(list)
        for row in payload_rows:
            symbol = self._row_symbol(row)
            row_date = self._to_date(self._pick(row, "date"))
            if not symbol or row_date is None:
                continue
            buy = self._to_float(self._pick(row, "buy", "Buy", "buy_volume", "buy_shares", "buy_amount"))
            sell = self._to_float(self._pick(row, "sell", "Sell", "sell_volume", "sell_shares", "sell_amount"))
            net = self._to_float(self._pick(row, "buy_sell", "net_buy_sell", "net"))
            if net is None:
                net = float((buy or 0.0) - (sell or 0.0))
            by_symbol_day[(symbol, row_date)].append(float(net))

        rows: list[dict] = []
        for (symbol, row_date), net_values in sorted(by_symbol_day.items(), key=lambda item: (item[0][0], item[0][1])):
            top5_sum = float(sum(sorted(net_values, reverse=True)[:5]))
            rows.append(
                {
                    "symbol": symbol,
                    "date": row_date,
                    "concentration_proxy": top5_sum,
                    "top5_net_buy": top5_sum,
                    "source": "finmind",
                }
            )
        return rows

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def fetch_user_info(self) -> dict:
        if not self.api_key:
            raise RuntimeError("FINMIND_API_KEY 未設定，無法查詢 API 用量")
        if not self.user_info_url:
            raise RuntimeError("FINMIND_USER_INFO_URL 未設定，無法查詢 API 用量")

        response = requests.get(self.user_info_url, headers=self._headers(), timeout=self.timeout_sec)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("FinMind user_info response format error")

        status = int(payload.get("status", 0))
        msg = str(payload.get("msg", "")).strip()
        if status != 200:
            reason = msg or "unknown"
            raise RuntimeError(f"FinMind user_info failed: {reason}")

        raw_data = payload.get("data")
        if isinstance(raw_data, list):
            data = raw_data[0] if raw_data and isinstance(raw_data[0], dict) else {}
        elif isinstance(raw_data, dict):
            data = raw_data
        else:
            data = {}

        request_limit = self._to_float(data.get("api_request_limit"))
        request_count = self._to_float(data.get("api_request_count"))
        remaining = None
        usage_ratio = None
        if request_limit is not None and request_count is not None:
            remaining = max(float(request_limit - request_count), 0.0)
            if request_limit > 0:
                usage_ratio = min(max(float(request_count / request_limit), 0.0), 1.0)

        return {
            "status": status,
            "msg": msg,
            "user_count": self._to_float(data.get("user_count")),
            "api_request_limit": request_limit,
            "api_request_count": request_count,
            "api_request_remaining": remaining,
            "usage_ratio": usage_ratio,
            "queried_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
