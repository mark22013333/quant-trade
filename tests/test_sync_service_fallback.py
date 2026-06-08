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

    def add_sync_job(self, job_name: str, status: str, rows_upserted: int = 0, error_msg: str = "") -> None:
        self.sync_jobs.append(
            {
                "job_name": job_name,
                "status": status,
                "rows_upserted": rows_upserted,
                "error_msg": error_msg,
            }
        )


class BrokenFinMind:
    def fetch_daily_bars(self, stock_id: str, start_date: date, end_date: date) -> list[dict]:  # noqa: ARG002
        raise RuntimeError("network unavailable")


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
