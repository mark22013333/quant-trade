from __future__ import annotations

import json
from datetime import date

from app.pipeline.daily_radar import DailyRadarRequest, DailyRadarService


class _Repo:
    def __init__(self):
        self.radar_rows: list[dict] = []
        self.watch_rows: list[dict] = []
        self.jobs: list[dict] = []
        self.summary_calls: list[tuple[str, dict]] = []
        self.allocation_plan: dict = {}
        self.allocation_outcomes: dict = {}
        self.allocation_calibration: dict = {}
        self.trading_feedback: dict = {}
        self.data_health_overview: dict = {}
        self.industry_coverage_sequence: list[dict] = []
        self.industry_coverage_calls: list[dict] = []

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

    def get_daily_radar_allocation_plan(self, **kwargs):  # noqa: ANN001
        self.summary_calls.append(("get_daily_radar_allocation_plan", dict(kwargs)))
        return self.allocation_plan

    def get_daily_radar_allocation_outcome_summary(self, **kwargs):  # noqa: ANN001
        self.summary_calls.append(("get_daily_radar_allocation_outcome_summary", dict(kwargs)))
        return self.allocation_outcomes

    def get_daily_radar_allocation_calibration(self, **kwargs):  # noqa: ANN001
        self.summary_calls.append(("get_daily_radar_allocation_calibration", dict(kwargs)))
        return self.allocation_calibration

    def get_daily_radar_trading_feedback_summary(self, **kwargs):  # noqa: ANN001
        self.summary_calls.append(("get_daily_radar_trading_feedback_summary", dict(kwargs)))
        return self.trading_feedback

    def get_data_health_overview(self, **kwargs):  # noqa: ANN001
        self.summary_calls.append(("get_data_health_overview", dict(kwargs)))
        return self.data_health_overview

    def get_instrument_name(self, symbol):  # noqa: ANN001
        return str(symbol)

    def get_instrument_industry_coverage(self, **kwargs):  # noqa: ANN001
        self.industry_coverage_calls.append(dict(kwargs))
        if self.industry_coverage_sequence:
            return self.industry_coverage_sequence.pop(0)
        return {"missing_industry_count": 0, "coverage_pct": 100.0}


class _Session:
    def __init__(self):
        self.flushed = False

    def flush(self):
        self.flushed = True


class _SyncService:
    def __init__(self, result: dict | None = None):
        self.market_bundle_kwargs: dict = {}
        self.result = result
        self.universe_sync_count = 0

    def sync_market_bundle(self, start_date, end_date, symbols, **kwargs):  # noqa: ANN001, ARG002
        self.market_bundle_kwargs = dict(kwargs)
        if self.result is not None:
            return self.result
        return {"rows_upserted_total": len(symbols), "partial_failure": False}

    def sync_twse_tpex_universe(self):  # noqa: ANN201
        self.universe_sync_count += 1
        return {"symbols_total": 3}

    def _resolve_symbols(self, symbols):  # noqa: ANN001
        return symbols or ["1111", "2222", "3333"]


