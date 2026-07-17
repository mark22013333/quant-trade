from __future__ import annotations

from datetime import date

from app.cli import (
    build_market_data_repair_plan,
    build_parser,
    cmd_daily_radar_health,
    cmd_data_health_overview,
    cmd_run_invest_pipeline,
    cmd_repair_market_data_coverage,
    cmd_sync_twse_tpex_universe,
    format_data_health_overview_message,
    format_market_data_repair_after_summary,
    format_market_data_repair_plan,
    format_market_data_repair_result_summary,
)
from app.reports.daily_radar_health import (
    DailyRadarDeploymentHealthService,
    daily_radar_health_should_notify,
    format_daily_radar_health_message,
)


class _Repo:
    def __init__(self, overview: dict, rows: list[dict] | None = None):
        self.overview = overview
        self.rows = rows or []

    def get_data_health_overview(self, *, as_of, freshness_days):  # noqa: ANN001
        assert as_of == date(2026, 6, 26)
        assert freshness_days >= 1
        return self.overview

    def get_latest_daily_radar(self, count=50):  # noqa: ANN001
        assert count >= 5
        return self.rows


def test_daily_radar_health_marks_recent_full_run_ok() -> None:
    repo = _Repo(
        {
            "latest_radar": {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "row_count": 12,
                "entry_ready_count": 3,
                "age_days": 0,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "ok",
                    "health_status": "ok",
                    "rows_upserted": 12,
                    "run_at": "2026-06-26T08:45:00",
                }
            ],
        }
    )

    payload = DailyRadarDeploymentHealthService().evaluate(repo, as_of=date(2026, 6, 26))

    assert payload["status"] == "ok"
    assert payload["latest_run_id"] == "radar_20260626"
    assert payload["row_count"] == 12
    assert payload["entry_ready_count"] == 3
    assert payload["blocking_reasons"] == []


def test_daily_radar_health_warns_when_recent_but_not_fresh_enough() -> None:
    repo = _Repo(
        {
            "latest_radar": {
                "run_id": "radar_20260624",
                "run_date": "2026-06-24",
                "row_count": 8,
                "entry_ready_count": 0,
                "age_days": 2,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "ok",
                    "health_status": "ok",
                    "rows_upserted": 8,
                    "run_at": "2026-06-24T08:45:00",
                }
            ],
        }
    )

    payload = DailyRadarDeploymentHealthService(max_age_days=3, warn_age_days=1).evaluate(
        repo,
        as_of=date(2026, 6, 26),
    )

    assert payload["status"] == "warn"
    assert "daily_radar_recent_enough" in payload["warnings"]
    assert "daily_radar_entry_ready" in payload["warnings"]
    assert payload["blocking_reasons"] == []


