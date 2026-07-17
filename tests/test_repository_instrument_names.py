from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.index_constituents import load_index_constituent_symbols, write_index_constituents_cache
from app.data.tpex_index_client import TPEX_MARKET_CONTEXT_NAME, TPEX_MARKET_INDEX_NAME, TPEX_SECTOR_INDEX_SOURCE
from app.data.sync_service import SyncService
from app.db.models import Base, DailyBar, DailyRadarSnapshot, IndustryIndexBar, Instrument, SyncJob
from app.db.repository import TradingRepository


def _build_repo() -> tuple[TradingRepository, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    return TradingRepository(session), session


def test_data_health_overview_score_is_normalized_by_component_count() -> None:
    components = [{"status": "ok"} for _ in range(4)] + [{"status": "warn"} for _ in range(8)]

    score = TradingRepository._data_health_overview_score(components, overall_status="warn")

    assert score == 77
    assert score > 0


def test_data_health_overview_score_caps_danger_status() -> None:
    components = [{"status": "ok"} for _ in range(11)] + [{"status": "danger"}]

    score = TradingRepository._data_health_overview_score(components, overall_status="danger")

    assert score == 49


def test_daily_radar_threshold_adoption_summary_tracks_matured_outcomes() -> None:
    repo, session = _build_repo()
    session.add(Instrument(symbol="2330", name="台積電", market="TWSE", industry="半導體業"))
    session.add_all(
        [
            DailyBar(symbol="2330", date=date(2026, 7, 13), open=100, high=101, low=99, close=100, volume=1000),
            DailyBar(symbol="2330", date=date(2026, 7, 14), open=101, high=103, low=100, close=102, volume=1000),
            DailyBar(symbol="2330", date=date(2026, 7, 15), open=102, high=104, low=101, close=103, volume=1000),
            DailyBar(symbol="2330", date=date(2026, 7, 16), open=103, high=105, low=102, close=104, volume=1000),
            DailyBar(symbol="2330", date=date(2026, 7, 17), open=104, high=106, low=103, close=105, volume=1000),
            DailyBar(symbol="2330", date=date(2026, 7, 20), open=105, high=107, low=104, close=106, volume=1000),
        ]
    )
    repo.upsert_daily_radar_snapshots(
        "radar_adopted",
        date(2026, 7, 13),
        [
            {
                "symbol": "2330",
                "name": "台積電",
                "price": 100,
                "rank": 1,
                "entry_score": 88,
                "risk_score": 0.2,
                "risk_adjusted_score": 86,
                "action_label": "ENTRY_READY",
                "reason_tags": [],
                "blocker_tags": [],
            }
        ],
    )
    session.add(
        SyncJob(
            job_name="daily_radar",
            status="ok",
            rows_upserted=1,
            error_msg=json.dumps(
                {
                    "run_id": "radar_adopted",
                    "trade_date": "2026-07-13",
                    "risk_profile": "balanced",
                    "threshold_offset": 2.0,
                    "universe": "research_ready",
                    "label_counts": {"ENTRY_READY": 1},
                    "threshold_adoption": {
                        "source": "threshold_optimization_batch",
                        "pool_label": "全部候選",
                        "threshold_offset": 2.0,
                        "top_symbols": ["2330"],
                    },
                },
                ensure_ascii=False,
            ),
        )
    )
    session.commit()

    summary = repo.get_daily_radar_threshold_adoption_summary(limit=4, lookahead_bars_list=(5,))

    assert summary["status"] == "ok"
    assert summary["tracked_count"] == 1
    assert summary["matured_run_count"] == 1
    assert summary["runs"][0]["adoption"]["pool_label"] == "全部候選"
    assert summary["runs"][0]["horizon_summary"][0]["matured_count"] == 1
    assert round(summary["runs"][0]["horizon_summary"][0]["average_return"], 4) == 0.06

    feedback = repo.get_daily_radar_symbol_adoption_feedback(symbols=["2330"], lookahead_bars_list=(5,))
    assert feedback["status"] == "ok"
    assert feedback["symbols"]["2330"]["evaluated_count"] == 1
    assert feedback["symbols"]["2330"]["positive_rate"] == 1.0
    assert feedback["symbols"]["2330"]["boost"] > 0
    assert feedback["symbols"]["2330"]["confidence_level"] == "low"
    assert 0 < feedback["symbols"]["2330"]["confidence_weight"] < 0.35
    assert feedback["symbols"]["2330"]["horizons"][0]["lookahead_bars"] == 5


def test_sync_job_failure_summary_compacts_upstream_errors() -> None:
    summary = TradingRepository._sync_job_failure_summary(
        json.dumps(
            {
                "failed_count": 2,
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": "2330",
                        "error_type": "FinMindRequestError",
                        "error": (
                            "FinMind request failed after 1 attempt(s): 403 Client Error: Forbidden "
                            "for url: https://api.finmindtrade.com/api/v4/data?token=secret"
                        ),
                    },
                    {
                        "scope": "attention_stocks",
                        "error_type": "RuntimeError",
                        "error": "shioaji notice API is unavailable",
                    },
                ],
                "sync_quality": {
                    "components": {
                        "daily_bars": {"status": "partial", "failed_count": 1, "rows_upserted": 0},
                        "attention_stocks": {"status": "partial", "failed_count": 1, "rows_upserted": 0},
                    }
                },
            },
            ensure_ascii=False,
        )
    )

    assert summary["failed_count"] == 2
    assert summary["reason_codes"] == ["upstream_forbidden", "shioaji_notice_unavailable"]
    assert summary["components"][0]["name"] == "daily_bars"
    assert summary["samples"][0]["message"].endswith("403 Client Error: Forbidden")
    assert summary["scope_failures"][0]["scope"] == "attention_stocks"
    assert summary["scope_failures"][0]["failed_count"] == 1
    assert {item["scope"] for item in summary["scope_failures"]} == {"daily_bars", "attention_stocks"}
    assert summary["symbol_failures"] == [
        {"symbol": "2330", "failed_count": 1, "scopes": ["daily_bars"], "reason_codes": ["upstream_forbidden"]}
    ]
    assert "secret" not in summary["samples"][0]["message"]
    assert "https://api.finmindtrade.com" not in summary["samples"][0]["message"]


def test_data_health_overview_aggregates_recent_sync_no_data_failures(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    repo.add_sync_job(
        "sync_market_bundle",
        "partial",
        rows_upserted=0,
        error_msg=json.dumps(
            {
                "failed_count_total": 1,
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": "1258",
                        "error_type": "NoDataError",
                        "error": "no daily bars returned for 1258",
                    }
                ],
            }
        ),
    )
    repo.add_sync_job("sync_market_bundle", "ok", rows_upserted=9)
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2026, 7, 2), freshness_days=3)

    failures = overview["recent_sync_failures"]["symbol_failures"]
    assert failures[0]["symbol"] == "1258"
    assert failures[0]["scopes"] == ["daily_bars"]
    assert failures[0]["reason_codes"] == ["upstream_no_data"]


def test_pipeline_runs_expose_candidate_mode_and_quality_warnings() -> None:
    repo, session = _build_repo()
    repo.add_sync_job(
        "invest_pipeline",
        "ok",
        rows_upserted=6,
        error_msg=json.dumps(
            {
                "run_id": "run_20260709_abcd1234",
                "trade_date": "2026-07-09",
                "target_count": 6,
                "degraded": False,
                "candidate_mode": "relaxed_watch",
                "insufficient_candidates": False,
                "degrade_reasons": [],
                "quality_warnings": ["relaxed_trigger_applied"],
                "skipped_steps": ["extended_chip_sync_skipped"],
            }
        ),
    )
    session.commit()

    rows = repo.get_pipeline_runs(limit=1)

    assert rows[0]["run_id"] == "run_20260709_abcd1234"
    assert rows[0]["candidate_mode"] == "relaxed_watch"
    assert rows[0]["quality_warnings"] == ["relaxed_trigger_applied"]
    assert rows[0]["skipped_steps"] == ["extended_chip_sync_skipped"]


