from __future__ import annotations

import json
from datetime import date

from app.data.errors import FinMindRequestError
from app.data.sync_service import SyncService


class DummyRepo:
    def __init__(self):
        self.jobs = []
        self._symbols = ["2330"]

    def get_latest_0050_symbols(self) -> list[str]:
        return self._symbols

    def upsert_daily_bars(self, symbol: str, records: list[dict]) -> int:  # noqa: ARG002
        return len(records)

    def upsert_institutional_chip(self, symbol: str, records: list[dict]) -> int:  # noqa: ARG002
        return len(records)

    def upsert_broker_agg(self, symbol: str, records: list[dict]) -> int:  # noqa: ARG002
        return len(records)

    def upsert_disposition_periods(self, symbol: str, records: list[dict]) -> int:  # noqa: ARG002
        return len(records)

    def upsert_attention_stocks(self, records: list[dict]) -> int:
        return len(records)

    def add_sync_job(self, job_name: str, status: str, rows_upserted: int = 0, error_msg: str = "") -> None:
        self.jobs.append(
            {
                "job_name": job_name,
                "status": status,
                "rows_upserted": rows_upserted,
                "error_msg": error_msg,
            }
        )


class DummyFinMind:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return [
            {
                "date": date(2024, 1, 2),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
                "source": "finmind",
            }
        ]

    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return [
            {
                "date": date(2024, 1, 2),
                "foreign_net_buy": 100.0,
                "investment_trust_net_buy": 20.0,
                "dealer_net_buy": 5.0,
                "source": "finmind",
            }
        ]

    def fetch_broker_agg_chip(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return [
            {
                "date": date(2024, 1, 2),
                "concentration_proxy": 250.0,
                "top5_net_buy": 250.0,
                "source": "finmind",
            }
        ]

    def fetch_disposition_periods(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return [
            {
                "start_date": date(2024, 1, 2),
                "end_date": date(2024, 1, 10),
                "disposition_type": "Notice",
                "reason": "test",
                "source": "finmind",
            }
        ]


def test_sync_market_bundle_aggregates_rows():
    repo = DummyRepo()
    service = SyncService(repo=repo, finmind=DummyFinMind())
    result = service.sync_market_bundle(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        symbols=["2330"],
    )

    assert result["partial_failure"] is False
    assert result["sync_quality"]["status"] == "complete"
    assert result["rows_upserted_total"] == 4
    assert set(result["bundle"].keys()) == {
        "daily_bars",
        "institutional_chip",
        "broker_agg_chip",
        "disposition_periods",
    }


class FailingFinMind(DummyFinMind):
    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        raise FinMindRequestError("upstream timeout")


class EmptyChipFinMind(DummyFinMind):
    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return []

    def fetch_broker_agg_chip(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return []


class EmptyOfficialStock:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return []

    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date):  # noqa: ARG002
        return []


class DummyShioajiGateway:
    def fetch_attention_stocks(self, trade_date: date):
        return [
            {
                "symbol": "2330",
                "trade_date": trade_date,
                "announced_date": trade_date,
                "close": 100.5,
                "reason": "注意交易資訊",
                "source": "shioaji_notice",
            }
        ]


def test_sync_attention_stocks_uses_shioaji_notice_gateway():
    repo = DummyRepo()
    service = SyncService(repo=repo, finmind=DummyFinMind(), shioaji_gateway=DummyShioajiGateway())

    result = service.sync_attention_stocks(trade_date=date(2026, 6, 26))

    assert result["failed_count"] == 0
    assert result["rows_fetched"] == 1
    assert result["rows_upserted"] == 1
    assert repo.jobs[-1]["job_name"] == "sync_attention_stocks"


def test_sync_market_bundle_can_include_attention_stocks():
    repo = DummyRepo()
    service = SyncService(repo=repo, finmind=DummyFinMind(), shioaji_gateway=DummyShioajiGateway())

    result = service.sync_market_bundle(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 26),
        symbols=["2330"],
        include_bars=False,
        include_institutional=False,
        include_broker_agg=False,
        include_disposition=False,
        include_attention=True,
    )

    assert result["partial_failure"] is False
    assert result["bundle"]["attention_stocks"]["rows_upserted"] == 1


def test_sync_market_bundle_reports_structured_failures():
    repo = DummyRepo()
    service = SyncService(repo=repo, finmind=FailingFinMind(), twse_stock=EmptyOfficialStock(), tpex_stock=EmptyOfficialStock())

    result = service.sync_market_bundle(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        symbols=["2330"],
    )

    assert result["partial_failure"] is True
    assert result["failed_count_total"] == 1
    assert result["sync_quality"]["status"] == "partial"
    sample = result["bundle"]["institutional_chip"]["failed_samples"][0]
    assert sample["scope"] == "institutional_chip"
    assert sample["error_type"] == "FinMindRequestError"
    assert result["bundle"]["institutional_chip"]["failed_symbol_scopes"] == [
        {"symbol": "2330", "scope": "institutional_chip"}
    ]

    market_job = repo.jobs[-1]
    assert market_job["status"] == "partial"
    market_job_payload = json.loads(market_job["error_msg"])
    assert market_job_payload["failed_count_total"] == 1
    assert market_job_payload["failed_samples"][0]["symbol"] == "2330"
    assert market_job_payload["failed_samples"][0]["scope"] == "institutional_chip"
    assert market_job_payload["failed_symbol_scopes"] == [{"component": "institutional_chip", "symbol": "2330", "scope": "institutional_chip"}]
    assert "prefer_official_bulk" in market_job_payload["sync_quality"]["components"]["daily_bars"]


def test_symbol_chip_sync_marks_empty_rows_as_no_data_failure():
    repo = DummyRepo()
    service = SyncService(repo=repo, finmind=EmptyChipFinMind(), twse_stock=EmptyOfficialStock(), tpex_stock=EmptyOfficialStock())

    institutional = service.sync_institutional_chip(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        symbols=["1258"],
    )
    broker = service.sync_broker_agg_chip(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        symbols=["1258"],
    )

    assert institutional["rows_upserted"] == 0
    assert institutional["failed_samples"][0]["error_type"] == "NoDataError"
    assert institutional["sync_quality"]["status"] == "partial"
    assert broker["rows_upserted"] == 0
    assert broker["failed_samples"][0]["error_type"] == "NoDataError"
    assert broker["sync_quality"]["status"] == "partial"
