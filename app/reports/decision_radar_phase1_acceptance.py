from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from app.reports.daily_radar_health import DailyRadarDeploymentHealthService
from app.reports.ops_readiness import OpsReadinessService


STATUS_RANK = {"ok": 0, "warn": 1, "danger": 2}


def _status_worst(*statuses: str) -> str:
    return max((status or "warn" for status in statuses), key=lambda item: STATUS_RANK.get(str(item), 1))


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _call_latest_radar(repo: Any, *, count: int) -> list[dict[str, Any]]:
    method = getattr(repo, "get_latest_daily_radar", None)
    if not callable(method):
        return []
    try:
        rows = method(count=count)
    except TypeError:
        rows = method()
    return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _action_grade(item: dict[str, Any]) -> str:
    action = str(item.get("action_label") or "").strip().upper()
    blockers = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
    risk_label = str(item.get("risk_quality_label") or "").strip()
    risk_score = _safe_float(item.get("risk_quality_score"))
    if blockers or action == "NOT_RECOMMENDED" or risk_label in {"先降溫", "先跳過"}:
        return "不碰"
    if action == "ENTRY_READY" and risk_label in {"核心候選", "可小量觀察"} and risk_score >= 64.0:
        return "可下手"
    if action in {"ENTRY_READY", "WATCH_WAIT_TRIGGER"}:
        return "只觀察"
    return "不碰"


def _action_grade_summary(rows: list[dict[str, Any]], *, limit: int = 6) -> dict[str, Any]:
    counts = {"可下手": 0, "只觀察": 0, "不碰": 0}
    symbols = {"可下手": [], "只觀察": [], "不碰": []}
    ordered = sorted(
        rows,
        key=lambda item: (
            _safe_float(item.get("risk_quality_score")),
            _safe_float(item.get("risk_adjusted_score") or item.get("entry_score")),
            -_safe_int(item.get("rank") or 9999),
        ),
        reverse=True,
    )
    for item in ordered:
        label = _action_grade(item)
        counts[label] += 1
        symbol = str(item.get("symbol") or "").strip()
        if symbol and len(symbols[label]) < limit:
            symbols[label].append(symbol)
    return {
        "counts": counts,
        "ready_symbols": symbols["可下手"],
        "watch_symbols": symbols["只觀察"],
        "avoid_symbols": symbols["不碰"],
    }


def _check(
    *,
    key: str,
    label: str,
    passed: bool,
    severity: str,
    observed: Any,
    message: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "passed": bool(passed),
        "severity": severity,
        "observed": observed,
        "message": message,
    }