def test_sync_job_failure_summary_classifies_bad_request_as_upstream_issue() -> None:
    summary = TradingRepository._sync_job_failure_summary(
        json.dumps(
            {
                "failed_count": 1,
                "failed_samples": [
                    {
                        "scope": "broker_agg_chip",
                        "symbol": "1101",
                        "error_type": "FinMindRequestError",
                        "error": (
                            "FinMind request failed after 1 attempt(s): 400 Client Error: Bad Request "
                            "for url: https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report_secid_agg"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    assert summary["reason_codes"] == ["upstream_bad_request"]
    assert summary["symbol_failures"] == [
        {"symbol": "1101", "failed_count": 1, "scopes": ["broker_agg_chip"], "reason_codes": ["upstream_bad_request"]}
    ]


def test_sync_job_failure_summary_classifies_unprocessable_entity_as_bad_request() -> None:
    summary = TradingRepository._sync_job_failure_summary(
        json.dumps(
            {
                "failed_count": 1,
                "failed_samples": [
                    {
                        "scope": "broker_agg_by_date",
                        "date": "2026-07-02",
                        "error_type": "FinMindRequestError",
                        "error": (
                            "FinMind request failed after 1 attempt(s): 422 Client Error: Unprocessable Entity "
                            "for url: https://api.finmindtrade.com/api/v4/data"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    assert summary["reason_codes"] == ["upstream_bad_request"]
    assert summary["samples"][0]["reason_code"] == "upstream_bad_request"


def test_sync_job_failure_summary_classifies_payment_required_as_provider_requirement() -> None:
    summary = TradingRepository._sync_job_failure_summary(
        json.dumps(
            {
                "failed_count": 1,
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": "1507",
                        "error_type": "FinMindRequestError",
                        "error": (
                            "FinMind request failed after 1 attempt(s): 402 Client Error: Payment Required "
                            "for url: https://api.finmindtrade.com/api/v4/data"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    assert summary["reason_codes"] == ["upstream_payment_required"]
    assert summary["samples"][0]["reason_code"] == "upstream_payment_required"
    assert summary["symbol_failures"] == [
        {"symbol": "1507", "failed_count": 1, "scopes": ["daily_bars"], "reason_codes": ["upstream_payment_required"]}
    ]


def test_sync_job_failure_summary_tracks_all_failed_symbol_scopes() -> None:
    summary = TradingRepository._sync_job_failure_summary(
        json.dumps(
            {
                "failed_count": 3,
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": "2928",
                        "error_type": "FinMindRequestError",
                        "error": "402 Client Error: Payment Required",
                    }
                ],
                "failed_symbol_scopes": [
                    {"scope": "daily_bars", "symbol": "2928"},
                    {"scope": "daily_bars", "symbol": "3089"},
                    {"scope": "daily_bars", "symbol": "3144"},
                ],
                "sync_quality": {
                    "components": {
                        "daily_bars": {
                            "status": "partial",
                            "failed_count": 3,
                            "rows_upserted": 0,
                            "official_fallback_rows": 0,
                            "prefer_official_bulk": True,
                            "api_mode": "",
                        }
                    }
                },
            },
            ensure_ascii=False,
        )
    )

    failures = {item["symbol"]: item for item in summary["symbol_failures"]}
    assert set(failures) == {"2928", "3089", "3144"}
    assert failures["2928"]["failed_count"] == 1
    assert failures["3089"]["reason_codes"] == ["upstream_payment_required"]
    assert summary["scope_failures"][0]["symbols"] == ["2928", "3089", "3144"]
    assert summary["components"][0]["prefer_official_bulk"] is True
    assert summary["components"][0]["official_fallback_rows"] == 0


def test_upsert_instruments_profile_writes_name_and_preserves_0050_flag() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(["2330"], market="TW", is_0050=True)
    repo.upsert_instruments(
        [
            {
                "symbol": "2330",
                "name": "台積電",
                "market": "TWSE",
                "industry": "半導體業",
                "is_0050": False,
            }
        ],
        market="TWSE",
        is_0050=False,
    )
    session.commit()

    row = session.get(Instrument, "2330")
    assert row is not None
    assert row.name == "台積電"
    assert row.market == "TWSE"
    assert row.industry == "半導體業"
    assert row.is_0050 is True


def test_attention_stocks_roundtrip_and_active_lookup() -> None:
    repo, session = _build_repo()
    rows = [
        {
            "symbol": "2330",
            "trade_date": date(2026, 6, 26),
            "announced_date": date(2026, 6, 26),
            "close": 100.5,
            "reason": "最近六個營業日累積收盤價漲幅達標。",
            "source": "shioaji_notice",
        }
    ]

    assert repo.upsert_attention_stocks(rows) == 1
    assert repo.upsert_attention_stocks(rows) == 0
    session.commit()

    active = repo.get_active_attention("2330", date(2026, 6, 26))
    inactive = repo.get_active_attention("2330", date(2026, 6, 27))

    assert active["active"] is True
    assert active["count"] == 1
    assert active["records"][0]["reason"].startswith("最近六個營業日")
    assert inactive == {"active": False, "count": 0, "records": []}


def test_sync_twse_tpex_universe_persists_symbol_names() -> None:
    class _StubFinMind:
        def fetch_stock_info(self) -> list[dict]:
            return [
                {"symbol": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體業"},
                {"symbol": "6443", "name": "元晶", "market": "TPEX", "industry": "光電業"},
            ]

    repo, session = _build_repo()
    service = SyncService(repo=repo, finmind=_StubFinMind())
    result = service.sync_twse_tpex_universe()
    session.commit()

    assert result["symbols_total"] == 2
    assert repo.get_instrument_name("2330") == "台積電"
    assert repo.get_instrument_name("6443") == "元晶"
    assert repo.get_instrument_industry("2330") == "半導體業"
    assert repo.get_instrument_industry("6443") == "光電業"


def test_instrument_industry_coverage_surfaces_missing_metadata() -> None:
    repo, _session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "1101", "market": "TWSE", "name": "台泥", "industry": ""},
            {"symbol": "3105", "market": "TPEX", "name": "穩懋"},
        ],
        market="TWSE",
        is_0050=False,
    )

    coverage = repo.get_instrument_industry_coverage()

    assert coverage["status"] == "warn"
    assert coverage["active_symbols"] == 3
    assert coverage["official_industry_symbols"] == 1
    assert coverage["missing_industry_count"] == 2
    assert coverage["coverage_pct"] == 33.3
    assert coverage["missing_symbols"] == ["1101", "3105"]
    assert coverage["repair_command"] == ".venv/bin/python -m app.cli sync-twse-tpex-universe"


def test_daily_radar_change_summary_compares_latest_and_previous_run() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2303", "name": "聯電", "industry": "半導體業"},
            {"symbol": "2317", "name": "鴻海", "industry": "電子零組件業"},
            {"symbol": "2881", "name": "富邦金", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_radar_snapshots(
        "radar_prev",
        date(2026, 6, 24),
        [
            {
                "rank": 1,
                "symbol": "2303",
                "name": "聯電",
                "entry_score": 78.0,
                "risk_score": 0.2,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                "blocker_tags": [],
            },
            {
                "rank": 2,
                "symbol": "2330",
                "name": "台積電",
                "entry_score": 70.0,
                "risk_score": 0.2,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                "blocker_tags": [],
            },
            {
                "rank": 3,
                "symbol": "2317",
                "name": "鴻海",
                "entry_score": 50.0,
                "risk_score": 0.5,
                "action_label": "NOT_RECOMMENDED",
                "reason_tags": ["INDUSTRY_GROUP=電子零組件業"],
                "blocker_tags": ["B_DATA_STALE"],
            },
        ],
    )
    session.flush()
    for row in session.query(DailyRadarSnapshot).filter_by(run_id="radar_prev").all():
        row.created_at = datetime(2026, 6, 24, 8, 0, 0)
    repo.upsert_daily_radar_snapshots(
        "radar_latest",
        date(2026, 6, 25),
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "entry_score": 83.0,
                "risk_score": 0.1,
                "risk_adjusted_score": 87.0,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                "blocker_tags": [],
            },
            {
                "rank": 2,
                "symbol": "2881",
                "name": "富邦金",
                "entry_score": 76.0,
                "risk_score": 0.2,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=金融保險業"],
                "blocker_tags": [],
            },
            {
                "rank": 3,
                "symbol": "2303",
                "name": "聯電",
                "entry_score": 62.0,
                "risk_score": 0.3,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                "blocker_tags": [],
            },
        ],
    )
    session.flush()
    for row in session.query(DailyRadarSnapshot).filter_by(run_id="radar_latest").all():
        row.created_at = datetime(2026, 6, 25, 8, 0, 0)
    session.commit()

    latest_rows = repo.get_latest_daily_radar(count=10)
    assert next(item for item in latest_rows if item["symbol"] == "2330")["risk_adjusted_score"] == 87.0
    watch_row = next(item for item in latest_rows if item["symbol"] == "2303")
    assert watch_row["trigger_plan"]["target_entry_score"] == 70.0
    assert watch_row["trigger_plan"]["entry_gap"] == 8.0
    assert watch_row["trigger_plan"]["trigger_readiness_label"] == "還早"
    assert "Entry Score 重新整理後站上 70 分" in watch_row["trigger_plan"]["summary"]

    summary = repo.get_daily_radar_change_summary(limit=3)

    assert summary["status"] == "ok"
    assert summary["latest_run_id"] == "radar_latest"
    assert summary["previous_run_id"] == "radar_prev"
    assert summary["new_count"] == 1
    assert summary["recurring_count"] == 2
    assert summary["dropped_count"] == 1
    assert summary["entry_ready_new_count"] == 1
    assert summary["entry_ready_recurring_count"] == 1
    assert summary["new_items"][0]["symbol"] == "2881"
    assert summary["dropped_items"][0]["symbol"] == "2317"
    assert summary["improved_items"][0]["symbol"] == "2330"
    assert summary["improved_items"][0]["entry_score_delta"] == 13.0
    assert summary["weakened_items"][0]["symbol"] == "2303"


def test_daily_radar_quality_summary_scores_latest_candidate_pool() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2881", "name": "富邦金", "industry": "金融保險業"},
            {"symbol": "2303", "name": "聯電", "industry": "半導體業"},
            {"symbol": "2317", "name": "鴻海", "industry": "電子零組件業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_radar_snapshots(
        "radar_quality",
        date(2026, 6, 26),
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "entry_score": 80.0,
                "risk_adjusted_score": 91.0,
                "risk_score": 0.12,
                "action_label": "ENTRY_READY",
                "reason_tags": [
                    "INDUSTRY_GROUP=半導體業",
                    "OUTCOME_RELIABILITY_BOOST=1.8",
                    "OUTCOME_RELIABILITY_SAMPLES=12",
                    "ATR_HEAT=normal",
                ],
                "blocker_tags": [],
                "data_quality": "fresh",
                "fundamental_data_quality": "fresh",
            },
            {
                "rank": 2,
                "symbol": "2881",
                "name": "富邦金",
                "entry_score": 88.0,
                "risk_adjusted_score": 73.0,
                "risk_score": 0.2,
                "action_label": "ENTRY_READY",
                "reason_tags": [
                    "INDUSTRY_GROUP=金融保險業",
                    "OUTCOME_RELIABILITY_BOOST=0.8",
                    "OUTCOME_RELIABILITY_SAMPLES=8",
                    "ATR_HEAT=normal",
                ],
                "blocker_tags": [],
                "data_quality": "fresh",
                "fundamental_data_quality": "partial",
            },
            {
                "rank": 3,
                "symbol": "2303",
                "name": "聯電",
                "entry_score": 64.0,
                "risk_adjusted_score": 62.0,
                "risk_score": 0.28,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["INDUSTRY_GROUP=半導體業", "ATR_HEAT=elevated"],
                "blocker_tags": [],
                "data_quality": "fresh",
                "fundamental_data_quality": "partial",
            },
            {
                "rank": 4,
                "symbol": "2317",
                "name": "鴻海",
                "entry_score": 52.0,
                "risk_adjusted_score": 35.0,
                "risk_score": 0.45,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["INDUSTRY_GROUP=電子零組件業", "ATR_HEAT=hot"],
                "blocker_tags": ["B_DATA_STALE"],
                "data_quality": "stale",
                "fundamental_data_quality": "missing",
            },
        ],
    )
    session.flush()
    for row in session.query(DailyRadarSnapshot).filter_by(run_id="radar_quality").all():
        row.created_at = datetime(2026, 6, 26, 8, 0, 0)
    session.commit()

    summary = repo.get_daily_radar_quality_summary(count=10)

    assert summary["status"] == "ok"
    assert summary["run_id"] == "radar_quality"
    assert summary["candidate_count"] == 4
    assert summary["metrics"]["entry_count"] == 2
    assert summary["metrics"]["no_blocker_count"] == 3
    assert summary["metrics"]["reliability_positive_count"] == 2
    assert summary["metrics"]["top_group"] == "半導體業"
    assert summary["quality_score"] > 50
    assert summary["top_picks"][0]["symbol"] == "2330"
    assert summary["top_picks"][0]["risk_adjusted_score"] == 91.0
    assert summary["top_picks"][1]["symbol"] == "2881"
    assert any(alert["code"] == "atr_hot" for alert in summary["alerts"])


def test_daily_radar_quality_summary_penalizes_candidate_data_gaps() -> None:
    items = [
        {
            "rank": 1,
            "symbol": "2330",
            "name": "台積電",
            "industry_group": "半導體業",
            "entry_score": 82.0,
            "risk_adjusted_score": 82.0,
            "risk_score": 0.12,
            "action_label": "ENTRY_READY",
            "blocker_tags": [],
            "data_quality": "stale",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 1.0,
            "outcome_reliability_samples": 8,
            "atr_heat": "normal",
        },
        {
            "rank": 2,
            "symbol": "2881",
            "name": "富邦金",
            "industry_group": "金融保險業",
            "entry_score": 80.0,
            "risk_adjusted_score": 80.0,
            "risk_score": 0.16,
            "action_label": "ENTRY_READY",
            "blocker_tags": [],
            "data_quality": "fresh",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 1.0,
            "outcome_reliability_samples": 8,
            "atr_heat": "normal",
        },
    ]
    overview = {
        "status": "danger",
        "score": 20,
        "sync_jobs": [
            {
                "job_name": "sync_daily_bars",
                "status": "partial",
                "failure_summary": {
                    "reason_codes": ["upstream_forbidden"],
                    "scope_failures": [
                        {
                            "scope": "daily_bars",
                            "failed_count": 1,
                            "reason_codes": ["upstream_forbidden"],
                            "symbols": ["2330"],
                        }
                    ],
                    "symbol_failures": [
                        {
                            "symbol": "2330",
                            "failed_count": 1,
                            "scopes": ["daily_bars"],
                            "reason_codes": ["upstream_forbidden"],
                        }
                    ],
                },
            }
        ],
    }

    summary = TradingRepository._daily_radar_quality_summary(items, data_health_overview=overview)

    assert summary["verdict"] == "先修資料再選股"
    assert summary["risk_level"] == "danger"
    assert summary["data_readiness"]["status"] == "danger"
    assert summary["data_readiness"]["affected_candidate_symbols"][0]["symbol"] == "2330"
    assert summary["metrics"]["data_gap_candidate_count"] == 1
    assert [item["symbol"] for item in summary["top_picks"]] == ["2881"]
    assert summary["top_pick_scope"] == "actionable_quality"
    lanes = {lane["key"]: lane for lane in summary["quality_lanes"]}
    assert lanes["repair_first"]["items"][0]["symbol"] == "2330"
    assert lanes["observe_only"]["items"][0]["symbol"] == "2881"
    assert lanes["research_now"]["count"] == 0
    gap_pick = lanes["repair_first"]["items"][0]
    assert gap_pick["data_gap_count"] == 1
    assert gap_pick["data_gap_scopes"] == ["daily_bars"]
    assert any(alert["code"] == "candidate_data_gap" for alert in summary["alerts"])


def test_daily_radar_quality_summary_ignores_resolved_candidate_sync_failures() -> None:
    items = [
        {
            "rank": 1,
            "symbol": "2330",
            "name": "台積電",
            "industry_group": "半導體業",
            "entry_score": 82.0,
            "risk_adjusted_score": 82.0,
            "risk_score": 0.12,
            "action_label": "ENTRY_READY",
            "blocker_tags": [],
            "reason_tags": ["CHIP_AVAILABLE=True"],
            "data_quality": "fresh",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 1.0,
            "outcome_reliability_samples": 8,
            "atr_heat": "normal",
        }
    ]
    overview = {
        "status": "danger",
        "score": 20,
        "sync_jobs": [
            {
                "job_name": "sync_market_bundle",
                "status": "partial",
                "failure_summary": {
                    "reason_codes": ["upstream_forbidden"],
                    "symbol_failures": [
                        {
                            "symbol": "2330",
                            "failed_count": 2,
                            "scopes": ["daily_bars", "institutional_chip"],
                            "reason_codes": ["upstream_forbidden"],
                        }
                    ],
                },
            }
        ],
    }

    summary = TradingRepository._daily_radar_quality_summary(items, data_health_overview=overview)

    assert summary["metrics"]["data_gap_candidate_count"] == 0
    assert summary["top_picks"][0]["data_gap_count"] == 0
    lanes = {lane["key"]: lane for lane in summary["quality_lanes"]}
    assert lanes["repair_first"]["count"] == 0
    assert not any(alert["code"] == "candidate_data_gap" for alert in summary["alerts"])


def test_daily_radar_quality_summary_keeps_hot_wait_trigger_in_observe_lane() -> None:
    items = [
        {
            "rank": 1,
            "symbol": "2327",
            "name": "國巨*",
            "industry_group": "電子零組件業",
            "entry_score": 70.0,
            "risk_adjusted_score": 52.0,
            "risk_score": 0.65,
            "action_label": "WATCH_WAIT_TRIGGER",
            "blocker_tags": [],
            "data_quality": "fresh",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 0.5,
            "outcome_reliability_samples": 8,
            "atr_heat": "hot",
        },
        {
            "rank": 2,
            "symbol": "2317",
            "name": "鴻海",
            "industry_group": "電子工業",
            "entry_score": 58.0,
            "risk_adjusted_score": 40.0,
            "risk_score": 0.45,
            "action_label": "NOT_RECOMMENDED",
            "blocker_tags": [],
            "data_quality": "fresh",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 0.5,
            "outcome_reliability_samples": 8,
            "atr_heat": "hot",
        },
    ]

    summary = TradingRepository._daily_radar_quality_summary(items, data_health_overview={"status": "ok", "score": 90})
    lanes = {lane["key"]: lane for lane in summary["quality_lanes"]}

    assert [item["symbol"] for item in lanes["observe_only"]["items"]] == ["2327"]
    assert [item["symbol"] for item in lanes["skip_for_now"]["items"]] == ["2317"]
    assert [item["symbol"] for item in summary["top_picks"]] == ["2327"]
    assert summary["top_pick_scope_label"] == "可研究/只觀察"


def test_daily_radar_quality_summary_has_no_top_picks_when_only_skip_candidates() -> None:
    items = [
        {
            "rank": 1,
            "symbol": "2887",
            "name": "台新新光金",
            "industry_group": "金融保險",
            "entry_score": 58.0,
            "risk_adjusted_score": 45.0,
            "risk_score": 0.35,
            "action_label": "NOT_RECOMMENDED",
            "blocker_tags": [],
            "data_quality": "fresh",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 0.1,
            "outcome_reliability_samples": 8,
            "atr_heat": "normal",
        },
        {
            "rank": 2,
            "symbol": "3231",
            "name": "緯創",
            "industry_group": "電腦及週邊設備業",
            "entry_score": 61.0,
            "risk_adjusted_score": 47.0,
            "risk_score": 0.42,
            "action_label": "NOT_RECOMMENDED",
            "blocker_tags": [],
            "data_quality": "fresh",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 0.2,
            "outcome_reliability_samples": 8,
            "atr_heat": "normal",
        },
    ]

    summary = TradingRepository._daily_radar_quality_summary(items, data_health_overview={"status": "ok", "score": 90})
    lanes = {lane["key"]: lane for lane in summary["quality_lanes"]}

    assert summary["top_picks"] == []
    assert summary["top_pick_scope"] == "no_actionable_candidates"
    assert summary["top_pick_scope_label"] == "沒有可行動首選"
    assert [item["symbol"] for item in lanes["skip_for_now"]["items"]] == ["3231", "2887"]


def test_daily_radar_quality_summary_ignores_optional_broker_only_candidate_gap() -> None:
    items = [
        {
            "rank": 1,
            "symbol": "2330",
            "name": "台積電",
            "industry_group": "半導體業",
            "entry_score": 82.0,
            "risk_adjusted_score": 82.0,
            "risk_score": 0.12,
            "action_label": "ENTRY_READY",
            "blocker_tags": [],
            "data_quality": "fresh",
            "fundamental_data_quality": "fresh",
            "outcome_reliability_boost": 1.0,
            "outcome_reliability_samples": 8,
            "atr_heat": "normal",
        }
    ]
    overview = {
        "status": "ok",
        "score": 88,
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "broker_agg_chip",
                    "label": "分點聚合（加值）",
                    "optional": True,
                    "repairable": False,
                }
            ]
        },
        "sync_jobs": [
            {
                "job_name": "sync_broker_agg_chip",
                "status": "partial",
                "failure_summary": {
                    "reason_codes": ["upstream_forbidden"],
                    "scope_failures": [
                        {
                            "scope": "broker_agg_chip",
                            "failed_count": 1,
                            "reason_codes": ["upstream_forbidden"],
                            "symbols": ["2330"],
                        }
                    ],
                    "symbol_failures": [
                        {
                            "symbol": "2330",
                            "failed_count": 1,
                            "scopes": ["broker_agg_chip"],
                            "reason_codes": ["upstream_forbidden"],
                        }
                    ],
                },
            }
        ],
    }

    summary = TradingRepository._daily_radar_quality_summary(items, data_health_overview=overview)

    assert summary["data_readiness"]["status"] == "ok"
    assert summary["metrics"]["data_gap_candidate_count"] == 0
    assert summary["top_picks"][0]["symbol"] == "2330"
    assert summary["top_picks"][0]["data_gap_count"] == 0
    lanes = {lane["key"]: lane for lane in summary["quality_lanes"]}
    assert lanes["repair_first"]["count"] == 0
    assert lanes["research_now"]["items"][0]["symbol"] == "2330"
    assert not any(alert["code"] == "candidate_data_gap" for alert in summary["alerts"])


def test_watch_pool_orders_by_risk_quality_score() -> None:
    repo, session = _build_repo()
    repo.upsert_watch_pool(
        "radar_watch_pool",
        date(2026, 6, 26),
        [
            {
                "symbol": "HOT",
                "name": "高分但過熱",
                "action_label": "ENTRY_READY",
                "entry_score": 92.0,
                "risk_adjusted_score": 90.0,
                "risk_score": 0.9,
                "reason_tags": ["ATR_HEAT=hot", "INDUSTRY_GROUP=半導體"],
                "blocker_tags": ["RISK_HIGH"],
            },
            {
                "symbol": "CLEAN",
                "name": "乾淨候選",
                "action_label": "ENTRY_READY",
                "entry_score": 84.0,
                "risk_adjusted_score": 82.0,
                "risk_score": 0.08,
                "reason_tags": ["ATR_HEAT=normal", "INDUSTRY_GROUP=金融"],
                "blocker_tags": [],
            },
            {
                "symbol": "WATCH",
                "name": "觀察候選",
                "action_label": "WATCH_WAIT_TRIGGER",
                "entry_score": 78.0,
                "risk_adjusted_score": 72.0,
                "risk_score": 0.2,
                "reason_tags": ["ATR_HEAT=normal", "INDUSTRY_GROUP=電機"],
                "blocker_tags": [],
            },
        ],
    )
    session.commit()

    rows = repo.get_watch_pool(limit=3)

    assert [item["symbol"] for item in rows] == ["CLEAN", "WATCH", "HOT"]
    assert rows[0]["risk_adjusted_score"] == 82.0
    assert rows[0]["risk_quality_label"] == "核心候選"
    assert rows[0]["risk_quality_score"] > rows[2]["risk_quality_score"]
    assert "波動過熱" in rows[2]["risk_quality_reason"]


def test_daily_radar_trading_feedback_summary_flags_candidate_operational_risk() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2881", "name": "富邦金", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_radar_snapshots(
        "radar_feedback",
        date(2026, 6, 26),
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "entry_score": 84.0,
                "risk_score": 0.12,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                "blocker_tags": [],
                "data_quality": "fresh",
            },
            {
                "rank": 2,
                "symbol": "2881",
                "name": "富邦金",
                "entry_score": 78.0,
                "risk_score": 0.2,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=金融保險業"],
                "blocker_tags": [],
                "data_quality": "fresh",
            },
        ],
    )
    session.flush()
    for row in session.query(DailyRadarSnapshot).filter_by(run_id="radar_feedback").all():
        row.created_at = datetime(2026, 6, 26, 8, 0, 0)
    repo.add_order_preview_record(
        preview={
            "preview_id": "preview-2330",
            "symbol": "2330",
            "side": "buy",
            "price": 620.0,
            "quantity": 1,
            "estimated_total_cost": 620_000.0,
            "expires_at": "2026-06-26T13:45:00",
            "created_at": "2026-06-26T13:20:00",
        },
        intent={"intent_id": "intent-preview-2330"},
    )
    repo.add_trading_execution_record(
        intent={
            "intent_id": "intent-2881",
            "source": "daily_radar",
            "environment": "live",
            "symbol": "2881",
            "side": "buy",
            "price": 80.0,
        },
        result={
            "intent_id": "intent-2881",
            "environment": "live",
            "symbol": "2881",
            "side": "buy",
            "price": 80.0,
            "quantity": 1,
            "accepted": False,
            "executed": False,
            "status": "rejected",
            "pretrade": {"reason": "promotion_readiness_blocked"},
        },
    )
    repo.add_promotion_gate_record(
        decision={
            "strategy_name": "daily_radar",
            "strategy_version": "v1",
            "paper_days": 1,
            "paper_trades": 1,
            "accepted": False,
            "reason": "paper_days_insufficient",
            "blocking_reasons": ["paper_days_insufficient"],
        }
    )
    repo.add_reconciliation_record(
        result={
            "matched": False,
            "cash_diff": 1200.0,
            "blocking_reasons": ["cash_mismatch"],
            "checked_at": "2026-06-26T15:10:00",
        }
    )
    session.commit()

    summary = repo.get_daily_radar_trading_feedback_summary(count=10, limit=20)
    by_symbol = {item["symbol"]: item for item in summary["symbols"]}

    assert summary["status"] == "ok"
    assert summary["run_id"] == "radar_feedback"
    assert summary["risk_level"] == "danger"
    assert summary["metrics"]["affected_symbol_count"] == 2
    assert summary["metrics"]["pending_preview_count"] == 1
    assert summary["metrics"]["execution_rejected_count"] == 1
    assert summary["metrics"]["latest_reconciliation_matched"] is False
    assert summary["metrics"]["latest_promotion_gate_accepted"] is False
    assert by_symbol["2330"]["pending_preview_count"] == 1
    assert by_symbol["2881"]["execution_rejected_count"] == 1
    assert any(alert["code"] == "reconciliation_mismatch" for alert in summary["alerts"])
    assert any(alert["code"] == "promotion_gate_blocked" for alert in summary["alerts"])

    priority = repo.get_daily_radar_research_priority_summary(lookback_runs=2, limit=5)
    priority_by_symbol = {item["symbol"]: item for item in priority["priority_items"]}

    assert priority["trading_feedback_risk_count"] == 2
    assert priority["next_action_counts"]["先清交易回饋"] == 2
    assert priority["primary_next_action"] == "先清交易回饋"
    assert priority["trading_feedback"]["risk_level"] == "danger"
    assert priority_by_symbol["2330"]["trading_feedback_issue_count"] == 1
    assert priority_by_symbol["2881"]["trading_feedback_issue_count"] == 1
    assert priority_by_symbol["2330"]["priority_score"] < priority_by_symbol["2330"]["priority_score_raw"]
    assert priority_by_symbol["2330"]["priority_tier"] in {"先清回饋", "先清風險"}
    assert priority_by_symbol["2330"]["priority_next_action"] == "先清交易回饋"
    assert "交易回饋待處理" in priority_by_symbol["2330"]["reason_summary"]
    assert priority_by_symbol["2330"]["priority_score_components"]["trading_feedback"] < 0
    assert any("回饋" in item for item in priority_by_symbol["2330"]["priority_score_component_summary"])

    plan = repo.get_daily_radar_allocation_plan(
        portfolio_equity=1_000_000,
        max_exposure_pct=0.30,
        max_single_pct=0.08,
        max_group_pct=0.12,
        lookback_runs=2,
        limit=3,
    )
    skipped_by_symbol = {item["symbol"]: item for item in plan["skipped_items"]}
    risk_lane = plan["allocation_diagnostics"]["review_queue"]["lanes"]["risk"]

    assert plan["allocation_count"] == 0
    assert skipped_by_symbol["2330"]["skip_reason"] == "交易回饋需先處理"
    assert skipped_by_symbol["2881"]["skip_reason"] == "交易回饋需先處理"
    assert plan["allocation_diagnostics"]["summary_metrics"]["trading_feedback_risk_count"] == 2
    assert any(alert["type"] == "trading_feedback_risk" for alert in plan["allocation_diagnostics"]["alerts"])
    assert risk_lane["count"] == 2
    assert risk_lane["items"][0]["trading_feedback_issue_count"] >= 1
    assert risk_lane["items"][0]["checklist"][0].startswith("處理交易回饋")


def test_expire_order_preview_records_marks_only_pending_expired_rows() -> None:
    repo, session = _build_repo()
    repo.add_order_preview_record(
        preview={
            "preview_id": "preview-expired",
            "symbol": "2330",
            "side": "buy",
            "price": 100.0,
            "quantity": 1,
            "expires_at": "2026-06-26T13:20:00",
            "created_at": "2026-06-26T13:18:00",
        }
    )
    repo.add_order_preview_record(
        preview={
            "preview_id": "preview-active",
            "symbol": "2454",
            "side": "buy",
            "price": 100.0,
            "quantity": 1,
            "expires_at": "2026-06-26T13:30:00",
            "created_at": "2026-06-26T13:18:00",
        }
    )
    session.commit()

    assert repo.expire_order_preview_records(now=datetime(2026, 6, 26, 13, 25, 0)) == 1
    session.commit()

    rows = {item["preview_id"]: item for item in repo.list_recent_order_preview_records(limit=10)}
    assert rows["preview-expired"]["status"] == "expired"
    assert rows["preview-expired"]["reason"] == "preview_expired"
    assert rows["preview-active"]["status"] == "created"


def test_daily_radar_stability_summary_scores_recurring_latest_symbols() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2303", "name": "聯電", "industry": "半導體業"},
            {"symbol": "2881", "name": "富邦金", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    industry_by_symbol = {"2330": "半導體業", "2303": "半導體業", "2881": "金融保險業"}
    for run_id, run_date, created_at, rows in [
        (
            "radar_old",
            date(2026, 6, 23),
            datetime(2026, 6, 23, 8, 0, 0),
            [
                {"rank": 1, "symbol": "2330", "name": "台積電", "entry_score": 72.0, "action_label": "WATCH_WAIT_TRIGGER"},
                {"rank": 2, "symbol": "2303", "name": "聯電", "entry_score": 68.0, "action_label": "WATCH_WAIT_TRIGGER"},
            ],
        ),
        (
            "radar_mid",
            date(2026, 6, 24),
            datetime(2026, 6, 24, 8, 0, 0),
            [
                {"rank": 1, "symbol": "2330", "name": "台積電", "entry_score": 78.0, "action_label": "ENTRY_READY"},
                {"rank": 2, "symbol": "2881", "name": "富邦金", "entry_score": 70.0, "action_label": "WATCH_WAIT_TRIGGER"},
            ],
        ),
        (
            "radar_latest",
            date(2026, 6, 25),
            datetime(2026, 6, 25, 8, 0, 0),
            [
                {"rank": 1, "symbol": "2330", "name": "台積電", "entry_score": 84.0, "action_label": "ENTRY_READY"},
                {"rank": 2, "symbol": "2881", "name": "富邦金", "entry_score": 74.0, "action_label": "ENTRY_READY"},
            ],
        ),
    ]:
        repo.upsert_daily_radar_snapshots(
            run_id,
            run_date,
            [
                {
                    **row,
                    "risk_score": 0.2,
                    "reason_tags": [f"INDUSTRY_GROUP={industry_by_symbol[row['symbol']]}"],
                    "blocker_tags": [],
                }
                for row in rows
            ],
        )
        session.flush()
        for snapshot in session.query(DailyRadarSnapshot).filter_by(run_id=run_id).all():
            snapshot.created_at = created_at
    session.commit()

    summary = repo.get_daily_radar_stability_summary(lookback_runs=3, limit=5)

    assert summary["status"] == "ok"
    assert summary["available_runs"] == 3
    assert summary["stable_count"] == 2
    assert summary["strong_streak_count"] == 1
    assert summary["entry_ready_stable_count"] == 2
    assert summary["stable_items"][0]["symbol"] == "2330"
    assert summary["stable_items"][0]["streak_count"] == 3
    assert summary["stable_items"][0]["seen_count"] == 3
    assert summary["stable_items"][0]["entry_score_delta"] == 12.0
    assert summary["stable_items"][0]["risk_quality_label"] == "核心候選"
    assert summary["stable_items"][0]["risk_quality_trend"] == "improving"
    assert summary["stable_items"][0]["risk_quality_score_delta"] > 0
    assert summary["average_risk_quality_score"] > 0
    assert summary["improving_risk_quality_count"] >= 1
    assert summary["stable_items"][1]["symbol"] == "2881"
    assert summary["stable_items"][1]["streak_count"] == 2


def test_daily_radar_cross_day_summaries_ignore_same_day_reruns() -> None:
    repo, session = _build_repo()
    records = [
        ("radar_original", date(2026, 6, 24), datetime(2026, 6, 24, 8, 0, 0), 62.0),
        ("radar_corrected", date(2026, 6, 24), datetime(2026, 6, 24, 9, 0, 0), 70.0),
        ("radar_next_day", date(2026, 6, 25), datetime(2026, 6, 25, 8, 0, 0), 80.0),
    ]
    for run_id, run_date, created_at, entry_score in records:
        repo.upsert_daily_radar_snapshots(
            run_id,
            run_date,
            [
                {
                    "rank": 1,
                    "symbol": "2330",
                    "name": "台積電",
                    "price": 100.0,
                    "entry_score": entry_score,
                    "risk_score": 0.2,
                    "action_label": "WATCH_WAIT_TRIGGER",
                    "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                    "blocker_tags": [],
                }
            ],
        )
        session.flush()
        session.query(DailyRadarSnapshot).filter_by(run_id=run_id).update({"created_at": created_at})
    session.commit()

    changes = repo.get_daily_radar_change_summary(limit=5)
    stability = repo.get_daily_radar_stability_summary(lookback_runs=3, limit=5)
    priority = repo.get_daily_radar_research_priority_summary(lookback_runs=3, limit=5)

    assert changes["latest_run_id"] == "radar_next_day"
    assert changes["previous_run_id"] == "radar_corrected"
    assert changes["latest_run_date"] == "2026-06-25"
    assert changes["previous_run_date"] == "2026-06-24"
    assert changes["duplicate_run_count"] == 1
    assert changes["recurring_items"][0]["entry_score_delta"] == 10.0
    assert stability["available_runs"] == 2
    assert stability["available_days"] == 2
    assert stability["duplicate_run_count"] == 1
    assert stability["stable_items"][0]["streak_count"] == 2
    assert priority["available_runs"] == 2
    assert priority["available_days"] == 2
    assert priority["duplicate_run_count"] == 1


def test_daily_radar_stability_excludes_non_actionable_symbols() -> None:
    repo, session = _build_repo()
    for run_id, run_date in [("radar_day_one", date(2026, 6, 24)), ("radar_day_two", date(2026, 6, 25))]:
        repo.upsert_daily_radar_snapshots(
            run_id,
            run_date,
            [
                {
                    "rank": 1,
                    "symbol": "2330",
                    "name": "台積電",
                    "price": 100.0,
                    "entry_score": 76.0,
                    "risk_score": 0.2,
                    "action_label": "WATCH_WAIT_TRIGGER",
                    "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                    "blocker_tags": [],
                },
                {
                    "rank": 2,
                    "symbol": "2317",
                    "name": "鴻海",
                    "price": 100.0,
                    "entry_score": 65.0,
                    "risk_score": 0.3,
                    "action_label": "NOT_RECOMMENDED",
                    "reason_tags": ["INDUSTRY_GROUP=電子工業"],
                    "blocker_tags": [],
                },
            ],
        )
        session.flush()
        for snapshot in session.query(DailyRadarSnapshot).filter_by(run_id=run_id).all():
            snapshot.created_at = datetime.combine(run_date, datetime.min.time())
    session.commit()

    summary = repo.get_daily_radar_stability_summary(lookback_runs=2, limit=5)

    assert summary["stable_count"] == 1
    assert summary["non_actionable_stable_count"] == 1
    assert [item["symbol"] for item in summary["stable_items"]] == ["2330"]


def test_daily_radar_research_priority_combines_score_stability_and_risk() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2303", "name": "聯電", "industry": "半導體業"},
            {"symbol": "2881", "name": "富邦金", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    industry_by_symbol = {"2330": "半導體業", "2303": "半導體業", "2881": "金融保險業"}
    for run_id, run_date, created_at, rows in [
        (
            "radar_prev",
            date(2026, 6, 24),
            datetime(2026, 6, 24, 8, 0, 0),
            [
                {"rank": 1, "symbol": "2303", "name": "聯電", "entry_score": 78.0, "risk_score": 0.2, "action_label": "ENTRY_READY"},
                {"rank": 2, "symbol": "2330", "name": "台積電", "entry_score": 70.0, "risk_score": 0.2, "action_label": "WATCH_WAIT_TRIGGER"},
            ],
        ),
        (
            "radar_latest",
            date(2026, 6, 25),
            datetime(2026, 6, 25, 8, 0, 0),
            [
                {"rank": 1, "symbol": "2330", "name": "台積電", "entry_score": 84.0, "risk_score": 0.1, "action_label": "ENTRY_READY"},
                {"rank": 2, "symbol": "2881", "name": "富邦金", "entry_score": 77.0, "risk_score": 0.2, "action_label": "ENTRY_READY"},
                {"rank": 3, "symbol": "2303", "name": "聯電", "entry_score": 66.0, "risk_score": 0.4, "action_label": "WATCH_WAIT_TRIGGER"},
            ],
        ),
    ]:
        repo.upsert_daily_radar_snapshots(
            run_id,
            run_date,
            [
                {
                    **row,
                    "reason_tags": [
                        f"INDUSTRY_GROUP={industry_by_symbol[row['symbol']]}",
                        "OUTCOME_RELIABILITY_BOOST=2.0" if row["symbol"] == "2330" else "OUTCOME_RELIABILITY_BOOST=0.0",
                        "OUTCOME_RELIABILITY_SAMPLES=12" if row["symbol"] == "2330" else "OUTCOME_RELIABILITY_SAMPLES=0",
                    ],
                    "blocker_tags": [],
                }
                for row in rows
            ],
        )
        session.flush()
        for snapshot in session.query(DailyRadarSnapshot).filter_by(run_id=run_id).all():
            snapshot.created_at = created_at
    session.commit()

    summary = repo.get_daily_radar_research_priority_summary(lookback_runs=2, limit=3)

    assert summary["status"] == "ok"
    assert summary["candidate_count"] == 3
    assert summary["next_action_counts"]["可建立 Preview"] == 1
    assert summary["next_action_counts"]["設觸發觀察"] == 2
    assert summary["primary_next_action"] == "可建立 Preview"
    assert summary["top_industry_group"] == "半導體業"
    assert summary["top_industry_group_count"] == 2
    assert summary["top_industry_group_share"] > 0.60
    assert summary["priority_group_count"] == 2
    assert summary["priority_concentration_level"] == "warn"
    assert summary["diversified_priority_count"] == 3
    assert summary["diversified_priority_group_count"] == 2
    assert summary["diversified_priority_items"][0]["symbol"] == "2330"
    assert any(item["symbol"] == "2881" for item in summary["diversified_priority_items"])
    assert summary["priority_items"][0]["symbol"] == "2330"
    assert summary["priority_items"][0]["priority_tier"] in {"優先研究", "候選研究"}
    assert summary["priority_items"][0]["priority_next_action"] == "可建立 Preview"
    assert "轉強" in summary["priority_items"][0]["reason_summary"]
    assert "可靠度加分" in summary["priority_items"][0]["reason_summary"]
    top_components = summary["priority_items"][0]["priority_score_components"]
    assert top_components["entry"] > 0
    assert top_components["stability"] > 0
    assert top_components["action"] > 0
    assert top_components["risk"] < 0
    assert any("Entry" in item for item in summary["priority_items"][0]["priority_score_component_summary"])
    assert summary["priority_items"][0]["priority_score"] > summary["priority_items"][1]["priority_score"]
    watch_item = next(item for item in summary["priority_items"] if item["symbol"] == "2303")
    assert watch_item["trigger_plan"]["trigger_readiness_score"] > 0
    assert watch_item["trigger_readiness_score"] == watch_item["trigger_plan"]["trigger_readiness_score"]
    assert watch_item["priority_score_components"]["trigger_readiness"] > 0
    assert any("觸發" in reason for reason in watch_item["reason_summary"])
    assert TradingRepository._daily_radar_priority_score_component_summary({"trigger_readiness": 3.2}) == ["觸發 +3.2"]


def test_daily_radar_priority_tier_keeps_wait_trigger_observable() -> None:
    watch_item = TradingRepository._daily_radar_research_priority_item(
        {
            "rank": 1,
            "symbol": "2454",
            "name": "聯發科",
            "industry_group": "電子工業",
            "entry_score": 50.0,
            "risk_score": 0.9,
            "action_label": "WATCH_WAIT_TRIGGER",
            "blocker_tags": [],
            "trigger_plan": {"trigger_readiness_score": 10.0, "trigger_readiness_label": "還早"},
        },
        None,
        None,
    )
    blocked_item = TradingRepository._daily_radar_research_priority_item(
        {
            "rank": 2,
            "symbol": "2303",
            "name": "聯電",
            "industry_group": "半導體業",
            "entry_score": 64.0,
            "risk_score": 0.3,
            "action_label": "WATCH_WAIT_TRIGGER",
            "blocker_tags": ["B_DATA_STALE"],
            "trigger_plan": {"trigger_readiness_score": 42.0, "trigger_readiness_label": "接近"},
        },
        None,
        None,
    )
    skipped_item = TradingRepository._daily_radar_research_priority_item(
        {
            "rank": 3,
            "symbol": "2887",
            "name": "台新新光金",
            "industry_group": "金融保險",
            "entry_score": 95.0,
            "risk_score": 0.05,
            "action_label": "NOT_RECOMMENDED",
            "blocker_tags": [],
        },
        None,
        None,
    )

    assert watch_item["priority_score"] < 45
    assert watch_item["priority_tier"] == "保留觀察"
    assert watch_item["priority_next_action"] == "設觸發觀察"
    assert blocked_item["priority_tier"] == "先處理阻擋"
    assert blocked_item["priority_next_action"] == "先處理阻擋"
    assert skipped_item["priority_tier"] == "先略過"
    assert skipped_item["priority_next_action"] == "先略過"


def test_daily_radar_allocation_weights_near_trigger_watch_items_higher() -> None:
    base_item = {
        "symbol": "2330",
        "name": "台積電",
        "industry_group": "半導體業",
        "priority_score": 62.0,
        "priority_tier": "候選研究",
        "risk_score": 0.25,
        "action_label": "WATCH_WAIT_TRIGGER",
        "blocker_count": 0,
        "outcome_reliability_boost": 0.0,
    }

    early = TradingRepository._daily_radar_allocation_candidate(
        {**base_item, "trigger_plan": {"trigger_readiness_score": 20.0, "trigger_readiness_label": "還早"}}
    )
    mature = TradingRepository._daily_radar_allocation_candidate(
        {**base_item, "trigger_plan": {"trigger_readiness_score": 82.0, "trigger_readiness_label": "接近觸發"}}
    )

    assert early["eligible"] is True
    assert mature["eligible"] is True
    assert mature["trigger_readiness_multiplier"] > early["trigger_readiness_multiplier"]
    assert mature["allocation_weight"] > early["allocation_weight"]


def test_daily_radar_allocation_uses_premarket_confidence_multiplier() -> None:
    base_item = {
        "symbol": "2330",
        "name": "台積電",
        "industry_group": "半導體業",
        "priority_score": 70.0,
        "priority_tier": "候選研究",
        "risk_score": 0.25,
        "action_label": "ENTRY_READY",
        "blocker_count": 0,
        "outcome_reliability_boost": 0.0,
    }

    neutral = TradingRepository._daily_radar_allocation_candidate(base_item)
    high_confidence = TradingRepository._daily_radar_allocation_candidate(
        {
            **base_item,
            "symbol_adoption_feedback_boost": 3.0,
            "symbol_adoption_feedback_samples": 9,
            "symbol_adoption_feedback_confidence_weight": 0.85,
            "symbol_adoption_feedback_confidence_level": "high",
        }
    )
    negative = TradingRepository._daily_radar_allocation_candidate(
        {
            **base_item,
            "symbol_adoption_feedback_boost": -3.0,
            "symbol_adoption_feedback_samples": 9,
            "symbol_adoption_feedback_confidence_weight": 0.85,
            "symbol_adoption_feedback_confidence_level": "high",
        }
    )
    low_confidence = TradingRepository._daily_radar_allocation_candidate(
        {
            **base_item,
            "symbol_adoption_feedback_boost": 3.0,
            "symbol_adoption_feedback_samples": 1,
            "symbol_adoption_feedback_confidence_weight": 0.2,
            "symbol_adoption_feedback_confidence_level": "low",
        }
    )

    assert high_confidence["premarket_multiplier"] > neutral["premarket_multiplier"]
    assert high_confidence["allocation_weight"] > neutral["allocation_weight"]
    assert negative["premarket_multiplier"] < neutral["premarket_multiplier"]
    assert negative["allocation_weight"] < neutral["allocation_weight"]
    assert low_confidence["premarket_multiplier"] == neutral["premarket_multiplier"]
    assert any(reason["type"] == "adoption_feedback" for reason in high_confidence["premarket_reasons"])
    assert any(reason["type"] == "adoption_feedback_low_confidence" for reason in low_confidence["premarket_reasons"])


def test_daily_radar_allocation_group_balance_reduces_repeated_industry() -> None:
    items = [
        {"symbol": "2330", "industry_group": "半導體業", "priority_score": 80, "allocation_weight": 0.80, "rank": 1},
        {"symbol": "2303", "industry_group": "半導體業", "priority_score": 79, "allocation_weight": 0.79, "rank": 2},
        {"symbol": "2881", "industry_group": "金融保險業", "priority_score": 78, "allocation_weight": 0.78, "rank": 3},
    ]

    balanced = TradingRepository._daily_radar_apply_allocation_group_balance(items)
    by_symbol = {item["symbol"]: item for item in balanced}

    assert by_symbol["2330"]["premarket_multiplier"] == 1.0
    assert by_symbol["2303"]["allocation_weight"] < 0.79
    assert by_symbol["2303"]["premarket_multiplier"] == 0.94
    assert any(reason["type"] == "group_balance" for reason in by_symbol["2303"]["premarket_reasons"])


def test_daily_radar_allocation_plan_caps_single_limit_and_skips_blocked() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2303", "name": "聯電", "industry": "半導體業"},
            {"symbol": "2881", "name": "富邦金", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    industry_by_symbol = {"2330": "半導體業", "2303": "半導體業", "2881": "金融保險業"}
    for run_id, run_date, created_at, rows in [
        (
            "radar_prev",
            date(2026, 6, 24),
            datetime(2026, 6, 24, 8, 0, 0),
            [
                {"rank": 1, "symbol": "2303", "name": "聯電", "price": 48.0, "entry_score": 76.0, "risk_score": 0.2, "action_label": "ENTRY_READY"},
                {"rank": 2, "symbol": "2330", "name": "台積電", "price": 600.0, "entry_score": 72.0, "risk_score": 0.2, "action_label": "WATCH_WAIT_TRIGGER"},
            ],
        ),
        (
            "radar_latest",
            date(2026, 6, 25),
            datetime(2026, 6, 25, 8, 0, 0),
            [
                {"rank": 1, "symbol": "2330", "name": "台積電", "price": 600.0, "entry_score": 86.0, "risk_score": 0.1, "action_label": "ENTRY_READY"},
                {"rank": 2, "symbol": "2881", "name": "富邦金", "price": 70.0, "entry_score": 78.0, "risk_score": 0.18, "action_label": "ENTRY_READY"},
                {"rank": 3, "symbol": "2303", "name": "聯電", "price": 48.0, "entry_score": 70.0, "risk_score": 0.35, "action_label": "WATCH_WAIT_TRIGGER"},
            ],
        ),
    ]:
        repo.upsert_daily_radar_snapshots(
            run_id,
            run_date,
            [
                {
                    **row,
                    "reason_tags": [
                        f"INDUSTRY_GROUP={industry_by_symbol[row['symbol']]}",
                        "OUTCOME_RELIABILITY_BOOST=2.0" if row["symbol"] == "2330" else "OUTCOME_RELIABILITY_BOOST=0.0",
                        "OUTCOME_RELIABILITY_SAMPLES=12" if row["symbol"] == "2330" else "OUTCOME_RELIABILITY_SAMPLES=0",
                    ],
                    "blocker_tags": ["DATA_STALE", "LOW_LIQUIDITY"] if run_id == "radar_latest" and row["symbol"] == "2303" else [],
                }
                for row in rows
            ],
        )
        session.flush()
        for snapshot in session.query(DailyRadarSnapshot).filter_by(run_id=run_id).all():
            snapshot.created_at = created_at
    session.commit()

    plan = repo.get_daily_radar_allocation_plan(
        portfolio_equity=1_000_000,
        max_exposure_pct=0.30,
        max_single_pct=0.08,
        max_group_pct=0.12,
        lookback_runs=2,
        limit=3,
    )

    assert plan["status"] == "ok"
    assert plan["research_budget"] == 300_000
    assert plan["allocation_items"][0]["symbol"] == "2330"
    assert plan["allocation_items"][0]["suggested_value"] <= 80_000
    assert plan["allocation_items"][0]["suggested_odd_lot_shares"] == int(
        plan["allocation_items"][0]["suggested_value"] // 600
    )
    assert "paper/Preview" in plan["allocation_items"][0]["next_step"]
    assert any(item["symbol"] == "2303" and item["skip_reason"] == "阻擋原因偏多" for item in plan["skipped_items"])
    assert plan["allocation_diagnostics"]["summary_metrics"]["entry_count"] >= 1
    assert "budget_utilization" in plan["allocation_diagnostics"]["summary_metrics"]
    assert plan["allocation_diagnostics"]["verdict"]


def test_daily_radar_allocation_diagnostics_flags_concentration() -> None:
    diagnostics = TradingRepository._daily_radar_allocation_diagnostics(
        allocation_items=[
            {"symbol": "2330", "action_label": "ENTRY_READY", "risk_score": 0.18, "calibration_multiplier": 1.04},
            {"symbol": "2303", "action_label": "ENTRY_READY", "risk_score": 0.22, "calibration_multiplier": 0.98},
            {
                "symbol": "2454",
                "action_label": "WATCH_WAIT_TRIGGER",
                "entry_score": 68.0,
                "risk_score": 0.30,
                "price": 900.0,
                "atr_heat": "elevated",
                "data_quality": "fresh",
                "calibration_multiplier": 1.02,
            },
        ],
        skipped_items=[],
        research_budget=300_000,
        allocated_value=210_000,
        group_summary=[{"industry_group": "半導體業", "allocated_value": 210_000, "allocated_pct": 0.21}],
        item_limit=6,
    )

    assert diagnostics["risk_level"] == "warn"
    assert diagnostics["verdict"] == "先處理配置風險"
    assert round(diagnostics["summary_metrics"]["budget_utilization"], 2) == 0.7
    assert round(diagnostics["summary_metrics"]["max_group_budget_share"], 2) == 0.7
    assert any(alert["type"] == "group_concentration" for alert in diagnostics["alerts"])
    assert diagnostics["next_actions"][0].startswith("先找第二、第三族群")
    queue = diagnostics["review_queue"]["lanes"]
    assert queue["preview"]["count"] == 2
    assert queue["trigger"]["count"] == 1
    assert queue["risk"]["count"] == 0
    assert queue["preview"]["items"][0]["symbol"] in {"2330", "2303"}
    assert queue["preview"]["alert_plan"]["rule"].startswith("清單內標的可優先建立 Preview")
    assert "確認即時價格與參考價偏離" in queue["preview"]["items"][0]["checklist"]
    assert queue["preview"]["items"][0]["alert_rule"].startswith("即時價格")
    trigger_item = queue["trigger"]["items"][0]
    assert trigger_item["alert_rule"].startswith("Entry Score")
    assert trigger_item["trigger_plan"]["target_entry_score"] == 71.0
    assert trigger_item["trigger_plan"]["breakout_price"] == 909.0
    assert trigger_item["trigger_plan"]["pullback_price"] == 868.5
    assert any("ATR 熱度不再升高" in rule for rule in trigger_item["trigger_plan"]["rules"])
    handoff = diagnostics["review_queue"]["handoff"]
    assert handoff["primary_lane"] == "preview"
    assert handoff["primary_action"] == "先檢查可建立 Preview"
    assert "可先建立 Preview 2 檔" in handoff["next_session_note"]


def test_daily_radar_allocation_review_queue_ignores_plain_skipped_not_recommended() -> None:
    diagnostics = TradingRepository._daily_radar_allocation_diagnostics(
        allocation_items=[
            {
                "symbol": "2881",
                "name": "富邦金",
                "action_label": "WATCH_WAIT_TRIGGER",
                "industry_group": "金融保險業",
                "risk_score": 0.22,
                "entry_score": 70.0,
                "price": 80.0,
                "data_quality": "fresh",
                "priority_score": 78.0,
                "suggested_value": 80_000,
                "calibration_multiplier": 1.0,
            }
        ],
        skipped_items=[
            {
                "symbol": "2207",
                "name": "和泰車",
                "action_label": "NOT_RECOMMENDED",
                "skip_reason": "最新雷達不建議",
                "priority_score": 55.0,
                "blocker_count": 0,
                "trading_feedback_risk_level": "ok",
                "trading_feedback_issue_count": 0,
            }
        ],
        research_budget=300_000,
        allocated_value=80_000,
        group_summary=[{"industry_group": "金融保險業", "allocated_value": 80_000, "allocated_pct": 0.08}],
        item_limit=6,
    )

    queue = diagnostics["review_queue"]["lanes"]
    handoff = diagnostics["review_queue"]["handoff"]

    assert queue["trigger"]["count"] == 1
    assert queue["trigger"]["items"][0]["symbol"] == "2881"
    assert queue["trigger"]["items"][0]["trigger_plan"]["breakout_price"] == 80.8
    assert queue["risk"]["count"] == 0
    assert queue["risk"]["items"] == []
    assert handoff["primary_lane"] == "trigger"
    assert handoff["primary_action"] == "先設定觸發觀察"


def test_daily_radar_allocation_review_queue_keeps_monitor_only_watch_items() -> None:
    diagnostics = TradingRepository._daily_radar_allocation_diagnostics(
        allocation_items=[
            {
                "symbol": "2327",
                "name": "國巨*",
                "action_label": "WATCH_WAIT_TRIGGER",
                "industry_group": "電子零組件業",
                "risk_score": 0.62,
                "entry_score": 70.0,
                "price": 900.0,
                "data_quality": "fresh",
                "priority_score": 53.0,
                "suggested_value": 80_000,
                "calibration_multiplier": 1.0,
            }
        ],
        skipped_items=[
            {
                "symbol": "2454",
                "name": "聯發科",
                "action_label": "WATCH_WAIT_TRIGGER",
                "industry_group": "電子工業",
                "risk_score": 0.84,
                "entry_score": 67.0,
                "price": 3925.0,
                "data_quality": "fresh",
                "priority_score": 38.0,
                "skip_reason": "觀察分數未達配置門檻",
                "monitor_only": True,
                "allocation_lane": "monitor_only",
                "blocker_count": 0,
                "trading_feedback_risk_level": "ok",
                "trading_feedback_issue_count": 0,
                "trigger_plan": {
                    "summary": "Entry Score 重新整理後站上 70 分",
                    "checks": ["等 Entry Score 從 67.0 提升到至少 70"],
                },
            },
            {
                "symbol": "2887",
                "name": "台新新光金",
                "action_label": "NOT_RECOMMENDED",
                "skip_reason": "最新雷達不建議",
                "priority_score": 55.0,
                "blocker_count": 0,
                "trading_feedback_risk_level": "ok",
                "trading_feedback_issue_count": 0,
            },
        ],
        research_budget=300_000,
        allocated_value=80_000,
        group_summary=[{"industry_group": "電子零組件業", "allocated_value": 80_000, "allocated_pct": 0.08}],
        item_limit=6,
    )

    queue = diagnostics["review_queue"]["lanes"]
    trigger_by_symbol = {item["symbol"]: item for item in queue["trigger"]["items"]}

    assert diagnostics["summary_metrics"]["monitor_only_count"] == 1
    assert any(alert["type"] == "monitor_only_watchlist" for alert in diagnostics["alerts"])
    assert queue["trigger"]["count"] == 2
    assert trigger_by_symbol["2454"]["allocated"] is False
    assert trigger_by_symbol["2454"]["monitor_only"] is True
    assert trigger_by_symbol["2454"]["allocation_lane"] == "monitor_only"
    assert trigger_by_symbol["2454"]["reason"].startswith("盯盤備選")
    assert trigger_by_symbol["2454"]["alert_rule"].startswith("優先分回到 45")
    assert "2454 聯發科 | 盯盤備選 | Entry>=70" in trigger_by_symbol["2454"]["trigger_watchlist_note"]
    assert "優先分 38" in trigger_by_symbol["2454"]["trigger_watchlist_note"]
    assert any("只盯盤不配置" in item for item in trigger_by_symbol["2454"]["checklist"])
    assert queue["risk"]["count"] == 0


def test_daily_radar_allocation_plan_applies_outcome_calibration() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2881", "name": "富邦金", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    for offset in range(4):
        run_date = date(2026, 6, 1) + timedelta(days=offset)
        repo.upsert_daily_radar_snapshots(
            f"radar_history_{offset}",
            run_date,
            [
                {
                    "rank": 1,
                    "symbol": "2330",
                    "name": "台積電",
                    "price": 100.0,
                    "entry_score": 80.0,
                    "risk_score": 0.1,
                    "action_label": "ENTRY_READY",
                    "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                    "blocker_tags": [],
                },
                {
                    "rank": 2,
                    "symbol": "2881",
                    "name": "富邦金",
                    "price": 100.0,
                    "entry_score": 80.0,
                    "risk_score": 0.1,
                    "action_label": "ENTRY_READY",
                    "reason_tags": ["INDUSTRY_GROUP=金融保險業"],
                    "blocker_tags": [],
                },
            ],
        )
        session.flush()
        for snapshot in session.query(DailyRadarSnapshot).filter_by(run_id=f"radar_history_{offset}").all():
            snapshot.created_at = datetime(2026, 6, 1 + offset, 8, 0, 0)
    for symbol, close in [("2330", 112.0), ("2881", 88.0)]:
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": date(2026, 6, day), "open": 1, "high": 1, "low": 1, "close": close, "volume": 1_000_000}
                for day in range(2, 12)
            ],
        )
    repo.upsert_daily_radar_snapshots(
        "radar_latest",
        date(2026, 6, 25),
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "price": 100.0,
                "entry_score": 80.0,
                "risk_score": 0.1,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                "blocker_tags": [],
            },
            {
                "rank": 2,
                "symbol": "2881",
                "name": "富邦金",
                "price": 100.0,
                "entry_score": 80.0,
                "risk_score": 0.1,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=金融保險業"],
                "blocker_tags": [],
            },
        ],
    )
    session.flush()
    for snapshot in session.query(DailyRadarSnapshot).filter_by(run_id="radar_latest").all():
        snapshot.created_at = datetime(2026, 6, 25, 8, 0, 0)
    session.commit()

    plan = repo.get_daily_radar_allocation_plan(
        portfolio_equity=1_000_000,
        max_exposure_pct=0.50,
        max_single_pct=0.50,
        max_group_pct=0.50,
        lookback_runs=2,
        limit=2,
        calibration_profile="standard",
    )
    conservative_plan = repo.get_daily_radar_allocation_plan(
        portfolio_equity=1_000_000,
        max_exposure_pct=0.50,
        max_single_pct=0.50,
        max_group_pct=0.50,
        lookback_runs=2,
        limit=2,
        calibration_profile="conservative",
    )
    off_plan = repo.get_daily_radar_allocation_plan(
        portfolio_equity=1_000_000,
        max_exposure_pct=0.50,
        max_single_pct=0.50,
        max_group_pct=0.50,
        lookback_runs=2,
        limit=2,
        calibration_profile="off",
    )

    by_symbol = {item["symbol"]: item for item in plan["allocation_items"]}
    conservative_by_symbol = {item["symbol"]: item for item in conservative_plan["allocation_items"]}
    off_by_symbol = {item["symbol"]: item for item in off_plan["allocation_items"]}
    assert plan["calibration"]["status"] == "ok"
    assert plan["calibration_profile"]["key"] == "standard"
    assert by_symbol["2330"]["calibration_multiplier"] > 1.0
    assert by_symbol["2881"]["calibration_multiplier"] < 1.0
    assert by_symbol["2330"]["suggested_value"] > by_symbol["2881"]["suggested_value"]
    assert by_symbol["2330"]["calibration_reasons"][0]["group"] == "industry"
    standard_gap = by_symbol["2330"]["suggested_value"] - by_symbol["2881"]["suggested_value"]
    conservative_gap = conservative_by_symbol["2330"]["suggested_value"] - conservative_by_symbol["2881"]["suggested_value"]
    off_gap = abs(off_by_symbol["2330"]["suggested_value"] - off_by_symbol["2881"]["suggested_value"])
    assert standard_gap > conservative_gap > off_gap
    assert off_plan["calibration_profile"]["enabled"] is False
    assert off_by_symbol["2330"]["calibration_multiplier"] == 1.0


def test_daily_radar_allocation_outcomes_compare_against_rank_baseline() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2303", "name": "聯電", "industry": "半導體業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_radar_snapshots(
        "radar_matured",
        date(2026, 6, 25),
        [
            {
                "rank": 1,
                "symbol": "2303",
                "name": "聯電",
                "price": 50.0,
                "entry_score": 70.0,
                "risk_score": 0.2,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
                "blocker_tags": [],
            },
            {
                "rank": 2,
                "symbol": "2330",
                "name": "台積電",
                "price": 100.0,
                "entry_score": 95.0,
                "risk_score": 0.05,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=半導體業", "OUTCOME_RELIABILITY_BOOST=2.0", "OUTCOME_RELIABILITY_SAMPLES=12"],
                "blocker_tags": [],
            },
        ],
    )
    for snapshot in session.query(DailyRadarSnapshot).filter_by(run_id="radar_matured").all():
        snapshot.created_at = datetime(2026, 6, 25, 8, 0, 0)
    repo.upsert_daily_bars(
        "2303",
        [
            {"date": date(2026, 6, 26), "open": 1, "high": 1, "low": 1, "close": 50.0, "volume": 1_000_000},
            {"date": date(2026, 6, 27), "open": 1, "high": 1, "low": 1, "close": 49.0, "volume": 1_000_000},
            {"date": date(2026, 6, 28), "open": 1, "high": 1, "low": 1, "close": 48.0, "volume": 1_000_000},
            {"date": date(2026, 6, 29), "open": 1, "high": 1, "low": 1, "close": 47.0, "volume": 1_000_000},
            {"date": date(2026, 6, 30), "open": 1, "high": 1, "low": 1, "close": 45.0, "volume": 1_000_000},
        ],
    )
    repo.upsert_daily_bars(
        "2330",
        [
            {"date": date(2026, 6, 26), "open": 1, "high": 1, "low": 1, "close": 100.0, "volume": 1_000_000},
            {"date": date(2026, 6, 27), "open": 1, "high": 1, "low": 1, "close": 105.0, "volume": 1_000_000},
            {"date": date(2026, 6, 28), "open": 1, "high": 1, "low": 1, "close": 110.0, "volume": 1_000_000},
            {"date": date(2026, 6, 29), "open": 1, "high": 1, "low": 1, "close": 115.0, "volume": 1_000_000},
            {"date": date(2026, 6, 30), "open": 1, "high": 1, "low": 1, "close": 120.0, "volume": 1_000_000},
        ],
    )
    session.commit()

    summary = repo.get_daily_radar_allocation_outcome_summary(
        portfolio_equity=1_000_000,
        max_exposure_pct=0.30,
        max_single_pct=0.10,
        max_group_pct=0.20,
        lookahead_bars=5,
        max_runs=3,
        limit=1,
    )

    assert summary["status"] == "ok"
    assert summary["evaluated_run_count"] == 1
    assert summary["recent_runs"][0]["top_symbols"] == ["2330"]
    assert round(summary["recent_runs"][0]["allocation_return"], 4) == 0.2
    assert round(summary["recent_runs"][0]["baseline_return"], 4) == -0.1
    assert summary["average_alpha"] > 0
    assert summary["verdict"] == "配置優於排名基準"


def test_industry_return_context_prefers_twse_index_bars() -> None:
    repo, session = _build_repo()
    repo.upsert_industry_index_bars(
        [
            {
                "date": date(2024, 6, 21),
                "index_name": "半導體類指數",
                "industry": "半導體業",
                "close": 100.0,
                "change_pct": 0.0,
            },
            {
                "date": date(2024, 6, 24),
                "index_name": "半導體類指數",
                "industry": "半導體業",
                "close": 106.0,
                "change_pct": 0.06,
            },
        ]
    )
    session.commit()

    context = repo.get_industry_return_context(
        industry="半導體業",
        start_date=date(2024, 6, 21),
        end_date=date(2024, 6, 24),
    )

    assert context["status"] == "ok"
    assert context["method"] == "twse_industry_price_index"
    assert context["index_name"] == "半導體類指數"
    assert context["average_return"] == 0.06000000000000005


def test_upsert_industry_index_bars_deduplicates_same_batch() -> None:
    repo, session = _build_repo()

    count = repo.upsert_industry_index_bars(
        [
            {
                "date": date(2026, 7, 1),
                "index_name": "櫃買指數",
                "industry": "上櫃市場",
                "close": 431.23,
                "change": 4.26,
                "change_pct": 0.0,
                "source": "tpex_index_info",
            },
            {
                "date": date(2026, 7, 1),
                "index_name": "櫃買指數",
                "industry": "上櫃市場",
                "close": 432.0,
                "change": 5.03,
                "change_pct": 0.0118,
                "source": "tpex_index_info",
            },
        ]
    )
    session.commit()

    rows = session.query(IndustryIndexBar).all()
    assert count == 1
    assert len(rows) == 1
    assert rows[0].close == 432.0
    assert rows[0].change == 5.03


def test_upsert_industry_index_bars_updates_pending_row_before_commit() -> None:
    repo, session = _build_repo()

    assert repo.upsert_industry_index_bars(
        [
            {
                "date": date(2026, 7, 1),
                "index_name": "櫃買指數",
                "industry": "上櫃市場",
                "close": 431.23,
                "source": "tpex_index_info",
            }
        ]
    ) == 1
    assert repo.upsert_industry_index_bars(
        [
            {
                "date": date(2026, 7, 1),
                "index_name": "櫃買指數",
                "industry": "上櫃市場",
                "close": 432.5,
                "change": 5.53,
                "source": "tpex_index_info",
            }
        ]
    ) == 1
    session.commit()

    rows = session.query(IndustryIndexBar).all()
    assert len(rows) == 1
    assert rows[0].close == 432.5
    assert rows[0].change == 5.53


def test_industry_return_context_uses_tpex_market_index_for_tpex_only() -> None:
    repo, session = _build_repo()
    repo.upsert_industry_index_bars(
        [
            {
                "date": date(2024, 6, 21),
                "index_name": "文化創意類指數",
                "industry": "文化創意業",
                "close": 100.0,
                "source": "twse_mi_index",
            },
            {
                "date": date(2024, 6, 24),
                "index_name": "文化創意類指數",
                "industry": "文化創意業",
                "close": 110.0,
                "source": "twse_mi_index",
            },
            {
                "date": date(2024, 6, 21),
                "index_name": TPEX_MARKET_INDEX_NAME,
                "industry": TPEX_MARKET_CONTEXT_NAME,
                "close": 100.0,
                "source": "tpex_index_info",
            },
            {
                "date": date(2024, 6, 24),
                "index_name": TPEX_MARKET_INDEX_NAME,
                "industry": TPEX_MARKET_CONTEXT_NAME,
                "close": 103.0,
                "source": "tpex_index_info",
            },
        ]
    )
    session.commit()

    context = repo.get_industry_return_context(
        industry="文化創意業",
        start_date=date(2024, 6, 21),
        end_date=date(2024, 6, 24),
        markets=["TPEX"],
    )

    assert context["status"] == "ok"
    assert context["method"] == "tpex_market_price_index"
    assert context["industry"] == TPEX_MARKET_CONTEXT_NAME
    assert abs(context["average_return"] - 0.03) < 1e-12


def test_industry_return_context_prefers_tpex_sector_index_for_tpex_only() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "3105", "market": "TPEX", "name": "穩懋", "industry": "半導體業"},
            {"symbol": "6488", "market": "TPEX", "name": "環球晶", "industry": "半導體業"},
        ],
        market="TPEX",
        is_0050=False,
    )
    for symbol, end_close in (("3105", 120.0), ("6488", 115.0)):
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": date(2024, 6, 21), "open": 1, "high": 1, "low": 1, "close": 100.0, "volume": 1_000_000},
                {"date": date(2024, 6, 24), "open": 1, "high": 1, "low": 1, "close": end_close, "volume": 1_200_000},
            ],
        )
    repo.upsert_industry_index_bars(
        [
            {
                "date": date(2024, 6, 21),
                "index_name": "半導體業指數",
                "industry": "半導體業",
                "close": 100.0,
                "source": TPEX_SECTOR_INDEX_SOURCE,
            },
            {
                "date": date(2024, 6, 24),
                "index_name": "半導體業指數",
                "industry": "半導體業",
                "close": 104.0,
                "source": TPEX_SECTOR_INDEX_SOURCE,
            },
        ]
    )
    session.commit()

    context = repo.get_industry_return_context(
        industry="半導體業",
        start_date=date(2024, 6, 21),
        end_date=date(2024, 6, 24),
        markets=["TPEX"],
    )

    assert context["status"] == "ok"
    assert context["method"] == "tpex_sector_price_index"
    assert context["source"] == TPEX_SECTOR_INDEX_SOURCE
    assert context["symbol_count"] == 1
    assert abs(context["average_return"] - 0.04) < 1e-12


