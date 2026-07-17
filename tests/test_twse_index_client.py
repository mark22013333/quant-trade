from __future__ import annotations

from datetime import date

from app.data.twse_index_client import TwseIndexClient, twse_index_name_for_industry


def test_twse_index_client_parses_official_industry_price_table():
    payload = {
        "stat": "OK",
        "tables": [
            {
                "title": "113年06月24日 價格指數(臺灣證券交易所)",
                "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
                "data": [
                    ["發行量加權股價指數", "22,813.70", "<p>-</p>", "439.69", "-1.89", ""],
                    ["半導體類指數", "604.44", "<p>-</p>", "19.43", "-3.11", ""],
                    ["金融保險類指數", "2,030.54", "<p>+</p>", "7.61", "0.38", ""],
                ],
            },
            {
                "title": "報酬指數(臺灣證券交易所)",
                "fields": ["報酬指數", "收盤指數"],
                "data": [["半導體類報酬指數", "1,111.82"]],
            },
        ],
    }

    rows = TwseIndexClient.parse_industry_index_payload(payload, trade_date=date(2024, 6, 24))

    assert rows == [
        {
            "date": date(2024, 6, 24),
            "index_name": "半導體類指數",
            "industry": "半導體業",
            "close": 604.44,
            "change": 19.43,
            "change_pct": -0.0311,
            "source": "twse_mi_index",
        },
        {
            "date": date(2024, 6, 24),
            "index_name": "金融保險類指數",
            "industry": "金融保險業",
            "close": 2030.54,
            "change": 7.61,
            "change_pct": 0.0038,
            "source": "twse_mi_index",
        },
    ]


def test_twse_index_name_for_industry_handles_finmind_names():
    assert twse_index_name_for_industry("半導體業") == "半導體類指數"
    assert twse_index_name_for_industry("金融保險業") == "金融保險類指數"
    assert twse_index_name_for_industry("水泥工業") == "水泥類指數"


def test_twse_index_client_retries_transient_non_json_response(monkeypatch):
    payload = {
        "stat": "OK",
        "tables": [
            {
                "title": "115年07月08日 價格指數(臺灣證券交易所)",
                "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
                "data": [["半導體類指數", "1,572.23", "<p>+</p>", "10.32", "0.66", ""]],
            }
        ],
    }

    class _Response:
        def __init__(self, value):
            self.value = value
            self.text = str(value)

        def raise_for_status(self):
            return None

        def json(self):
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    responses = [_Response(ValueError("empty body")), _Response(payload)]

    def fake_get(*args, **kwargs):  # noqa: ANN001, ARG001
        return responses.pop(0)

    monkeypatch.setattr("app.data.twse_index_client.requests.get", fake_get)
    monkeypatch.setattr("app.data.twse_index_client.time.sleep", lambda _seconds: None)

    client = TwseIndexClient(retry_count=1)
    rows = client.fetch_industry_index_bars(date(2026, 7, 8))

    assert rows == [
        {
            "date": date(2026, 7, 8),
            "index_name": "半導體類指數",
            "industry": "半導體業",
            "close": 1572.23,
            "change": 10.32,
            "change_pct": 0.0066,
            "source": "twse_mi_index",
        }
    ]
