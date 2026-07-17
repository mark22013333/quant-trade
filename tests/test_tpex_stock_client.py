from __future__ import annotations

from datetime import date

from app.data.tpex_stock_client import TpexStockClient


def test_tpex_stock_client_parses_trading_stock_payload():
    payload = {
        "stat": "ok",
        "tables": [
            {
                "fields": ["日 期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"],
                "data": [
                    ["113/06/24", "6,619", "1,059,216", "164.00", "164.00", "158.00", "158.00", "-5.50", "5,746"],
                    ["113/06/25", "4,997", "785,506", "157.00", "159.00", "154.50", "158.00", "0.00", "4,533"],
                    ["113/06/26", "100", "100", "--", "--", "--", "--", "0.00", "10"],
                ],
            }
        ],
    }

    rows = TpexStockClient.parse_trading_stock_payload(payload, symbol="3105")

    assert rows == [
        {
            "date": date(2024, 6, 24),
            "open": 164.0,
            "high": 164.0,
            "low": 158.0,
            "close": 158.0,
            "volume": 6619000.0,
            "source": "tpex_trading_stock",
            "symbol": "3105",
        },
        {
            "date": date(2024, 6, 25),
            "open": 157.0,
            "high": 159.0,
            "low": 154.5,
            "close": 158.0,
            "volume": 4997000.0,
            "source": "tpex_trading_stock",
            "symbol": "3105",
        },
    ]


def test_tpex_stock_client_ignores_non_ok_payload():
    assert TpexStockClient.parse_trading_stock_payload({"stat": "參數輸入錯誤"}, symbol="3105") == []


def test_tpex_stock_client_parses_daily_quotes_payload_for_requested_symbols():
    payload = {
        "stat": "ok",
        "date": "20260708",
        "tables": [
            {
                "fields": ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)"],
                "data": [
                    ["3105", "穩懋", "158.00", "+1.00", "157.00", "160.00", "156.50", "158.20", "4,997,000", "785,506,000"],
                    ["006201", "元大富櫃50", "46.34", "+0.15", "46.33", "46.65", "45.11", "45.85", "349,332", "16,015,622"],
                    ["8077", "洛碁", "---", "---", "---", "---", "---", "45.00", "40", "1,800"],
                ],
            }
        ],
    }

    rows = TpexStockClient.parse_daily_quotes_payload(payload, symbols=["3105", "8077"])

    assert rows == [
        {
            "date": date(2026, 7, 8),
            "open": 157.0,
            "high": 160.0,
            "low": 156.5,
            "close": 158.0,
            "volume": 4997000.0,
            "source": "tpex_daily_quotes",
            "symbol": "3105",
        }
    ]


def test_tpex_stock_client_parses_institutional_payload_as_shares():
    payload = {
        "stat": "ok",
        "date": "20260703",
        "flagField": "張數",
        "tables": [
            {
                "fields": [
                    "排行",
                    "代號",
                    "名稱",
                    "買進",
                    "賣出",
                    "買賣超(張數)",
                    "買進",
                    "賣出",
                    "買賣超(張數)",
                    "買進",
                    "賣出",
                    "買賣超(張數)",
                ],
                "data": [
                    ["1", "3105", "穩懋", "5,411", "5,810", "-399", "0", "0", "0", "5,411", "5,810", "-399"],
                    ["2", "5347", "世界", "6,751", "10,326", "-3,575", "0", "0", "0", "6,751", "10,326", "-3,575"],
                ],
            }
        ],
    }

    rows = TpexStockClient.parse_institutional_payload(payload, symbol="3105")

    assert rows == [
        {
            "date": date(2026, 7, 3),
            "symbol": "3105",
            "net_buy": -399000.0,
        }
    ]


def test_tpex_stock_client_parses_attention_payload():
    payload = {
        "stat": "ok",
        "tables": [
            {
                "fields": ["編號", "證券代號", "證券名稱", "累計", "注意交易資訊", "公告日期", "收盤價", "本益比", "link"],
                "data": [
                    [1, "3147", "大綜", 24, "最近六個營業日(含當日)累積之最後成交價跌幅達36.27%(第一款)<br>第二行", "115/07/03", "236.00", "26.43", ""],
                ],
            }
        ],
    }

    rows = TpexStockClient.parse_attention_payload(payload)

    assert rows == [
        {
            "symbol": "3147",
            "trade_date": date(2026, 7, 3),
            "announced_date": date(2026, 7, 3),
            "close": 236.0,
            "reason": "最近六個營業日(含當日)累積之最後成交價跌幅達36.27%(第一款) 第二行",
            "source": "tpex_attention",
        }
    ]


def test_tpex_stock_client_parses_disposition_payload():
    payload = {
        "stat": "ok",
        "tables": [
            {
                "fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置起訖時間", "處置原因", "處置內容", "收盤價", "本益比", " "],
                "data": [
                    [
                        1,
                        "115/06/30",
                        "3230",
                        "錦明(../../mainboard/listed/company-detail.html?code=3230)",
                        1,
                        "115/07/01~115/07/14",
                        "因連續3個營業日達本中心作業要點第四條第一項第一款(./attention.html)",
                        "錦明實業股份有限公司股票(代號：3230)改以人工管制撮合作業。<br>信用交易部分收足保證金。",
                        "46.45",
                        "N/A",
                        "",
                    ],
                ],
            }
        ],
    }

    rows = TpexStockClient.parse_disposition_payload(payload)

    assert rows == [
        {
            "symbol": "3230",
            "start_date": date(2026, 7, 1),
            "end_date": date(2026, 7, 14),
            "disposition_type": "因連續3個營業日達本中心作業要點第四條第一項第一款(./attention.html)",
            "reason": "錦明實業股份有限公司股票(代號：3230)改以人工管制撮合作業。 信用交易部分收足保證金。",
            "source": "tpex_disposal",
        }
    ]