def test_daily_radar_health_surfaces_watch_trigger_candidates() -> None:
    repo = _Repo(
        {
            "latest_radar": {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "row_count": 3,
                "entry_ready_count": 0,
                "age_days": 0,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "ok",
                    "health_status": "ok",
                    "rows_upserted": 3,
                    "run_at": "2026-06-26T08:45:00",
                }
            ],
        },
        rows=[
            {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "price": 900.0,
                "entry_score": 72.0,
                "risk_score": 0.2,
                "risk_adjusted_score": 74.0,
                "action_label": "WATCH_WAIT_TRIGGER",
                "data_quality": "fresh",
                "atr_heat": "normal",
            },
            {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "rank": 2,
                "symbol": "2303",
                "name": "聯電",
                "price": 50.0,
                "entry_score": 69.0,
                "risk_score": 0.35,
                "risk_adjusted_score": 70.0,
                "action_label": "WATCH_WAIT_TRIGGER",
                "data_quality": "fresh",
                "atr_heat": "elevated",
            },
            {"run_id": "radar_20260626", "run_date": "2026-06-26", "rank": 3, "symbol": "2317", "action_label": "NOT_RECOMMENDED"},
        ],
    )

    payload = DailyRadarDeploymentHealthService(min_rows=3).evaluate(repo, as_of=date(2026, 6, 26))

    assert payload["status"] == "warn"
    assert payload["action_counts"]["WATCH_WAIT_TRIGGER"] == 2
    assert payload["watch_trigger_count"] == 2
    assert payload["not_recommended_count"] == 1
    assert payload["watch_trigger_summary"]["primary_wait"] == "entry_score"
    assert payload["watch_trigger_summary"]["reason_counts"]["entry_score"] == 2
    assert payload["watch_trigger_summary"]["reason_counts"]["risk_cooling"] == 1
    assert payload["watch_trigger_candidates"][0]["symbol"] == "2330"
    assert payload["watch_trigger_candidates"][0]["primary_wait"] == "entry_score"
    assert payload["watch_trigger_candidates"][0]["trigger_plan"]["breakout_price"] == 909.0
    assert payload["entry_ready_diagnosis"]["reason"] == "score_not_ready"
    assert payload["entry_ready_diagnosis"]["title"] == "分數還沒站上門檻"
    assert "2 檔待觸發觀察" in payload["entry_ready_diagnosis"]["summary"]
    assert "daily_radar_watch_trigger_available" not in payload["warnings"]
    assert any("2 檔待觸發觀察標的" in step for step in payload["next_steps"])

    message = format_daily_radar_health_message(payload)
    assert "待觸發: 2 檔" in message
    assert "主要等待: 等 Entry Score 重新整理轉強" in message
    assert "無可進場原因: 分數還沒站上門檻" in message
    assert "2330" in message


def test_daily_radar_health_diagnoses_risk_cooling_when_watchlist_is_hot() -> None:
    repo = _Repo(
        {
            "latest_radar": {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "row_count": 2,
                "entry_ready_count": 0,
                "age_days": 0,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "ok",
                    "health_status": "ok",
                    "rows_upserted": 2,
                    "run_at": "2026-06-26T08:45:00",
                }
            ],
        },
        rows=[
            {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "rank": 1,
                "symbol": "2327",
                "name": "國巨",
                "price": 900.0,
                "entry_score": 70.0,
                "risk_score": 0.65,
                "risk_adjusted_score": 51.0,
                "action_label": "WATCH_WAIT_TRIGGER",
                "data_quality": "fresh",
                "atr_heat": "hot",
            },
            {"run_id": "radar_20260626", "run_date": "2026-06-26", "rank": 2, "symbol": "1216", "action_label": "NOT_RECOMMENDED"},
        ],
    )

    payload = DailyRadarDeploymentHealthService(min_rows=2).evaluate(repo, as_of=date(2026, 6, 26))

    assert payload["entry_ready_diagnosis"]["reason"] == "risk_cooling"
    assert payload["entry_ready_diagnosis"]["title"] == "風險尚未降溫"
    assert any(item["label"] == "過熱/偏高" and item["value"] == 1 for item in payload["entry_ready_diagnosis"]["evidence"])


def test_daily_radar_health_surfaces_sync_issue_summary() -> None:
    repo = _Repo(
        {
            "latest_radar": {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "row_count": 15,
                "entry_ready_count": 0,
                "age_days": 0,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "partial",
                    "health_status": "warn",
                    "rows_upserted": 15,
                    "run_at": "2026-06-26T08:45:00",
                    "failure_summary": {"failed_count": 1, "reason_codes": ["sync_partial_failure"]},
                },
                {
                    "job_name": "sync_market_bundle",
                    "status": "partial",
                    "health_status": "warn",
                    "rows_upserted": 0,
                    "run_at": "2026-06-26T08:44:00",
                    "failure_summary": {
                        "failed_count": 34,
                        "reason_codes": ["upstream_forbidden", "shioaji_notice_unavailable"],
                        "components": [
                            {"name": "daily_bars", "status": "partial", "failed_count": 33},
                            {"name": "attention_stocks", "status": "partial", "failed_count": 1},
                        ],
                        "samples": [
                            {
                                "scope": "daily_bars",
                                "symbol": "2330",
                                "error_type": "FinMindRequestError",
                                "reason_code": "upstream_forbidden",
                                "message": "403 Client Error: Forbidden",
                            }
                        ],
                    },
                },
            ],
        }
    )

    payload = DailyRadarDeploymentHealthService().evaluate(repo, as_of=date(2026, 6, 26))

    assert payload["status"] == "warn"
    assert "daily_radar_sync_job_partial" in payload["warnings"]
    assert payload["entry_ready_diagnosis"]["reason"] == "data_issue"
    assert payload["sync_issues"][1]["job_name"] == "sync_market_bundle"
    assert "上游資料權限不足或 token 未生效" in payload["sync_issues"][1]["reason_labels"]
    assert "Shioaji 注意股 API 不可用" in payload["sync_issues"][1]["reason_labels"]
    assert any("FinMind token" in step for step in payload["next_steps"])
    assert any("Shioaji" in step for step in payload["next_steps"])


