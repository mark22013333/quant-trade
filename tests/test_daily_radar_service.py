from __future__ import annotations

from datetime import date

from app.pipeline.daily_radar import DailyRadarRequest, DailyRadarService


class _Repo:
    def __init__(self):
        self.radar_rows: list[dict] = []
        self.watch_rows: list[dict] = []
        self.jobs: list[dict] = []

    def get_symbols_by_markets(self, markets):  # noqa: ANN001, ARG002
        return ["1111", "2222", "3333"]

    def count_symbols_with_recent_bars(self, symbols, min_date):  # noqa: ANN001, ARG002
        return len(symbols)

    def upsert_daily_radar_snapshots(self, run_id, run_date, records):  # noqa: ANN001
        self.radar_rows.extend(records)
        return len(records)

    def upsert_watch_pool(self, run_id, run_date, records):  # noqa: ANN001
        self.watch_rows.extend(records)
        return len(records)

    def add_sync_job(self, job_name, status, rows_upserted=0, error_msg=""):  # noqa: ANN001
        self.jobs.append(
            {
                "job_name": job_name,
                "status": status,
                "rows_upserted": rows_upserted,
                "error_msg": error_msg,
            }
        )


class _SyncService:
    def sync_market_bundle(self, start_date, end_date, symbols, **kwargs):  # noqa: ANN001, ARG002
        return {"rows_upserted_total": len(symbols), "partial_failure": False}

    def sync_twse_tpex_universe(self):  # noqa: ANN201
        return {"symbols_total": 3}

    def _resolve_symbols(self, symbols):  # noqa: ANN001
        return symbols or ["1111", "2222", "3333"]


def test_daily_radar_service_runs_and_persists(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)

    monkeypatch.setattr(
        "app.pipeline.daily_radar.generate_daily_radar",
        lambda repo, symbols, trade_date, target_count, data_freshness_required, risk_profile: [  # noqa: ARG005
            {
                "rank": 1,
                "symbol": "1111",
                "name": "AAA",
                "price": 100.0,
                "entry_score": 82.4,
                "risk_score": 0.62,
                "action_label": "ENTRY_READY",
                "candidate_type": "entry",
                "reason_tags": ["TREND_OK=True"],
                "blocker_tags": [],
                "last_bar_date": "2024-04-01",
                "data_quality": "fresh",
            },
            {
                "rank": 2,
                "symbol": "2222",
                "name": "BBB",
                "price": 65.0,
                "entry_score": 66.1,
                "risk_score": 0.51,
                "action_label": "WATCH_WAIT_TRIGGER",
                "candidate_type": "watch",
                "reason_tags": ["TRIGGER=False"],
                "blocker_tags": [],
                "last_bar_date": "2024-04-01",
                "data_quality": "fresh",
            },
        ],
    )

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            universe="twse_tpex",
            data_freshness_required=True,
            symbols=None,
        )
    )

    assert result["target_count"] == 15
    assert result["risk_profile"] == "balanced"
    assert len(result["items"]) == 2
    assert result["label_counts"]["ENTRY_READY"] == 1
    assert result["label_counts"]["WATCH_WAIT_TRIGGER"] == 1
    assert result["degraded"] is False
    assert any(job["job_name"] == "daily_radar" for job in repo.jobs)
