from __future__ import annotations

from datetime import date

from app.cli import build_parser, cmd_eod_summary
from app.reports.eod_reconciliation import EndOfDayReconciliationSummaryService


class _Repo:
    def __init__(
        self,
        *,
        executions: list[dict] | None = None,
        previews: list[dict] | None = None,
        promotion_gates: list[dict] | None = None,
        reconciliations: list[dict] | None = None,
        data_overview: dict | None = None,
    ) -> None:
        self.executions = executions or []
        self.previews = previews or []
        self.promotion_gates = promotion_gates or []
        self.reconciliations = reconciliations or []
        self.data_overview = data_overview or {"status": "ok", "score": 100, "freshness_alerts": []}

    def list_recent_trading_execution_records(self, *, limit=20):  # noqa: ANN001
        return self.executions[:limit]

    def list_recent_order_preview_records(self, *, limit=20):  # noqa: ANN001
        return self.previews[:limit]

    def list_recent_promotion_gate_records(self, *, limit=20):  # noqa: ANN001
        return self.promotion_gates[:limit]

    def list_recent_reconciliation_records(self, *, limit=20):  # noqa: ANN001
        return self.reconciliations[:limit]

    def get_data_health_overview(self, *, as_of):  # noqa: ANN001
        assert as_of == date(2026, 6, 26)
        return self.data_overview


def test_eod_summary_marks_clean_day_ok() -> None:
    repo = _Repo(
        executions=[
            {
                "environment": "simulation",
                "accepted": True,
                "executed": True,
                "status": "submitted",
                "created_at": "2026-06-26T13:30:00",
            }
        ],
        previews=[
            {
                "status": "accepted",
                "created_at": "2026-06-26T13:20:00",
            }
        ],
        promotion_gates=[{"accepted": True, "reason": "ok", "created_at": "2026-06-26T13:10:00"}],
        reconciliations=[{"matched": True, "blocking_reasons": [], "checked_at": "2026-06-26T15:10:00"}],
    )

    payload = EndOfDayReconciliationSummaryService().build(repo, as_of=date(2026, 6, 26))

    assert payload["status"] == "ok"
    assert payload["metrics"]["today_execution_count"] == 1
    assert payload["metrics"]["pending_preview_count"] == 0
    assert payload["metrics"]["latest_reconciliation_matched"] is True
    assert payload["blocking_reasons"] == []
    assert payload["reconciliation_runbook"]["status"] == "ok"
    assert payload["reconciliation_runbook"]["reason"] == "today_reconciliation_matched"


def test_eod_summary_blocks_mismatched_reconciliation_and_execution_error() -> None:
    repo = _Repo(
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
        promotion_gates=[{"accepted": False, "reason": "paper_days_insufficient", "created_at": "2026-06-26T13:10:00"}],
        reconciliations=[
            {
                "matched": False,
                "blocking_reasons": ["position_diff"],
                "checked_at": "2026-06-26T15:10:00",
            }
        ],
        data_overview={"status": "warn", "score": 84, "freshness_alerts": [{"key": "daily_bars"}]},
    )

    payload = EndOfDayReconciliationSummaryService().build(repo, as_of=date(2026, 6, 26))

    assert payload["status"] == "danger"
    assert "reconciliation" in payload["blocking_reasons"]
    assert "executions" in payload["blocking_reasons"]
    assert "previews" in payload["warnings"]
    assert payload["metrics"]["today_live_execution_count"] == 1
    assert payload["metrics"]["today_error_count"] == 1
    assert payload["metrics"]["pending_preview_count"] == 1
    assert payload["reconciliation_runbook"]["status"] == "action_required"
    assert payload["reconciliation_runbook"]["reason"] == "reconciliation_mismatch"
    assert "reconcile --expected-cash" in payload["reconciliation_runbook"]["command_template"]