def test_industry_return_context_prefers_local_tpex_industry_before_market_index() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "3105", "market": "TPEX", "name": "穩懋", "industry": "半導體業"},
            {"symbol": "6488", "market": "TPEX", "name": "環球晶", "industry": "半導體業"},
        ],
        market="TPEX",
        is_0050=False,
    )
    for symbol, end_close in (("3105", 110.0), ("6488", 106.0)):
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": date(2024, 6, 21), "open": 1, "high": 1, "low": 1, "close": 100.0, "volume": 1_000_000},
                {"date": date(2024, 6, 24), "open": 1, "high": 1, "low": 1, "close": end_close, "volume": 1_200_000},
            ],
        )
    repo.upsert_industry_index_bars(
        [
            {
                "date": date(2024, 6, 21),
                "index_name": TPEX_MARKET_INDEX_NAME,
                "industry": TPEX_MARKET_CONTEXT_NAME,
                "close": 100.0,
                "source": "tpex_index_info",
            },
            {
                "date": date(2024, 6, 24),
                "index_name": TPEX_MARKET_INDEX_NAME,
                "industry": TPEX_MARKET_CONTEXT_NAME,
                "close": 101.0,
                "source": "tpex_index_info",
            },
        ]
    )
    session.commit()

    context = repo.get_industry_return_context(
        industry="半導體業",
        start_date=date(2024, 6, 21),
        end_date=date(2024, 6, 24),
        markets=["TPEX"],
    )

    assert context["status"] == "ok"
    assert context["method"] == "local_equal_weight_industry_return"
    assert context["industry"] == "半導體業"
    assert context["symbol_count"] == 2
    assert abs(context["average_return"] - 0.08) < 1e-12


