from __future__ import annotations

from datetime import date

from app.cli import build_parser, cmd_ops_readiness, _merge_intraday_quote_review_readiness
from app.reports.ops_readiness import OpsReadinessService


class _Repo:
    def __init__(
        self,
        *,
        overview: dict,
        radar_rows: list[dict] | None = None,
        executions: list[dict] | None = None,
        previews: list[dict] | None = None,
        promotion_gates: list[dict] | None = None,
        reconciliations: list[dict] | None = None,
        observation_summary: dict | None = None,
    ) -> None:
        self.overview = overview
        self.radar_rows = radar_rows or []
        self.executions = executions or []
        self.previews = previews or []
        self.promotion_gates = promotion_gates or []
        self.reconciliations = reconciliations or []
        self.observation_summary = observation_summary or {"blocking_reasons": []}

    def get_data_health_overview(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self.overview

    def get_latest_daily_radar(self, count=50):  # noqa: ANN001
        return self.radar_rows[:count]

    def list_recent_trading_execution_records(self, *, limit=20):  # noqa: ANN001
        return self.executions[:limit]

    def list_recent_order_preview_records(self, *, limit=20):  # noqa: ANN001
        return self.previews[:limit]

    def list_recent_promotion_gate_records(self, *, limit=20):  # noqa: ANN001
        return self.promotion_gates[:limit]

    def list_recent_reconciliation_records(self, *, limit=20):  # noqa: ANN001
        return self.reconciliations[:limit]

    def summarize_advisor_observation_period(self, *, advisor_name, min_days, limit):  # noqa: ANN001
        assert advisor_name == "stub"
        assert min_days >= 3
        assert limit >= min_days
        return self.observation_summary


def test_ops_readiness_marks_clean_small_live_handoff_ok() -> None:
    repo = _Repo(
        overview={
            "status": "ok",
            "score": 98,
            "freshness_alerts": [],
            "latest_radar": {
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "row_count": 12,
                "entry_ready_count": 2,
                "age_days": 0,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "ok",
                    "health_status": "ok",
                    "run_at": "2026-06-26T08:45:00",
                }
            ],
        },
        executions=[
            {
                "environment": "simulation",
                "accepted": True,
                "executed": True,
                "status": "submitted",
                "created_at": "2026-06-26T13:30:00",
            }
        ],
        previews=[{"status": "accepted", "created_at": "2026-06-26T13:20:00"}],
        promotion_gates=[
            {
                "strategy_name": "manual",
                "strategy_version": "manual",
                "accepted": True,
                "reason": "ok",
                "blocking_reasons": [],
                "created_at": "2026-06-26T13:10:00",
            }
        ],
        reconciliations=[{"matched": True, "blocking_reasons": [], "checked_at": "2026-06-26T15:10:00"}],
        observation_summary={"blocking_reasons": [], "ready_for_next_phase": True},
    )

    payload = OpsReadinessService(env={"SHIOAJI_ENABLE_LIVE_ORDERS": "1", "CODEX_ADVISOR_ENABLED": "1"}).build(
        repo,
        as_of=date(2026, 6, 26),
        target_phase="small_live",
    )

    assert payload["status"] == "ok"
    assert payload["target_phase"] == "small_live"
    assert payload["blocking_reasons"] == []
    assert payload["metrics"]["target_ready"] is True
    assert payload["metrics"]["live_orders_enabled"] is True
    assert payload["decision_brief"]["state"] == "review_ready"
    assert payload["decision_brief"]["primary_action"]["key"] == "daily-radar"
    assert payload["decision_brief"]["facts"][0]["value"] == 2