def test_daily_radar_health_ignores_benign_partial_market_gap_sync_job() -> None:
    repo = _Repo(
        {
            "latest_radar": {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "row_count": 15,
                "entry_ready_count": 1,
                "age_days": 0,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "ok",
                    "health_status": "ok",
                    "rows_upserted": 15,
                    "run_at": "2026-06-26T08:45:00",
                },
                {
                    "job_name": "sync_market_bundle",
                    "status": "partial",
                    "health_status": "ok",
                    "benign_partial_market_gap": True,
                    "rows_upserted": 144,
                    "run_at": "2026-06-26T08:44:00",
                    "failure_summary": {
                        "failed_count": 2,
                        "reason_codes": ["upstream_no_data"],
                        "symbol_failures": [
                            {
                                "symbol": "3142",
                                "scopes": ["daily_bars", "institutional_chip"],
                                "reason_codes": ["upstream_no_data"],
                            }
                        ],
                    },
                },
            ],
        }
    )

    payload = DailyRadarDeploymentHealthService().evaluate(repo, as_of=date(2026, 6, 26))

    assert payload["status"] == "ok"
    assert payload["sync_issues"] == []
    assert "daily_radar_sync_job_partial" not in payload["warnings"]
    assert payload["next_steps"] == ["維持現有排程，下一步可接 Telegram/LINE 告警。"]


def test_daily_radar_health_blocks_missing_run_and_failed_job() -> None:
    repo = _Repo(
        {
            "latest_radar": {"run_id": "", "run_date": None, "row_count": 0, "entry_ready_count": 0},
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "failed",
                    "health_status": "danger",
                    "rows_upserted": 0,
                    "run_at": "2026-06-26T08:45:00",
                }
            ],
        }
    )

    payload = DailyRadarDeploymentHealthService().evaluate(repo, as_of=date(2026, 6, 26))

    assert payload["status"] == "danger"
    assert "daily_radar_run_exists" in payload["blocking_reasons"]
    assert "daily_radar_row_count" in payload["blocking_reasons"]
    assert "daily_radar_sync_job_ok" in payload["blocking_reasons"]
    assert any("run-daily-radar-job" in step for step in payload["next_steps"])


def test_daily_radar_health_notification_thresholds_and_message() -> None:
    danger_payload = {
        "status": "danger",
        "status_text": "需處理",
        "latest_run_date": "2026-03-03",
        "age_days": 117,
        "row_count": 1,
        "entry_ready_count": 0,
        "blocking_reasons": ["daily_radar_max_age"],
        "warnings": ["daily_radar_entry_ready"],
        "next_steps": ["檢查 launchd/systemd/Docker 排程日誌。"],
    }
    warn_payload = dict(danger_payload, status="warn", status_text="需留意", blocking_reasons=[])
    ok_payload = dict(danger_payload, status="ok", status_text="正常", blocking_reasons=[], warnings=[])

    assert daily_radar_health_should_notify(danger_payload, notify_on="danger") is True
    assert daily_radar_health_should_notify(warn_payload, notify_on="danger") is False
    assert daily_radar_health_should_notify(warn_payload, notify_on="warn") is True
    assert daily_radar_health_should_notify(ok_payload, notify_on="always") is True
    assert daily_radar_health_should_notify(danger_payload, notify_on="none") is False

    message = format_daily_radar_health_message(danger_payload)
    assert "[每日雷達部署] 需處理" in message
    assert "2026-03-03" in message
    assert "daily_radar_max_age" in message
    assert "下一步" in message