def test_sync_mid100_universe_persists_pcf_symbols_names_and_index_cache(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    monkeypatch.setattr(
        "app.data.sync_service.sync_yuanta_mid100_symbols",
        lambda: {
            "source": "yuanta_0051_pcf",
            "ticker": "0051",
            "trade_date": "20260624",
            "update_time": "2026-06-24 17:06:08",
            "count": 2,
            "file": "data/cache/tw_mid100_symbols.txt",
            "constituents": [
                {"symbol": "1101", "name": "台泥"},
                {"symbol": "2301", "name": "光寶科"},
            ],
        },
    )
    repo, session = _build_repo()
    service = SyncService(repo=repo)

    result = service.sync_mid100_universe()
    session.commit()

    assert result["source"] == "yuanta_0051_pcf"
    assert result["symbols_total"] == 2
    assert result["instruments_upserted"] == 2
    assert result["index_constituents_cache"]["index_key"] == "tw_mid100"
    assert result["index_constituents_cache"]["count"] == 2
    assert load_index_constituent_symbols("tw_mid100", cache_dir=tmp_path) == ["1101", "2301"]
    assert repo.get_instrument_name("1101") == "台泥"
    assert repo.get_instrument_name("2301") == "光寶科"


def test_sync_0050_universe_persists_pcf_symbols_names_snapshot_and_index_cache(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    monkeypatch.setattr(
        "app.data.sync_service.sync_yuanta_0050_symbols",
        lambda: {
            "source": "yuanta_0050_pcf",
            "ticker": "0050",
            "trade_date": "20260626",
            "update_time": "2026-06-26 17:06:08",
            "count": 2,
            "file": "data/cache/tw0050_symbols.txt",
            "symbols": ["2330", "2454"],
            "constituents": [
                {"symbol": "2330", "name": "台積電"},
                {"symbol": "2454", "name": "聯發科"},
            ],
        },
    )
    repo, session = _build_repo()
    service = SyncService(repo=repo)

    result = service.sync_0050_universe(snapshot_date=date(2026, 6, 26))
    session.commit()

    assert result["source"] == "yuanta_0050_pcf"
    assert result["symbols_total"] == 2
    assert result["snapshot_rows"] == 2
    assert result["instruments_upserted"] == 2
    assert result["index_constituents_cache"]["index_key"] == "tw0050"
    assert result["index_constituents_cache"]["count"] == 2
    assert load_index_constituent_symbols("tw0050", cache_dir=tmp_path) == ["2330", "2454"]
    assert repo.get_latest_0050_symbols() == ["2330", "2454"]
    assert repo.get_instrument_name("2330") == "台積電"
    assert repo.get_instrument_name("2454") == "聯發科"


def test_sync_twse_industry_indices_persists_external_context() -> None:
    class _StubTwseIndex:
        def fetch_industry_index_bars(self, trade_date):  # noqa: ANN001
            return [
                {
                    "date": trade_date,
                    "index_name": "金融保險類指數",
                    "industry": "金融保險業",
                    "close": 2000.0,
                    "change": 10.0,
                    "change_pct": 0.005,
                    "source": "twse_mi_index",
                }
            ]

    class _StubTpexIndex:
        def fetch_sector_index_bars(self, year_month):  # noqa: ANN001
            return [
                {
                    "date": date(2024, 6, 24),
                    "index_name": "半導體業指數",
                    "industry": "半導體業",
                    "close": 160.0,
                    "change": 2.0,
                    "change_pct": 0.0125,
                    "source": TPEX_SECTOR_INDEX_SOURCE,
                }
            ]

        def fetch_market_index_bars(self, year_month):  # noqa: ANN001
            return [
                {
                    "date": date(2024, 6, 24),
                    "index_name": TPEX_MARKET_INDEX_NAME,
                    "industry": TPEX_MARKET_CONTEXT_NAME,
                    "close": 260.0,
                    "change": 1.0,
                    "change_pct": 0.0,
                    "source": "tpex_index_info",
                }
            ]

    repo, session = _build_repo()
    service = SyncService(repo=repo, twse_index=_StubTwseIndex(), tpex_index=_StubTpexIndex())

    result = service.sync_market_bundle(
        start_date=date(2024, 6, 24),
        end_date=date(2024, 6, 24),
        symbols=[],
        include_bars=False,
        include_institutional=False,
        include_broker_agg=False,
        include_disposition=False,
        include_industry_indices=True,
    )
    session.commit()

    external = result["bundle"]["external_index_context"]
    assert result["rows_upserted_total"] == 3
    assert result["sync_quality"]["rows_upserted"] == 3
    assert external["rows_upserted_total"] == 3
    assert external["bundle"]["twse_industry_indices"]["rows_upserted"] == 1
    assert external["bundle"]["tpex_sector_indices"]["rows_upserted"] == 1
    assert external["bundle"]["tpex_market_index"]["rows_upserted"] == 1
    context = repo.get_industry_index_return_context(
        industry="金融保險業",
        start_date=date(2024, 6, 24),
        end_date=date(2024, 6, 24),
        min_bars=1,
    )
    assert context["status"] == "insufficient"


def test_get_recent_liquid_symbols_orders_by_turnover_and_excludes_large_caps() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "market": "TWSE", "name": "大型股"},
            {"symbol": "2301", "market": "TWSE", "name": "中型高流動"},
            {"symbol": "2324", "market": "TWSE", "name": "中型次高"},
            {"symbol": "3105", "market": "TPEX", "name": "上櫃流動"},
            {"symbol": "9999", "market": "TWSE", "name": "資料不足"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_bars(
        "2330",
        [
            {"date": date(2024, 4, 1), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000},
            {"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000},
        ],
    )
    repo.upsert_daily_bars(
        "2301",
        [
            {"date": date(2024, 4, 1), "open": 1, "high": 1, "low": 1, "close": 50, "volume": 1_000_000},
            {"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 50, "volume": 1_200_000},
        ],
    )
    repo.upsert_daily_bars(
        "2324",
        [
            {"date": date(2024, 4, 1), "open": 1, "high": 1, "low": 1, "close": 20, "volume": 1_000_000},
            {"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 20, "volume": 1_100_000},
        ],
    )
    repo.upsert_daily_bars(
        "3105",
        [
            {"date": date(2024, 4, 1), "open": 1, "high": 1, "low": 1, "close": 10, "volume": 1_000_000},
            {"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 10, "volume": 1_100_000},
        ],
    )
    repo.upsert_daily_bars(
        "9999",
        [{"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 500, "volume": 1_000_000}],
    )
    session.commit()

    symbols = repo.get_recent_liquid_symbols(
        markets=["TWSE", "TPEX"],
        exclude_symbols=["2330"],
        limit=3,
        lookback_days=10,
        end_date=date(2024, 4, 2),
        min_bars=2,
    )

    assert symbols == ["2301", "2324", "3105"]


def test_get_recent_liquid_symbols_by_industry_rotates_industry_groups() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2301", "market": "TWSE", "name": "電子一", "industry": "電子零組件業"},
            {"symbol": "2324", "market": "TWSE", "name": "電子二", "industry": "電子零組件業"},
            {"symbol": "1101", "market": "TWSE", "name": "水泥一", "industry": "水泥工業"},
            {"symbol": "2881", "market": "TWSE", "name": "金融一", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    bars = {
        "2301": [(100, 1_000_000), (100, 1_100_000)],
        "2324": [(90, 1_000_000), (90, 1_100_000)],
        "1101": [(20, 1_000_000), (20, 1_100_000)],
        "2881": [(60, 1_000_000), (60, 1_100_000)],
    }
    for symbol, values in bars.items():
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": date(2024, 4, idx + 1), "open": 1, "high": 1, "low": 1, "close": close, "volume": volume}
                for idx, (close, volume) in enumerate(values)
            ],
        )
    session.commit()

    rows = repo.get_recent_liquid_symbols_by_industry(
        markets=["TWSE"],
        limit=3,
        per_industry=1,
        lookback_days=10,
        end_date=date(2024, 4, 2),
        min_bars=2,
    )

    assert [row["industry"] for row in rows] == ["水泥工業", "金融保險業", "電子零組件業"]
    assert {row["symbol"] for row in rows} == {"1101", "2881", "2301"}


def test_get_recent_liquid_symbols_by_industry_uses_symbol_proxy_when_industry_missing() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2301", "market": "TWSE", "name": "電子一"},
            {"symbol": "1101", "market": "TWSE", "name": "水泥一"},
            {"symbol": "2881", "market": "TWSE", "name": "金融一"},
        ],
        market="TWSE",
        is_0050=False,
    )
    bars = {
        "2301": [(100, 1_000_000), (100, 1_100_000)],
        "1101": [(20, 1_000_000), (20, 1_100_000)],
        "2881": [(60, 1_000_000), (60, 1_100_000)],
    }
    for symbol, values in bars.items():
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": date(2024, 4, idx + 1), "open": 1, "high": 1, "low": 1, "close": close, "volume": volume}
                for idx, (close, volume) in enumerate(values)
            ],
        )
    session.commit()

    rows = repo.get_recent_liquid_symbols_by_industry(
        markets=["TWSE"],
        limit=3,
        per_industry=1,
        lookback_days=10,
        end_date=date(2024, 4, 2),
        min_bars=2,
    )

    assert [row["industry"] for row in rows] == ["水泥/建材", "金融/保險", "電子/科技"]
    assert {row["symbol"] for row in rows} == {"1101", "2881", "2301"}
    assert {row["industry_source"] for row in rows} == {"symbol_proxy"}


def test_get_research_ready_symbols_requires_history_freshness_and_chip_data() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2301", "market": "TWSE", "industry": "電子零組件業"},
            {"symbol": "1101", "market": "TWSE", "industry": "水泥工業"},
            {"symbol": "2324", "market": "TWSE", "industry": "電子零組件業"},
            {"symbol": "2881", "market": "TWSE", "industry": "金融保險業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    for symbol, close, bar_count in (("2301", 100, 6), ("1101", 20, 6), ("2324", 50, 2), ("2881", 60, 6)):
        repo.upsert_daily_bars(
            symbol,
            [
                {
                    "date": date(2024, 4, idx + 1),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000 + idx,
                }
                for idx in range(bar_count)
            ],
        )
    for symbol, chip_dates in {
        "2301": [date(2024, 4, 4), date(2024, 4, 5), date(2024, 4, 6)],
        "1101": [date(2024, 4, 4), date(2024, 4, 5), date(2024, 4, 6)],
        "2324": [date(2024, 4, 4), date(2024, 4, 5), date(2024, 4, 6)],
        "2881": [date(2024, 3, 30), date(2024, 3, 31), date(2024, 4, 1)],
    }.items():
        repo.upsert_institutional_chip(
            symbol,
            [
                {
                    "date": chip_date,
                    "foreign_net_buy": 100,
                    "investment_trust_net_buy": 50,
                    "dealer_net_buy": 10,
                }
                for chip_date in chip_dates
            ],
        )
    session.commit()

    rows = repo.get_research_ready_symbols_by_industry(
        markets=["TWSE"],
        limit=10,
        per_industry=2,
        history_lookback_days=30,
        min_history_bars=5,
        chip_lookback_days=30,
        min_chip_days=3,
        freshness_days=2,
        end_date=date(2024, 4, 6),
    )

    assert {row["symbol"] for row in rows} == {"1101", "2301"}
    assert all(row["history_bars"] == 6 for row in rows)
    assert all(row["chip_days"] == 3 for row in rows)
    assert all(row["latest_bar_date"] == "2024-04-06" for row in rows)
    assert all(row["latest_chip_date"] == "2024-04-06" for row in rows)


def test_get_industry_return_context_builds_equal_weight_context() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2301", "market": "TWSE", "name": "電子一", "industry": "電子零組件業"},
            {"symbol": "2324", "market": "TWSE", "name": "電子二", "industry": "電子零組件業"},
            {"symbol": "1101", "market": "TWSE", "name": "水泥一", "industry": "水泥工業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_bars(
        "2301",
        [
            {"date": date(2024, 4, 1), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000},
            {"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 110, "volume": 1_000_000},
        ],
    )
    repo.upsert_daily_bars(
        "2324",
        [
            {"date": date(2024, 4, 1), "open": 1, "high": 1, "low": 1, "close": 50, "volume": 1_000_000},
            {"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 45, "volume": 1_000_000},
        ],
    )
    repo.upsert_daily_bars(
        "1101",
        [
            {"date": date(2024, 4, 1), "open": 1, "high": 1, "low": 1, "close": 20, "volume": 1_000_000},
            {"date": date(2024, 4, 2), "open": 1, "high": 1, "low": 1, "close": 22, "volume": 1_000_000},
        ],
    )
    session.commit()

    context = repo.get_industry_return_context(
        industry="電子零組件業",
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 2),
        markets=["TWSE"],
        min_bars=2,
    )

    assert context["status"] == "ok"
    assert context["industry"] == "電子零組件業"
    assert context["symbol_count"] == 2
    assert round(context["average_return"], 4) == 0.0
    assert context["breadth"] == 0.5
    assert context["label"] == "mixed"


def test_data_health_overview_summarizes_investment_workflow_freshness(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    cache = write_index_constituents_cache(
        [
            {"symbol": "2330", "name": "台積電", "source": "official_url"},
            {"symbol": "2454", "name": "聯發科", "source": "official_url"},
        ],
        index_key="tip_tw50",
        cache_dir=tmp_path,
        source="official_url",
    )
    cache_ts = datetime(2024, 6, 25, 8, 45, 0).timestamp()
    os.utime(cache["file"], (cache_ts, cache_ts))

    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "3105", "market": "TPEX", "name": "穩懋", "industry": "半導體業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    for symbol in ("2330", "3105"):
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": date(2024, 6, 21), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000},
                {"date": date(2024, 6, 24), "open": 1, "high": 1, "low": 1, "close": 103, "volume": 1_100_000},
            ],
        )
        repo.upsert_institutional_chip(
            symbol,
            [
                {"date": date(2024, 6, 21), "foreign_net_buy": 10, "investment_trust_net_buy": 2, "dealer_net_buy": 1},
                {"date": date(2024, 6, 24), "foreign_net_buy": 12, "investment_trust_net_buy": 3, "dealer_net_buy": 1},
            ],
        )
        repo.upsert_broker_agg(
            symbol,
            [
                {"date": date(2024, 6, 21), "concentration_proxy": 0.2, "top5_net_buy": 10},
                {"date": date(2024, 6, 24), "concentration_proxy": 0.3, "top5_net_buy": 12},
            ],
        )
    repo.upsert_industry_index_bars(
        [
            {
                "date": date(2024, 6, 24),
                "index_name": "半導體類指數",
                "industry": "半導體業",
                "close": 620.0,
                "source": "twse_mi_index",
            },
            {
                "date": date(2024, 6, 24),
                "index_name": TPEX_MARKET_INDEX_NAME,
                "industry": TPEX_MARKET_CONTEXT_NAME,
                "close": 260.0,
                "source": "tpex_index_info",
            },
        ]
    )
    repo.upsert_candidate_snapshots(
        "candidate_run",
        date(2024, 6, 24),
        [
            {"symbol": "2330", "rank": 1, "score": 82.0, "risk_score": 0.2},
            {"symbol": "3105", "rank": 2, "score": 75.0, "risk_score": 0.3},
        ],
    )
    repo.upsert_daily_radar_snapshots(
        "radar_run",
        date(2024, 6, 24),
        [
            {"symbol": "2330", "rank": 1, "entry_score": 81.0, "action_label": "ENTRY_READY"},
            {"symbol": "3105", "rank": 2, "entry_score": 74.0, "action_label": "WATCH_WAIT_TRIGGER"},
        ],
    )
    repo.upsert_disposition_periods(
        "2330",
        [
            {
                "start_date": date(2024, 6, 24),
                "end_date": date(2024, 6, 26),
                "disposition_type": "處置股票",
                "reason": "測試處置期間",
            }
        ],
    )
    repo.upsert_attention_stocks(
        [
            {
                "symbol": "3105",
                "trade_date": date(2024, 6, 25),
                "announced_date": date(2024, 6, 25),
                "close": 120.0,
                "reason": "測試注意股票",
            }
        ]
    )
    repo.add_sync_job("invest_pipeline", "ok", rows_upserted=2)
    repo.add_sync_job("daily_radar", "ok", rows_upserted=2)
    repo.add_sync_job("sync_disposition_periods", "ok", rows_upserted=1)
    repo.add_sync_job("sync_attention_stocks", "ok", rows_upserted=1)
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2024, 6, 25), freshness_days=3)

    assert overview["status"] == "ok"
    assert overview["daily_bars"]["latest_date"] == "2024-06-24"
    assert overview["universe"]["active_symbols"] == 2
    assert overview["universe"]["recent_symbols"] == 2
    assert overview["instrument_industry_coverage"]["status"] == "ok"
    assert overview["instrument_industry_coverage"]["coverage_pct"] == 100.0
    assert any(component["key"] == "instrument_industry" for component in overview["components"])
    assert overview["latest_candidates"]["row_count"] == 2
    assert overview["latest_radar"]["entry_ready_count"] == 1
    assert overview["external_indices"]["twse_latest_date"] == "2024-06-24"
    assert overview["external_indices"]["tpex_latest_date"] == "2024-06-24"
    assert overview["market_context_coverage"]["status"] == "ok"
    assert overview["market_context_coverage"]["twse_industry_index_count"] == 1
    assert overview["market_context_coverage"]["tpex_recent_local_industry_count"] == 1
    assert overview["market_context_coverage"]["tpex_market_index_count"] == 1
    assert overview["market_data_coverage"]["status"] == "ok"
    assert {dataset["key"] for dataset in overview["market_data_coverage"]["datasets"]} == {
        "daily_bars",
        "institutional_chip",
        "broker_agg_chip",
    }
    daily_coverage = next(dataset for dataset in overview["market_data_coverage"]["datasets"] if dataset["key"] == "daily_bars")
    assert daily_coverage["coverage_pct"] == 100.0
    assert {market["market"]: market["coverage_pct"] for market in daily_coverage["markets"]} == {"TPEX": 100.0, "TWSE": 100.0}
    assert daily_coverage["missing_symbols"] == []
    assert overview["index_constituents"]["status"] == "ok"
    assert overview["index_constituents"]["cache_count"] == 1
    assert overview["index_constituents"]["symbol_count"] == 2
    assert overview["index_constituents"]["latest_updated_at"] == "2024-06-25T08:45:00"
    assert overview["index_constituents"]["caches"][0]["index_key"] == "tip_tw50"
    assert {source["method"] for source in overview["market_context_coverage"]["sources"]} == {
        "twse_industry_price_index",
        "local_equal_weight_industry_return",
        "tpex_market_price_index",
    }
    assert overview["disposition_periods"]["active_count"] == 1
    assert overview["attention_stocks"]["active_count"] == 1
    assert overview["attention_stocks"]["latest_trade_date"] == "2024-06-25"
    assert {component["key"] for component in overview["components"]} >= {
        "daily_bars",
        "universe",
        "market_data_coverage",
        "index_constituents",
        "candidates",
        "daily_radar",
        "external_indices",
        "market_context",
        "disposition_periods",
        "attention_stocks",
        "sync_jobs",
    }


def test_data_health_overview_treats_empty_risk_lists_as_fresh_when_sync_succeeded(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    repo.add_sync_job("sync_disposition_periods", "ok", rows_upserted=0)
    repo.add_sync_job("sync_attention_stocks", "ok", rows_upserted=0)
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2024, 6, 25), freshness_days=3)

    assert overview["disposition_periods"]["status"] == "ok"
    assert overview["disposition_periods"]["active_count"] == 0
    assert overview["attention_stocks"]["status"] == "ok"
    assert overview["attention_stocks"]["active_count"] == 0
    assert overview["index_constituents"]["status"] == "warn"
    assert overview["index_constituents"]["optional"] is True
    assert next(component for component in overview["components"] if component["key"] == "index_constituents")["optional"] is True


def test_data_health_overview_market_data_coverage_surfaces_missing_symbols(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "00679B", "market": "TWSE", "name": "元大美債", "industry": "ETF"},
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "3105", "market": "TPEX", "name": "穩懋", "industry": "半導體業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_bars(
        "2330",
        [{"date": date(2024, 6, 24), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000}],
    )
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2024, 6, 25), freshness_days=3)

    datasets = {dataset["key"]: dataset for dataset in overview["market_data_coverage"]["datasets"]}
    assert overview["market_data_coverage"]["status"] == "danger"
    assert overview["market_data_coverage"]["scope"] == "common_stock"
    assert datasets["daily_bars"]["status"] == "danger"
    assert datasets["daily_bars"]["missing_symbols"] == ["3105"]
    assert "00679B" not in datasets["daily_bars"]["missing_symbols"]
    tpex_daily = next(market for market in datasets["daily_bars"]["markets"] if market["market"] == "TPEX")
    assert tpex_daily["coverage_pct"] == 0.0
    assert tpex_daily["missing_symbols"] == ["3105"]
    assert "--symbols 3105" in datasets["daily_bars"]["repair_command"]
    assert "--prefer-official-bulk" in datasets["daily_bars"]["repair_command"]
    assert "--include-bars" in datasets["daily_bars"]["repair_command"]
    assert set(datasets["institutional_chip"]["missing_symbols"]) == {"2330", "3105"}
    assert "--prefer-official-bulk" in datasets["institutional_chip"]["repair_command"]
    assert "00679B" not in datasets["institutional_chip"]["missing_symbols"]


