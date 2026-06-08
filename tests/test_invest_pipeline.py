from __future__ import annotations

from datetime import date

from app.pipeline.invest_pipeline import InvestPipelineService, OneClickPipelineRequest


class _Repo:
    def __init__(self):
        self.snapshot_rows: list[dict] = []
        self.sync_jobs: list[dict] = []

    def get_symbols_by_markets(self, markets):  # noqa: ANN001, ARG002
        return ["1111", "2222", "3333", "4444", "5555", "6666"]

    def count_symbols_with_recent_bars(self, symbols, min_date):  # noqa: ANN001, ARG002
        return len(symbols)

    def upsert_candidate_snapshots(self, run_id, run_date, records):  # noqa: ANN001
        self.snapshot_rows.extend(records)
        return len(records)

    def add_sync_job(self, job_name, status, rows_upserted=0, error_msg=""):  # noqa: ANN001
        self.sync_jobs.append(
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

    def sync_extended_chip_bundle(self, start_date, end_date, symbols):  # noqa: ANN001, ARG002
        return {"rows_upserted_total": len(symbols), "partial_failure": False, "degraded_count": 0}

    def sync_twse_tpex_universe(self):  # noqa: ANN201
        return {"symbols_total": 6}

    def _resolve_symbols(self, symbols):  # noqa: ANN001
        return ["2330", "2317", "2454", "2603", "2881", "2891"] if not symbols else symbols


def test_invest_pipeline_generates_result_and_persists_job(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = InvestPipelineService(repo=repo, sync_service=sync_service)

    monkeypatch.setattr(
        "app.pipeline.invest_pipeline.rebuild_feature_snapshots",
        lambda repo, start_date, end_date, symbols: {  # noqa: ARG005
            "rows_upserted": len(symbols),
            "failed_count": 0,
            "symbols_processed": len(symbols),
        },
    )
    monkeypatch.setattr(
        "app.pipeline.invest_pipeline.generate_candidate_suggestions",
        lambda repo, symbols, trade_date, target_count, data_freshness_required, risk_profile: (  # noqa: ARG005
            [
                {
                    "rank": idx + 1,
                    "symbol": symbol,
                    "name": f"Name{symbol}",
                    "price": 100.0 + idx,
                    "score": 0.8 - idx * 0.02,
                    "risk_score": 0.7 - idx * 0.01,
                    "candidate_type": "entry",
                    "reason_codes": ["test"],
                    "risk_flags": [],
                    "last_bar_date": "2024-04-01",
                    "data_quality": "fresh",
                }
                for idx, symbol in enumerate(symbols[:target_count])
            ],
            False,
        ),
    )

    result = service.run(
        OneClickPipelineRequest(
            trade_date="2024-04-01",
            target_count=8,
            risk_profile="balanced",
            universe="twse_tpex",
            data_freshness_required=True,
        )
    )

    assert result["target_count"] == 8
    assert result["risk_profile"] == "balanced"
    assert len(result["candidates"]) == 6
    assert result["degraded"] is False
    assert result["insufficient_candidates"] is False
    assert any(job["job_name"] == "invest_pipeline" for job in repo.sync_jobs)