def test_daily_radar_service_runs_and_persists(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)

    monkeypatch.setattr(
        "app.pipeline.daily_radar.generate_daily_radar",
        lambda repo, symbols, trade_date, target_count, data_freshness_required, risk_profile, threshold_offset: [  # noqa: ARG005
            {
                "rank": 1,
                "symbol": "1111",
                "name": "AAA",
                "price": 100.0,
                "entry_score": 82.4,
                "risk_score": 0.62,
                "risk_adjusted_score": 76.0,
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
                "risk_adjusted_score": 57.2,
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
    assert result["threshold_offset"] == 0.0
    assert len(result["items"]) == 2
    assert result["items"][0]["risk_adjusted_score"] == 76.0
    assert result["items"][0]["risk_quality_label"]
    assert result["items"][0]["risk_quality_score"] > 0
    assert repo.radar_rows[0]["risk_adjusted_score"] == 76.0
    assert repo.radar_rows[0]["risk_quality_label"] == result["items"][0]["risk_quality_label"]
    assert repo.watch_rows[0]["risk_quality_label"] == result["items"][0]["risk_quality_label"]
    assert result["label_counts"]["ENTRY_READY"] == 1
    assert result["label_counts"]["WATCH_WAIT_TRIGGER"] == 1
    assert result["degraded"] is False
    assert any(job["job_name"] == "daily_radar" for job in repo.jobs)
    assert sync_service.market_bundle_kwargs["include_attention"] is True
    assert sync_service.market_bundle_kwargs["include_broker_agg"] is False
    assert sync_service.market_bundle_kwargs["include_disposition"] is False
    assert sync_service.market_bundle_kwargs["include_fundamentals"] is False
    assert sync_service.market_bundle_kwargs["include_news"] is False
    assert sync_service.market_bundle_kwargs["prefer_official_bulk"] is True


def test_daily_radar_service_passes_threshold_offset_to_engine(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    captured = {}

    def _generate(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", _generate)

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            risk_profile="balanced",
            threshold_offset=2.0,
            universe="twse_tpex",
        )
    )

    assert captured["threshold_offset"] == 2.0
    assert result["threshold_offset"] == 2.0
    assert '"threshold_offset": 2.0' in repo.jobs[-1]["error_msg"]


def test_daily_radar_service_adjusts_threshold_for_risk_off_regime(monkeypatch):
    class _RegimeRepo(_Repo):
        def get_instrument_industry(self, symbol):  # noqa: ANN001
            return "半導體業" if str(symbol) in {"1111", "2222"} else "金融保險業"

        def get_industry_return_context(self, **kwargs):  # noqa: ANN001
            industry = str(kwargs.get("industry") or "")
            if industry == "半導體業":
                return {
                    "status": "ok",
                    "industry": industry,
                    "average_return": -0.08,
                    "breadth": 0.2,
                    "label": "headwind",
                }
            return {
                "status": "ok",
                "industry": industry,
                "average_return": -0.03,
                "breadth": 0.4,
                "label": "headwind",
            }

    repo = _RegimeRepo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    captured = {}

    def _generate(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", _generate)

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            risk_profile="balanced",
            threshold_offset=0.0,
            universe="twse_tpex",
        )
    )

    meta = json.loads(repo.jobs[-1]["error_msg"])
    assert captured["risk_profile"] == "conservative"
    assert captured["threshold_offset"] == 2.0
    assert result["requested_risk_profile"] == "balanced"
    assert result["risk_profile"] == "conservative"
    assert result["threshold_offset"] == 2.0
    assert result["market_regime"]["regime"] == "risk_off"
    assert result["market_regime"]["threshold_offset_adjustment"] == 2.0
    assert meta["market_regime"]["label"] == "風險收縮"


def test_daily_radar_service_persists_threshold_adoption_metadata(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", lambda **kwargs: [])  # noqa: ARG005

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            risk_profile="balanced",
            threshold_offset=2.0,
            universe="twse_tpex",
            threshold_adoption={
                "source": "control_panel_threshold_backtest",
                "action": "manual_apply",
                "pool_label": "全部候選",
                "profile": "balanced",
                "threshold_offset": 2.0,
                "decision_score": 78.5,
                "top_symbols": ["2330", "2454", "2330"],
            },
        )
    )

    meta = json.loads(repo.jobs[-1]["error_msg"])
    assert result["threshold_adoption"]["source"] == "control_panel_threshold_backtest"
    assert result["threshold_adoption"]["top_symbols"] == ["2330", "2454"]
    assert meta["threshold_adoption"]["action"] == "manual_apply"
    assert meta["threshold_adoption"]["decision_score"] == 78.5


def test_daily_radar_service_can_include_extended_data(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", lambda **kwargs: [])  # noqa: ARG005

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            universe="twse_tpex",
            include_extended_data=True,
        )
    )

    assert result["degraded"] is False
    assert sync_service.market_bundle_kwargs["include_broker_agg"] is True
    assert sync_service.market_bundle_kwargs["include_disposition"] is True
    assert sync_service.market_bundle_kwargs["include_fundamentals"] is True
    assert sync_service.market_bundle_kwargs["include_news"] is True