def test_data_health_overview_market_data_coverage_excludes_recent_no_data_symbols(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "1258", "market": "TWSE", "name": "其祥-KY", "industry": "其他"},
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "3105", "market": "TPEX", "name": "穩懋", "industry": "半導體業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_bars(
        "2330",
        [{"date": date(2026, 7, 8), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000}],
    )
    repo.add_sync_job(
        "sync_market_bundle",
        "partial",
        rows_upserted=1,
        error_msg=json.dumps(
            {
                "failed_count_total": 1,
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": "1258",
                        "error_type": "NoDataError",
                        "error": "no daily bars returned for 1258",
                    }
                ],
            }
        ),
    )
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2026, 7, 9), freshness_days=3)

    daily_coverage = next(
        dataset for dataset in overview["market_data_coverage"]["datasets"] if dataset["key"] == "daily_bars"
    )
    assert daily_coverage["raw_active_symbols"] == 3
    assert daily_coverage["active_symbols"] == 2
    assert daily_coverage["recent_symbols"] == 1
    assert daily_coverage["coverage_pct"] == 50.0
    assert daily_coverage["unavailable_symbol_count"] == 1
    assert daily_coverage["unavailable_symbols"] == ["1258"]
    assert daily_coverage["unavailable_symbol_reasons"]["1258"] == ["upstream_no_data"]
    assert daily_coverage["missing_symbols"] == ["3105"]
    assert "1258" not in daily_coverage["repair_command"]
    twse_daily = next(market for market in daily_coverage["markets"] if market["market"] == "TWSE")
    assert twse_daily["raw_active_symbols"] == 2
    assert twse_daily["active_symbols"] == 1
    assert twse_daily["recent_symbols"] == 1
    assert twse_daily["coverage_pct"] == 100.0
    assert twse_daily["unavailable_symbols"] == ["1258"]
    failures = overview["recent_sync_failures"]["symbol_failures"]
    assert failures[0]["symbol"] == "1258"
    assert failures[0]["reason_codes"] == ["upstream_no_data"]


