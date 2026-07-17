from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import requests


TPEX_INDEX_INFO_URL = "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx"
TPEX_SECTOR_INDEX_LIST_URL = "https://www.tpex.org.tw/www/zh-tw/statistics/idx"
TPEX_SITE_URL = "https://www.tpex.org.tw"
TPEX_MARKET_INDEX_NAME = "櫃買指數"
TPEX_MARKET_CONTEXT_NAME = "上櫃市場"
TPEX_SECTOR_INDEX_SOURCE = "tpex_sector_index"

_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

TPEX_SECTOR_INDEX_INDUSTRY_MAP = {
    "其他電子": "其他電子業",
    "綠能環保": "綠能環保",
    "數位雲端": "數位雲端",
    "居家生活": "居家生活",
}


def normalize_tpex_sector_index_name(index_name: str) -> str:
    return re.sub(r"\s+", "", str(index_name or "").strip())


def industry_for_tpex_sector_index_name(index_name: str) -> str:
    text = normalize_tpex_sector_index_name(index_name)
    if not text or "報酬" in text:
        return ""
    for suffix in ("類指數", "指數"):
        if text.endswith(suffix):
            base = text[: -len(suffix)]
            return TPEX_SECTOR_INDEX_INDUSTRY_MAP.get(base, base)
    return ""