def test_daily_radar_service_syncs_metadata_when_industry_missing(monkeypatch):
    repo = _Repo()
    repo.industry_coverage_sequence = [
        {"missing_industry_count": 2, "coverage_pct": 33.3},
        {"missing_industry_count": 0, "coverage_pct": 100.0},
    ]
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", lambda **kwargs: [])  # noqa: ARG005

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            universe="twse_tpex",
            include_attention=False,
        )
    )

    metrics = result["step_metrics"]["resolve_universe"]
    assert sync_service.universe_sync_count == 1
    assert metrics["missing_industries_before"] == 2
    assert metrics["missing_industries_after"] == 0
    assert metrics["industry_coverage_pct"] == 100.0
    assert "instrument_industry_missing" not in result["degrade_reasons"]


def test_daily_radar_service_can_disable_official_bulk(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", lambda **kwargs: [])  # noqa: ARG005

    service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            universe="twse_tpex",
            prefer_official_bulk=False,
        )
    )

    assert sync_service.market_bundle_kwargs["prefer_official_bulk"] is False


def test_daily_radar_service_can_skip_attention_sync(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", lambda **kwargs: [])  # noqa: ARG005

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            universe="twse_tpex",
            include_attention=False,
        )
    )

    assert sync_service.market_bundle_kwargs["include_attention"] is False
    assert result["degraded"] is True
    assert "attention_sync_skipped" in result["degrade_reasons"]
    assert repo.jobs[-1]["status"] == "partial"


def test_daily_radar_service_records_market_sync_quality_in_job_metadata(monkeypatch):
    repo = _Repo()
    sync_service = _SyncService(
        result={
            "rows_upserted_total": 3,
            "partial_failure": True,
            "failed_count_total": 2,
            "failed_samples": [
                {
                    "scope": "daily_bars",
                    "symbol": "2330",
                    "error_type": "NoDataError",
                    "error": "no data returned",
                }
            ],
            "sync_quality": {
                "status": "partial",
                "rows_upserted": 3,
                "failed_count": 2,
                "components": {
                    "daily_bars": {
                        "status": "partial",
                        "rows_upserted": 2,
                        "failed_count": 1,
                        "api_mode": "official_bulk",
                    },
                    "institutional_chip": {
                        "status": "partial",
                        "rows_upserted": 1,
                        "failed_count": 1,
                        "api_mode": "official_bulk",
                    },
                },
            },
        }
    )
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", lambda **kwargs: [])  # noqa: ARG005

    result = service.run(DailyRadarRequest(trade_date=date(2024, 4, 1), universe="twse_tpex"))

    metadata = json.loads(repo.jobs[-1]["error_msg"])
    assert result["degraded"] is True
    assert result["sync_result"]["failed_count_total"] == 2
    assert repo.jobs[-1]["status"] == "partial"
    assert metadata["prefer_official_bulk"] is True
    assert metadata["failed_count_total"] == 2
    assert metadata["failed_samples"][0]["symbol"] == "2330"
    assert metadata["sync_quality"]["components"]["daily_bars"]["api_mode"] == "official_bulk"


def test_daily_radar_service_flushes_synced_data_before_scoring(monkeypatch):
    repo = _Repo()
    repo.session = _Session()
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)

    def _generate(repo, symbols, trade_date, target_count, data_freshness_required, risk_profile, threshold_offset):  # noqa: ANN001, ARG001
        assert repo.session.flushed is True
        return []

    monkeypatch.setattr("app.pipeline.daily_radar.generate_daily_radar", _generate)

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            universe="twse_tpex",
            data_freshness_required=True,
            symbols=None,
        )
    )

    assert result["items"] == []
    assert repo.session.flushed is True


