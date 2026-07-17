from __future__ import annotations

from datetime import date
from io import BytesIO
from zipfile import ZipFile

from app.data.tpex_index_client import (
    TPEX_MARKET_CONTEXT_NAME,
    TPEX_MARKET_INDEX_NAME,
    TPEX_SECTOR_INDEX_SOURCE,
    TpexIndexClient,
    industry_for_tpex_sector_index_name,
)


def _xlsx_bytes(*, strings: list[str], rows: list[list[tuple[str, int | float | str, bool]]]) -> bytes:
    buffer = BytesIO()
    shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
    row_xml = []
    for row_idx, cells in enumerate(rows, start=1):
        cell_xml = []
        for ref, value, is_shared in cells:
            cell_type = ' t="s"' if is_shared else ""
            cell_xml.append(f'<c r="{ref}"{cell_type}><v>{value}</v></c>')
        row_xml.append(f'<row r="{row_idx}">{"".join(cell_xml)}</row>')
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared}</sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(row_xml)}</sheetData>'
            "</worksheet>",
        )
    return buffer.getvalue()


def test_tpex_index_client_parses_official_market_index_table() -> None:
    payload = {
        "stat": "ok",
        "tables": [
            {
                "title": "櫃買指數(月查詢)",
                "fields": ["日期", "開市", "最高", "最低", "收市", "漲/跌"],
                "data": [
                    ["2026/06/24", "438.54", "442.46", "431.79", "442.09", "1.28"],
                    ["2026/06/25", "442.80", "447.63", "438.30", "439.84", "-2.25"],
                ],
            }
        ],
    }

    rows = TpexIndexClient.parse_market_index_payload(payload)

    assert rows == [
        {
            "date": date(2026, 6, 24),
            "index_name": TPEX_MARKET_INDEX_NAME,
            "industry": TPEX_MARKET_CONTEXT_NAME,
            "close": 442.09,
            "change": 1.28,
            "change_pct": 0.0,
            "source": "tpex_index_info",
        },
        {
            "date": date(2026, 6, 25),
            "index_name": TPEX_MARKET_INDEX_NAME,
            "industry": TPEX_MARKET_CONTEXT_NAME,
            "close": 439.84,
            "change": -2.25,
            "change_pct": 0.0,
            "source": "tpex_index_info",
        },
    ]


def test_tpex_index_client_normalizes_month_inputs() -> None:
    assert TpexIndexClient._to_month("202606").isoformat() == "2026-06-01"
    assert TpexIndexClient._to_month("2026/06/25").isoformat() == "2026-06-01"
    assert TpexIndexClient._to_month(date(2026, 6, 25)).isoformat() == "2026-06-01"


def test_tpex_sector_index_list_parses_official_download_links() -> None:
    payload = {
        "stat": "ok",
        "tables": [
            {
                "title": "產業分類股價指數一覽表",
                "fields": ["月份", "下載XLSX", "下載ODS"],
                "data": [
                    [
                        "115/5",
                        "/storage/ch/stock/statistics/idx/115/IDX3_11505.xlsx",
                        "/zh-tw/api/convertToOds?f=/ch/stock/statistics/idx/115/IDX3_11505.xlsx",
                    ]
                ],
            }
        ],
    }

    rows = TpexIndexClient.parse_sector_index_list_payload(payload, site_url="https://www.tpex.org.tw")

    assert rows == [
        {
            "month": date(2026, 5, 1),
            "label": "115/5",
            "xlsx_url": "https://www.tpex.org.tw/storage/ch/stock/statistics/idx/115/IDX3_11505.xlsx",
            "ods_url": "https://www.tpex.org.tw/zh-tw/api/convertToOds?f=/ch/stock/statistics/idx/115/IDX3_11505.xlsx",
            "source": TPEX_SECTOR_INDEX_SOURCE,
        }
    ]


def test_tpex_sector_index_workbook_parses_price_indexes_only() -> None:
    workbook = _xlsx_bytes(
        strings=[
            "指數種類",
            "半導體業\n指數",
            "半導體業\n報酬指數",
            "其他電子類\n指數",
            "平均數  Average",
        ],
        rows=[
            [("A1", 0, True), ("B1", 1, True), ("C1", 2, True), ("D1", 3, True)],
            [("A2", 45464, False), ("B2", 100.0, False), ("C2", 150.0, False), ("D2", 88.0, False)],
            [("A3", 45465, False), ("B3", 106.0, False), ("C3", 156.0, False), ("D3", 90.0, False)],
            [("A4", 4, True), ("B4", 103.0, False), ("D4", 89.0, False)],
        ],
    )

    rows = TpexIndexClient.parse_sector_index_workbook(workbook)

    assert rows == [
        {
            "date": date(2024, 6, 21),
            "index_name": "半導體業指數",
            "industry": "半導體業",
            "close": 100.0,
            "change": 0.0,
            "change_pct": 0.0,
            "source": TPEX_SECTOR_INDEX_SOURCE,
        },
        {
            "date": date(2024, 6, 21),
            "index_name": "其他電子類指數",
            "industry": "其他電子業",
            "close": 88.0,
            "change": 0.0,
            "change_pct": 0.0,
            "source": TPEX_SECTOR_INDEX_SOURCE,
        },
        {
            "date": date(2024, 6, 22),
            "index_name": "半導體業指數",
            "industry": "半導體業",
            "close": 106.0,
            "change": 6.0,
            "change_pct": 0.06000000000000005,
            "source": TPEX_SECTOR_INDEX_SOURCE,
        },
        {
            "date": date(2024, 6, 22),
            "index_name": "其他電子類指數",
            "industry": "其他電子業",
            "close": 90.0,
            "change": 2.0,
            "change_pct": 0.022727272727272707,
            "source": TPEX_SECTOR_INDEX_SOURCE,
        },
    ]


def test_tpex_sector_index_industry_mapping_handles_official_names() -> None:
    assert industry_for_tpex_sector_index_name("其他電子類\n指數") == "其他電子業"
    assert industry_for_tpex_sector_index_name("航運業類\n指數") == "航運業"
    assert industry_for_tpex_sector_index_name("半導體業\n報酬指數") == ""