def test_daily_radar_health_cli_parser_exposes_monitor_options() -> None:
    args = build_parser().parse_args(
        [
            "daily-radar-health",
            "--as-of",
            "2026-06-26",
            "--max-age-days",
            "4",
            "--warn-age-days",
            "2",
            "--min-rows",
            "9",
            "--min-entry-ready",
            "0",
            "--notify",
            "both",
            "--notify-on",
            "warn",
            "--fail-on-danger",
            "--fail-on-notify-error",
        ]
    )

    assert args.func is cmd_daily_radar_health
    assert args.as_of == "2026-06-26"
    assert args.max_age_days == 4
    assert args.warn_age_days == 2
    assert args.min_rows == 9
    assert args.min_entry_ready == 0
    assert args.notify == "both"
    assert args.notify_on == "warn"
    assert args.fail_on_danger is True
    assert args.fail_on_notify_error is True


def test_run_invest_pipeline_cli_parser_exposes_one_click_options() -> None:
    args = build_parser().parse_args(
        [
            "run-invest-pipeline",
            "--trade-date",
            "2026-07-09",
            "--target-count",
            "10",
            "--risk-profile",
            "aggressive",
            "--universe",
            "custom",
            "--symbols",
            "2330,2454",
            "--no-include-attention",
            "--include-extended-chip",
            "--no-data-freshness-required",
            "--json",
            "--fail-on-degraded",
        ]
    )

    assert args.func is cmd_run_invest_pipeline
    assert args.trade_date == "2026-07-09"
    assert args.target_count == 10
    assert args.risk_profile == "aggressive"
    assert args.universe == "custom"
    assert args.symbols == "2330,2454"
    assert args.include_attention is False
    assert args.include_extended_chip is True
    assert args.data_freshness_required is False
    assert args.json is True
    assert args.fail_on_degraded is True


def test_data_health_overview_formatter_surfaces_market_coverage() -> None:
    message = format_data_health_overview_message(
        {
            "status": "warn",
            "score": 76,
            "daily_bars": {"latest_date": "2026-06-26", "status": "ok", "age_days": 0},
            "universe": {"active_symbols": 2, "recent_symbols": 1, "coverage": 0.5},
            "instrument_industry_coverage": {
                "active_symbols": 2,
                "official_industry_symbols": 1,
                "missing_industry_count": 1,
                "coverage_pct": 50.0,
            },
            "market_data_coverage": {
                "datasets": [
                    {
                        "key": "daily_bars",
                        "label": "日線資料",
                        "status": "danger",
                        "coverage_pct": 50.0,
                        "missing_symbols": ["3105"],
                        "unavailable_symbol_count": 1,
                        "unavailable_symbols": ["1258"],
                        "markets": [
                            {"market": "TWSE", "label": "上市", "coverage_pct": 100.0, "missing_count": 0},
                            {"market": "TPEX", "label": "上櫃", "coverage_pct": 0.0, "missing_count": 1},
                        ],
                    }
                ]
            },
            "freshness_alerts": [{"key": "market_data_coverage", "label": "市場資料覆蓋", "message": "上櫃缺日線"}],
        }
    )

    assert "[資料健康度] 需留意 · 76/100" in message
    assert "標的池: 1/2（覆蓋 50%）" in message
    assert "產業分類: 1/2（正式覆蓋 50%；缺 1，不足時使用代號 proxy）" in message
    assert "- 日線資料: 50%（danger；上市 100% / 上櫃 0% 缺 1；缺口 3105；不可重試暫排 1（1258））" in message
    assert "市場資料覆蓋: 上櫃缺日線" in message