def test_data_health_overview_treats_partial_no_data_repair_as_sync_ok(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "3130", "market": "TWSE", "name": "一零四", "industry": "數位雲端"},
            {"symbol": "3142", "market": "TWSE", "name": "測試查無", "industry": "其他"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_bars(
        "3130",
        [{"date": date(2026, 7, 9), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000}],
    )
    repo.add_sync_job(
        "sync_market_bundle",
        "partial",
        rows_upserted=9,
        error_msg=json.dumps(
            {
                "failed_count_total": 1,
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": "3142",
                        "error_type": "NoDataError",
                        "error": "no daily bars returned for 3142",
                    }
                ],
            }
        ),
    )
    session.flush()
    session.query(SyncJob).filter_by(job_name="sync_market_bundle").one().run_at = datetime(2026, 7, 9, 8, 30, 0)
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2026, 7, 9), freshness_days=3)

    sync_job = next(job for job in overview["sync_jobs"] if job["job_name"] == "sync_market_bundle")
    sync_component = next(component for component in overview["components"] if component["key"] == "sync_jobs")
    daily_coverage = next(
        dataset for dataset in overview["market_data_coverage"]["datasets"] if dataset["key"] == "daily_bars"
    )
    assert sync_job["status"] == "partial"
    assert sync_job["health_status"] == "ok"
    assert sync_job["benign_partial_market_gap"] is True
    assert sync_component["status"] == "ok"
    assert daily_coverage["unavailable_symbols"] == ["3142"]
    assert overview["recent_sync_failures"]["symbol_failures"][0]["symbol"] == "3142"


