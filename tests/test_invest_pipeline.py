from __future__ import annotations

from datetime import date

from app.pipeline.invest_pipeline import InvestPipelineService, OneClickPipelineRequest


class _Repo:
    def __init__(self):
        self.snapshot_rows: list[dict] = []
        self.sync_jobs: list[dict] = []
        self.industry_coverage_sequence: list[dict] = []
        self.industry_coverage_calls: list[dict] = []

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

    def get_instrument_name(self, symbol):  # noqa: ANN001
        return str(symbol)

    def get_instrument_industry_coverage(self, **kwargs):  # noqa: ANN001
        self.industry_coverage_calls.append(dict(kwargs))
        if self.industry_coverage_sequence:
            return self.industry_coverage_sequence.pop(0)
        return {"missing_industry_count": 0, "coverage_pct": 100.0}


class _SyncService:
    def __init__(self):
        self.market_bundle_kwargs: dict = {}
        self.extended_chip_called = False
        self.universe_sync_count = 0

    def sync_market_bundle(self, start_date, end_date, symbols, **kwargs):  # noqa: ANN001, ARG002
        self.market_bundle_kwargs = dict(kwargs)
        return {"rows_upserted_total": len(symbols), "partial_failure": False}

    def sync_extended_chip_bundle(self, start_date, end_date, symbols):  # noqa: ANN001, ARG002
        self.extended_chip_called = True
        return {"rows_upserted_total": len(symbols), "partial_failure": False, "degraded_count": 0}

    def sync_twse_tpex_universe(self):  # noqa: ANN201
        self.universe_sync_count += 1
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
    assert sync_service.market_bundle_kwargs["include_attention"] is True
    assert sync_service.extended_chip_called is True


def test_invest_pipeline_syncs_metadata_when_industry_missing(monkeypatch):
    repo = _Repo()
    repo.industry_coverage_sequence = [
        {"missing_industry_count": 4, "coverage_pct": 20.0},
        {"missing_industry_count": 0, "coverage_pct": 100.0},
    ]
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
        lambda repo, symbols, trade_date, target_count, data_freshness_required, risk_profile: ([], False),  # noqa: ARG005
    )

    result = service.run(
        OneClickPipelineRequest(
            trade_date="2024-04-01",
            target_count=8,
            risk_profile="balanced",
            universe="twse_tpex",
            data_freshness_required=True,
            include_attention=False,
        )
    )

    metrics = result["step_metrics"]["resolve_universe"]
    assert sync_service.universe_sync_count == 1
    assert metrics["missing_industries_before"] == 4
    assert metrics["missing_industries_after"] == 0
    assert metrics["industry_coverage_pct"] == 100.0
    assert "instrument_industry_missing" not in result["degrade_reasons"]


def test_invest_pipeline_can_skip_attention_sync(monkeypatch):
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
        lambda repo, symbols, trade_date, target_count, data_freshness_required, risk_profile: ([], False),  # noqa: ARG005
    )

    result = service.run(
        OneClickPipelineRequest(
            trade_date="2024-04-01",
            universe="twse_tpex",
            include_attention=False,
        )
    )

    assert sync_service.market_bundle_kwargs["include_attention"] is False
    assert result["degraded"] is True
    assert "attention_sync_skipped" in result["degrade_reasons"]


def test_invest_pipeline_can_skip_extended_chip_sync(monkeypatch):
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
        lambda repo, symbols, trade_date, target_count, data_freshness_required, risk_profile: ([], False),  # noqa: ARG005
    )

    result = service.run(
        OneClickPipelineRequest(
            trade_date="2024-04-01",
            universe="twse_tpex",
            include_extended_chip=False,
        )
    )

    assert sync_service.extended_chip_called is False
    assert result["degraded"] is True
    assert "extended_chip_sync_skipped" not in result["degrade_reasons"]
    assert "extended_chip_sync_skipped" in result["skipped_steps"]


def test_invest_pipeline_treats_relaxed_trigger_as_quality_warning(monkeypatch):
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
                    "score": 0.6 - idx * 0.01,
                    "risk_score": 0.3,
                    "candidate_type": "watch",
                    "reason_codes": ["test"],
                    "risk_flags": [],
                    "last_bar_date": "2024-04-01",
                    "data_quality": "fresh",
                }
                for idx, symbol in enumerate(symbols[:target_count])
            ],
            True,
        ),
    )

    result = service.run(
        OneClickPipelineRequest(
            trade_date="2024-04-01",
            target_count=6,
            universe="twse_tpex",
            include_extended_chip=False,
        )
    )

    assert result["degraded"] is False
    assert result["candidate_mode"] == "relaxed_watch"
    assert result["degrade_reasons"] == []
    assert result["quality_warnings"] == ["relaxed_trigger_applied"]
    assert repo.sync_jobs[-1]["status"] == "ok"
    assert "relaxed_trigger_applied" in repo.sync_jobs[-1]["error_msg"]


def test_invest_pipeline_defaults_to_curated_watchlist(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = InvestPipelineService(repo=repo, sync_service=sync_service)
    monkeypatch.setattr("app.services.universe.load_curated_watchlist_symbols", lambda: ["2330", "2454", "2382"])
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

    result = service.run(OneClickPipelineRequest(trade_date="2024-04-01"))

    assert result["universe"] == "curated_watchlist"
    assert result["symbols"] == ["2330", "2454", "2382"]
    assert result["step_metrics"]["resolve_universe"]["universe"] == "curated_watchlist"
