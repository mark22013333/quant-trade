from __future__ import annotations

from datetime import date

from app.cli import build_parser, cmd_decision_radar_phase1_acceptance
from app.reports.decision_radar_phase1_acceptance import (
    DecisionRadarPhase1AcceptanceService,
    format_decision_radar_phase1_acceptance_message,
)


class _Repo:
    def __init__(self, *, rows: list[dict], overview: dict | None = None) -> None:
        self.rows = rows
        self.overview = overview or {
            "status": "ok",
            "score": 95,
            "latest_radar": {
                "run_id": "radar_phase1",
                "run_date": "2026-07-15",
                "row_count": len(rows),
                "entry_ready_count": sum(1 for row in rows if row.get("action_label") == "ENTRY_READY"),
                "age_days": 0,
            },
            "sync_jobs": [
                {
                    "job_name": "daily_radar",
                    "status": "ok",
                    "health_status": "ok",
                    "rows_upserted": len(rows),
                    "run_at": "2026-07-15T08:45:00",
                }
            ],
        }

    def get_data_health_overview(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self.overview

    def get_latest_daily_radar(self, count=50):  # noqa: ANN001
        return self.rows[:count]

    def list_recent_trading_execution_records(self, *, limit=20):  # noqa: ANN001
        return []

    def list_recent_order_preview_records(self, *, limit=20):  # noqa: ANN001
        return []

    def list_recent_promotion_gate_records(self, *, limit=20):  # noqa: ANN001
        return []

    def list_recent_reconciliation_records(self, *, limit=20):  # noqa: ANN001
        return []

    def summarize_advisor_observation_period(self, *, advisor_name, min_days, limit):  # noqa: ANN001
        return {"blocking_reasons": [], "ready_for_next_phase": True}


def test_decision_radar_phase1_acceptance_ready_for_user_testing() -> None:
    rows = [
        {
            "rank": 1,
            "symbol": "2330",
            "name": "台積電",
            "action_label": "ENTRY_READY",
            "risk_quality_label": "核心候選",
            "risk_quality_score": 82.0,
            "risk_adjusted_score": 88.0,
            "blocker_tags": [],
        },
        {
            "rank": 2,
            "symbol": "2454",
            "name": "聯發科",
            "action_label": "WATCH_WAIT_TRIGGER",
            "risk_quality_label": "等待確認",
            "risk_quality_score": 58.0,
            "risk_adjusted_score": 76.0,
            "blocker_tags": [],
        },
        {
            "rank": 3,
            "symbol": "2317",
            "name": "鴻海",
            "action_label": "NOT_RECOMMENDED",
            "risk_quality_label": "先跳過",
            "risk_quality_score": 42.0,
            "risk_adjusted_score": 60.0,
            "blocker_tags": ["LOW_LIQUIDITY"],
        },
    ]
    repo = _Repo(rows=rows)

    payload = DecisionRadarPhase1AcceptanceService(min_rows=3, env={"SHIOAJI_ENABLE_LIVE_ORDERS": ""}).build(
        repo,
        as_of=date(2026, 7, 15),
    )

    assert payload["status"] == "ok"
    assert payload["ready_for_user_testing"] is True
    assert payload["requires_user_confirmation"] is True
    assert payload["action_grade"]["counts"] == {"可下手": 1, "只觀察": 1, "不碰": 1}
    assert payload["action_grade"]["ready_symbols"] == ["2330"]
    assert payload["blocking_reasons"] == []
    assert "docs/decision_radar_phase1_acceptance.md" in payload["completion_rule"]
    assert payload["test_journal"]["path"] == "docs/decision_radar_phase1_test_journal.md"
    assert payload["test_journal"]["minimum_trading_days"] == 2
    assert payload["user_confirmation"]["required_phrase"] == "Phase 1 通過"
    message = format_decision_radar_phase1_acceptance_message(payload)
    assert "[Phase 1] 可交給使用者測試" in message
    assert "分級: 可下手 1 / 只觀察 1 / 不碰 1" in message
    assert "測試紀錄: docs/decision_radar_phase1_test_journal.md" in message
    assert "通過回覆: Phase 1 通過" in message


def test_decision_radar_phase1_acceptance_blocks_missing_risk_quality() -> None:
    repo = _Repo(
        rows=[
            {"rank": 1, "symbol": "2330", "action_label": "ENTRY_READY", "risk_quality_score": 0.0},
            {"rank": 2, "symbol": "2454", "action_label": "WATCH_WAIT_TRIGGER", "risk_quality_score": 0.0},
        ]
    )

    payload = DecisionRadarPhase1AcceptanceService(min_rows=2).build(repo, as_of=date(2026, 7, 15))

    assert payload["status"] == "danger"
    assert payload["ready_for_user_testing"] is False
    assert "risk_quality_present" in payload["blocking_reasons"]
    assert any("風險品質" in step for step in payload["next_steps"])


def test_decision_radar_phase1_acceptance_defers_observation_shortage_until_promotion() -> None:
    repo = _Repo(
        rows=[
            {
                "rank": 1,
                "symbol": "3481",
                "action_label": "WATCH_WAIT_TRIGGER",
                "risk_quality_label": "先降溫",
                "risk_quality_score": 34.0,
                "risk_adjusted_score": 51.9,
                "blocker_tags": [],
            }
        ]
    )
    ops_payload = {
        "status": "danger",
        "blocking_reasons": ["promotion_readiness"],
        "warnings": ["eod_summary"],
        "metrics": {"live_orders_enabled": False},
        "components": [
            {
                "key": "promotion_readiness",
                "details": {"blocking_reasons": ["observation_days_insufficient"]},
            }
        ],
    }

    payload = DecisionRadarPhase1AcceptanceService(min_rows=1).build(
        repo,
        as_of=date(2026, 7, 21),
        ops_payload=ops_payload,
    )

    assert payload["status"] == "warn"
    assert payload["ready_for_user_testing"] is True
    assert payload["blocking_reasons"] == []
    assert payload["warnings"] == ["observation_period_pending"]
    assert payload["evidence"]["ops_readiness"]["user_testing_blocking_reasons"] == []
    assert payload["evidence"]["ops_readiness"]["deferred_until_promotion"] == [
        "observation_days_insufficient"
    ]


def test_decision_radar_phase1_acceptance_keeps_other_promotion_blockers() -> None:
    repo = _Repo(
        rows=[
            {
                "rank": 1,
                "symbol": "3481",
                "action_label": "WATCH_WAIT_TRIGGER",
                "risk_quality_label": "先降溫",
                "risk_quality_score": 34.0,
                "risk_adjusted_score": 51.9,
                "blocker_tags": [],
            }
        ]
    )
    ops_payload = {
        "status": "danger",
        "blocking_reasons": ["promotion_readiness"],
        "warnings": [],
        "metrics": {"live_orders_enabled": False},
        "components": [
            {
                "key": "promotion_readiness",
                "details": {
                    "blocking_reasons": [
                        "observation_days_insufficient",
                        "reconciliation_not_matched",
                    ]
                },
            }
        ],
    }

    payload = DecisionRadarPhase1AcceptanceService(min_rows=1).build(
        repo,
        as_of=date(2026, 7, 21),
        ops_payload=ops_payload,
    )

    assert payload["status"] == "danger"
    assert payload["ready_for_user_testing"] is False
    assert payload["blocking_reasons"] == ["ops_not_danger"]


def test_decision_radar_phase1_acceptance_cli_parser_exposes_command() -> None:
    args = build_parser().parse_args(
        [
            "decision-radar-phase1-acceptance",
            "--as-of",
            "2026-07-15",
            "--max-age-days",
            "4",
            "--warn-age-days",
            "2",
            "--min-rows",
            "8",
            "--min-entry-ready",
            "1",
            "--json",
            "--fail-on-danger",
        ]
    )

    assert args.func is cmd_decision_radar_phase1_acceptance
    assert args.as_of == "2026-07-15"
    assert args.max_age_days == 4
    assert args.warn_age_days == 2
    assert args.min_rows == 8
    assert args.min_entry_ready == 1
    assert args.json is True
    assert args.fail_on_danger is True