def test_data_health_overview_keeps_large_unavailable_symbol_samples(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    symbols = [f"10{idx:02d}" for idx in range(35)]
    repo.upsert_instruments(
        [{"symbol": symbol, "market": "TWSE", "name": f"測試{symbol}", "industry": "測試"} for symbol in symbols],
        market="TWSE",
        is_0050=False,
    )
    repo.add_sync_job(
        "sync_market_bundle",
        "partial",
        rows_upserted=0,
        error_msg=json.dumps(
            {
                "failed_count_total": len(symbols),
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": symbol,
                        "error_type": "NoDataError",
                        "error": f"no daily bars returned for {symbol}",
                    }
                    for symbol in symbols
                ],
            }
        ),
    )
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2026, 7, 9), freshness_days=3)

    daily_coverage = next(
        dataset for dataset in overview["market_data_coverage"]["datasets"] if dataset["key"] == "daily_bars"
    )
    assert daily_coverage["unavailable_symbol_count"] == 35
    assert len(daily_coverage["unavailable_symbols"]) == 35
    assert daily_coverage["unavailable_symbols"][0] == "1000"
    assert daily_coverage["unavailable_symbols"][-1] == "1034"
    assert daily_coverage["unavailable_symbol_reasons"]["1034"] == ["upstream_no_data"]
    assert len(overview["recent_sync_failures"]["symbol_failures"]) == 35