def test_ops_readiness_surfaces_radar_eod_promotion_and_env_blockers() -> None:
    repo = _Repo(
        overview={
            "status": "warn",
            "score": 70,
            "freshness_alerts": [{"key": "daily_bars"}],
            "latest_radar": {"run_id": "", "run_date": None, "row_count": 0, "entry_ready_count": 0},
            "sync_jobs": [{"job_name": "daily_radar", "status": "failed", "health_status": "danger"}],
        },
        executions=[
            {
                "environment": "live",
                "accepted": False,
                "executed": False,
                "status": "error",
                "created_at": "2026-06-26T13:30:00",
            }
        ],
        previews=[{"status": "created", "created_at": "2026-06-26T13:20:00"}],
        promotion_gates=[],
        reconciliations=[{"matched": False, "blocking_reasons": ["cash_diff"], "checked_at": "2026-06-26T15:10:00"}],
        observation_summary={"blocking_reasons": ["observation_days_insufficient"]},
    )

    payload = OpsReadinessService(env={"SHIOAJI_ENABLE_LIVE_ORDERS": "1", "CODEX_ADVISOR_ENABLED": "1"}).build(
        repo,
        as_of=date(2026, 6, 26),
        target_phase="paper",
    )

    assert payload["status"] == "danger"
    assert payload["blocking_reasons"] == [
        "daily_radar",
        "eod_summary",
        "promotion_readiness",
        "environment_safety",
    ]
    assert payload["metrics"]["target_ready"] is False
    assert any("run-daily-radar-job" in step for step in payload["next_steps"])
    assert any("正式下單環境旗標" in step for step in payload["next_steps"])
    assert payload["decision_brief"]["state"] == "blocked"
    assert "暫停建立新部位" in payload["decision_brief"]["title"]


def test_ops_readiness_builds_wait_trigger_decision_brief() -> None:
    repo = _Repo(
        overview={
            "status": "warn",
            "score": 80,
            "freshness_alerts": [{"key": "daily_bars"}],
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
        radar_rows=[
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
            {"symbol": "1216", "action_label": "NOT_RECOMMENDED"},
        ],
        promotion_gates=[
            {
                "strategy_name": "manual",
                "strategy_version": "manual",
                "accepted": True,
                "reason": "ok",
                "blocking_reasons": [],
                "created_at": "2026-06-26T13:10:00",
            }
        ],
        reconciliations=[{"matched": True, "blocking_reasons": [], "checked_at": "2026-06-26T15:10:00"}],
        observation_summary={"blocking_reasons": [], "ready_for_next_phase": True},
    )

    payload = OpsReadinessService(min_rows=2, env={}).build(repo, as_of=date(2026, 6, 26), target_phase="paper")

    brief = payload["decision_brief"]
    assert brief["state"] == "wait_trigger"
    assert brief["title"] == "今天先觀察，不追價"
    assert brief["focus_candidate"]["symbol"] == "2327"
    assert "先等風險降溫" in brief["summary"]
    assert "條件未成立前維持觀察" in brief["next_step"]
    assert brief["facts"][1] == {"label": "待觸發", "value": 1, "unit": "檔"}
    assert payload["eod_summary"]["reconciliation_runbook"]["status"] == "ok"


def test_ops_readiness_compacts_daily_radar_sync_issues() -> None:
    repo = _Repo(
        overview={
            "status": "warn",
            "score": 70,
            "freshness_alerts": [{"key": "sync_jobs"}],
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
                        "reason_codes": ["upstream_forbidden"],
                        "components": [{"name": "daily_bars", "status": "partial", "failed_count": 33}],
                        "samples": [],
                    },
                },
            ],
        },
        executions=[{"environment": "simulation", "accepted": True, "executed": True, "status": "submitted"}],
        previews=[{"status": "accepted"}],
        promotion_gates=[
            {
                "strategy_name": "manual",
                "strategy_version": "manual",
                "accepted": True,
                "reason": "ok",
                "blocking_reasons": [],
                "created_at": "2026-06-26T13:10:00",
            }
        ],
        reconciliations=[{"matched": True, "blocking_reasons": [], "checked_at": "2026-06-26T15:10:00"}],
        observation_summary={"blocking_reasons": [], "ready_for_next_phase": True},
    )

    payload = OpsReadinessService(env={}).build(repo, as_of=date(2026, 6, 26), target_phase="paper")
    daily_component = next(item for item in payload["components"] if item["key"] == "daily_radar")

    assert payload["status"] == "warn"
    assert "資料源：上游資料權限不足或 token 未生效" in daily_component["meta"]
    assert daily_component["details"]["sync_issues"][1]["job_name"] == "sync_market_bundle"
    assert payload["daily_radar"]["sync_issues"][1]["reason_codes"] == ["upstream_forbidden"]
    assert any("FinMind token" in step for step in payload["next_steps"])


