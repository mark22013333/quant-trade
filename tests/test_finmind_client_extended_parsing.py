from __future__ import annotations

from datetime import date

from app.data.finmind_client import FinMindClient


def test_fetch_stock_info_filters_twse_tpex(monkeypatch):
    client = FinMindClient(api_key="dummy")
    rows = [
        {"stock_id": "2330", "stock_name": "TSMC", "type": "twse"},
        {"stock_id": "4743", "stock_name": "Oneness", "type": "tpex"},
        {"stock_id": "0001", "stock_name": "ETF", "type": "etf"},
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_stock_info()

    assert len(result) == 2
    assert result[0]["market"] in {"TWSE", "TPEX"}
    assert {item["symbol"] for item in result} == {"2330", "4743"}


def test_fetch_shareholding_by_date_normalizes_bucket_rows(monkeypatch):
    client = FinMindClient(api_key="dummy")
    rows = [
        {"date": "2024-01-02", "stock_id": "2330", "holding_shares_level": "1-10", "percent": 12.5},
        {"date": "2024-01-02", "stock_id": "2330", "holding_shares_level": "1000以上", "percent": 34.2},
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_shareholding_by_date(date(2024, 1, 2))

    assert len(result) == 1
    assert result[0]["symbol"] == "2330"
    assert result[0]["major_holder_ratio"] == 34.2
    assert result[0]["retail_holder_ratio"] == 12.5


def test_fetch_holding_shares_per_by_date_fallback_to_endpoint(monkeypatch):
    client = FinMindClient(api_key="dummy")
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: (_ for _ in ()).throw(RuntimeError("dataset failed")))  # noqa: ARG005

    def _endpoint(endpoint, **kwargs):  # noqa: ANN001
        assert endpoint == "taiwan_stock_holding_shares_per"
        return [
            {"date": "2024-01-02", "stock_id": "2330", "holding_shares_level": "1000以上", "percent": 40.0},
            {"date": "2024-01-02", "stock_id": "2330", "holding_shares_level": "1-10", "percent": 10.0},
        ]

    monkeypatch.setattr(client, "_request_endpoint", _endpoint)
    result = client.fetch_holding_shares_per_by_date("2024-01-02")

    assert len(result) == 1
    assert result[0]["concentration_proxy"] == 40.0
    assert result[0]["dispersion_proxy"] == 10.0


def test_fetch_broker_agg_by_date_groups_rows_by_symbol(monkeypatch):
    client = FinMindClient(api_key="dummy")
    rows = [
        {"date": "2024-01-02", "stock_id": "2330", "buy": 100, "sell": 10},
        {"date": "2024-01-02", "stock_id": "2330", "buy": 90, "sell": 20},
        {"date": "2024-01-02", "stock_id": "2317", "buy": 50, "sell": 10},
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_broker_agg_by_date("2024-01-02", async_mode=True)
    result_map = {item["symbol"]: item for item in result}

    assert set(result_map.keys()) == {"2330", "2317"}
    assert result_map["2330"]["top5_net_buy"] == 160.0
    assert result_map["2317"]["top5_net_buy"] == 40.0
