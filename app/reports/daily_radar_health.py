from __future__ import annotations

from datetime import date, datetime
from typing import Any


STATUS_RANK = {"ok": 0, "warn": 1, "danger": 2}
WAIT_CATEGORY_PRIORITY = {
    "risk_cooling": 0,
    "blocker_clear": 1,
    "data_refresh": 2,
    "price_data": 3,
    "entry_score": 4,
    "price_pullback": 5,
    "price_breakout": 6,
    "review_trigger": 7,
}
BENIGN_PARTIAL_MARKET_GAP_REASONS = {"upstream_no_data", "upstream_not_found"}
BENIGN_PARTIAL_MARKET_GAP_SCOPES = {"daily_bars", "institutional_chip", "broker_agg_chip"}


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _radar_sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    rank = _safe_int(row.get("rank"), 9999)
    score = _safe_float(row.get("risk_adjusted_score") or row.get("entry_score"), 0.0)
    return (rank if rank > 0 else 9999, -score, str(row.get("symbol") or ""))


def _radar_action_counts(rows: list[dict[str, Any]], *, row_count: int, entry_ready_count: int) -> dict[str, int]:
    counts = {"ENTRY_READY": 0, "WATCH_WAIT_TRIGGER": 0, "NOT_RECOMMENDED": 0}
    for row in rows:
        action = str(row.get("action_label") or "").strip().upper()
        if action not in counts:
            action = "NOT_RECOMMENDED"
        counts[action] += 1
    if counts["ENTRY_READY"] <= 0 and entry_ready_count > 0:
        counts["ENTRY_READY"] = entry_ready_count
    counted = sum(counts.values())
    if row_count > counted:
        counts["NOT_RECOMMENDED"] += row_count - counted
    return counts


def _trigger_plan_for_row(row: dict[str, Any]) -> dict[str, Any]:
    existing = row.get("trigger_plan") if isinstance(row.get("trigger_plan"), dict) else {}
    if existing:
        return existing
    try:
        from app.db.repository import TradingRepository

        plan = TradingRepository._daily_radar_trigger_plan(row)
        return plan if isinstance(plan, dict) else {}
    except Exception:
        return {}


def _trigger_wait_categories(row: dict[str, Any], trigger_plan: dict[str, Any] | None = None) -> list[str]:
    plan = trigger_plan if isinstance(trigger_plan, dict) else _trigger_plan_for_row(row)
    rules = plan.get("rules") if isinstance(plan.get("rules"), list) else []
    checks = plan.get("checks") if isinstance(plan.get("checks"), list) else []
    text = " ".join(str(item) for item in [plan.get("summary"), *rules, *checks] if item)
    categories: list[str] = []
    if "Entry Score" in text or "Entry" in text:
        categories.append("entry_score")
    if "價格回落" in text or "回測" in text:
        categories.append("price_pullback")
    if "突破" in text or "站回" in text:
        categories.append("price_breakout")
    if "ATR 熱度" in text or "風險分數下降" in text or "不再升高" in text:
        categories.append("risk_cooling")
    if "freshness" in text or "同步" in text:
        categories.append("data_refresh")
    if "阻擋解除" in text or "阻擋標籤" in text:
        categories.append("blocker_clear")
    if "補齊最新價格" in text or "最新價格" in text:
        categories.append("price_data")
    return list(dict.fromkeys(categories or ["review_trigger"]))


def _wait_category_label(category: str) -> str:
    return {
        "entry_score": "等 Entry Score 重新整理轉強",
        "price_pullback": "等價格回落",
        "price_breakout": "等突破或站回",
        "risk_cooling": "先等風險降溫",
        "data_refresh": "先補資料 freshness",
        "blocker_clear": "先處理阻擋標籤",
        "price_data": "先補最新價格",
        "review_trigger": "檢查觸發條件",
    }.get(category, category)


def _primary_wait_category(reason_counts: dict[str, int]) -> str:
    if not reason_counts:
        return "review_trigger"
    return sorted(reason_counts, key=lambda key: (-reason_counts[key], WAIT_CATEGORY_PRIORITY.get(key, 99), key))[0]