def test_daily_radar_service_export_includes_allocation_evidence(monkeypatch, tmp_path):
    repo = _Repo()
    repo.allocation_plan = {
        "status": "ok",
        "research_budget": 350_000.0,
        "allocated_value": 80_000.0,
        "allocation_count": 1,
        "calibration_profile": {"key": "standard", "label": "標準", "enabled": True},
        "allocation_items": [
            {
                "symbol": "1111",
                "name": "AAA",
                "industry_group": "半導體業",
                "suggested_value": 80_000.0,
                "suggested_odd_lot_shares": 800,
                "suggested_board_lots": 0,
                "allocation_weight": 1.0,
                "calibration_multiplier": 1.0,
                "next_step": "可建立 Preview，仍需通過 gate",
            }
        ],
        "allocation_diagnostics": {
            "risk_level": "ok",
            "verdict": "配置可先檢查",
            "alerts": [],
            "review_queue": {
                "lanes": {
                    "preview": {
                        "label": "可先建立 Preview",
                        "hint": "配置、動作與阻擋條件相對完整",
                        "count": 1,
                        "alert_plan": {
                            "rule": "清單內標的可優先建立 Preview，但仍需逐檔通過交易 gate。",
                            "checklist": ["先檢查即時價格", "再查部位與可賣股數"],
                        },
                        "items": [
                            {
                                "symbol": "1111",
                                "name": "AAA",
                                "industry_group": "半導體業",
                                "action_label": "ENTRY_READY",
                                "priority_score": 86.0,
                                "suggested_value": 80_000.0,
                                "allocated": True,
                                "reason": "可先檢查價格、部位與正式 gate",
                                "next_step": "可建立 Preview，仍需通過 gate",
                                "alert_rule": "即時價格、部位與 promotion gate 都通過後建立 Preview",
                                "checklist": ["確認即時價格與參考價偏離", "查詢目前持股"],
                            }
                        ],
                    },
                    "trigger": {"label": "設觸發觀察", "hint": "等待條件", "count": 0, "items": []},
                    "risk": {"label": "先處理風險", "hint": "先確認阻擋", "count": 0, "items": []},
                },
                "summary": {"preview_count": 1, "trigger_count": 0, "risk_count": 0},
                "handoff": {
                    "primary_lane": "preview",
                    "primary_label": "可先建立 Preview",
                    "primary_action": "先檢查可建立 Preview",
                    "next_session_note": "可先建立 Preview 1 檔：1111",
                    "top_symbols": ["1111"],
                    "total_review_count": 1,
                },
            },
            "summary_metrics": {"budget_utilization": 0.23},
        },
    }
    repo.allocation_outcomes = {"status": "ok", "verdict": "配置優於排名基準", "evaluated_run_count": 3}
    repo.allocation_calibration = {
        "status": "ok",
        "profile": {"key": "standard", "label": "標準", "enabled": True},
        "top_positive": [{"label": "半導體業", "multiplier": 1.05}],
        "top_negative": [],
    }
    repo.trading_feedback = {
        "status": "ok",
        "verdict": "交易回饋正常",
        "risk_level": "ok",
        "primary_action": "可依行動線檢查。",
        "alerts": [],
        "symbols": [],
    }
    repo.data_health_overview = {
        "status": "danger",
        "score": 0,
        "sync_jobs": [
            {
                "job_name": "sync_market_bundle",
                "status": "partial",
                "failure_summary": {
                    "reason_codes": ["upstream_forbidden"],
                    "scope_failures": [
                        {
                            "scope": "daily_bars",
                            "failed_count": 1,
                            "reason_codes": ["upstream_forbidden"],
                            "symbols": ["1111"],
                        }
                    ],
                    "symbol_failures": [
                        {
                            "symbol": "1111",
                            "failed_count": 1,
                            "scopes": ["daily_bars"],
                            "reason_codes": ["upstream_forbidden"],
                        }
                    ],
                },
            }
        ],
    }
    sync_service = _SyncService()
    service = DailyRadarService(repo=repo, sync_service=sync_service)
    service.reports_dir = tmp_path

    monkeypatch.setattr(
        "app.pipeline.daily_radar.generate_daily_radar",
        lambda repo, symbols, trade_date, target_count, data_freshness_required, risk_profile, threshold_offset: [  # noqa: ARG005
            {
                "rank": 1,
                "symbol": "1111",
                "name": "AAA",
                "price": 100.0,
                "entry_score": 88.0,
                "risk_score": 0.2,
                "risk_adjusted_score": 86.0,
                "action_label": "ENTRY_READY",
                "candidate_type": "entry",
                "reason_tags": ["TREND_OK=True"],
                "blocker_tags": [],
                "last_bar_date": "2024-04-01",
                "data_quality": "stale",
            }
        ],
    )

    result = service.run(
        DailyRadarRequest(
            trade_date=date(2024, 4, 1),
            target_count=15,
            universe="twse_tpex",
            data_freshness_required=True,
        )
    )

    export = result["export"]
    json_payload = json.loads((tmp_path / export["files"]["json"]).read_text(encoding="utf-8"))
    csv_text = (tmp_path / export["files"]["csv"]).read_text(encoding="utf-8")
    review_csv = (tmp_path / export["files"]["allocation_review_preview_csv"]).read_text(encoding="utf-8")
    brief_text = (tmp_path / export["files"]["decision_brief_md"]).read_text(encoding="utf-8")
    summary_methods = [name for name, _kwargs in repo.summary_calls]

    assert "get_daily_radar_allocation_plan" in summary_methods
    assert "get_daily_radar_allocation_outcome_summary" in summary_methods
    assert "get_daily_radar_allocation_calibration" in summary_methods
    assert "get_daily_radar_trading_feedback_summary" in summary_methods
    assert "get_data_health_overview" in summary_methods
    assert json_payload["allocation_plan"]["research_budget"] == 350_000.0
    assert json_payload["allocation_plan"]["allocation_items"][0]["symbol"] == "1111"
    assert json_payload["quality_summary"]["data_readiness"]["status"] == "danger"
    assert json_payload["decision_brief"]["quality"]["lanes"][2]["key"] == "repair_first"
    assert json_payload["decision_brief"]["quality"]["lanes"][2]["data_gap_scope_summary"] == ["daily_bars 1 檔"]
    assert json_payload["allocation_outcomes"]["verdict"] == "配置優於排名基準"
    assert json_payload["allocation_calibration"]["top_positive"][0]["label"] == "半導體業"
    assert json_payload["decision_brief"]["handoff"]["primary_action"] == "先檢查可建立 Preview"
    assert json_payload["items"][0]["risk_quality_label"] == "核心候選"
    assert json_payload["decision_brief"]["risk_quality"]["top_symbols"] == ["1111"]
    assert json_payload["decision_brief"]["action_grade"]["counts"]["可下手"] == 1
    assert json_payload["decision_brief"]["action_grade"]["ready_symbols"] == ["1111"]
    assert json_payload["decision_brief"]["lanes"][0]["items"][0]["risk_quality_label"] == "核心候選"
    assert json_payload["decision_brief"]["lanes"][0]["items"][0]["action_grade_label"] == "可下手"
    assert export["data"]["decision_brief"]["handoff"]["top_symbols"] == ["1111"]
    assert "risk_quality_score,risk_quality_label,risk_quality_reason" in csv_text
    assert "核心候選" in csv_text
    assert "risk_quality_score,risk_quality_label,risk_quality_reason" in review_csv
    assert "- 主線: 先檢查可建立 Preview" in brief_text
    assert "- 行動分級: 可下手 1 檔 / 只觀察 0 檔 / 不碰 0 檔" in brief_text
    assert "- 可下手: 1111" in brief_text
    assert "- 風險品質:" in brief_text
    assert "風險層: 核心候選" in brief_text
    assert "分級: 可下手" in brief_text
    assert "缺口 daily_bars 1 檔" in brief_text