def test_data_health_overview_market_data_coverage_excludes_payment_required_after_official_retry(
    monkeypatch,
    tmp_path,
) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "1507", "market": "TWSE", "name": "永大", "industry": "電機機械"},
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "3105", "market": "TPEX", "name": "穩懋", "industry": "半導體業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_bars(
        "2330",
        [{"date": date(2026, 7, 8), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000}],
    )
    repo.add_sync_job(
        "sync_market_bundle",
        "partial",
        rows_upserted=0,
        error_msg=json.dumps(
            {
                "failed_count_total": 1,
                "failed_samples": [
                    {
                        "scope": "daily_bars",
                        "symbol": "1507",
                        "error_type": "FinMindRequestError",
                        "error": "402 Client Error: Payment Required",
                    }
                ],
                "failed_symbol_scopes": [{"component": "daily_bars", "scope": "daily_bars", "symbol": "1507"}],
                "sync_quality": {
                    "components": {
                        "daily_bars": {
                            "status": "partial",
                            "failed_count": 1,
                            "rows_upserted": 0,
                            "official_fallback_rows": 0,
                            "prefer_official_bulk": True,
                            "api_mode": "",
                        }
                    }
                },
            }
        ),
    )
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2026, 7, 9), freshness_days=3)

    daily_coverage = next(
        dataset for dataset in overview["market_data_coverage"]["datasets"] if dataset["key"] == "daily_bars"
    )
    assert daily_coverage["raw_active_symbols"] == 3
    assert daily_coverage["active_symbols"] == 2
    assert daily_coverage["unavailable_symbol_count"] == 1
    assert daily_coverage["unavailable_symbols"] == ["1507"]
    assert daily_coverage["unavailable_symbol_reasons"]["1507"] == ["upstream_payment_required_after_official"]
    assert daily_coverage["missing_symbols"] == ["3105"]
    assert "1507" not in daily_coverage["repair_command"]
    twse_daily = next(market for market in daily_coverage["markets"] if market["market"] == "TWSE")
    assert twse_daily["coverage_pct"] == 100.0
    assert twse_daily["unavailable_symbol_reasons"]["1507"] == ["upstream_payment_required_after_official"]


def test_data_health_overview_treats_broker_agg_as_optional_premium_data(monkeypatch, tmp_path) -> None:
    import app.data.index_constituents as index_constituents

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "3105", "market": "TPEX", "name": "穩懋", "industry": "半導體業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    for symbol in ["2330", "3105"]:
        repo.upsert_daily_bars(
            symbol,
            [{"date": date(2024, 6, 24), "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1_000_000}],
        )
        repo.upsert_institutional_chip(
            symbol,
            [
                {
                    "date": date(2024, 6, 24),
                    "foreign_net_buy": 1,
                    "investment_trust_net_buy": 0,
                    "dealer_net_buy": 0,
                }
            ],
        )
    session.commit()

    overview = repo.get_data_health_overview(as_of=date(2024, 6, 25), freshness_days=3)

    datasets = {dataset["key"]: dataset for dataset in overview["market_data_coverage"]["datasets"]}
    assert overview["market_data_coverage"]["status"] == "ok"
    assert datasets["daily_bars"]["status"] == "ok"
    assert datasets["institutional_chip"]["status"] == "ok"
    assert datasets["broker_agg_chip"]["label"] == "分點聚合（加值）"
    assert datasets["broker_agg_chip"]["status"] == "warn"
    assert datasets["broker_agg_chip"]["optional"] is True
    assert datasets["broker_agg_chip"]["repairable"] is False
    assert datasets["broker_agg_chip"]["provider_requirement"]["plan"] == "FinMind sponsor"
    assert "securities_trader_id" in datasets["broker_agg_chip"]["provider_requirement"]["parameters"]


def test_symbol_decision_detail_includes_radar_peers_and_data_health() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2454", "market": "TWSE", "name": "聯發科", "industry": "半導體業"},
            {"symbol": "1101", "market": "TWSE", "name": "台泥", "industry": "水泥工業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    for symbol, closes in {"2330": [100, 105, 110], "2454": [80, 82, 84], "1101": [30, 31, 32]}.items():
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": date(2024, 6, day), "open": 1, "high": 1, "low": 1, "close": close, "volume": 1_000_000 + day}
                for day, close in zip([20, 21, 24], closes)
            ],
        )
    repo.upsert_candidate_snapshots(
        "candidate_run",
        date(2024, 6, 24),
        [{"symbol": "2330", "rank": 1, "score": 88.0, "risk_score": 0.18, "reason_codes": ["score_ok"]}],
    )
    repo.upsert_daily_radar_snapshots(
        "radar_run",
        date(2024, 6, 24),
        [
            {
                "symbol": "2330",
                "name": "台積電",
                "rank": 1,
                "price": 110,
                "entry_score": 85,
                "risk_score": 0.18,
                "action_label": "ENTRY_READY",
                "reason_tags": [
                    "INDUSTRY_GROUP=半導體業",
                    "INDUSTRY_ROTATION_SCORE=62.0",
                    "ATR_HEAT=elevated",
                    "ATR_PCT=4.5",
                ],
            },
            {
                "symbol": "2454",
                "name": "聯發科",
                "rank": 2,
                "price": 84,
                "entry_score": 78,
                "risk_score": 0.25,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["INDUSTRY_GROUP=半導體業"],
            },
            {
                "symbol": "1101",
                "name": "台泥",
                "rank": 3,
                "price": 32,
                "entry_score": 72,
                "risk_score": 0.2,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["INDUSTRY_GROUP=水泥工業"],
            },
        ],
    )
    session.commit()

    detail = repo.get_symbol_decision_detail("2330", peer_limit=3)

    assert detail["status"] == "ok"
    assert detail["instrument"]["name"] == "台積電"
    assert detail["radar"]["action_label"] == "ENTRY_READY"
    assert detail["radar"]["industry_group"] == "半導體業"
    assert detail["radar"]["industry_rotation_score"] == 0.62
    assert detail["radar"]["atr_heat"] == "elevated"
    assert detail["candidate"]["score"] == 88.0
    assert detail["price_summary"]["latest_close"] == 110.0
    assert round(detail["price_summary"]["lookback_return"], 4) == 0.1
    assert detail["data_health"]["daily_bars_count"] == 3
    assert [peer["symbol"] for peer in detail["peer_radar"]] == ["2454"]


def test_latest_radar_outcomes_tracks_forward_returns() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(
        [
            {"symbol": "2330", "market": "TWSE", "name": "台積電", "industry": "半導體業"},
            {"symbol": "2454", "market": "TWSE", "name": "聯發科", "industry": "半導體業"},
        ],
        market="TWSE",
        is_0050=False,
    )
    repo.upsert_daily_radar_snapshots(
        "radar_run",
        date(2024, 6, 24),
        [
            {
                "symbol": "2330",
                "name": "台積電",
                "rank": 1,
                "price": 100,
                "entry_score": 84,
                "risk_score": 0.2,
                "action_label": "ENTRY_READY",
                "reason_tags": ["INDUSTRY_GROUP=半導體業", "ATR_HEAT=normal", "TREND_OK=true", "ENTRY_SCORE=84.0"],
            },
            {
                "symbol": "2454",
                "name": "聯發科",
                "rank": 2,
                "price": 80,
                "entry_score": 76,
                "risk_score": 0.24,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["INDUSTRY_GROUP=半導體業", "ATR_HEAT=hot", "TREND_OK=false", "ENTRY_SCORE=76.0"],
            },
        ],
    )
    for symbol, closes in {
        "2330": [105, 110, *[111 + idx for idx in range(18)]],
        "2454": [78, 76, *[75 - idx for idx in range(18)]],
    }.items():
        values = [
            (date.fromordinal(date(2024, 6, 25).toordinal() + idx), close)
            for idx, close in enumerate(closes)
        ]
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": bar_date, "open": 1, "high": 1, "low": 1, "close": close, "volume": 1_000_000}
                for bar_date, close in values
            ],
        )
    session.commit()

    outcomes = repo.get_latest_radar_outcomes(lookahead_bars=2)

    assert outcomes["status"] == "ok"
    assert outcomes["candidate_count"] == 2
    assert outcomes["evaluated_count"] == 2
    assert outcomes["matured_count"] == 2
    assert outcomes["positive_count"] == 1
    assert outcomes["positive_rate"] == 0.5
    assert round(outcomes["average_return"], 4) == 0.025
    assert outcomes["by_action"]["ENTRY_READY"]["positive_rate"] == 1.0
    assert outcomes["by_industry"][0]["key"] == "半導體業"
    assert outcomes["by_industry"][0]["evaluated_count"] == 2
    assert outcomes["by_industry"][0]["positive_rate"] == 0.5
    heat_summary = {row["key"]: row for row in outcomes["by_atr_heat"]}
    assert heat_summary["normal"]["positive_rate"] == 1.0
    assert heat_summary["hot"]["positive_rate"] == 0.0
    reason_summary = {row["key"]: row for row in outcomes["by_reason"]}
    assert reason_summary["TREND_OK=true"]["positive_rate"] == 1.0
    assert reason_summary["TREND_OK=false"]["positive_rate"] == 0.0
    assert "ENTRY_SCORE" not in reason_summary
    assert [round(row["lookahead_return"], 4) for row in outcomes["rows"]] == [0.1, -0.05]
    horizon_summary = {row["lookahead_bars"]: row for row in outcomes["horizons"]}
    assert horizon_summary[2]["positive_rate"] == 0.5
    assert horizon_summary[5]["evaluated_count"] == 2
    assert horizon_summary[10]["evaluated_count"] == 2
    assert horizon_summary[20]["evaluated_count"] == 2


def test_radar_signal_reliability_aggregates_matured_signal_buckets() -> None:
    repo, session = _build_repo()
    repo.upsert_daily_radar_snapshots(
        "radar_run",
        date(2024, 6, 24),
        [
            {
                "symbol": "2330",
                "name": "台積電",
                "rank": 1,
                "price": 100,
                "entry_score": 84,
                "risk_score": 0.2,
                "action_label": "ENTRY_READY",
                "reason_tags": ["TRIGGER_REASON=breakout", "TREND_OK=true", "ENTRY_SCORE=84.0"],
            },
            {
                "symbol": "2454",
                "name": "聯發科",
                "rank": 2,
                "price": 80,
                "entry_score": 76,
                "risk_score": 0.24,
                "action_label": "WATCH_WAIT_TRIGGER",
                "reason_tags": ["TRIGGER_REASON=rsi3<20", "TREND_OK=false", "ENTRY_SCORE=76.0"],
            },
        ],
    )
    for symbol, values in {
        "2330": [(date(2024, 6, 25), 105), (date(2024, 6, 26), 110), (date(2024, 6, 27), 112)],
        "2454": [(date(2024, 6, 25), 78), (date(2024, 6, 26), 76), (date(2024, 6, 27), 74)],
    }.items():
        repo.upsert_daily_bars(
            symbol,
            [
                {"date": bar_date, "open": 1, "high": 1, "low": 1, "close": close, "volume": 1_000_000}
                for bar_date, close in values
            ],
        )
    session.commit()

    reliability = repo.get_radar_signal_reliability(lookahead_bars=2, min_evaluated=1)

    assert reliability["status"] == "ok"
    assert reliability["evaluated_count"] == 2
    assert reliability["baseline_positive_rate"] == 0.5
    assert reliability["buckets"]["TRIGGER_REASON=breakout"]["positive_rate"] == 1.0
    assert reliability["buckets"]["TRIGGER_REASON=rsi3<20"]["positive_rate"] == 0.0
    assert "ENTRY_SCORE" not in reliability["buckets"]

    multi = repo.get_radar_signal_reliability_horizons(lookahead_bars_list=[2, 3], min_evaluated=1)
    assert multi["status"] == "ok"
    assert multi["evaluated_count"] == 4
    assert multi["buckets"]["TRIGGER_REASON=breakout"]["positive_rate"] == 1.0
    assert len(multi["buckets"]["TRIGGER_REASON=breakout"]["horizons"]) == 2


def test_radar_outcomes_deduplicate_same_day_reruns() -> None:
    repo, session = _build_repo()
    for run_id, price, created_at in [
        ("radar_first", 90.0, datetime(2024, 6, 24, 8, 0, 0)),
        ("radar_latest", 100.0, datetime(2024, 6, 24, 9, 0, 0)),
    ]:
        repo.upsert_daily_radar_snapshots(
            run_id,
            date(2024, 6, 24),
            [
                {
                    "symbol": "2330",
                    "name": "台積電",
                    "rank": 1,
                    "price": price,
                    "entry_score": 84,
                    "risk_score": 0.2,
                    "action_label": "ENTRY_READY",
                    "reason_tags": ["INDUSTRY_GROUP=半導體業", "TRIGGER_REASON=breakout"],
                }
            ],
        )
        session.flush()
        session.query(DailyRadarSnapshot).filter_by(run_id=run_id).update({"created_at": created_at})
    repo.upsert_daily_bars(
        "2330",
        [
            {"date": date(2024, 6, 25), "open": 1, "high": 1, "low": 1, "close": 105, "volume": 1_000_000},
            {"date": date(2024, 6, 26), "open": 1, "high": 1, "low": 1, "close": 110, "volume": 1_000_000},
        ],
    )
    session.commit()

    reliability = repo.get_radar_signal_reliability(lookahead_bars=2, min_evaluated=1)
    calibration = repo.get_daily_radar_allocation_calibration(lookahead_bars=2, min_evaluated=2)
    allocation_outcomes = repo.get_daily_radar_allocation_outcome_summary(
        lookahead_bars=2,
        max_runs=2,
        limit=1,
    )

    assert reliability["evaluated_count"] == 1
    assert reliability["duplicate_snapshot_count"] == 1
    assert reliability["buckets"]["TRIGGER_REASON=breakout"]["evaluated_count"] == 1
    assert calibration["status"] == "insufficient"
    assert calibration["evaluated_count"] == 1
    assert calibration["duplicate_snapshot_count"] == 1
    assert allocation_outcomes["status"] == "ok"
    assert allocation_outcomes["evaluated_run_count"] == 1
    assert allocation_outcomes["duplicate_run_count"] == 1
    assert allocation_outcomes["recent_runs"][0]["run_id"] == "radar_latest"
