from __future__ import annotations

from datetime import date

import pandas as pd

from app.data.sync_service import SyncService


class DummyRepo:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.sync_jobs: list[dict] = []

    def upsert_daily_bars(self, symbol: str, records: list[dict]) -> int:
        self.rows.setdefault(symbol, []).extend(records)
        return len(records)

    def upsert_institutional_chip(self, symbol: str, records: list[dict]) -> int:
        self.rows.setdefault(symbol, []).extend(records)
        return len(records)

    def upsert_disposition_periods(self, symbol: str, records: list[dict]) -> int:
        self.rows.setdefault(symbol, []).extend(records)
        return len(records)

    def upsert_attention_stocks(self, records: list[dict]) -> int:
        self.rows.setdefault("attention_stocks", []).extend(records)
        return len(records)

    def add_sync_job(self, job_name: str, status: str, rows_upserted: int = 0, error_msg: str = "") -> None:
        self.sync_jobs.append(
            {
                "job_name": job_name,
                "status": status,
                "rows_upserted": rows_upserted,
                "error_msg": error_msg,
            }
        )


class MarketAwareRepo(DummyRepo):
    def __init__(self, markets: dict[str, str]) -> None:
        super().__init__()
        self.markets = {str(symbol).strip().upper(): str(market).strip().upper() for symbol, market in markets.items()}

    def get_instrument_market(self, symbol: str) -> str:
        return self.markets.get(str(symbol).strip().upper(), "")


class BrokenFinMind:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        raise RuntimeError("network unavailable")


class UnexpectedFinMind:
    def __init__(self) -> None:
        self.daily_calls = 0
        self.institutional_calls = 0

    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        self.daily_calls += 1
        raise AssertionError("FinMind should not be called when official bulk has rows")

    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        self.institutional_calls += 1
        raise AssertionError("FinMind should not be called when official bulk has rows")


class EmptyFinMind:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return []

    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return []


class BrokenInstitutionalFinMind(EmptyFinMind):
    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        raise RuntimeError("sponsor required")