def _watch_trigger_candidates(rows: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    watch_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("action_label") or "").strip().upper() == "WATCH_WAIT_TRIGGER"
    ]
    output: list[dict[str, Any]] = []
    for row in sorted(watch_rows, key=_radar_sort_key)[: max(0, int(limit))]:
        trigger_plan = _trigger_plan_for_row(row)
        blocker_tags = row.get("blocker_tags") if isinstance(row.get("blocker_tags"), list) else []
        wait_categories = _trigger_wait_categories(row, trigger_plan)
        primary_wait = _primary_wait_category({category: 1 for category in wait_categories})
        output.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "name": str(row.get("name") or ""),
                "rank": _safe_int(row.get("rank"), 0),
                "entry_score": round(_safe_float(row.get("entry_score"), 0.0), 2),
                "risk_score": round(_safe_float(row.get("risk_score"), 0.0), 4),
                "risk_adjusted_score": round(_safe_float(row.get("risk_adjusted_score"), 0.0), 2),
                "price": round(_safe_float(row.get("price"), 0.0), 2),
                "industry_group": str(row.get("industry_group") or ""),
                "data_quality": str(row.get("data_quality") or ""),
                "atr_heat": str(row.get("atr_heat") or ""),
                "blocker_tags": [str(item) for item in blocker_tags[:5]],
                "wait_categories": wait_categories,
                "primary_wait": primary_wait,
                "primary_wait_label": _wait_category_label(primary_wait),
                "trigger_summary": str(trigger_plan.get("summary") or ""),
                "trigger_plan": trigger_plan,
            }
        )
    return output


def _watch_trigger_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    watch_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("action_label") or "").strip().upper() == "WATCH_WAIT_TRIGGER"
    ]
    reason_counts: dict[str, int] = {}
    heat_counts: dict[str, int] = {}
    data_quality_counts: dict[str, int] = {}
    for row in watch_rows:
        trigger_plan = _trigger_plan_for_row(row)
        for category in _trigger_wait_categories(row, trigger_plan):
            reason_counts[category] = reason_counts.get(category, 0) + 1
        heat = str(row.get("atr_heat") or "unknown").strip().lower() or "unknown"
        heat_counts[heat] = heat_counts.get(heat, 0) + 1
        quality = str(row.get("data_quality") or "unknown").strip().lower() or "unknown"
        data_quality_counts[quality] = data_quality_counts.get(quality, 0) + 1

    primary = _primary_wait_category(reason_counts)
    reason_labels = [
        {"key": key, "label": _wait_category_label(key), "count": int(reason_counts[key])}
        for key in sorted(reason_counts, key=lambda item: (-reason_counts[item], WAIT_CATEGORY_PRIORITY.get(item, 99), item))
    ]
    return {
        "count": len(watch_rows),
        "primary_wait": primary,
        "primary_wait_label": _wait_category_label(primary),
        "reason_counts": reason_counts,
        "reason_labels": reason_labels,
        "atr_heat_counts": heat_counts,
        "data_quality_counts": data_quality_counts,
    }