def test_data_health_overview_formatter_marks_premium_market_data_requirement() -> None:
    message = format_data_health_overview_message(
        {
            "status": "warn",
            "score": 61,
            "daily_bars": {"latest_date": "2026-06-26", "status": "ok", "age_days": 0},
            "universe": {"active_symbols": 2, "recent_symbols": 2, "coverage": 1.0},
            "market_data_coverage": {
                "scope": "common_stock",
                "datasets": [
                    {
                        "key": "broker_agg_chip",
                        "label": "分點聚合（加值）",
                        "status": "warn",
                        "optional": True,
                        "repairable": False,
                        "provider_requirement": {"plan": "FinMind sponsor"},
                        "coverage_pct": 0.0,
                        "missing_symbols": ["2330", "3105"],
                        "markets": [
                            {"market": "TWSE", "label": "上市", "coverage_pct": 0.0, "missing_count": 1},
                            {"market": "TPEX", "label": "上櫃", "coverage_pct": 0.0, "missing_count": 1},
                        ],
                    }
                ],
            },
        }
    )

    assert "市場資料覆蓋（普通股）" in message
    assert "- 分點聚合（加值):" not in message
    assert "- 分點聚合（加值）: 0%（warn；加值資料；需 FinMind sponsor" in message


def test_data_health_overview_cli_parser_exposes_command() -> None:
    args = build_parser().parse_args(
        [
            "data-health-overview",
            "--as-of",
            "2026-06-26",
            "--freshness-days",
            "5",
            "--json",
            "--fail-on-danger",
        ]
    )

    assert args.func is cmd_data_health_overview
    assert args.as_of == "2026-06-26"
    assert args.freshness_days == 5
    assert args.json is True
    assert args.fail_on_danger is True


def test_market_data_repair_plan_filters_dataset_and_market() -> None:
    overview = {
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "danger",
                    "coverage_pct": 50.0,
                    "missing_count": 3,
                    "missing_symbols": ["1101", "3105", "6488"],
                    "markets": [
                        {"market": "TWSE", "label": "上市", "missing_symbols": ["1101"], "missing_count": 1},
                        {"market": "TPEX", "label": "上櫃", "missing_symbols": ["3105", "6488"], "missing_count": 2},
                    ],
                },
                {
                    "key": "institutional_chip",
                    "label": "法人買賣超",
                    "status": "danger",
                    "coverage_pct": 0.0,
                    "missing_count": 2,
                    "missing_symbols": ["2330", "3105"],
                    "markets": [],
                },
            ]
        }
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["daily_bars"],
        markets=["TPEX"],
        as_of=date(2026, 6, 26),
        freshness_days=3,
        lookback_days=10,
        max_symbols=5,
    )

    assert plan["status"] == "ready"
    assert plan["symbols"] == ["3105", "6488"]
    assert plan["include_bars"] is True
    assert plan["include_institutional"] is False
    assert plan["include_broker_agg"] is False
    assert "--symbols 3105,6488" in plan["command"]
    assert "--include-bars" in plan["command"]
    assert "--no-include-chip" in plan["command"]
    message = format_market_data_repair_plan(plan)
    assert "[市場資料覆蓋修復] 乾跑" in message
    assert "3105, 6488" in message


def test_market_data_repair_plan_skips_recent_no_data_symbols() -> None:
    overview = {
        "recent_sync_failures": {
            "symbol_failures": [
                {
                    "symbol": "3105",
                    "scopes": ["daily_bars"],
                    "reason_codes": ["upstream_no_data"],
                }
            ]
        },
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "danger",
                    "missing_count": 2,
                    "missing_symbols": ["3105", "6488"],
                    "markets": [
                        {
                            "market": "TPEX",
                            "label": "上櫃",
                            "missing_symbols": ["3105", "6488"],
                            "missing_count": 2,
                        }
                    ],
                }
            ]
        },
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["daily_bars"],
        markets=["TPEX"],
        as_of=date(2026, 6, 26),
        max_symbols=5,
    )

    assert plan["symbols"] == ["6488"]
    assert plan["skipped_symbols"] == ["3105"]
    assert "3105" in format_market_data_repair_plan(plan)


