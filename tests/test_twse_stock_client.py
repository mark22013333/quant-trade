from __future__ import annotations

from datetime import date

from app.data.twse_stock_client import TwseStockClient


def test_twse_stock_client_parses_stock_day_payload():
    payload = {
        "stat": "OK",
        "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數"],
        "data": [
            ["113/06/24", "62,029,941", "59,006,299,542", "958.00", "960.00", "940.00", "940.00", "-30.00", "74,999"],
            ["113/06/25", "54,147,828", "51,581,874,688", "925.00", "945.00", "923.00", "945.00", "+5.00", "67,001"],
            ["113/06/26", "1,234", "1,234", "--", "--", "--", "--", "0.00", "10"],
        ],
    }

    rows = TwseStockClient.parse_stock_day_payload(payload, symbol="2330")

    assert rows == [
        {
            "date": date(2024, 6, 24),
            "open": 958.0,
            "high": 960.0,
            "low": 940.0,
            "close": 940.0,
            "volume": 62029941.0,
            "source": "twse_stock_day",
            "symbol": "2330",
        },
        {
            "date": date(2024, 6, 25),
            "open": 925.0,
            "high": 945.0,
            "low": 923.0,
            "close": 945.0,
            "volume": 54147828.0,
            "source": "twse_stock_day",
            "symbol": "2330",
        },
    ]


def test_twse_stock_client_ignores_non_ok_payload():
    assert TwseStockClient.parse_stock_day_payload({"stat": "很抱歉，沒有符合條件的資料!"}, symbol="2330") == []


def test_twse_stock_client_parses_mi_index_daily_payload_for_requested_symbols():
    payload = {
        "stat": "OK",
        "date": "20260708",
        "tables": [
            {
                "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
                "data": [
                    ["2330", "台積電", "21,528,011", "25,705", "23,294,157,260", "1065.00", "1130.00", "1020.00", "1105.00"],
                    ["0050", "元大台灣50", "1,000", "10", "200,000", "200.00", "201.00", "199.00", "200.50"],
                    ["2317", "鴻海", "100", "1", "10,000", "--", "--", "--", "--"],
                ],
            }
        ],
    }

    rows = TwseStockClient.parse_mi_index_daily_payload(payload, symbols=["2330", "2317"])

    assert rows == [
        {
            "date": date(2026, 7, 8),
            "open": 1065.0,
            "high": 1130.0,
            "low": 1020.0,
            "close": 1105.0,
            "volume": 21528011.0,
            "source": "twse_mi_index",
            "symbol": "2330",
        }
    ]


def test_twse_stock_client_parses_t86_institutional_payload():
    payload = {
        "stat": "OK",
        "date": "20260703",
        "fields": [
            "證券代號",
            "證券名稱",
            "外陸資買進股數(不含外資自營商)",
            "外陸資賣出股數(不含外資自營商)",
            "外陸資買賣超股數(不含外資自營商)",
            "外資自營商買進股數",
            "外資自營商賣出股數",
            "外資自營商買賣超股數",
            "投信買進股數",
            "投信賣出股數",
            "投信買賣超股數",
            "自營商買賣超股數",
            "三大法人買賣超股數",
        ],
        "data": [
            ["2330", "台積電", "2,000", "1,000", "1,000", "0", "50", "-50", "300", "100", "200", "300", "1,450"],
            ["2317", "鴻海", "10", "20", "-10", "0", "0", "0", "1", "0", "1", "2", "-7"],
        ],
    }

    rows = TwseStockClient.parse_t86_payload(payload, symbol="2330")

    assert rows == [
        {
            "date": date(2026, 7, 3),
            "foreign_net_buy": 950.0,
            "investment_trust_net_buy": 200.0,
            "dealer_net_buy": 300.0,
            "source": "twse_t86",
            "symbol": "2330",
        }
    ]


def test_twse_stock_client_parses_attention_payload():
    payload = {
        "stat": "OK",
        "fields": ["編號", "證券代號", "證券名稱", "累計次數", "注意交易資訊", "日期", "收盤價", "本益比"],
        "data": [
            [1, "2308", "台達電", "1", "六個營業日起迄兩個營業日收盤價價差達265.00元<font>﹝第十一款﹞</font>。", "115.07.03", "2075.00", "76.51"],
        ],
    }

    rows = TwseStockClient.parse_attention_payload(payload)

    assert rows == [
        {
            "symbol": "2308",
            "trade_date": date(2026, 7, 3),
            "announced_date": date(2026, 7, 3),
            "close": 2075.0,
            "reason": "六個營業日起迄兩個營業日收盤價價差達265.00元﹝第十一款﹞。",
            "source": "twse_notice",
        }
    ]


def test_twse_stock_client_parses_disposition_payload():
    payload = {
        "stat": "OK",
        "fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置條件", "處置起迄時間", "處置措施", "處置內容", "備註"],
        "data": [
            [
                1,
                "115/07/02",
                "1303",
                "南亞",
                1,
                "連續三次",
                "115/06/24～115/07/07",
                "第一次處置",
                "１處置原因：連續三個營業日達公布注意交易資訊標準。<br>２處置期間：測試。",
                "",
            ],
        ],
    }

    rows = TwseStockClient.parse_disposition_payload(payload)

    assert rows == [
        {
            "symbol": "1303",
            "start_date": date(2026, 6, 24),
            "end_date": date(2026, 7, 7),
            "disposition_type": "第一次處置",
            "reason": "連續三次；１處置原因：連續三個營業日達公布注意交易資訊標準。 ２處置期間：測試。",
            "source": "twse_punish",
        }
    ]