def test_eod_summary_downgrades_background_market_gaps_when_daily_radar_is_ready() -> None:
    repo = _Repo(
        promotion_gates=[{"accepted": True, "reason": "ok", "created_at": "2026-06-26T13:10:00"}],
        reconciliations=[{"matched": True, "blocking_reasons": [], "checked_at": "2026-06-26T15:10:00"}],
        data_overview={
            "status": "danger",
            "score": 49,
            "daily_bars": {"status": "ok", "latest_date": "2026-06-25", "age_days": 1},
            "latest_radar": {
                "status": "ok",
                "run_id": "radar_20260626",
                "run_date": "2026-06-26",
                "row_count": 15,
                "entry_ready_count": 0,
            },
            "external_indices": {"status": "ok"},
            "market_context_coverage": {"status": "ok"},
            "components": [
                {"key": "universe", "status": "danger"},
                {"key": "market_data_coverage", "status": "danger"},
                {"key": "candidates", "status": "danger"},
                {"key": "disposition_periods", "status": "danger"},
            ],
            "freshness_alerts": [
                {"key": "universe"},
                {"key": "market_data_coverage"},
                {"key": "candidates"},
                {"key": "disposition_periods"},
            ],
        },
    )

    payload = EndOfDayReconciliationSummaryService().build(repo, as_of=date(2026, 6, 26))
    data_component = next(item for item in payload["components"] if item["key"] == "data_health")

    assert payload["status"] == "warn"
    assert payload["blocking_reasons"] == []
    assert payload["warnings"] == ["data_health"]
    assert data_component["status"] == "warn"
    assert "日常雷達可用" in data_component["meta"]


def test_eod_summary_surfaces_reconciliation_runbook_when_missing() -> None:
    repo = _Repo(
        previews=[
            {
                "status": "created",
                "created_at": "2026-06-26T13:20:00",
            }
        ]
    )

    payload = EndOfDayReconciliationSummaryService().build(repo, as_of=date(2026, 6, 26))

    runbook = payload["reconciliation_runbook"]
    assert runbook["status"] == "action_required"
    assert runbook["reason"] == "reconciliation_missing_after_activity"
    assert "2330:1000" in runbook["position_format"]
    assert "actual_cash" in runbook["required_inputs"][1]


def test_eod_summary_does_not_count_expired_preview_as_pending() -> None:
    repo = _Repo(
        previews=[
            {
                "preview_id": "expired-preview",
                "symbol": "2330",
                "status": "created",
                "created_at": "2026-06-26T13:20:00",
                "expires_at": "2026-06-26T13:21:00",
            }
        ]
    )

    payload = EndOfDayReconciliationSummaryService().build(repo, as_of=date(2026, 6, 26))

    assert payload["metrics"]["pending_preview_count"] == 0
    assert payload["metrics"]["expired_preview_count"] == 1
    assert payload["preview_queue"]["active_count"] == 0
    assert payload["preview_queue"]["expired_count"] == 1
    assert payload["components"][2]["status"] == "ok"


def test_eod_summary_preview_queue_marks_duplicate_and_stale_radar_preview() -> None:
    class _RadarRepo(_Repo):
        def get_latest_daily_radar(self, count=50):  # noqa: ANN001
            assert count == 50
            return [{"symbol": "2330", "action_label": "NOT_RECOMMENDED"}]

    repo = _RadarRepo(
        previews=[
            {
                "preview_id": "preview-a",
                "symbol": "2330",
                "status": "created",
                "created_at": "2026-06-26T13:20:00",
                "expires_at": "2099-06-26T13:25:00",
            },
            {
                "preview_id": "preview-b",
                "symbol": "2330",
                "status": "created",
                "created_at": "2026-06-26T13:21:00",
                "expires_at": "2099-06-26T13:25:00",
            },
        ]
    )

    payload = EndOfDayReconciliationSummaryService().build(repo, as_of=date(2026, 6, 26))

    assert payload["metrics"]["pending_preview_count"] == 2
    assert payload["preview_queue"]["active_count"] == 2
    assert {item["state"] for item in payload["preview_queue"]["items"]} == {"duplicate"}


def test_eod_summary_cli_parser_exposes_command() -> None:
    args = build_parser().parse_args(["eod-summary", "--as-of", "2026-06-26", "--limit", "50"])

    assert args.func is cmd_eod_summary
    assert args.as_of == "2026-06-26"
    assert args.limit == 50