def test_market_data_repair_plan_skips_coverage_unavailable_symbols_without_recent_failures() -> None:
    overview = {
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "danger",
                    "missing_count": 2,
                    "missing_symbols": ["2928", "6488"],
                    "unavailable_symbols": ["2928"],
                    "unavailable_symbol_reasons": {"2928": ["upstream_no_data"]},
                    "markets": [
                        {
                            "market": "TPEX",
                            "label": "上櫃",
                            "missing_symbols": ["2928", "6488"],
                            "missing_count": 2,
                            "unavailable_symbols": ["2928"],
                            "unavailable_symbol_reasons": {"2928": ["upstream_no_data"]},
                        }
                    ],
                }
            ]
        },
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["daily_bars"],
        markets=["TPEX"],
        as_of=date(2026, 7, 9),
        max_symbols=5,
    )

    assert plan["symbols"] == ["6488"]
    assert plan["skipped_symbols"] == ["2928"]
    assert plan["skipped_symbol_reasons"]["2928"] == ["upstream_no_data"]
    assert "2928（查無資料）" in format_market_data_repair_plan(plan)


def test_market_data_repair_plan_retries_payment_required_symbols_with_official_bulk() -> None:
    overview = {
        "recent_sync_failures": {
            "symbol_failures": [
                {
                    "symbol": "1507",
                    "scopes": ["daily_bars"],
                    "reason_codes": ["upstream_payment_required"],
                }
            ]
        },
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "danger",
                    "missing_count": 2,
                    "missing_symbols": ["1507", "6488"],
                    "markets": [
                        {
                            "market": "TWSE",
                            "label": "上市",
                            "missing_symbols": ["1507", "6488"],
                            "missing_count": 2,
                        }
                    ],
                }
            ]
        },
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["daily_bars"],
        markets=["TWSE"],
        as_of=date(2026, 7, 3),
        max_symbols=5,
    )

    assert plan["symbols"] == ["1507", "6488"]
    assert plan["skipped_symbols"] == []
    assert "--prefer-official-bulk" in plan["command"]


def test_market_data_repair_plan_skips_payment_required_after_official_bulk_attempt() -> None:
    overview = {
        "sync_jobs": [
            {
                "job_name": "sync_market_bundle",
                "status": "partial",
                "failure_summary": {
                    "reason_codes": ["upstream_payment_required"],
                    "components": [
                        {
                            "name": "daily_bars",
                            "status": "partial",
                            "failed_count": 1,
                            "prefer_official_bulk": True,
                            "official_fallback_rows": 0,
                            "api_mode": "",
                        }
                    ],
                    "symbol_failures": [
                        {
                            "symbol": "1507",
                            "scopes": ["daily_bars"],
                            "reason_codes": ["upstream_payment_required"],
                        }
                    ],
                },
            }
        ],
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "danger",
                    "missing_count": 2,
                    "missing_symbols": ["1507", "6488"],
                    "markets": [
                        {
                            "market": "TWSE",
                            "label": "上市",
                            "missing_symbols": ["1507", "6488"],
                            "missing_count": 2,
                        }
                    ],
                }
            ]
        },
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["daily_bars"],
        markets=["TWSE"],
        as_of=date(2026, 7, 3),
        max_symbols=5,
    )

    assert plan["symbols"] == ["6488"]
    assert plan["skipped_symbols"] == ["1507"]
    assert plan["skipped_symbol_reasons"]["1507"] == ["upstream_payment_required_after_official"]
    assert "1507（付費限制且官方重試未補到）" in format_market_data_repair_plan(plan)