def test_ops_readiness_cli_merge_intraday_quote_review_marks_blocking_component() -> None:
    payload = {
        "status": "ok",
        "status_text": "正常",
        "components": [{"key": "daily_radar", "label": "盤前每日雷達", "status": "ok", "value": "正常"}],
        "blocking_reasons": [],
        "warnings": [],
        "metrics": {},
        "next_steps": ["營運總覽正常；可依今日雷達與人工 gate 繼續日常流程。"],
        "decision_brief": {"state": "review_ready", "primary_action": {"key": "daily-radar"}},
    }
    review = {
        "status": "danger",
        "label": "盤後需處理",
        "summary": "盤中行情服務本輪可靠度不足。",
        "issues": [{"code": "no_quotes_during_session", "severity": "danger", "message": "本輪沒有報價。"}],
        "next_actions": ["下一輪先用少量代號測試訂閱。"],
        "evidence": {"heartbeat_count": 2, "max_quote_coverage": 0.0, "snapshot_freshness": "fresh"},
    }

    merged = _merge_intraday_quote_review_readiness(payload, review)
    quote_component = next(item for item in merged["components"] if item["key"] == "intraday_quote_review")

    assert merged["status"] == "danger"
    assert "intraday_quote_review" in merged["blocking_reasons"]
    assert merged["metrics"]["intraday_quote_review_status"] == "danger"
    assert merged["metrics"]["intraday_quote_review_heartbeat_count"] == 2
    assert quote_component["details"]["blocking_reasons"] == ["no_quotes_during_session"]
    assert quote_component["meta"] == "heartbeat 2 次，報價覆蓋 0%，snapshot fresh。"
    assert merged["decision_brief"]["primary_action"]["key"] == "intraday_quote_review"


def test_ops_readiness_cli_parser_exposes_command() -> None:
    args = build_parser().parse_args(
        [
            "ops-readiness",
            "--as-of",
            "2026-06-26",
            "--target-phase",
            "small_live",
            "--advisor-name",
            "stub",
            "--strategy-name",
            "manual",
            "--strategy-version",
            "manual",
            "--max-age-days",
            "4",
            "--warn-age-days",
            "2",
            "--min-rows",
            "8",
            "--min-entry-ready",
            "0",
            "--eod-limit",
            "50",
            "--min-observation-days",
            "5",
            "--promotion-gate-limit",
            "80",
            "--include-intraday-quote-review",
            "--intraday-quote-state-path",
            "data/cache/quote.json",
            "--intraday-quote-review-max-age-seconds",
            "7200",
            "--intraday-quote-review-min-heartbeat-count",
            "3",
            "--intraday-quote-review-min-quote-coverage",
            "0.6",
            "--fail-on-danger",
        ]
    )

    assert args.func is cmd_ops_readiness
    assert args.as_of == "2026-06-26"
    assert args.target_phase == "small_live"
    assert args.max_age_days == 4
    assert args.warn_age_days == 2
    assert args.min_rows == 8
    assert args.min_entry_ready == 0
    assert args.eod_limit == 50
    assert args.min_observation_days == 5
    assert args.promotion_gate_limit == 80
    assert args.include_intraday_quote_review is True
    assert args.intraday_quote_state_path == "data/cache/quote.json"
    assert args.intraday_quote_review_max_age_seconds == 7200
    assert args.intraday_quote_review_min_heartbeat_count == 3
    assert args.intraday_quote_review_min_quote_coverage == 0.6
    assert args.fail_on_danger is True