def _entry_ready_diagnosis(
    *,
    row_count: int,
    entry_ready_count: int,
    min_entry_ready: int,
    watch_trigger_count: int,
    not_recommended_count: int,
    watch_trigger_summary: dict[str, Any],
    sync_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    if entry_ready_count >= min_entry_ready:
        return {
            "status": "ok",
            "reason": "entry_ready_available",
            "title": "已有可進場標的",
            "summary": f"目前有 {entry_ready_count} 檔 ENTRY_READY，可進入人工 Preview 檢查。",
            "primary_action": "逐檔檢查價格、部位與交易 gate，再決定是否建立 Preview。",
            "evidence": [
                {"label": "可進場", "value": entry_ready_count},
                {"label": "候選", "value": row_count},
            ],
        }
    if row_count <= 0:
        return {
            "status": "danger",
            "reason": "no_radar_rows",
            "title": "尚無雷達候選",
            "summary": "最新每日雷達沒有候選列，通常代表資料同步、標的池或排程需要先檢查。",
            "primary_action": "先重跑每日雷達，並檢查 market data 與 universe 設定。",
            "evidence": [{"label": "候選", "value": row_count}],
        }
    issue_codes = {
        str(code)
        for issue in sync_issues
        for code in (issue.get("reason_codes") if isinstance(issue.get("reason_codes"), list) else [])
        if str(code or "").strip()
    }
    if issue_codes:
        labels = [_sync_issue_label(code) for code in sorted(issue_codes)[:3]]
        return {
            "status": "warn",
            "reason": "data_issue",
            "title": "先確認資料源",
            "summary": f"目前沒有可進場標的，且同步摘要仍有資料源提醒：{'、'.join(labels)}。",
            "primary_action": "先處理資料源或重試同步，再重新整理每日雷達。",
            "evidence": [
                {"label": "同步提醒", "value": len(sync_issues)},
                {"label": "候選", "value": row_count},
            ],
        }
    primary_wait = str(watch_trigger_summary.get("primary_wait") or "")
    primary_wait_label = str(watch_trigger_summary.get("primary_wait_label") or "")
    heat_counts = watch_trigger_summary.get("atr_heat_counts") if isinstance(watch_trigger_summary.get("atr_heat_counts"), dict) else {}
    hot_count = _safe_int(heat_counts.get("hot"), 0) + _safe_int(heat_counts.get("elevated"), 0)
    if watch_trigger_count > 0:
        reason = "trigger_not_ready"
        title = "先等觸發條件成熟"
        action = "先把待觸發標的放進觀察線，等條件成熟後再重新評估 Preview。"
        if primary_wait == "risk_cooling" or (hot_count > 0 and hot_count >= watch_trigger_count):
            reason = "risk_cooling"
            title = "風險尚未降溫"
            action = "先看觸發條件與 ATR/風險分數，不急著建立 Preview。"
        elif primary_wait == "entry_score":
            reason = "score_not_ready"
            title = "分數還沒站上門檻"
            action = "等 Entry Score 重新整理轉強，或用研究模式檢查門檻是否過嚴。"
        elif primary_wait == "data_refresh":
            reason = "data_refresh"
            title = "資料 freshness 需要補齊"
            action = "先重試核心市場資料同步，再重新整理雷達。"
        elif primary_wait == "blocker_clear":
            reason = "blocker_clear"
            title = "阻擋標籤尚未解除"
            action = "先處理注意股、處置股、流動性或交易回饋阻擋。"
        wait_text = primary_wait_label or _wait_category_label(primary_wait)
        return {
            "status": "warn",
            "reason": reason,
            "title": title,
            "summary": f"目前沒有 ENTRY_READY；有 {watch_trigger_count} 檔待觸發觀察，主因是「{wait_text}」。",
            "primary_action": action,
            "evidence": [
                {"label": "待觸發", "value": watch_trigger_count},
                {"label": "過熱/偏高", "value": hot_count},
                {"label": "不建議", "value": not_recommended_count},
            ],
        }
    return {
        "status": "warn",
        "reason": "filter_or_market_weak",
        "title": "候選多數未達進場條件",
        "summary": f"目前 {row_count} 檔候選沒有可進場或待觸發標的，可能是市場條件偏弱、風險檔位偏保守或 universe 過窄。",
        "primary_action": "先查看阻擋分布；研究時可用 aggressive 風險檔或擴大 universe 做對照，不要直接放寬正式 gate。",
        "evidence": [
            {"label": "候選", "value": row_count},
            {"label": "不建議", "value": not_recommended_count},
        ],
    }


def _sync_issue_label(code: str) -> str:
    return {
        "upstream_forbidden": "上游資料權限不足或 token 未生效",
        "upstream_unauthorized": "上游資料未授權",
        "upstream_bad_request": "上游資料拒收請求或參數不支援",
        "upstream_rate_limited": "上游資料達到呼叫限制",
        "upstream_not_found": "上游資料集或代號不存在",
        "upstream_timeout": "上游資料逾時",
        "shioaji_notice_unavailable": "Shioaji 注意股 API 不可用",
        "sync_partial_failure": "資料同步部分完成",
    }.get(str(code or ""), str(code or "upstream_error"))


def _is_benign_partial_market_gap(row: dict[str, Any], summary: dict[str, Any]) -> bool:
    if bool(row.get("benign_partial_market_gap")):
        return True
    if str(row.get("status") or "").lower() != "partial" or _safe_int(row.get("rows_upserted"), 0) <= 0:
        return False
    reason_codes = {
        str(code or "").strip()
        for code in (summary.get("reason_codes") if isinstance(summary.get("reason_codes"), list) else [])
        if str(code or "").strip()
    }
    if not reason_codes or not reason_codes.issubset(BENIGN_PARTIAL_MARKET_GAP_REASONS):
        return False
    symbol_failures = summary.get("symbol_failures") if isinstance(summary.get("symbol_failures"), list) else []
    if not symbol_failures:
        return False
    for item in symbol_failures:
        if not isinstance(item, dict):
            return False
        scopes = {str(scope or "").strip() for scope in item.get("scopes", []) if str(scope or "").strip()}
        if not scopes or not scopes.issubset(BENIGN_PARTIAL_MARKET_GAP_SCOPES):
            return False
    return True


def _status_from_checks(checks: list[dict[str, Any]]) -> str:
    failed = [item for item in checks if item.get("passed") is False]
    if any(str(item.get("severity")) == "danger" for item in failed):
        return "danger"
    if failed:
        return "warn"
    return "ok"


def daily_radar_health_should_notify(payload: dict[str, Any], notify_on: str = "danger") -> bool:
    mode = str(notify_on or "danger").strip().lower()
    status = str(payload.get("status") or "").strip().lower()
    if mode in {"none", "never", "off"}:
        return False
    if mode == "always":
        return True
    if mode == "warn":
        return STATUS_RANK.get(status, 0) >= STATUS_RANK["warn"]
    return STATUS_RANK.get(status, 0) >= STATUS_RANK["danger"]


def format_daily_radar_health_message(payload: dict[str, Any]) -> str:
    status = str(payload.get("status_text") or payload.get("status") or "未知")
    run_date = payload.get("latest_run_date") or "尚無 run"
    age_days = payload.get("age_days")
    age_text = "-" if age_days is None else f"{age_days} 天"
    row_count = _safe_int(payload.get("row_count"), 0)
    entry_ready_count = _safe_int(payload.get("entry_ready_count"), 0)
    watch_trigger_count = _safe_int(payload.get("watch_trigger_count"), 0)
    trigger_candidates = payload.get("watch_trigger_candidates") if isinstance(payload.get("watch_trigger_candidates"), list) else []
    trigger_summary = payload.get("watch_trigger_summary") if isinstance(payload.get("watch_trigger_summary"), dict) else {}
    primary_wait_label = str(trigger_summary.get("primary_wait_label") or "")
    blockers = [str(item) for item in payload.get("blocking_reasons") or [] if item]
    warnings = [str(item) for item in payload.get("warnings") or [] if item]
    next_steps = [str(item) for item in payload.get("next_steps") or [] if item]
    lines = [
        f"[每日雷達部署] {status}",
        f"最新 run: {run_date}（距今 {age_text}）",
        f"候選: {row_count} 檔，可進場: {entry_ready_count} 檔，待觸發: {watch_trigger_count} 檔",
    ]
    if trigger_candidates:
        symbols = [str(item.get("symbol") or "") for item in trigger_candidates[:3] if isinstance(item, dict) and item.get("symbol")]
        if symbols:
            lines.append(f"待觸發觀察: {', '.join(symbols)}")
    if primary_wait_label and watch_trigger_count > 0:
        lines.append(f"主要等待: {primary_wait_label}")
    diagnosis = payload.get("entry_ready_diagnosis") if isinstance(payload.get("entry_ready_diagnosis"), dict) else {}
    if diagnosis and entry_ready_count <= 0:
        title = str(diagnosis.get("title") or "")
        summary = str(diagnosis.get("summary") or "")
        if title or summary:
            lines.append(f"無可進場原因: {title}{' · ' if title and summary else ''}{summary}")
    if blockers:
        lines.append(f"阻擋: {', '.join(blockers[:4])}")
    if warnings:
        lines.append(f"提醒: {', '.join(warnings[:4])}")
    if next_steps:
        lines.append(f"下一步: {next_steps[0]}")
    return "\n".join(lines)


def _call_data_health_overview(repo: Any, *, as_of: date, freshness_days: int) -> dict[str, Any]:
    method = getattr(repo, "get_data_health_overview", None)
    if not callable(method):
        return {}
    try:
        payload = method(as_of=as_of, freshness_days=freshness_days)
    except TypeError:
        payload = method()
    return payload if isinstance(payload, dict) else {}


def _call_latest_radar(repo: Any, *, count: int) -> list[dict[str, Any]]:
    method = getattr(repo, "get_latest_daily_radar", None)
    if not callable(method):
        return []
    try:
        rows = method(count=count)
    except TypeError:
        rows = method()
    return rows if isinstance(rows, list) else []


class DailyRadarDeploymentHealthService:
    def __init__(
        self,
        *,
        max_age_days: int = 3,
        warn_age_days: int = 1,
        min_rows: int = 5,
        min_entry_ready: int = 1,
    ) -> None:
        self.max_age_days = max(0, int(max_age_days))
        self.warn_age_days = max(0, int(warn_age_days))
        self.min_rows = max(1, int(min_rows))
        self.min_entry_ready = max(0, int(min_entry_ready))

    def evaluate(self, repo: Any, *, as_of: date | None = None) -> dict[str, Any]:
        today = as_of or datetime.now().date()
        overview = _call_data_health_overview(repo, as_of=today, freshness_days=max(1, self.max_age_days))
        latest_radar = overview.get("latest_radar") if isinstance(overview.get("latest_radar"), dict) else {}
        radar_rows = _call_latest_radar(repo, count=max(50, self.min_rows))

        run_id = str(latest_radar.get("run_id") or (radar_rows[0].get("run_id") if radar_rows else "") or "")
        run_date = _parse_date(latest_radar.get("run_date") or (radar_rows[0].get("run_date") if radar_rows else None))
        row_count = _safe_int(latest_radar.get("row_count"), len(radar_rows))
        if row_count <= 0 and radar_rows:
            row_count = len(radar_rows)
        entry_ready_count = _safe_int(
            latest_radar.get("entry_ready_count"),
            sum(1 for row in radar_rows if str(row.get("action_label") or "") == "ENTRY_READY"),
        )
        action_counts = _radar_action_counts(radar_rows, row_count=row_count, entry_ready_count=entry_ready_count)
        watch_trigger_count = action_counts.get("WATCH_WAIT_TRIGGER", 0)
        not_recommended_count = action_counts.get("NOT_RECOMMENDED", 0)
        watch_trigger_candidates = _watch_trigger_candidates(radar_rows, limit=3)
        watch_trigger_summary = _watch_trigger_summary(radar_rows)
        age_days = _safe_int(latest_radar.get("age_days"), -1)
        if age_days < 0:
            age_days = max(0, (today - run_date).days) if run_date else None

        latest_job = self._latest_daily_radar_job(overview)
        sync_issues = self._sync_issues(overview)
        entry_ready_diagnosis = _entry_ready_diagnosis(
            row_count=row_count,
            entry_ready_count=entry_ready_count,
            min_entry_ready=self.min_entry_ready,
            watch_trigger_count=watch_trigger_count,
            not_recommended_count=not_recommended_count,
            watch_trigger_summary=watch_trigger_summary,
            sync_issues=sync_issues,
        )
        checks = self._build_checks(
            run_id=run_id,
            run_date=run_date,
            row_count=row_count,
            entry_ready_count=entry_ready_count,
            watch_trigger_count=watch_trigger_count,
            age_days=age_days,
            latest_job=latest_job,
            sync_issues=sync_issues,
        )
        status = _status_from_checks(checks)
        failed = [item for item in checks if item.get("passed") is False]
        blocking_reasons = [str(item.get("name")) for item in failed if str(item.get("severity")) == "danger"]
        warnings = [str(item.get("name")) for item in failed if str(item.get("severity")) != "danger"]

        return {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": today.isoformat(),
            "status": status,
            "status_text": {"ok": "正常", "warn": "需留意", "danger": "需處理"}.get(status, "需留意"),
            "latest_run_id": run_id,
            "latest_run_date": run_date.isoformat() if run_date else None,
            "age_days": age_days,
            "row_count": row_count,
            "entry_ready_count": entry_ready_count,
            "watch_trigger_count": watch_trigger_count,
            "not_recommended_count": not_recommended_count,
            "action_counts": action_counts,
            "watch_trigger_candidates": watch_trigger_candidates,
            "watch_trigger_summary": watch_trigger_summary,
            "entry_ready_diagnosis": entry_ready_diagnosis,
            "min_rows": self.min_rows,
            "min_entry_ready": self.min_entry_ready,
            "max_age_days": self.max_age_days,
            "warn_age_days": self.warn_age_days,
            "latest_sync_job": latest_job,
            "sync_issues": sync_issues,
            "checks": checks,
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "next_steps": self._next_steps(
                failed,
                sync_issues=sync_issues,
                watch_trigger_count=watch_trigger_count,
                watch_trigger_summary=watch_trigger_summary,
            ),
        }

    def _latest_daily_radar_job(self, overview: dict[str, Any]) -> dict[str, Any] | None:
        rows = overview.get("sync_jobs")
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and str(row.get("job_name") or "") == "daily_radar":
                return dict(row)
        return None

    def _sync_issues(self, overview: dict[str, Any]) -> list[dict[str, Any]]:
        rows = overview.get("sync_jobs")
        if not isinstance(rows, list):
            return []
        issues: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "").lower()
            health_status = str(row.get("health_status") or "").lower()
            summary = row.get("failure_summary") if isinstance(row.get("failure_summary"), dict) else {}
            if _is_benign_partial_market_gap(row, summary):
                continue
            if status not in {"partial", "failed", "error"} and health_status not in {"warn", "danger"} and not summary:
                continue
            reason_codes = [str(code) for code in summary.get("reason_codes", []) if code] if isinstance(summary, dict) else []
            components = summary.get("components") if isinstance(summary.get("components"), list) else []
            samples = summary.get("samples") if isinstance(summary.get("samples"), list) else []
            scope_failures = summary.get("scope_failures") if isinstance(summary.get("scope_failures"), list) else []
            symbol_failures = summary.get("symbol_failures") if isinstance(summary.get("symbol_failures"), list) else []
            issues.append(
                {
                    "job_name": str(row.get("job_name") or ""),
                    "status": status or health_status or "unknown",
                    "run_at": row.get("run_at"),
                    "rows_upserted": _safe_int(row.get("rows_upserted"), 0),
                    "failed_count": _safe_int(summary.get("failed_count"), 0) if summary else 0,
                    "reason_codes": reason_codes,
                    "reason_labels": [_sync_issue_label(code) for code in reason_codes],
                    "components": components[:8],
                    "samples": samples[:3],
                    "scope_failures": scope_failures[:6],
                    "symbol_failures": symbol_failures[:6],
                }
            )
        return issues[:10]

    def _build_checks(
        self,
        *,
        run_id: str,
        run_date: date | None,
        row_count: int,
        entry_ready_count: int,
        watch_trigger_count: int,
        age_days: int | None,
        latest_job: dict[str, Any] | None,
        sync_issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = [
            {
                "name": "daily_radar_run_exists",
                "label": "已有每日雷達 run",
                "passed": bool(run_id and row_count > 0),
                "severity": "danger",
                "observed": run_id or "no_run",
                "message": "找得到最新每日雷達快照。",
            },
            {
                "name": "daily_radar_max_age",
                "label": "雷達資料未過期",
                "passed": bool(run_date and age_days is not None and age_days <= self.max_age_days),
                "severity": "danger",
                "observed": age_days,
                "expected": f"<= {self.max_age_days} 天",
                "message": "部署後應持續產出近期每日雷達。",
            },
            {
                "name": "daily_radar_recent_enough",
                "label": "雷達足夠新",
                "passed": bool(run_date and age_days is not None and age_days <= self.warn_age_days),
                "severity": "warn",
                "observed": age_days,
                "expected": f"<= {self.warn_age_days} 天",
                "message": "超過警示天數但未過期時，先確認是否遇到假日或休市。",
            },
            {
                "name": "daily_radar_row_count",
                "label": "候選數量足夠",
                "passed": row_count >= self.min_rows,
                "severity": "danger",
                "observed": row_count,
                "expected": f">= {self.min_rows}",
                "message": "候選太少通常代表資料同步、標的池或篩選條件需要檢查。",
            },
            {
                "name": "daily_radar_entry_ready",
                "label": "有可優先觀察標的",
                "passed": entry_ready_count >= self.min_entry_ready,
                "severity": "warn",
                "observed": entry_ready_count,
                "expected": f">= {self.min_entry_ready}",
                "message": "沒有可進場標的不一定錯，但需要知道今天是市場條件問題還是資料問題。",
            },
            {
                "name": "daily_radar_sync_job_recorded",
                "label": "有排程執行紀錄",
                "passed": latest_job is not None,
                "severity": "warn",
                "observed": (latest_job or {}).get("run_at") if latest_job else "no_job",
                "message": "同步紀錄可用來分辨漏跑、失敗或只有舊快照。",
            },
        ]
        if entry_ready_count < self.min_entry_ready:
            checks.append(
                {
                    "name": "daily_radar_watch_trigger_available",
                    "label": "有待觸發觀察標的",
                    "passed": watch_trigger_count > 0,
                    "severity": "warn",
                    "observed": watch_trigger_count,
                    "expected": "> 0",
                    "message": "沒有可進場標的時，至少應知道是否有觀察中候選可設定觸發條件。",
                }
            )
        if latest_job is not None:
            raw_status = str(latest_job.get("status") or "").lower()
            health_status = str(latest_job.get("health_status") or raw_status or "").lower()
            failure_summary = (
                latest_job.get("failure_summary") if isinstance(latest_job.get("failure_summary"), dict) else {}
            )
            benign_partial = _is_benign_partial_market_gap(latest_job, failure_summary)
            if (raw_status == "partial" and not benign_partial) or health_status == "warn":
                checks.append(
                    {
                        "name": "daily_radar_sync_job_partial",
                        "label": "排程部分完成",
                        "passed": False,
                        "severity": "warn",
                        "observed": raw_status or health_status,
                        "details": {"sync_issues": sync_issues[:5]},
                        "message": "每日雷達有產出，但同步工作回報部分完成。",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "daily_radar_sync_job_ok",
                        "label": "排程狀態正常",
                        "passed": (raw_status == "ok" or benign_partial) and health_status in {"ok", ""},
                        "severity": "danger",
                        "observed": raw_status or health_status or "unknown",
                        "expected": "ok",
                        "message": "最新 daily_radar sync job 應為 ok。",
                    }
                )
        return checks

    def _next_steps(
        self,
        failed_checks: list[dict[str, Any]],
        *,
        sync_issues: list[dict[str, Any]] | None = None,
        watch_trigger_count: int = 0,
        watch_trigger_summary: dict[str, Any] | None = None,
    ) -> list[str]:
        names = {str(item.get("name")) for item in failed_checks}
        issue_codes = {
            str(code)
            for issue in (sync_issues or [])
            for code in (issue.get("reason_codes") if isinstance(issue.get("reason_codes"), list) else [])
        }
        issue_jobs = [str(issue.get("job_name") or "") for issue in (sync_issues or []) if str(issue.get("job_name") or "")]
        steps: list[str] = []
        if "daily_radar_run_exists" in names:
            steps.append("先手動執行 `python -m app.cli run-daily-radar-job --universe curated_watchlist` 確認流程能產生快照。")
        if "daily_radar_max_age" in names or "daily_radar_recent_enough" in names:
            steps.append("檢查 launchd/systemd/Docker 排程日誌，確認最近交易日是否有執行每日雷達。")
        if "daily_radar_row_count" in names:
            steps.append("重新同步市場資料並檢查 universe 設定；必要時改用 dynamic_balanced 或擴大觀察池。")
        if "daily_radar_entry_ready" in names:
            if watch_trigger_count > 0:
                primary_label = ""
                if isinstance(watch_trigger_summary, dict):
                    primary_label = str(watch_trigger_summary.get("primary_wait_label") or "")
                suffix = f"，主要等待條件是「{primary_label}」" if primary_label else ""
                steps.append(f"目前有 {watch_trigger_count} 檔待觸發觀察標的{suffix}；先看觸發條件與阻擋標籤，不急著建立 Preview。")
            else:
                steps.append("查看阻擋原因與資料 freshness，分辨是市場條件不足或資料缺漏造成沒有可進場標的。")
        if "daily_radar_watch_trigger_available" in names:
            steps.append("若可進場與觀察中都掛零，先放寬風險檔位、擴大 universe 或重跑資料同步，確認不是篩選過窄。")
        if "daily_radar_sync_job_recorded" in names:
            steps.append("確認每日雷達流程有寫入 sync job 紀錄，方便後續監控與告警判讀。")
        if "daily_radar_sync_job_ok" in names or "daily_radar_sync_job_partial" in names:
            if "upstream_forbidden" in issue_codes or "upstream_unauthorized" in issue_codes:
                steps.append("檢查 FinMind token、資料集權限與 API 額度；目前上游回報權限不足，日線/籌碼/基本面可能不完整。")
            if "upstream_rate_limited" in issue_codes:
                steps.append("降低同步範圍或延後重試 FinMind，避免達到上游呼叫限制。")
            if "shioaji_notice_unavailable" in issue_codes:
                steps.append("確認 Shioaji 版本與 `api.notice()` 可用性；注意股資料不可用時，正式 Preview 前要保守處理。")
            if issue_jobs:
                steps.append(f"優先查看 sync job：{', '.join(issue_jobs[:5])}。")
            else:
                steps.append("打開最新 daily_radar sync job 的錯誤訊息或容器日誌，先處理失敗/部分完成原因。")
        return steps or ["維持現有排程，下一步可接 Telegram/LINE 告警。"]