def test_market_data_repair_plan_skips_recent_bad_request_symbols_by_scope() -> None:
    overview = {
        "recent_sync_failures": {
            "symbol_failures": [
                {
                    "symbol": "1101",
                    "scopes": ["broker_agg_chip"],
                    "reason_codes": ["upstream_bad_request"],
                }
            ]
        },
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "broker_agg_chip",
                    "label": "分點聚合",
                    "status": "danger",
                    "missing_count": 2,
                    "missing_symbols": ["1101", "1102"],
                    "markets": [
                        {
                            "market": "TWSE",
                            "label": "上市",
                            "missing_symbols": ["1101", "1102"],
                            "missing_count": 2,
                        }
                    ],
                }
            ]
        },
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["broker_agg_chip"],
        markets=["TWSE"],
        as_of=date(2026, 6, 26),
        max_symbols=5,
    )

    assert plan["symbols"] == ["1102"]
    assert plan["skipped_symbols"] == ["1101"]


def test_market_data_repair_plan_skips_recent_forbidden_symbols() -> None:
    overview = {
        "recent_sync_failures": {
            "symbol_failures": [
                {
                    "symbol": "1591",
                    "scopes": ["daily_bars"],
                    "reason_codes": ["upstream_forbidden"],
                }
            ]
        },
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "danger",
                    "missing_count": 2,
                    "missing_symbols": ["1591", "3206"],
                    "markets": [
                        {
                            "market": "TPEX",
                            "label": "上櫃",
                            "missing_symbols": ["1591", "3206"],
                            "missing_count": 2,
                        }
                    ],
                }
            ]
        },
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["daily_bars"],
        markets=["TPEX"],
        as_of=date(2026, 6, 26),
        max_symbols=5,
    )

    assert plan["symbols"] == ["3206"]
    assert plan["skipped_symbols"] == ["1591"]
    assert plan["skipped_symbol_reasons"]["1591"] == ["upstream_forbidden"]
    assert "1591" in format_market_data_repair_plan(plan)


def test_market_data_repair_plan_skips_premium_non_repairable_dataset() -> None:
    overview = {
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "broker_agg_chip",
                    "label": "分點聚合（加值）",
                    "status": "warn",
                    "optional": True,
                    "repairable": False,
                    "provider_requirement": {
                        "plan": "FinMind sponsor",
                        "reason": "需要 sponsor 會員與券商代碼，不能用一般市場缺口批次修復。",
                    },
                    "missing_count": 2,
                    "missing_symbols": ["2330", "3105"],
                    "markets": [
                        {
                            "market": "TWSE",
                            "label": "上市",
                            "missing_symbols": ["2330"],
                            "missing_count": 1,
                        },
                        {
                            "market": "TPEX",
                            "label": "上櫃",
                            "missing_symbols": ["3105"],
                            "missing_count": 1,
                        },
                    ],
                }
            ]
        }
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["broker_agg_chip"],
        as_of=date(2026, 6, 26),
        max_symbols=5,
    )

    assert plan["status"] == "empty"
    assert plan["symbols"] == []
    assert plan["include_broker_agg"] is False
    assert plan["skipped_datasets"] == [
        {
            "key": "broker_agg_chip",
            "label": "分點聚合（加值）",
            "reason": "需要 sponsor 會員與券商代碼，不能用一般市場缺口批次修復。",
        }
    ]
    message = format_market_data_repair_plan(plan)
    assert "略過加值/不可自動修復資料集: 分點聚合（加值）" in message
    assert "目前沒有可用的市場資料覆蓋缺口樣本可重試。" in message


def test_market_data_repair_plan_excludes_symbols_already_attempted_in_same_run() -> None:
    overview = {
        "market_data_coverage": {
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "warn",
                    "missing_count": 3,
                    "missing_symbols": ["1101", "1102", "1103"],
                    "markets": [
                        {
                            "market": "TWSE",
                            "missing_symbols": ["1101", "1102", "1103"],
                            "missing_count": 3,
                        }
                    ],
                }
            ]
        }
    }

    plan = build_market_data_repair_plan(
        overview,
        datasets=["daily_bars"],
        markets=["TWSE"],
        as_of=date(2026, 6, 26),
        max_symbols=2,
        exclude_symbols={"1101", "1102"},
    )

    assert plan["symbols"] == ["1103"]
    assert plan["excluded_symbols"] == ["1101", "1102"]
    assert "1101" not in plan["command"]


