from __future__ import annotations

from datetime import date

from app.data.finmind_client import FinMindClient


def test_fetch_institutional_chip_aggregates_by_investor_name(monkeypatch):
    client = FinMindClient(api_key="dummy")

    rows = [
        {"date": "2024-01-02", "name": "Foreign Investor", "buy": 2000, "sell": 1000},
        {"date": "2024-01-02", "name": "Investment Trust", "buy": 1200, "sell": 1000},
        {"date": "2024-01-02", "name": "Dealer", "buy": 500, "sell": 650},
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_institutional_chip("2330", date(2024, 1, 1), date(2024, 1, 10))

    assert len(result) == 1
    item = result[0]
    assert item["foreign_net_buy"] == 1000
    assert item["investment_trust_net_buy"] == 200
    assert item["dealer_net_buy"] == -150


def test_fetch_broker_agg_chip_builds_top5_proxy(monkeypatch):
    client = FinMindClient(api_key="dummy")
    rows = [
        {"date": "2024-01-02", "buy": 100, "sell": 20},
        {"date": "2024-01-02", "buy": 80, "sell": 10},
        {"date": "2024-01-02", "buy": 70, "sell": 30},
        {"date": "2024-01-02", "buy": 60, "sell": 15},
        {"date": "2024-01-02", "buy": 50, "sell": 10},
        {"date": "2024-01-02", "buy": 40, "sell": 60},
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_broker_agg_chip("2330", date(2024, 1, 1), date(2024, 1, 10))

    assert len(result) == 1
    # nets: 80,70,40,45,40,-20 => top5 sum = 275
    assert result[0]["top5_net_buy"] == 275
    assert result[0]["concentration_proxy"] == 275


def test_fetch_disposition_periods_normalizes_dates(monkeypatch):
    client = FinMindClient(api_key="dummy")
    rows = [
        {
            "period_start": "2024-02-01",
            "period_end": "2024-02-10",
            "measure": "10 分鐘撮合一次",
            "condition": "最近 6 個營業日累積收盤價漲幅達 25.38%",
        }
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_disposition_periods("2330", date(2024, 1, 1), date(2024, 3, 1))

    assert len(result) == 1
    assert result[0]["start_date"].isoformat() == "2024-02-01"
    assert result[0]["end_date"].isoformat() == "2024-02-10"
    assert result[0]["disposition_type"] == "10 分鐘撮合一次"
    assert "25.38%" in result[0]["reason"]


def test_fetch_broker_agg_chip_fallback_to_endpoint(monkeypatch):
    client = FinMindClient(api_key="dummy")
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: (_ for _ in ()).throw(RuntimeError("dataset failed")))  # noqa: ARG005

    called = {}

    def _endpoint(endpoint, **kwargs):  # noqa: ANN001
        called["endpoint"] = endpoint
        called["params"] = kwargs
        return [
            {"date": "2024-01-02", "buy_volume": 100, "sell_volume": 20},
            {"date": "2024-01-02", "buy_volume": 80, "sell_volume": 10},
            {"date": "2024-01-02", "buy_volume": 70, "sell_volume": 30},
        ]

    monkeypatch.setattr(client, "_request_endpoint", _endpoint)
    result = client.fetch_broker_agg_chip("2330", date(2024, 1, 1), date(2024, 1, 10))

    assert called["endpoint"] == "taiwan_stock_trading_daily_report_secid_agg"
    assert called["params"]["stock_id"] == "2330"
    assert len(result) == 1
    assert result[0]["top5_net_buy"] == 190