class TpexIndexClient:
    def __init__(
        self,
        base_url: str = TPEX_INDEX_INFO_URL,
        sector_list_url: str = TPEX_SECTOR_INDEX_LIST_URL,
        site_url: str = TPEX_SITE_URL,
        timeout_sec: int = 15,
    ):
        self.base_url = str(base_url).strip()
        self.sector_list_url = str(sector_list_url).strip()
        self.site_url = str(site_url).strip().rstrip("/") + "/"
        self.timeout_sec = int(timeout_sec)

    def fetch_market_index_bars(self, year_month: date | str | datetime) -> list[dict[str, Any]]:
        month = self._to_month(year_month)
        response = requests.post(
            self.base_url,
            data={"date": month.strftime("%Y%m"), "response": "json"},
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("stat", "")).lower() != "ok":
            return []
        return self.parse_market_index_payload(payload)

    def fetch_sector_index_links(self, year: date | str | datetime | int) -> list[dict[str, Any]]:
        year_start = self._to_year_start(year)
        response = requests.post(
            self.sector_list_url,
            data={"date": year_start.strftime("%Y"), "type": "3", "response": "json"},
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("stat", "")).lower() != "ok":
            return []
        return self.parse_sector_index_list_payload(payload, site_url=self.site_url)

    def fetch_sector_index_bars(self, year_month: date | str | datetime) -> list[dict[str, Any]]:
        month = self._to_month(year_month)
        links = self.fetch_sector_index_links(month)
        selected = next((item for item in links if item.get("month") == month), None)
        if selected is None:
            return []
        response = requests.get(
            str(selected["xlsx_url"]),
            headers={"accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return self.parse_sector_index_workbook(response.content)

    @staticmethod
    def parse_market_index_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [str(item) for item in table.get("fields") or []]
            if not {"日期", "收市"}.issubset(set(fields)):
                continue
            date_idx = fields.index("日期")
            close_idx = fields.index("收市")
            change_idx = fields.index("漲/跌") if "漲/跌" in fields else None
            for raw in table.get("data") or []:
                if not isinstance(raw, list) or len(raw) <= max(date_idx, close_idx):
                    continue
                bar_date = TpexIndexClient._parse_date(raw[date_idx])
                close = TpexIndexClient._parse_number(raw[close_idx])
                if bar_date is None or close <= 0:
                    continue
                rows.append(
                    {
                        "date": bar_date,
                        "index_name": TPEX_MARKET_INDEX_NAME,
                        "industry": TPEX_MARKET_CONTEXT_NAME,
                        "close": close,
                        "change": TpexIndexClient._parse_number(raw[change_idx]) if change_idx is not None and len(raw) > change_idx else 0.0,
                        "change_pct": 0.0,
                        "source": "tpex_index_info",
                    }
                )
        return rows

    @staticmethod
    def parse_sector_index_list_payload(payload: dict[str, Any], *, site_url: str = TPEX_SITE_URL) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        base_url = str(site_url or TPEX_SITE_URL).strip().rstrip("/") + "/"
        for table in payload.get("tables") or []:
            if not isinstance(table, dict):
                continue
            fields = [str(item) for item in table.get("fields") or []]
            if not {"月份", "下載XLSX"}.issubset(set(fields)):
                continue
            month_idx = fields.index("月份")
            xlsx_idx = fields.index("下載XLSX")
            ods_idx = fields.index("下載ODS") if "下載ODS" in fields else None
            for raw in table.get("data") or []:
                if not isinstance(raw, list) or len(raw) <= max(month_idx, xlsx_idx):
                    continue
                month = TpexIndexClient._parse_roc_month(raw[month_idx])
                xlsx_path = str(raw[xlsx_idx] or "").strip()
                if month is None or not xlsx_path:
                    continue
                rows.append(
                    {
                        "month": month,
                        "label": str(raw[month_idx] or "").strip(),
                        "xlsx_url": urljoin(base_url, xlsx_path),
                        "ods_url": urljoin(base_url, str(raw[ods_idx] or "").strip()) if ods_idx is not None and len(raw) > ods_idx else "",
                        "source": TPEX_SECTOR_INDEX_SOURCE,
                    }
                )
        rows.sort(key=lambda item: item["month"])
        return rows

    @staticmethod
    def parse_sector_index_workbook(content: bytes) -> list[dict[str, Any]]:
        matrix = TpexIndexClient._read_xlsx_matrix(content)
        header_idx = next(
            (
                idx
                for idx, row in enumerate(matrix)
                if normalize_tpex_sector_index_name(str(row.get(0) or "")) == "指數種類"
            ),
            None,
        )
        if header_idx is None:
            return []

        header = matrix[header_idx]
        columns: list[tuple[int, str, str]] = []
        for col_idx, value in sorted(header.items()):
            if col_idx == 0:
                continue
            index_name = normalize_tpex_sector_index_name(str(value or ""))
            if not index_name or "報酬" in index_name:
                continue
            industry = industry_for_tpex_sector_index_name(index_name)
            if not industry:
                continue
            columns.append((col_idx, index_name, industry))

        records: list[dict[str, Any]] = []
        previous_close: dict[str, float] = {}
        for row in matrix[header_idx + 1 :]:
            first_cell = str(row.get(0) or "").strip()
            if not first_cell or "平均" in first_cell:
                continue
            bar_date = TpexIndexClient._parse_excel_or_text_date(first_cell)
            if bar_date is None:
                continue
            for col_idx, index_name, industry in columns:
                close = TpexIndexClient._parse_number(row.get(col_idx))
                if close <= 0:
                    continue
                prev = previous_close.get(index_name)
                change = close - prev if prev and prev > 0 else 0.0
                change_pct = (close / prev) - 1.0 if prev and prev > 0 else 0.0
                previous_close[index_name] = close
                records.append(
                    {
                        "date": bar_date,
                        "index_name": index_name,
                        "industry": industry,
                        "close": close,
                        "change": change,
                        "change_pct": change_pct,
                        "source": TPEX_SECTOR_INDEX_SOURCE,
                    }
                )
        return records

    @staticmethod
    def _to_month(value: date | str | datetime) -> date:
        if isinstance(value, datetime):
            return value.date().replace(day=1)
        if isinstance(value, date):
            return value.replace(day=1)
        text = str(value or "").strip()
        samples = [
            (text[:10], "%Y-%m-%d"),
            (text[:10], "%Y/%m/%d"),
            (text[:8], "%Y%m%d"),
            (text[:6], "%Y%m"),
        ]
        for sample, fmt in samples:
            try:
                return datetime.strptime(sample, fmt).date().replace(day=1)
            except Exception:
                continue
        raise ValueError(f"invalid month: {value}")

    @staticmethod
    def _to_year_start(value: date | str | datetime | int) -> date:
        if isinstance(value, datetime):
            return value.date().replace(month=1, day=1)
        if isinstance(value, date):
            return value.replace(month=1, day=1)
        text = str(value or "").strip()
        if text.isdigit() and len(text) == 3:
            return date(int(text) + 1911, 1, 1)
        if text.isdigit() and len(text) >= 4:
            return date(int(text[:4]), 1, 1)
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt).date().replace(month=1, day=1)
            except Exception:
                continue
        raise ValueError(f"invalid year: {value}")

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        text = str(value or "").strip()
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except Exception:
                continue
        return None

    @staticmethod
    def _parse_roc_month(value: Any) -> date | None:
        text = str(value or "").strip()
        match = re.match(r"^(\d{2,3})\s*/\s*(\d{1,2})$", text)
        if not match:
            return None
        year = int(match.group(1)) + 1911
        month = int(match.group(2))
        if not 1 <= month <= 12:
            return None
        return date(year, month, 1)

    @staticmethod
    def _parse_excel_or_text_date(value: Any) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            serial = float(text)
            if serial > 20_000:
                return date(1899, 12, 30) + timedelta(days=int(serial))
        except ValueError:
            pass
        return TpexIndexClient._parse_date(text)

    @staticmethod
    def _parse_number(value: Any) -> float:
        text = re.sub(r"<[^>]+>", "", str(value if value is not None else "")).replace(",", "").strip()
        if text in {"", "--", "-"}:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    @staticmethod
    def _read_xlsx_matrix(content: bytes) -> list[dict[int, str]]:
        try:
            with ZipFile(BytesIO(content)) as archive:
                shared_strings = TpexIndexClient._read_shared_strings(archive)
                sheet_name = "xl/worksheets/sheet1.xml"
                if sheet_name not in archive.namelist():
                    return []
                root = ET.fromstring(archive.read(sheet_name))
        except (BadZipFile, KeyError, ET.ParseError):
            return []

        rows: list[dict[int, str]] = []
        for row in root.findall(".//m:sheetData/m:row", _XLSX_NS):
            values: dict[int, str] = {}
            for cell in row.findall("m:c", _XLSX_NS):
                ref = str(cell.attrib.get("r") or "")
                value_node = cell.find("m:v", _XLSX_NS)
                if not ref or value_node is None:
                    continue
                raw = str(value_node.text or "")
                value = raw
                if cell.attrib.get("t") == "s":
                    try:
                        value = shared_strings[int(raw)]
                    except (IndexError, ValueError):
                        value = ""
                values[TpexIndexClient._xlsx_column_index(ref)] = value
            if values:
                rows.append(values)
        return rows

    @staticmethod
    def _read_shared_strings(archive: ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        values: list[str] = []
        for item in root.findall("m:si", _XLSX_NS):
            text = "".join(node.text or "" for node in item.findall(".//m:t", _XLSX_NS))
            values.append(text.strip())
        return values

    @staticmethod
    def _xlsx_column_index(ref: str) -> int:
        letters = re.sub(r"[^A-Za-z]", "", str(ref or ""))
        total = 0
        for char in letters.upper():
            total = (total * 26) + (ord(char) - ord("A") + 1)
        return max(0, total - 1)