class DecisionRadarPhase1AcceptanceService:
    """Build the user-test gate for the daily decision radar phase."""

    def __init__(
        self,
        *,
        max_age_days: int = 3,
        warn_age_days: int = 1,
        min_rows: int = 5,
        min_entry_ready: int = 0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.max_age_days = max(0, int(max_age_days))
        self.warn_age_days = max(0, int(warn_age_days))
        self.min_rows = max(1, int(min_rows))
        self.min_entry_ready = max(0, int(min_entry_ready))
        self.env = env

    def build(
        self,
        repo: Any,
        *,
        as_of: date | None = None,
        ops_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_day = as_of or datetime.now().date()
        radar_health = DailyRadarDeploymentHealthService(
            max_age_days=self.max_age_days,
            warn_age_days=self.warn_age_days,
            min_rows=self.min_rows,
            min_entry_ready=self.min_entry_ready,
        ).evaluate(repo, as_of=target_day)
        ops = ops_payload if isinstance(ops_payload, dict) else OpsReadinessService(
            max_age_days=self.max_age_days,
            warn_age_days=self.warn_age_days,
            min_rows=self.min_rows,
            min_entry_ready=self.min_entry_ready,
            env=self.env,
        ).build(repo, as_of=target_day, target_phase="paper")
        rows = _call_latest_radar(repo, count=max(50, self.min_rows))
        action_grade = _action_grade_summary(rows)
        checks = self._checks(rows=rows, radar_health=radar_health, ops=ops, action_grade=action_grade)
        status = self._status_from_checks(checks)
        ready_for_user_testing = status != "danger"
        return {
            "phase": "decision_radar_phase1",
            "phase_label": "日常決策雷達驗收版",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": target_day.isoformat(),
            "status": status,
            "status_text": {"ok": "可交給使用者測試", "warn": "可測但需留意", "danger": "先修再測"}.get(status, "可測但需留意"),
            "ready_for_user_testing": ready_for_user_testing,
            "requires_user_confirmation": True,
            "completion_rule": "使用者依 docs/decision_radar_phase1_acceptance.md 實測 2 到 3 個交易日並明確回覆 Phase 1 通過後，才進入 Phase 2。",
            "test_journal": {
                "path": "docs/decision_radar_phase1_test_journal.md",
                "minimum_trading_days": 2,
                "recommended_trading_days": 3,
                "required_before_phase2": True,
            },
            "user_confirmation": {
                "required_phrase": "Phase 1 通過",
                "pass_template": "Phase 1 通過。測試日期：YYYY-MM-DD, YYYY-MM-DD。備註：...",
                "fail_template": "Phase 1 未通過。測試日期：YYYY-MM-DD。希望先修：...",
            },
            "checks": checks,
            "blocking_reasons": [item["key"] for item in checks if not item["passed"] and item["severity"] == "danger"],
            "warnings": [item["key"] for item in checks if not item["passed"] and item["severity"] != "danger"],
            "action_grade": action_grade,
            "next_steps": self._next_steps(ready_for_user_testing=ready_for_user_testing, checks=checks),
            "manual_user_tasks": [
                "連續 2 到 3 個交易日跑每日雷達並查看控制台第一屏。",
                "確認 Brief/通知能在盤前直接判斷可下手、只觀察、不碰。",
                "確認可下手標的仍需人工 Preview、價格保護與 promotion gate，不會自動正式下單。",
            ],
            "evidence": {
                "daily_radar": {
                    "status": radar_health.get("status"),
                    "latest_run_id": radar_health.get("latest_run_id"),
                    "latest_run_date": radar_health.get("latest_run_date"),
                    "row_count": radar_health.get("row_count"),
                    "entry_ready_count": radar_health.get("entry_ready_count"),
                    "watch_trigger_count": radar_health.get("watch_trigger_count"),
                },
                "ops_readiness": {
                    "status": ops.get("status"),
                    "blocking_reasons": ops.get("blocking_reasons") or [],
                    "warnings": ops.get("warnings") or [],
                },
            },
        }

    def _checks(
        self,
        *,
        rows: list[dict[str, Any]],
        radar_health: dict[str, Any],
        ops: dict[str, Any],
        action_grade: dict[str, Any],
    ) -> list[dict[str, Any]]:
        row_count = len(rows)
        counts = action_grade.get("counts") if isinstance(action_grade.get("counts"), dict) else {}
        risk_quality_count = sum(1 for row in rows if str(row.get("risk_quality_label") or "").strip())
        action_count_total = sum(_safe_int(value) for value in counts.values())
        return [
            _check(
                key="daily_radar_available",
                label="每日雷達可用",
                passed=str(radar_health.get("status")) != "danger",
                severity="danger",
                observed=radar_health.get("status"),
                message="最新每日雷達不能是 danger，否則 Phase 1 無法交給使用者測。",
            ),
            _check(
                key="candidate_rows_enough",
                label="候選列足夠",
                passed=row_count >= self.min_rows,
                severity="danger",
                observed=row_count,
                message=f"至少需要 {self.min_rows} 檔候選，才足夠驗收日常決策介面。",
            ),
            _check(
                key="risk_quality_present",
                label="風險品質已寫入",
                passed=row_count > 0 and risk_quality_count == row_count,
                severity="danger",
                observed=f"{risk_quality_count}/{row_count}",
                message="每檔候選都要有風險品質層，才能支撐可下手/只觀察/不碰分級。",
            ),
            _check(
                key="action_grade_complete",
                label="三類行動分級可計算",
                passed=row_count > 0 and action_count_total == row_count and set(counts) == {"可下手", "只觀察", "不碰"},
                severity="danger",
                observed=counts,
                message="Phase 1 必須能把最新候選完整分成可下手、只觀察、不碰。",
            ),
            _check(
                key="ops_not_danger",
                label="營運總檢非 danger",
                passed=str(ops.get("status")) != "danger",
                severity="danger",
                observed=ops.get("status"),
                message="營運總檢不能有未處理 danger，否則先修環境或資料再測。",
            ),
            _check(
                key="live_orders_disabled",
                label="正式下單未開啟",
                passed=not bool((ops.get("metrics") or {}).get("live_orders_enabled")),
                severity="danger",
                observed=(ops.get("metrics") or {}).get("live_orders_enabled"),
                message="Phase 1 是觀察與 Preview 驗收，不應開啟正式下單。",
            ),
            _check(
                key="manual_confirmation_required",
                label="保留人工確認",
                passed=True,
                severity="warn",
                observed="required",
                message="可下手只代表優先人工檢查，不代表自動下單。",
            ),
        ]

    @staticmethod
    def _status_from_checks(checks: list[dict[str, Any]]) -> str:
        failed = [item for item in checks if item.get("passed") is False]
        if any(str(item.get("severity")) == "danger" for item in failed):
            return "danger"
        if failed:
            return "warn"
        return "ok"

    @staticmethod
    def _next_steps(*, ready_for_user_testing: bool, checks: list[dict[str, Any]]) -> list[str]:
        failed = [item for item in checks if item.get("passed") is False]
        if not ready_for_user_testing:
            return [str(item.get("message")) for item in failed[:3]]
        return [
            "依 docs/decision_radar_phase1_acceptance.md 跑 2 到 3 個交易日。",
            "測完後回報 Phase 1 通過或列出不順的項目，再決定是否進入 Phase 2。",
        ]


def format_decision_radar_phase1_acceptance_message(payload: dict[str, Any]) -> str:
    status = str(payload.get("status_text") or payload.get("status") or "未知")
    grade = payload.get("action_grade") if isinstance(payload.get("action_grade"), dict) else {}
    counts = grade.get("counts") if isinstance(grade.get("counts"), dict) else {}
    lines = [
        f"[Phase 1] {status}",
        f"日期: {payload.get('as_of') or '-'}",
        "分級: "
        + (
            " / ".join(f"{label} {count}" for label, count in counts.items())
            if counts
            else "-"
        ),
    ]
    if payload.get("blocking_reasons"):
        lines.append(f"阻擋: {', '.join(str(item) for item in payload['blocking_reasons'])}")
    if payload.get("warnings"):
        lines.append(f"提醒: {', '.join(str(item) for item in payload['warnings'])}")
    next_steps = payload.get("next_steps") if isinstance(payload.get("next_steps"), list) else []
    if next_steps:
        lines.append(f"下一步: {next_steps[0]}")
    test_journal = payload.get("test_journal") if isinstance(payload.get("test_journal"), dict) else {}
    if test_journal.get("path"):
        lines.append(f"測試紀錄: {test_journal['path']}")
    confirmation = payload.get("user_confirmation") if isinstance(payload.get("user_confirmation"), dict) else {}
    if confirmation.get("required_phrase"):
        lines.append(f"通過回覆: {confirmation['required_phrase']}")
    return "\n".join(lines)
