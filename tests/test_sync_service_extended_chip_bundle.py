from __future__ import annotations

from datetime import date

from app.data.sync_service import SyncService


class _Repo:
    def __init__(self):
        self.jobs = []

    def get_latest_0050_symbols(self) -> list[str]:
        return ["2330"]

    def upsert_broker_agg(self, symbol: str, records: list[dict]) -> int:  # noqa: ARG002
        return len(records)

    def upsert_shareholding(self, symbol: str, records: list[dict]) -> int:  # noqa: ARG002
        return len(records)

    def upsert_holding_shares_per(self, symbol: str, records: list[dict]) -> int:  # noqa: ARG002
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


class _FinMind:
    def fetch_broker_agg_by_date(self, as_of_date, async_mode=True):  # noqa: ANN001, ARG002
        return [
            {
                "symbol": "2330",
                "date": as_of_date,
                "concentration_proxy": 260.0,
                "top5_net_buy": 260.0,
                "source": "finmind",
            }
        ]

    def fetch_shareholding_by_date(self, as_of_date):  # noqa: ANN001
        return [
            {
                "symbol": "2330",
                "date": as_of_date,
                "major_holder_ratio": 35.0,
                "retail_holder_ratio": 11.0,
                "source": "finmind",
            }
        ]

    def fetch_holding_shares_per_by_date(self, as_of_date):  # noqa: ANN001
        return [
            {
                "symbol": "2330",
                "date": as_of_date,
                "concentration_proxy": 39.0,
                "dispersion_proxy": 9.0,
                "source": "finmind",
            }
        ]


def test_sync_extended_chip_bundle_aggregates_rows():
    repo = _Repo()
    service = SyncService(repo=repo, finmind=_FinMind())

    result = service.sync_extended_chip_bundle(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        symbols=["2330"],
    )

    assert result["partial_failure"] is False
    assert result["degraded_count"] == 0
    assert result["rows_upserted_total"] == 3
    assert set(result["bundle"].keys()) == {"broker_agg_chip", "shareholding", "holding_shares_per"}