class BrokenDispositionFinMind(EmptyFinMind):
    def fetch_disposition_periods(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        raise RuntimeError("sponsor required")


class DummyTwseStock:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return [
            {
                "date": date(2025, 2, 24),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
                "source": "twse_stock_day",
            },
            {
                "date": date(2025, 2, 25),
                "open": 102.0,
                "high": 103.0,
                "low": 101.0,
                "close": 102.5,
                "volume": 1200.0,
                "source": "twse_stock_day",
            },
        ]


class EmptyOfficialStock:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return []


class DummyTwseInstitutionalStock(EmptyOfficialStock):
    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return [
            {
                "date": date(2025, 2, 24),
                "foreign_net_buy": 1000.0,
                "investment_trust_net_buy": 200.0,
                "dealer_net_buy": -50.0,
                "source": "twse_t86",
            }
        ]


class DummyTwseRiskStock(EmptyOfficialStock):
    def fetch_attention_stocks(self, trade_date: date) -> list[dict]:
        return [
            {
                "symbol": "2330",
                "trade_date": trade_date,
                "announced_date": trade_date,
                "close": 100.5,
                "reason": "官方注意交易資訊",
                "source": "twse_notice",
            }
        ]

    def fetch_disposition_periods(self, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return [
            {
                "symbol": "2330",
                "start_date": date(2025, 2, 24),
                "end_date": date(2025, 3, 7),
                "disposition_type": "第一次處置",
                "reason": "官方處置資訊",
                "source": "twse_punish",
            }
        ]


class DummyTpexStock:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return [
            {
                "date": date(2025, 2, 24),
                "open": 164.0,
                "high": 164.0,
                "low": 158.0,
                "close": 158.0,
                "volume": 6_619_000.0,
                "source": "tpex_trading_stock",
            },
            {
                "date": date(2025, 2, 25),
                "open": 158.0,
                "high": 160.0,
                "low": 156.5,
                "close": 159.5,
                "volume": 4_997_000.0,
                "source": "tpex_trading_stock",
            },
        ]


class DummyTwseBulkStock(EmptyOfficialStock):
    def fetch_daily_bars_by_date(self, trade_date: date, symbols=None) -> list[dict]:  # noqa: ANN001
        allow = {str(symbol).strip().upper() for symbol in (symbols or [])}
        row = {
            "date": trade_date,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
            "source": "twse_mi_index",
            "symbol": "2330",
        }
        return [row] if not allow or "2330" in allow else []

    def fetch_institutional_chip_by_date(self, trade_date: date, symbols=None) -> list[dict]:  # noqa: ANN001
        allow = {str(symbol).strip().upper() for symbol in (symbols or [])}
        row = {
            "date": trade_date,
            "foreign_net_buy": 1000.0,
            "investment_trust_net_buy": 200.0,
            "dealer_net_buy": -50.0,
            "source": "twse_t86",
            "symbol": "2330",
        }
        return [row] if not allow or "2330" in allow else []


class DummyTpexBulkStock(EmptyOfficialStock):
    def fetch_daily_bars_by_date(self, trade_date: date, symbols=None) -> list[dict]:  # noqa: ANN001
        allow = {str(symbol).strip().upper() for symbol in (symbols or [])}
        row = {
            "date": trade_date,
            "open": 164.0,
            "high": 164.0,
            "low": 158.0,
            "close": 158.0,
            "volume": 6_619_000.0,
            "source": "tpex_daily_quotes",
            "symbol": "3105",
        }
        return [row] if not allow or "3105" in allow else []

    def fetch_institutional_chip_by_date(self, trade_date: date, symbols=None) -> list[dict]:  # noqa: ANN001
        allow = {str(symbol).strip().upper() for symbol in (symbols or [])}
        row = {
            "date": trade_date,
            "foreign_net_buy": -399000.0,
            "investment_trust_net_buy": 0.0,
            "dealer_net_buy": 95000.0,
            "source": "tpex_institutional",
            "symbol": "3105",
        }
        return [row] if not allow or "3105" in allow else []


class TrackingTpexBulkStock(DummyTpexBulkStock):
    def __init__(self) -> None:
        self.daily_bulk_calls: list[set[str]] = []
        self.institutional_bulk_calls: list[set[str]] = []

    def fetch_daily_bars_by_date(self, trade_date: date, symbols=None) -> list[dict]:  # noqa: ANN001
        self.daily_bulk_calls.append({str(symbol).strip().upper() for symbol in (symbols or [])})
        return super().fetch_daily_bars_by_date(trade_date, symbols=symbols)

    def fetch_institutional_chip_by_date(self, trade_date: date, symbols=None) -> list[dict]:  # noqa: ANN001
        self.institutional_bulk_calls.append({str(symbol).strip().upper() for symbol in (symbols or [])})
        return super().fetch_institutional_chip_by_date(trade_date, symbols=symbols)


class DummyTpexInstitutionalStock(EmptyOfficialStock):
    def fetch_institutional_chip(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        return [
            {
                "date": date(2025, 2, 24),
                "foreign_net_buy": -399000.0,
                "investment_trust_net_buy": 0.0,
                "dealer_net_buy": 95000.0,
                "source": "tpex_institutional",
            }
        ]


def test_sync_daily_bars_fallback_to_local_parquet(tmp_path):
    local_dir = tmp_path / "stock_data"
    local_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = local_dir / "2330.TW_1d.parquet"

    idx = pd.to_datetime(["2025-02-24", "2025-02-25", "2025-02-26"])
    frame = pd.DataFrame(
        {
            "('Open', '2330.TW')": [100.0, 102.0, 103.0],
            "('High', '2330.TW')": [101.0, 103.0, 104.0],
            "('Low', '2330.TW')": [99.0, 101.0, 102.0],
            "('Close', '2330.TW')": [100.5, 102.5, 103.5],
            "('Volume', '2330.TW')": [1000, 1200, 1100],
            "Date": [pd.NaT, pd.NaT, pd.NaT],
        },
        index=idx,
    )
    frame.to_parquet(parquet_path)

    repo = DummyRepo()
    service = SyncService(repo=repo, finmind=BrokenFinMind(), local_data_dir=local_dir)
    result = service.sync_daily_bars(
        start_date=date(2025, 2, 23),
        end_date=date(2025, 2, 28),
        symbols=["2330"],
    )

    assert result["rows_upserted"] == 3
    assert result["fallback_rows"] == 3
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "degraded"
    assert result["sync_quality"]["fallback_rows"] == 3
    assert len(repo.rows.get("2330", [])) == 3
    assert all(item.get("source") == "local_parquet" for item in repo.rows["2330"])


def test_sync_daily_bars_fallback_to_twse_official_when_finmind_fails(tmp_path):
    repo = DummyRepo()
    service = SyncService(repo=repo, finmind=BrokenFinMind(), twse_stock=DummyTwseStock(), local_data_dir=tmp_path / "empty")

    result = service.sync_daily_bars(
        start_date=date(2025, 2, 23),
        end_date=date(2025, 2, 28),
        symbols=["2330"],
    )

    assert result["rows_upserted"] == 2
    assert result["fallback_rows"] == 0
    assert result["official_fallback_rows"] == 2
    assert result["official_fallback_symbols"] == ["2330"]
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "degraded"
    assert result["sync_quality"]["official_fallback_rows"] == 2
    assert repo.sync_jobs[-1]["status"] == "ok"
    assert len(repo.rows.get("2330", [])) == 2
    assert all(item.get("source") == "twse_stock_day" for item in repo.rows["2330"])


def test_sync_daily_bars_fallback_to_tpex_official_after_twse_empty(tmp_path):
    repo = DummyRepo()
    service = SyncService(
        repo=repo,
        finmind=BrokenFinMind(),
        twse_stock=EmptyOfficialStock(),
        tpex_stock=DummyTpexStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_daily_bars(
        start_date=date(2025, 2, 23),
        end_date=date(2025, 2, 28),
        symbols=["3105"],
    )

    assert result["rows_upserted"] == 2
    assert result["fallback_rows"] == 0
    assert result["official_fallback_rows"] == 2
    assert result["official_fallback_symbols"] == ["3105"]
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "degraded"
    assert result["sync_quality"]["official_fallback_rows"] == 2
    assert repo.sync_jobs[-1]["status"] == "ok"
    assert len(repo.rows.get("3105", [])) == 2
    assert all(item.get("source") == "tpex_trading_stock" for item in repo.rows["3105"])


def test_sync_daily_bars_prefers_official_bulk_without_finmind_calls(tmp_path):
    repo = DummyRepo()
    finmind = UnexpectedFinMind()
    service = SyncService(
        repo=repo,
        finmind=finmind,
        twse_stock=DummyTwseBulkStock(),
        tpex_stock=DummyTpexBulkStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_daily_bars(
        start_date=date(2025, 2, 24),
        end_date=date(2025, 2, 24),
        symbols=["2330", "3105"],
        prefer_official_bulk=True,
    )

    assert result["rows_upserted"] == 2
    assert result["official_fallback_rows"] == 2
    assert result["api_mode"] == "official_bulk"
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "complete"
    assert finmind.daily_calls == 0
    assert repo.rows["2330"][0]["source"] == "twse_mi_index"
    assert repo.rows["3105"][0]["source"] == "tpex_daily_quotes"


def test_sync_daily_bars_official_bulk_uses_known_market_client_only(tmp_path):
    repo = MarketAwareRepo({"2330": "TWSE"})
    finmind = UnexpectedFinMind()
    tpex_stock = TrackingTpexBulkStock()
    service = SyncService(
        repo=repo,
        finmind=finmind,
        twse_stock=DummyTwseBulkStock(),
        tpex_stock=tpex_stock,
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_daily_bars(
        start_date=date(2025, 2, 24),
        end_date=date(2025, 2, 24),
        symbols=["2330"],
        prefer_official_bulk=True,
    )

    assert result["rows_upserted"] == 1
    assert result["api_mode"] == "official_bulk"
    assert finmind.daily_calls == 0
    assert tpex_stock.daily_bulk_calls == []
    assert repo.rows["2330"][0]["source"] == "twse_mi_index"


def test_sync_institutional_chip_fallback_to_twse_official_when_finmind_fails(tmp_path):
    repo = DummyRepo()
    service = SyncService(
        repo=repo,
        finmind=BrokenInstitutionalFinMind(),
        twse_stock=DummyTwseInstitutionalStock(),
        tpex_stock=EmptyOfficialStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_institutional_chip(
        start_date=date(2025, 2, 23),
        end_date=date(2025, 2, 28),
        symbols=["2330"],
    )

    assert result["rows_upserted"] == 1
    assert result["official_fallback_rows"] == 1
    assert result["official_fallback_symbols"] == ["2330"]
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "degraded"
    assert result["sync_quality"]["official_fallback_rows"] == 1
    assert repo.sync_jobs[-1]["status"] == "ok"
    assert repo.rows["2330"][0]["source"] == "twse_t86"


def test_sync_institutional_chip_prefers_official_bulk_without_finmind_calls(tmp_path):
    repo = DummyRepo()
    finmind = UnexpectedFinMind()
    service = SyncService(
        repo=repo,
        finmind=finmind,
        twse_stock=DummyTwseBulkStock(),
        tpex_stock=DummyTpexBulkStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_institutional_chip(
        start_date=date(2025, 2, 24),
        end_date=date(2025, 2, 24),
        symbols=["2330", "3105"],
        prefer_official_bulk=True,
    )

    assert result["rows_upserted"] == 2
    assert result["official_fallback_rows"] == 2
    assert result["api_mode"] == "official_bulk"
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "complete"
    assert finmind.institutional_calls == 0
    assert repo.rows["2330"][0]["source"] == "twse_t86"
    assert repo.rows["3105"][0]["source"] == "tpex_institutional"


def test_sync_institutional_chip_official_bulk_uses_known_market_client_only(tmp_path):
    repo = MarketAwareRepo({"2330": "TWSE"})
    finmind = UnexpectedFinMind()
    tpex_stock = TrackingTpexBulkStock()
    service = SyncService(
        repo=repo,
        finmind=finmind,
        twse_stock=DummyTwseBulkStock(),
        tpex_stock=tpex_stock,
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_institutional_chip(
        start_date=date(2025, 2, 24),
        end_date=date(2025, 2, 24),
        symbols=["2330"],
        prefer_official_bulk=True,
    )

    assert result["rows_upserted"] == 1
    assert result["api_mode"] == "official_bulk"
    assert finmind.institutional_calls == 0
    assert tpex_stock.institutional_bulk_calls == []
    assert repo.rows["2330"][0]["source"] == "twse_t86"


def test_sync_institutional_chip_fallback_to_tpex_official_after_twse_empty(tmp_path):
    repo = DummyRepo()
    service = SyncService(
        repo=repo,
        finmind=BrokenInstitutionalFinMind(),
        twse_stock=EmptyOfficialStock(),
        tpex_stock=DummyTpexInstitutionalStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_institutional_chip(
        start_date=date(2025, 2, 23),
        end_date=date(2025, 2, 28),
        symbols=["3105"],
    )

    assert result["rows_upserted"] == 1
    assert result["official_fallback_rows"] == 1
    assert result["official_fallback_symbols"] == ["3105"]
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "degraded"
    assert repo.sync_jobs[-1]["status"] == "ok"
    assert repo.rows["3105"][0]["source"] == "tpex_institutional"


def test_sync_disposition_periods_fallback_to_official_when_finmind_fails(tmp_path):
    repo = DummyRepo()
    service = SyncService(
        repo=repo,
        finmind=BrokenDispositionFinMind(),
        twse_stock=DummyTwseRiskStock(),
        tpex_stock=EmptyOfficialStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_disposition_periods(
        start_date=date(2025, 2, 24),
        end_date=date(2025, 3, 7),
        symbols=["2330"],
    )

    assert result["rows_upserted"] == 1
    assert result["official_fallback_rows"] == 1
    assert result["official_fallback_symbols"] == ["2330"]
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "degraded"
    assert repo.sync_jobs[-1]["status"] == "ok"
    assert repo.rows["2330"][0]["source"] == "twse_punish"


def test_sync_attention_stocks_uses_official_source_without_shioaji(tmp_path):
    repo = DummyRepo()
    service = SyncService(
        repo=repo,
        finmind=EmptyFinMind(),
        twse_stock=DummyTwseRiskStock(),
        tpex_stock=EmptyOfficialStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_attention_stocks(trade_date=date(2025, 2, 24))

    assert result["rows_fetched"] == 1
    assert result["rows_upserted"] == 1
    assert result["failed_count"] == 0
    assert result["sync_quality"]["status"] == "complete"
    assert result["api_mode"] == "official"
    assert repo.sync_jobs[-1]["status"] == "ok"
    assert repo.rows["attention_stocks"][0]["source"] == "twse_notice"


def test_sync_daily_bars_marks_empty_source_as_no_data_failure(tmp_path):
    repo = DummyRepo()
    service = SyncService(
        repo=repo,
        finmind=EmptyFinMind(),
        twse_stock=EmptyOfficialStock(),
        tpex_stock=EmptyOfficialStock(),
        local_data_dir=tmp_path / "empty",
    )

    result = service.sync_daily_bars(
        start_date=date(2025, 2, 23),
        end_date=date(2025, 2, 28),
        symbols=["1258"],
    )

    assert result["rows_upserted"] == 0
    assert result["failed_count"] == 1
    assert result["failed_samples"][0]["symbol"] == "1258"
    assert result["failed_samples"][0]["error_type"] == "NoDataError"
    assert result["sync_quality"]["status"] == "partial"
    assert repo.sync_jobs[-1]["status"] == "partial"
