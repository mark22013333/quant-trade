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


def test_fetch_monthly_revenue_normalizes_growth_fields(monkeypatch):
    client = FinMindClient(api_key="dummy")
    rows = [
        {
            "date": "2024-02-10",
            "revenue_month": "2024-01",
            "revenue": "100000",
            "revenue_year": "12.5",
            "mom_growth_pct": "-3.2",
        }
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_monthly_revenue("2330", "2024-01-01", "2024-02-29")

    assert result == [
        {
            "period": "2024-01",
            "announce_date": date(2024, 2, 10),
            "revenue": 100000.0,
            "revenue_yoy_pct": 12.5,
            "revenue_mom_pct": -3.2,
            "source": "finmind",
        }
    ]


def test_fetch_financial_statement_summary_merges_statement_rows(monkeypatch):
    client = FinMindClient(api_key="dummy")

    def _dataset(dataset, **kwargs):  # noqa: ANN001, ARG001
        if dataset == "TaiwanStockFinancialStatements":
            return [
                {"date": "2024-05-15", "type": "EPS", "value": "2.3"},
                {"date": "2024-05-15", "type": "毛利率", "value": "45.0"},
                {"date": "2024-05-15", "type": "營益率", "value": "30.0"},
            ]
        if dataset == "TaiwanStockBalanceSheet":
            return [{"date": "2024-05-15", "type": "負債比", "value": "35.0"}]
        if dataset == "TaiwanStockCashFlowsStatement":
            return [{"date": "2024-05-15", "type": "營業活動現金流量", "value": "1000"}]
        return []

    monkeypatch.setattr(client, "_request_dataset", _dataset)

    result = client.fetch_financial_statement_summary("2330", "2024-01-01", "2024-06-30")

    assert len(result) == 1
    assert result[0]["period"] == "2024-Q2"
    assert result[0]["eps"] == 2.3
    assert result[0]["gross_margin_pct"] == 45.0
    assert result[0]["operating_margin_pct"] == 30.0
    assert result[0]["debt_ratio_pct"] == 35.0
    assert result[0]["operating_cash_flow"] == 1000.0


def test_fetch_stock_news_infers_risk_tags(monkeypatch):
    client = FinMindClient(api_key="dummy")
    rows = [
        {"date": "2024-06-01", "title": "公司上修獲利並創高", "source_name": "news", "url": "https://example.test/a"},
        {"date": "2024-06-02", "title": "公司遭處分且營運下修", "source_name": "news"},
    ]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)  # noqa: ARG005

    result = client.fetch_stock_news("2330", "2024-06-01", "2024-06-30")

    assert result[0]["risk_tags"] == ["positive_event"]
    assert "negative_event" in result[1]["risk_tags"]