def test_market_data_repair_result_summary_is_compact_and_actionable() -> None:
    message = format_market_data_repair_result_summary(
        {
            "rows_upserted_total": 222,
            "failed_count_total": 3,
            "degraded_count": 0,
            "partial_failure": True,
            "sync_quality": {
                "status": "partial",
                "rows_upserted": 222,
                "failed_count": 3,
                "degraded_count": 0,
                "components": {
                    "daily_bars": {
                        "status": "partial",
                        "rows_upserted": 110,
                        "failed_count": 1,
                        "official_fallback_rows": 104,
                        "api_mode": "official_bulk",
                    },
                    "institutional_chip": {
                        "status": "partial",
                        "rows_upserted": 112,
                        "failed_count": 2,
                        "official_fallback_rows": 112,
                        "api_mode": "official_bulk",
                    },
                },
            },
            "bundle": {
                "daily_bars": {
                    "failed_samples": [
                        {
                            "symbol": "3474",
                            "error": "no daily bars returned for 3474",
                        }
                    ]
                },
                "institutional_chip": {
                    "failed_samples": [
                        {
                            "symbol": "3454",
                            "error": "no institutional chip rows returned for 3454",
                        }
                    ]
                },
            },
        }
    )

    assert "結果: partial，寫入 222 筆，失敗 3，降級 0" in message
    assert "- 日線資料: partial，寫入 110，官方 104，失敗 1，official_bulk" in message
    assert "- 法人買賣超: partial，寫入 112，官方 112，失敗 2，official_bulk" in message
    assert "失敗樣本: 3474 日線資料: no daily bars returned for 3474" in message
    assert len(message.splitlines()) <= 5


def test_market_data_repair_after_summary_shows_health_delta_context() -> None:
    message = format_market_data_repair_after_summary(
        {
            "universe": {"active_symbols": 2741, "recent_symbols": 1517, "coverage": 0.5534},
            "market_data_coverage": {
                "datasets": [
                    {"key": "daily_bars", "label": "日線資料", "coverage_pct": 74.0, "status": "warn"},
                    {"key": "institutional_chip", "label": "法人買賣超", "coverage_pct": 72.0, "status": "warn"},
                ]
            },
        }
    )

    assert "修復後健康摘要:" in message
    assert "標的池: 1517/2741（覆蓋 55%）" in message
    assert "市場資料: 日線資料: 74%（warn） / 法人買賣超: 72%（warn）" in message


def test_repair_market_data_coverage_cli_parser_exposes_command() -> None:
    args = build_parser().parse_args(
        [
            "repair-market-data-coverage",
            "--as-of",
            "2026-06-26",
            "--freshness-days",
            "5",
            "--lookback-days",
            "14",
            "--datasets",
            "daily_bars,institutional_chip",
            "--markets",
            "TPEX",
            "--max-symbols",
            "8",
            "--batches",
            "3",
            "--execute",
            "--json",
            "--fail-on-empty",
        ]
    )

    assert args.func is cmd_repair_market_data_coverage
    assert args.as_of == "2026-06-26"
    assert args.freshness_days == 5
    assert args.lookback_days == 14
    assert args.datasets == "daily_bars,institutional_chip"
    assert args.markets == "TPEX"
    assert args.max_symbols == 8
    assert args.batches == 3
    assert args.execute is True
    assert args.json is True
    assert args.fail_on_empty is True


def test_sync_twse_tpex_universe_cli_parser_exposes_command() -> None:
    args = build_parser().parse_args(["sync-twse-tpex-universe"])

    assert args.func is cmd_sync_twse_tpex_universe
