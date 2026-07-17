from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from app.db.repository import TradingRepository
from app.reports.daily_decision_summary import (
    action_queue_lanes_for_item,
    build_action_queue,
    build_diversified_shortlist,
    build_investment_actions,
    export_daily_decision_summary_report,
    investment_action_label_for_item,
)
from app.reports.daily_radar_threshold_sweep import build_daily_radar_threshold_sweep
from app.security.reporting import csv_join_pipe, csv_join_row, sanitize_report_payload
from app.services.daily_radar_risk_quality import enrich_risk_quality


def _quality_summary_for_export(items: list[dict[str, Any]], data_health_overview: dict[str, Any] | None = None) -> dict[str, Any]:
    quality_fn = getattr(TradingRepository, "_daily_radar_quality_summary", None)
    if callable(quality_fn):
        try:
            summary = quality_fn(items, data_health_overview=data_health_overview)
            if isinstance(summary, dict):
                return summary
        except Exception:
            pass

    rows = [item for item in items if isinstance(item, dict)]
    if not rows:
        return {
            "status": "empty",
            "reason": "no_daily_radar_items",
            "metrics": {},
            "top_picks": [],
            "top_pick_scope": "none",
            "top_pick_scope_label": "等待候選",
            "alerts": [],
        }

    def num(value: Any) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return 0.0

    def top_pick_score(item: dict[str, Any]) -> float:
        risk_adjusted = num(item.get("risk_adjusted_score"))
        if risk_adjusted > 0:
            return risk_adjusted
        return num(item.get("entry_score")) - (min(1.0, num(item.get("risk_score"))) * 20.0)

    def quality_good(value: object) -> bool:
        return str(value or "").strip().lower() in {"fresh", "complete", "ok", "good"}

    def actionable_top_pick(item: dict[str, Any]) -> bool:
        action = str(item.get("action_label") or "")
        blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
        return action in {"ENTRY_READY", "WATCH_WAIT_TRIGGER"} and not blocker_tags and quality_good(item.get("data_quality"))

    count = len(rows)
    entry_count = sum(1 for item in rows if str(item.get("action_label") or "") == "ENTRY_READY")
    blocker_count = sum(len(item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []) for item in rows)
    no_blocker_count = sum(
        1 for item in rows if not (isinstance(item.get("blocker_tags"), list) and len(item.get("blocker_tags") or []) > 0)
    )
    average_entry = sum(num(item.get("entry_score")) for item in rows) / count
    average_risk = sum(num(item.get("risk_score")) for item in rows) / count
    quality_score = max(
        0,
        min(
            100,
            int(
                round(
                    (entry_count / count * 40.0)
                    + (no_blocker_count / count * 30.0)
                    + (average_entry * 0.25)
                    + ((1.0 - min(1.0, average_risk)) * 10.0)
                )
            ),
        ),
    )
    if quality_score >= 70 and entry_count > 0:
        verdict = "可優先研究"
        risk_level = "ok"
        primary_action = "先檢查高分、無阻擋的標的。"
    elif quality_score >= 50:
        verdict = "可研究，先分散"
        risk_level = "watch"
        primary_action = "先看無阻擋標的，再決定是否建立 Preview。"
    else:
        verdict = "先處理風險"
        risk_level = "warn"
        primary_action = "先處理資料品質與阻擋，再研究個股。"
    sorted_rows = sorted(
        rows,
        key=lambda item: (top_pick_score(item), num(item.get("entry_score")), -num(item.get("risk_score"))),
        reverse=True,
    )
    top_pick_rows = [item for item in sorted_rows if actionable_top_pick(item)]
    top_picks = [
        {
            "symbol": str(item.get("symbol") or ""),
            "name": str(item.get("name") or ""),
            "industry_group": str(item.get("industry_group") or "其他"),
            "entry_score": num(item.get("entry_score")),
            "risk_adjusted_score": num(item.get("risk_adjusted_score")),
            "risk_score": num(item.get("risk_score")),
            "risk_quality_score": num(item.get("risk_quality_score")),
            "risk_quality_label": str(item.get("risk_quality_label") or ""),
            "action_label": str(item.get("action_label") or ""),
        }
        for item in top_pick_rows[:5]
    ]
    alerts = []
    if entry_count == 0:
        alerts.append(
            {
                "code": "no_entry_ready",
                "label": "沒有可進場標的",
                "severity": "warn",
                "detail": "本輪沒有 ENTRY_READY，清單主要用於觀察。",
                "action": "先看觸發觀察與穩定候選。",
            }
        )
    return {
        "status": "ok",
        "verdict": verdict,
        "risk_level": risk_level,
        "quality_score": quality_score,
        "primary_action": primary_action,
        "metrics": {
            "candidate_count": count,
            "entry_count": entry_count,
            "blocker_count": blocker_count,
            "no_blocker_count": no_blocker_count,
            "average_entry_score": average_entry,
            "average_risk_score": average_risk,
        },
        "top_picks": top_picks,
        "top_pick_scope": "actionable_quality" if top_picks else "no_actionable_candidates",
        "top_pick_scope_label": "可研究/只觀察" if top_picks else "沒有可行動首選",
        "alerts": alerts,
    }


def _brief_num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _daily_radar_action_grade(item: dict[str, Any]) -> dict[str, Any]:
    action = str(item.get("action_label") or "").strip().upper()
    blockers = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
    risk_label = str(item.get("risk_quality_label") or "").strip()
    risk_score = _brief_num(item.get("risk_quality_score"))
    symbol = str(item.get("symbol") or "").strip()
    name = str(item.get("name") or "").strip()
    risk_text = f"{risk_label} {risk_score:.1f}".strip() if risk_label else f"{risk_score:.1f}"

    if blockers or action == "NOT_RECOMMENDED" or risk_label in {"先降溫", "先跳過"}:
        label = "不碰"
        key = "avoid"
        reason = "有阻擋或風險層偏弱，先排除。"
    elif action == "ENTRY_READY" and risk_label in {"核心候選", "可小量觀察"} and risk_score >= 64.0:
        label = "可下手"
        key = "ready"
        reason = "可進場、無阻擋，風險品質達標。"
    elif action in {"ENTRY_READY", "WATCH_WAIT_TRIGGER"}:
        label = "只觀察"
        key = "watch"
        reason = "仍需觸發、降溫或補確認，不急著建立 Preview。"
    else:
        label = "不碰"
        key = "avoid"
        reason = "尚未形成可研究動作，先排除。"

    if blockers:
        reason = f"{reason} 阻擋: {', '.join(str(tag) for tag in blockers[:3])}。"
    elif risk_label:
        reason = f"{reason} 風險層: {risk_text}。"

    return {
        "key": key,
        "label": label,
        "symbol": symbol,
        "name": name,
        "action_label": action,
        "risk_quality_label": risk_label,
        "risk_quality_score": risk_score,
        "reason": reason,
    }


def _daily_radar_action_grade_summary(items: list[dict[str, Any]], *, limit: int = 6) -> dict[str, Any]:
    buckets = {
        "ready": {"label": "可下手", "items": []},
        "watch": {"label": "只觀察", "items": []},
        "avoid": {"label": "不碰", "items": []},
    }
    rows = sorted(
        [item for item in items if isinstance(item, dict) and item.get("symbol")],
        key=lambda item: (
            _brief_num(item.get("risk_quality_score")),
            _brief_num(item.get("risk_adjusted_score") or item.get("entry_score")),
            -_brief_num(item.get("rank") or 9999),
        ),
        reverse=True,
    )
    for item in rows:
        grade = _daily_radar_action_grade(item)
        buckets[grade["key"]]["items"].append(grade)

    counts = {bucket["label"]: len(bucket["items"]) for bucket in buckets.values()}
    summary: dict[str, Any] = {
        "counts": counts,
        "ready_label": buckets["ready"]["label"],
        "watch_label": buckets["watch"]["label"],
        "avoid_label": buckets["avoid"]["label"],
    }
    for key, bucket in buckets.items():
        items_for_bucket = bucket["items"][:limit]
        summary[f"{key}_symbols"] = [item["symbol"] for item in items_for_bucket if item.get("symbol")]
        summary[f"{key}_items"] = items_for_bucket
    return summary


def _review_queue_has_items(plan: dict[str, Any]) -> bool:
    diagnostics = plan.get("allocation_diagnostics") if isinstance(plan.get("allocation_diagnostics"), dict) else {}
    review_queue = diagnostics.get("review_queue") if isinstance(diagnostics.get("review_queue"), dict) else {}
    lanes = review_queue.get("lanes") if isinstance(review_queue.get("lanes"), dict) else {}
    for lane in lanes.values():
        if not isinstance(lane, dict):
            continue
        items = lane.get("items") if isinstance(lane.get("items"), list) else []
        try:
            count = int(lane.get("count", len(items)) or 0)
        except Exception:
            count = len(items)
        if count > 0 or items:
            return True
    return False


def _fallback_review_queue_from_actions(items: list[dict[str, Any]]) -> dict[str, Any]:
    lanes = {
        "preview": {
            "label": "可先建立 Preview",
            "hint": "有可進場標的時，先檢查即時價格、部位與 promotion gate。",
            "items": [],
        },
        "trigger": {
            "label": "設觸發觀察",
            "hint": "觀察中標的先放 watchlist，等價格、量能或風控條件到位再升級。",
            "items": [],
        },
        "risk": {
            "label": "先處理風險",
            "hint": "有阻擋標籤的標的先處理資料、事件或交易回饋風險。",
            "items": [],
        },
    }

    def num(value: Any) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return 0.0

    def make_item(row: dict[str, Any], lane_key: str, *, reason: str, next_step: str, alert_rule: str, checklist: list[str]) -> dict[str, Any]:
        trigger_plan: dict[str, Any] = {}
        if lane_key == "trigger":
            try:
                trigger_plan = TradingRepository._daily_radar_trigger_plan(row)
            except Exception:
                trigger_plan = {}
        return {
            "symbol": str(row.get("symbol") or ""),
            "name": row.get("name", ""),
            "industry_group": row.get("industry_group", ""),
            "action_label": row.get("action_label", ""),
            "entry_score": row.get("entry_score", ""),
            "risk_score": row.get("risk_score", ""),
            "price": row.get("price", ""),
            "priority_score": num(row.get("risk_adjusted_score")) or num(row.get("entry_score")),
            "reason": reason,
            "next_step": next_step,
            "alert_rule": alert_rule,
            "checklist": checklist,
            "checklist_text": csv_join_pipe(checklist),
            "trigger_plan": trigger_plan,
            "allocated": False,
            "suggested_value": "",
            "source": "investment_actions_fallback",
            "lane": lane_key,
        }

    for row in sorted(
        [item for item in items if isinstance(item, dict) and item.get("symbol")],
        key=lambda item: (
            int(item.get("rank", 9999) or 9999),
            -(num(item.get("risk_adjusted_score")) or num(item.get("entry_score"))),
        ),
    ):
        blockers = row.get("blocker_tags") if isinstance(row.get("blocker_tags"), list) else []
        action_label = str(row.get("action_label") or "")
        if blockers:
            lanes["risk"]["items"].append(
                make_item(
                    row,
                    "risk",
                    reason="有阻擋標籤，先處理風險再研究",
                    next_step="先確認阻擋是否仍有效，未解除前不建立 Preview",
                    alert_rule="阻擋解除且資料 freshness 正常後再回到觀察清單。",
                    checklist=["檢查阻擋標籤", "確認資料 freshness", "重新計算每日雷達"],
                )
            )
        elif action_label == "ENTRY_READY":
            lanes["preview"]["items"].append(
                make_item(
                    row,
                    "preview",
                    reason="可進場標的，先檢查交易 gate",
                    next_step="確認價格、部位與 promotion gate 後再建立 Preview",
                    alert_rule="即時價格、部位與 promotion gate 都通過後建立 Preview。",
                    checklist=["檢查即時價格與參考價偏離", "查詢目前持股與可賣股數", "確認 promotion readiness"],
                )
            )
        elif action_label == "WATCH_WAIT_TRIGGER":
            lanes["trigger"]["items"].append(
                make_item(
                    row,
                    "trigger",
                    reason="觀察中，等待觸發條件確認",
                    next_step="設定價格、量能或訊號觸發觀察，不急著建立 Preview",
                    alert_rule="價格、量能或觸發訊號到位後，再重新檢查是否可升級。",
                    checklist=["設定觸發價或訊號條件", "確認量能與 ATR 熱度", "觸發後重跑 promotion/preview gate"],
                )
            )

    for lane_key, lane in lanes.items():
        lane["count"] = len(lane["items"])
        checklist = {
            "preview": ["檢查即時價格", "查詢部位", "確認 promotion gate"],
            "trigger": ["設定觸發價或訊號條件", "觸發後重跑每日雷達"],
            "risk": ["先處理阻擋標籤", "阻擋解除後重新計算雷達"],
        }[lane_key]
        lane["alert_plan"] = {
            "rule": lane["hint"],
            "checklist": checklist,
            "checklist_text": csv_join_pipe(checklist),
        }

    counts = {key: int(lane["count"]) for key, lane in lanes.items()}
    primary_key = next((key for key in ("preview", "trigger", "risk") if counts.get(key, 0) > 0), "")
    if primary_key:
        primary_lane = lanes[primary_key]
        top_symbols = [str(item.get("symbol") or "") for item in primary_lane["items"][:4] if item.get("symbol")]
        primary_action = f"{primary_lane['label']} {counts[primary_key]} 檔"
        next_session_note = f"{primary_lane['label']} {counts[primary_key]} 檔：{', '.join(top_symbols)}" if top_symbols else primary_action
    else:
        top_symbols = []
        primary_action = "等待配置行動線"
        next_session_note = "等待配置行動線"

    return {
        "lanes": lanes,
        "summary": {
            "preview_count": counts["preview"],
            "trigger_count": counts["trigger"],
            "risk_count": counts["risk"],
        },
        "handoff": {
            "primary_lane": primary_key,
            "primary_label": lanes[primary_key]["label"] if primary_key else "",
            "primary_action": primary_action,
            "next_session_note": next_session_note,
            "top_symbols": top_symbols,
            "total_review_count": sum(counts.values()),
            "source": "investment_actions_fallback",
        },
        "source": "investment_actions_fallback",
    }


def _ensure_actionable_review_queue(plan: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    if _review_queue_has_items(plan):
        return plan
    review_queue = _fallback_review_queue_from_actions(items)
    if not any(int(value or 0) > 0 for value in review_queue.get("summary", {}).values()):
        return plan
    output = dict(plan)
    diagnostics = dict(output.get("allocation_diagnostics") if isinstance(output.get("allocation_diagnostics"), dict) else {})
    diagnostics.setdefault("risk_level", "watch")
    diagnostics.setdefault("verdict", "依每日雷達行動分類產生保底行動線")
    diagnostics.setdefault("alerts", [])
    diagnostics["review_queue"] = review_queue
    output["allocation_diagnostics"] = diagnostics
    output.setdefault("allocation_items", [])
    output.setdefault("status", "action_queue_fallback")
    output.setdefault("reason", "allocation_plan_unavailable")
    return output


def export_daily_radar_files(
    payload: list[dict[str, Any]],
    *,
    output_dir: Path,
    report_date: date | str,
    run_id: str,
    allocation_plan: dict[str, Any] | None = None,
    allocation_outcomes: dict[str, Any] | None = None,
    allocation_calibration: dict[str, Any] | None = None,
    trading_feedback: dict[str, Any] | None = None,
    data_health_overview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_payload = sanitize_report_payload(payload)
    safe_payload = enrich_risk_quality(safe_payload if isinstance(safe_payload, list) else [])
    safe_allocation_plan = sanitize_report_payload(allocation_plan) if isinstance(allocation_plan, dict) else {}
    safe_allocation_outcomes = sanitize_report_payload(allocation_outcomes) if isinstance(allocation_outcomes, dict) else {}
    safe_allocation_calibration = sanitize_report_payload(allocation_calibration) if isinstance(allocation_calibration, dict) else {}
    safe_trading_feedback = sanitize_report_payload(trading_feedback) if isinstance(trading_feedback, dict) else {}
    safe_run_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(run_id or "latest"))
    json_name = f"daily_radar_{safe_run_id}.json"
    csv_name = f"daily_radar_{safe_run_id}.csv"
    brief_name = f"daily_radar_{safe_run_id}_brief.md"
    json_path = output_dir / json_name
    csv_path = output_dir / csv_name
    brief_path = output_dir / brief_name
    action_queue = build_action_queue(safe_payload)
    investment_actions = build_investment_actions(safe_payload)
    diversified_shortlist = build_diversified_shortlist(safe_payload)
    safe_data_health_overview = sanitize_report_payload(data_health_overview) if isinstance(data_health_overview, dict) else None
    quality_summary = _quality_summary_for_export(safe_payload, data_health_overview=safe_data_health_overview)
    threshold_sweep = build_daily_radar_threshold_sweep(safe_payload)
    safe_allocation_plan = _ensure_actionable_review_queue(safe_allocation_plan, safe_payload)
    json_payload: dict[str, Any] = {
        "run_id": run_id,
        "trade_date": str(report_date),
        "count": len(safe_payload),
        "quality_summary": quality_summary,
        "threshold_sweep": threshold_sweep,
        "investment_actions": investment_actions,
        "action_queue": action_queue,
        "diversified_shortlist": diversified_shortlist,
        "allocation_plan": safe_allocation_plan,
        "allocation_outcomes": safe_allocation_outcomes,
        "allocation_calibration": safe_allocation_calibration,
        "trading_feedback": safe_trading_feedback,
        "items": safe_payload,
    }
    header = [
        "rank",
        "diversified_shortlist_rank",
        "allocation_review_lane",
        "allocation_review_label",
        "allocation_review_reason",
        "allocation_review_allocated",
        "allocation_review_monitor_only",
        "allocation_review_allocation_lane",
        "allocation_review_status",
        "allocation_review_alert_rule",
        "allocation_review_checklist",
        "allocation_review_trigger_watchlist_note",
        "allocation_review_trigger_plan",
        "allocation_review_trigger_target_entry_score",
        "allocation_review_trigger_breakout_price",
        "allocation_review_trigger_pullback_price",
        "allocation_review_trigger_entry_gap",
        "allocation_review_trigger_breakout_gap_pct",
        "allocation_review_trigger_pullback_gap_pct",
        "allocation_review_trigger_readiness_score",
        "allocation_review_trigger_readiness_label",
        "allocation_review_trigger_rules",
        "allocation_review_trigger_checks",
        "allocation_review_trading_feedback_risk",
        "allocation_review_trading_feedback",
        "allocation_review_focus",
        "allocation_review_primary_action",
        "allocation_review_next_session_note",
        "allocation_review_preview_count",
        "allocation_review_trigger_count",
        "allocation_review_risk_count",
        "allocation_suggested_value",
        "allocation_odd_lot_shares",
        "allocation_board_lots",
        "allocation_weight",
        "allocation_premarket_multiplier",
        "allocation_premarket_reasons",
        "allocation_symbol_adoption_feedback_boost",
        "allocation_symbol_adoption_feedback_confidence_weight",
        "allocation_symbol_adoption_feedback_confidence_level",
        "allocation_calibration_multiplier",
        "allocation_next_step",
        "symbol",
        "name",
        "price",
        "entry_score",
        "risk_adjusted_score",
        "risk_score",
        "risk_quality_score",
        "risk_quality_label",
        "risk_quality_reason",
        "industry_group",
        "industry_rotation_score",
        "industry_trend_label",
        "industry_trend_score",
        "industry_short_return",
        "industry_medium_return",
        "industry_breadth",
        "outcome_reliability_score",
        "outcome_reliability_boost",
        "outcome_reliability_samples",
        "outcome_reliability_horizons",
        "symbol_adoption_feedback_score",
        "symbol_adoption_feedback_raw_boost",
        "symbol_adoption_feedback_boost",
        "symbol_adoption_feedback_samples",
        "symbol_adoption_feedback_confidence_weight",
        "symbol_adoption_feedback_confidence_level",
        "symbol_adoption_feedback_horizons",
        "atr_pct",
        "atr_heat",
        "action_label",
        "candidate_type",
        "last_bar_date",
        "data_quality",
        "fundamental_data_quality",
        "revenue_score",
        "quality_score",
        "valuation_or_growth_score",
        "news_risk_score",
        "reason_tags",
        "blocker_tags",
        "investment_action",
        "action_queue_lanes",
    ]
    diversified_rank_by_symbol = {
        str(item.get("symbol") or ""): item.get("shortlist_rank", "")
        for item in diversified_shortlist
        if item.get("symbol")
    }
    allocation_by_symbol = {
        str(item.get("symbol") or ""): item
        for item in safe_allocation_plan.get("allocation_items", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    radar_by_symbol = {
        str(item.get("symbol") or ""): item
        for item in safe_payload
        if isinstance(item, dict) and item.get("symbol")
    }

    def merge_risk_quality(target: dict[str, Any], symbol: str | None = None) -> None:
        key = str(symbol or target.get("symbol") or "")
        radar = radar_by_symbol.get(key, {})
        if not radar:
            return
        for field in ("risk_quality_score", "risk_quality_label", "risk_quality_reason"):
            if radar.get(field) not in (None, ""):
                target[field] = radar.get(field)

    for allocation_item in safe_allocation_plan.get("allocation_items", []):
        if isinstance(allocation_item, dict):
            merge_risk_quality(allocation_item)

    review_by_symbol: dict[str, dict[str, Any]] = {}
    diagnostics = safe_allocation_plan.get("allocation_diagnostics")
    review_queue = diagnostics.get("review_queue") if isinstance(diagnostics, dict) else {}
    review_lanes = review_queue.get("lanes") if isinstance(review_queue, dict) else {}
    review_summary = review_queue.get("summary") if isinstance(review_queue, dict) and isinstance(review_queue.get("summary"), dict) else {}
    review_handoff = review_queue.get("handoff") if isinstance(review_queue, dict) and isinstance(review_queue.get("handoff"), dict) else {}

    def brief_text(value: Any) -> str:
        return str(value or "").replace("\r", " ").replace("\n", " ").strip()

    def review_checklist_text(item: dict[str, Any]) -> str:
        raw_text = item.get("checklist_text")
        if raw_text:
            return str(raw_text)
        raw_checks = item.get("checklist") if isinstance(item.get("checklist"), list) else []
        checks = [check for check in raw_checks if str(check) != "<redacted-depth-limit>"]
        return csv_join_pipe(checks)

    def review_trading_feedback_text(item: dict[str, Any]) -> str:
        issues = item.get("trading_feedback_issues") if isinstance(item.get("trading_feedback_issues"), list) else []
        labels = [
            brief_text(issue.get("label") or issue.get("code") or issue.get("detail"))
            for issue in issues
            if isinstance(issue, dict) and brief_text(issue.get("label") or issue.get("code") or issue.get("detail"))
        ]
        if labels:
            return csv_join_pipe(labels[:4])
        issue_count = int(item.get("trading_feedback_issue_count") or 0)
        return f"{issue_count} 個交易回饋待處理" if issue_count > 0 else ""

    def review_allocation_status(item: dict[str, Any]) -> str:
        if item.get("monitor_only") is True or str(item.get("allocation_lane") or "") == "monitor_only":
            return "盯盤備選"
        if item.get("allocated") is False:
            return "未配置"
        if str(item.get("allocated") or "").strip().lower() == "false":
            return "未配置"
        return "已配置"

    def trigger_plan_payload(item: dict[str, Any], radar_row: dict[str, Any] | None = None) -> dict[str, Any]:
        raw_plan = item.get("trigger_plan") if isinstance(item.get("trigger_plan"), dict) else {}
        plan_redacted = any(
            str(raw_plan.get(key) or "") == "<redacted-depth-limit>"
            for key in (
                "summary",
                "target_entry_score",
                "breakout_price",
                "pullback_price",
                "reference_price",
                "entry_gap",
                "breakout_gap_pct",
                "pullback_gap_pct",
                "trigger_readiness_score",
                "trigger_readiness_label",
                "risk_adjusted_score",
                "atr_heat",
            )
        )
        should_infer = (
            (not raw_plan or plan_redacted)
            and (
                str(item.get("lane") or "") == "trigger"
                or str(item.get("action_label") or "") == "WATCH_WAIT_TRIGGER"
            )
        )
        if should_infer:
            merged: dict[str, Any] = {}
            if isinstance(radar_row, dict):
                merged.update(radar_row)
            merged.update({key: value for key, value in item.items() if value not in (None, "")})
            try:
                raw_plan = TradingRepository._daily_radar_trigger_plan(merged)
            except Exception:
                raw_plan = {}
        if not isinstance(raw_plan, dict) or not raw_plan:
            return {}
        rules = [brief_text(rule) for rule in raw_plan.get("rules", []) if brief_text(rule)] if isinstance(raw_plan.get("rules"), list) else []
        checks = [brief_text(check) for check in raw_plan.get("checks", []) if brief_text(check)] if isinstance(raw_plan.get("checks"), list) else []
        return {
            "summary": brief_text(raw_plan.get("summary")),
            "rules": rules,
            "checks": checks,
            "target_entry_score": raw_plan.get("target_entry_score", ""),
            "breakout_price": raw_plan.get("breakout_price", ""),
            "pullback_price": raw_plan.get("pullback_price", ""),
            "reference_price": raw_plan.get("reference_price", ""),
            "entry_gap": raw_plan.get("entry_gap", ""),
            "breakout_gap_pct": raw_plan.get("breakout_gap_pct", ""),
            "pullback_gap_pct": raw_plan.get("pullback_gap_pct", ""),
            "trigger_readiness_score": raw_plan.get("trigger_readiness_score", ""),
            "trigger_readiness_label": brief_text(raw_plan.get("trigger_readiness_label")),
            "risk_adjusted_score": raw_plan.get("risk_adjusted_score", ""),
            "atr_heat": brief_text(raw_plan.get("atr_heat")),
        }

    def trigger_plan_text(plan: dict[str, Any]) -> str:
        if not isinstance(plan, dict) or not plan:
            return ""
        summary = brief_text(plan.get("summary"))
        if summary:
            return summary
        rules = plan.get("rules") if isinstance(plan.get("rules"), list) else []
        return csv_join_pipe([brief_text(rule) for rule in rules if brief_text(rule)])

    def trigger_watchlist_note(item: dict[str, Any], plan: dict[str, Any], radar_row: dict[str, Any] | None = None) -> str:
        direct = brief_text(item.get("trigger_watchlist_note"))
        if direct:
            return direct
        merged: dict[str, Any] = {}
        if isinstance(radar_row, dict):
            merged.update(radar_row)
        merged.update({key: value for key, value in item.items() if value not in (None, "")})
        try:
            return TradingRepository._daily_radar_trigger_watchlist_note(
                merged,
                plan,
                allocation_status=review_allocation_status(item),
            )
        except Exception:
            return ""

    def trigger_plan_metric_text(plan: dict[str, Any]) -> str:
        if not isinstance(plan, dict) or not plan:
            return ""

        def fmt(value: Any, digits: int = 2) -> str:
            try:
                return f"{float(value):.{digits}f}"
            except Exception:
                return ""

        parts = []
        target = fmt(plan.get("target_entry_score"), 0)
        breakout = fmt(plan.get("breakout_price"), 2)
        pullback = fmt(plan.get("pullback_price"), 2)
        if target:
            parts.append(f"Entry >= {target}")
        if breakout:
            parts.append(f"突破 {breakout}")
        if pullback:
            parts.append(f"回測 {pullback}")
        return " / ".join(parts)

    def trigger_plan_short_metric_text(plan: dict[str, Any]) -> str:
        if not isinstance(plan, dict) or not plan:
            return ""

        def fmt(value: Any, digits: int = 2) -> str:
            try:
                return f"{float(value):.{digits}f}"
            except Exception:
                return ""

        parts = []
        target = fmt(plan.get("target_entry_score"), 0)
        breakout = fmt(plan.get("breakout_price"), 2)
        pullback = fmt(plan.get("pullback_price"), 2)
        if target:
            parts.append(f"Entry>={target}")
        if breakout:
            parts.append(f"突破{breakout}")
        if pullback:
            parts.append(f"回測{pullback}")
        return " / ".join(parts)

    def trigger_plan_brief_text(plan: dict[str, Any]) -> str:
        metric_text = trigger_plan_metric_text(plan)
        summary = trigger_plan_text(plan)
        if metric_text and summary:
            return f"{metric_text}；{summary}"
        return metric_text or summary

    def dedupe_brief_parts(parts: list[str]) -> list[str]:
        result: list[str] = []
        normalized_seen: list[str] = []
        for part in parts:
            text = brief_text(part).strip("；; ")
            if not text:
                continue
            normalized = "".join(ch for ch in text if ch not in " 　；;，,。./|：:")
            if not normalized:
                continue
            if any(normalized == seen or normalized in seen or seen in normalized for seen in normalized_seen):
                continue
            result.append(text)
            normalized_seen.append(normalized)
        return result

    def quality_lane_brief_items(summary: dict[str, Any]) -> list[dict[str, Any]]:
        raw_lanes = summary.get("quality_lanes") if isinstance(summary.get("quality_lanes"), list) else []
        lanes: list[dict[str, Any]] = []
        for lane in [item for item in raw_lanes if isinstance(item, dict)]:
            raw_items = lane.get("items") if isinstance(lane.get("items"), list) else []
            items = []
            scope_counts: dict[str, int] = {}
            for item in [row for row in raw_items if isinstance(row, dict)]:
                gap_scopes = item.get("data_gap_scopes") if isinstance(item.get("data_gap_scopes"), list) else []
                for scope in gap_scopes:
                    scope_key = brief_text(scope)
                    if scope_key:
                        scope_counts[scope_key] = scope_counts.get(scope_key, 0) + 1
            for item in [row for row in raw_items if isinstance(row, dict)][:6]:
                gap_scopes = item.get("data_gap_scopes") if isinstance(item.get("data_gap_scopes"), list) else []
                items.append(
                    {
                        "symbol": brief_text(item.get("symbol")),
                        "name": brief_text(item.get("name")),
                        "industry_group": brief_text(item.get("industry_group")),
                        "action_label": brief_text(item.get("action_label")),
                        "candidate_quality_score": item.get("candidate_quality_score", item.get("quality_score", "")),
                        "data_gap_count": item.get("data_gap_count", 0),
                        "data_gap_scopes": [brief_text(scope) for scope in gap_scopes if brief_text(scope)][:5],
                    }
                )
            data_gap_scopes = [
                scope for scope, _count in sorted(scope_counts.items(), key=lambda row: (-row[1], row[0]))
            ][:6]
            data_gap_scope_summary = [
                f"{scope} {count} 檔" for scope, count in sorted(scope_counts.items(), key=lambda row: (-row[1], row[0]))[:6]
            ]
            try:
                count = int(lane.get("count") or len(raw_items))
            except Exception:
                count = len(raw_items)
            lanes.append(
                {
                    "key": brief_text(lane.get("key")),
                    "label": brief_text(lane.get("label") or lane.get("key")),
                    "summary": brief_text(lane.get("summary")),
                    "action": brief_text(lane.get("action")),
                    "count": count,
                    "symbols": [item["symbol"] for item in items if item["symbol"]],
                    "data_gap_scopes": data_gap_scopes,
                    "data_gap_scope_summary": data_gap_scope_summary,
                    "items": items,
                }
            )
        return lanes

    def review_lane_count(key: str, summary_key: str) -> int:
        lane = review_lanes.get(key) if isinstance(review_lanes, dict) and isinstance(review_lanes.get(key), dict) else {}
        items = lane.get("items") if isinstance(lane.get("items"), list) else []
        raw_count = review_summary.get(summary_key, lane.get("count", len(items)))
        try:
            return int(raw_count)
        except Exception:
            return len(items)

    review_counts = {
        "preview": review_lane_count("preview", "preview_count"),
        "trigger": review_lane_count("trigger", "trigger_count"),
        "risk": review_lane_count("risk", "risk_count"),
    }
    review_labels = {
        key: str(review_lanes.get(key, {}).get("label") or fallback)
        if isinstance(review_lanes, dict) and isinstance(review_lanes.get(key), dict)
        else fallback
        for key, fallback in {
            "preview": "可先建立 Preview",
            "trigger": "設觸發觀察",
            "risk": "先處理風險",
        }.items()
    }
    review_focus_key = ""
    for candidate in ("risk", "preview", "trigger"):
        if review_counts.get(candidate, 0) > 0:
            review_focus_key = candidate
            break
    review_focus = (
        f"{review_labels[review_focus_key]} {review_counts[review_focus_key]} 檔"
        if review_focus_key
        else "等待配置行動線"
    )
    if isinstance(review_lanes, dict):
        for lane_key, lane in review_lanes.items():
            if not isinstance(lane, dict):
                continue
            lane_label = str(lane.get("label") or lane_key)
            for item in lane.get("items", []) if isinstance(lane.get("items"), list) else []:
                if not isinstance(item, dict) or not item.get("symbol"):
                    continue
                merge_risk_quality(item)
                item_trigger_plan = trigger_plan_payload(item, radar_by_symbol.get(str(item.get("symbol") or "")))
                if item_trigger_plan:
                    item["trigger_plan"] = item_trigger_plan
                watchlist_note = trigger_watchlist_note(item, item_trigger_plan, radar_by_symbol.get(str(item.get("symbol") or "")))
                review_by_symbol.setdefault(
                    str(item.get("symbol") or ""),
                    {
                        "lane": lane_key,
                        "label": lane_label,
                        "reason": item.get("reason") or item.get("next_step") or "",
                        "allocated": item.get("allocated", ""),
                        "monitor_only": item.get("monitor_only", ""),
                        "allocation_lane": item.get("allocation_lane", ""),
                        "allocation_status": review_allocation_status(item),
                        "alert_rule": item.get("alert_rule", ""),
                        "checklist": review_checklist_text(item),
                        "trigger_watchlist_note": watchlist_note,
                        "trigger_plan": item_trigger_plan,
                        "trading_feedback_risk": item.get("trading_feedback_risk_level", ""),
                        "trading_feedback": review_trading_feedback_text(item),
                        "allocation_weight": item.get("allocation_weight", ""),
                        "premarket_multiplier": item.get("premarket_multiplier", ""),
                        "premarket_reasons": csv_join_pipe(
                            [
                                str(reason.get("label") or reason.get("type") or "")
                                for reason in item.get("premarket_reasons", [])
                                if isinstance(reason, dict)
                            ]
                        ),
                        "symbol_adoption_feedback_boost": item.get("symbol_adoption_feedback_boost", ""),
                        "symbol_adoption_feedback_confidence_weight": item.get("symbol_adoption_feedback_confidence_weight", ""),
                        "symbol_adoption_feedback_confidence_level": item.get("symbol_adoption_feedback_confidence_level", ""),
                    },
                )
    review_lane_header = [
        "review_rank",
        "lane",
        "lane_label",
        "symbol",
        "name",
        "industry_group",
        "action_label",
        "priority_score",
        "suggested_value",
        "allocated",
        "monitor_only",
        "allocation_lane",
        "allocation_status",
        "allocation_weight",
        "premarket_multiplier",
        "premarket_reasons",
        "symbol_adoption_feedback_boost",
        "symbol_adoption_feedback_confidence_weight",
        "symbol_adoption_feedback_confidence_level",
        "calibration_multiplier",
        "reason",
        "next_step",
        "alert_rule",
        "checklist",
        "trigger_watchlist_note",
        "trigger_plan",
        "trigger_target_entry_score",
        "trigger_breakout_price",
        "trigger_pullback_price",
        "trigger_entry_gap",
        "trigger_breakout_gap_pct",
        "trigger_pullback_gap_pct",
        "trigger_readiness_score",
        "trigger_readiness_label",
        "trigger_rules",
        "trigger_checks",
        "trading_feedback_risk",
        "trading_feedback",
        "radar_rank",
        "price",
        "entry_score",
        "risk_adjusted_score",
        "risk_score",
        "risk_quality_score",
        "risk_quality_label",
        "risk_quality_reason",
        "atr_heat",
        "last_bar_date",
        "data_quality",
        "reason_tags",
        "blocker_tags",
        "allocation_review_focus",
        "allocation_review_primary_action",
        "allocation_review_next_session_note",
        "allocation_review_preview_count",
        "allocation_review_trigger_count",
        "allocation_review_risk_count",
    ]
    review_lane_files: dict[str, str] = {}
    review_lane_urls: dict[str, str] = {}
    for lane_key in ("preview", "trigger", "risk"):
        lane = review_lanes.get(lane_key) if isinstance(review_lanes, dict) and isinstance(review_lanes.get(lane_key), dict) else {}
        lane_label = review_labels.get(lane_key, lane_key)
        lane_items = lane.get("items") if isinstance(lane.get("items"), list) else []
        lane_lines = [csv_join_row(review_lane_header)]
        for idx, item in enumerate([row for row in lane_items if isinstance(row, dict)], start=1):
            symbol = str(item.get("symbol") or "")
            radar_row = radar_by_symbol.get(symbol, {})
            trigger_plan = trigger_plan_payload(item, radar_row)
            watchlist_note = trigger_watchlist_note(item, trigger_plan, radar_row)
            lane_lines.append(
                csv_join_row(
                    [
                        idx,
                        lane_key,
                        lane_label,
                        symbol,
                        item.get("name", ""),
                        item.get("industry_group", ""),
                        item.get("action_label", ""),
                        item.get("priority_score", ""),
                        item.get("suggested_value", ""),
                        item.get("allocated", ""),
                        item.get("monitor_only", ""),
                        item.get("allocation_lane", ""),
                        review_allocation_status(item),
                        item.get("allocation_weight", ""),
                        item.get("premarket_multiplier", ""),
                        item.get("premarket_reasons", ""),
                        item.get("symbol_adoption_feedback_boost", ""),
                        item.get("symbol_adoption_feedback_confidence_weight", ""),
                        item.get("symbol_adoption_feedback_confidence_level", ""),
                        item.get("calibration_multiplier", ""),
                        item.get("reason", ""),
                        item.get("next_step", ""),
                        item.get("alert_rule", ""),
                        review_checklist_text(item),
                        watchlist_note,
                        trigger_plan_text(trigger_plan),
                        trigger_plan.get("target_entry_score", ""),
                        trigger_plan.get("breakout_price", ""),
                        trigger_plan.get("pullback_price", ""),
                        trigger_plan.get("entry_gap", ""),
                        trigger_plan.get("breakout_gap_pct", ""),
                        trigger_plan.get("pullback_gap_pct", ""),
                        trigger_plan.get("trigger_readiness_score", ""),
                        trigger_plan.get("trigger_readiness_label", ""),
                        csv_join_pipe(trigger_plan.get("rules", [])),
                        csv_join_pipe(trigger_plan.get("checks", [])),
                        item.get("trading_feedback_risk_level", ""),
                        review_trading_feedback_text(item),
                        radar_row.get("rank", ""),
                        radar_row.get("price", ""),
                        radar_row.get("entry_score", ""),
                        radar_row.get("risk_adjusted_score", ""),
                        radar_row.get("risk_score", ""),
                        radar_row.get("risk_quality_score", ""),
                        radar_row.get("risk_quality_label", ""),
                        radar_row.get("risk_quality_reason", ""),
                        radar_row.get("atr_heat", ""),
                        radar_row.get("last_bar_date", ""),
                        radar_row.get("data_quality", ""),
                        csv_join_pipe(radar_row.get("reason_tags", [])),
                        csv_join_pipe(radar_row.get("blocker_tags", [])),
                        review_focus,
                        review_handoff.get("primary_action", ""),
                        review_handoff.get("next_session_note", ""),
                        review_counts["preview"],
                        review_counts["trigger"],
                        review_counts["risk"],
                    ]
                )
            )
        lane_name = f"daily_radar_{safe_run_id}_review_{lane_key}.csv"
        (output_dir / lane_name).write_text("\n".join(lane_lines), encoding="utf-8")
        review_lane_files[f"allocation_review_{lane_key}_csv"] = lane_name
        review_lane_urls[f"allocation_review_{lane_key}_csv"] = f"/reports/{lane_name}"
        lane_symbols = list(
            dict.fromkeys(
                str(item.get("symbol") or "").strip()
                for item in lane_items
                if isinstance(item, dict) and str(item.get("symbol") or "").strip()
            )
        )
        symbols_name = f"daily_radar_{safe_run_id}_review_{lane_key}_symbols.txt"
        (output_dir / symbols_name).write_text("\n".join(lane_symbols), encoding="utf-8")
        review_lane_files[f"allocation_review_{lane_key}_symbols_txt"] = symbols_name
        review_lane_urls[f"allocation_review_{lane_key}_symbols_txt"] = f"/reports/{symbols_name}"
    quality_lane_files: dict[str, str] = {}
    quality_lane_urls: dict[str, str] = {}
    quality_lanes = quality_summary.get("quality_lanes") if isinstance(quality_summary.get("quality_lanes"), list) else []
    quality_lane_header = [
        "rank",
        "lane_key",
        "lane_label",
        "symbol",
        "name",
        "industry_group",
        "action_label",
        "candidate_quality_score",
        "entry_score",
        "risk_adjusted_score",
        "risk_score",
        "risk_quality_score",
        "risk_quality_label",
        "risk_quality_reason",
        "data_quality",
        "data_gap_count",
        "data_gap_scopes",
        "lane_summary",
        "lane_action",
    ]
    for lane in [item for item in quality_lanes if isinstance(item, dict)]:
        raw_lane_key = str(lane.get("key") or "lane")
        lane_key = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in raw_lane_key) or "lane"
        lane_label = str(lane.get("label") or raw_lane_key)
        lane_items = lane.get("items") if isinstance(lane.get("items"), list) else []
        lane_lines = [csv_join_row(quality_lane_header)]
        for idx, item in enumerate([row for row in lane_items if isinstance(row, dict)], start=1):
            lane_lines.append(
                csv_join_row(
                    [
                        idx,
                        raw_lane_key,
                        lane_label,
                        item.get("symbol", ""),
                        item.get("name", ""),
                        item.get("industry_group", ""),
                        item.get("action_label", ""),
                        item.get("candidate_quality_score", ""),
                        item.get("entry_score", ""),
                        item.get("risk_adjusted_score", ""),
                        item.get("risk_score", ""),
                        item.get("risk_quality_score", ""),
                        item.get("risk_quality_label", ""),
                        item.get("risk_quality_reason", ""),
                        item.get("data_quality", ""),
                        item.get("data_gap_count", ""),
                        csv_join_pipe(item.get("data_gap_scopes", [])),
                        lane.get("summary", ""),
                        lane.get("action", ""),
                    ]
                )
            )
        lane_name = f"daily_radar_{safe_run_id}_quality_{lane_key}.csv"
        (output_dir / lane_name).write_text("\n".join(lane_lines), encoding="utf-8")
        quality_lane_files[f"quality_lane_{lane_key}_csv"] = lane_name
        quality_lane_urls[f"quality_lane_{lane_key}_csv"] = f"/reports/{lane_name}"
        lane_symbols = list(
            dict.fromkeys(
                str(item.get("symbol") or "").strip()
                for item in lane_items
                if isinstance(item, dict) and str(item.get("symbol") or "").strip()
            )
        )
        symbols_name = f"daily_radar_{safe_run_id}_quality_{lane_key}_symbols.txt"
        (output_dir / symbols_name).write_text("\n".join(lane_symbols), encoding="utf-8")
        quality_lane_files[f"quality_lane_{lane_key}_symbols_txt"] = symbols_name
        quality_lane_urls[f"quality_lane_{lane_key}_symbols_txt"] = f"/reports/{symbols_name}"
    lines = [csv_join_row(header)]
    for row in safe_payload:
        symbol = str(row.get("symbol") or "")
        allocation = allocation_by_symbol.get(symbol, {})
        review = review_by_symbol.get(symbol, {})
        review_trigger_plan = review.get("trigger_plan") if isinstance(review.get("trigger_plan"), dict) else {}
        values = [
            row.get("rank", ""),
            diversified_rank_by_symbol.get(symbol, ""),
            review.get("lane", ""),
            review.get("label", ""),
            review.get("reason", ""),
            review.get("allocated", ""),
            review.get("monitor_only", ""),
            review.get("allocation_lane", ""),
            review.get("allocation_status", ""),
            review.get("alert_rule", ""),
            review.get("checklist", ""),
            review.get("trigger_watchlist_note", ""),
            trigger_plan_text(review_trigger_plan),
            review_trigger_plan.get("target_entry_score", ""),
            review_trigger_plan.get("breakout_price", ""),
            review_trigger_plan.get("pullback_price", ""),
            review_trigger_plan.get("entry_gap", ""),
            review_trigger_plan.get("breakout_gap_pct", ""),
            review_trigger_plan.get("pullback_gap_pct", ""),
            review_trigger_plan.get("trigger_readiness_score", ""),
            review_trigger_plan.get("trigger_readiness_label", ""),
            csv_join_pipe(review_trigger_plan.get("rules", [])),
            csv_join_pipe(review_trigger_plan.get("checks", [])),
            review.get("trading_feedback_risk", ""),
            review.get("trading_feedback", ""),
            review_focus,
            review_handoff.get("primary_action", ""),
            review_handoff.get("next_session_note", ""),
            review_counts["preview"],
            review_counts["trigger"],
            review_counts["risk"],
            allocation.get("suggested_value", ""),
            allocation.get("suggested_odd_lot_shares", ""),
            allocation.get("suggested_board_lots", ""),
            allocation.get("allocation_weight", ""),
            allocation.get("premarket_multiplier", ""),
            csv_join_pipe(
                [
                    str(reason.get("label") or reason.get("type") or "")
                    for reason in allocation.get("premarket_reasons", [])
                    if isinstance(reason, dict)
                ]
            ),
            allocation.get("symbol_adoption_feedback_boost", ""),
            allocation.get("symbol_adoption_feedback_confidence_weight", ""),
            allocation.get("symbol_adoption_feedback_confidence_level", ""),
            allocation.get("calibration_multiplier", ""),
            allocation.get("next_step", ""),
            row.get("symbol", ""),
            row.get("name", ""),
            row.get("price", ""),
            row.get("entry_score", ""),
            row.get("risk_adjusted_score", ""),
            row.get("risk_score", ""),
            row.get("risk_quality_score", ""),
            row.get("risk_quality_label", ""),
            row.get("risk_quality_reason", ""),
            row.get("industry_group", ""),
            f"{float(row.get('industry_rotation_score', 0.0) or 0.0):.4f}",
            row.get("industry_trend_label", ""),
            f"{float(row.get('industry_trend_score', 0.0) or 0.0):.4f}",
            f"{float(row.get('industry_short_return', 0.0) or 0.0):.4f}",
            f"{float(row.get('industry_medium_return', 0.0) or 0.0):.4f}",
            f"{float(row.get('industry_breadth', 0.0) or 0.0):.4f}",
            f"{float(row.get('outcome_reliability_score', 0.0) or 0.0):.4f}",
            f"{float(row.get('outcome_reliability_boost', 0.0) or 0.0):.4f}",
            int(row.get("outcome_reliability_samples", 0) or 0),
            csv_join_pipe(
                [
                    f"{int(item.get('lookahead_bars', 0) or 0)}d:{float(item.get('positive_rate', 0.0) or 0.0):.2f}"
                    for item in row.get("outcome_reliability_horizons", [])
                    if isinstance(item, dict)
                ]
            ),
            f"{float(row.get('symbol_adoption_feedback_score', 0.0) or 0.0):.4f}",
            f"{float(row.get('symbol_adoption_feedback_raw_boost', 0.0) or 0.0):.4f}",
            f"{float(row.get('symbol_adoption_feedback_boost', 0.0) or 0.0):.4f}",
            int(row.get("symbol_adoption_feedback_samples", 0) or 0),
            f"{float(row.get('symbol_adoption_feedback_confidence_weight', 0.0) or 0.0):.4f}",
            row.get("symbol_adoption_feedback_confidence_level", ""),
            csv_join_pipe(
                [
                    f"{int(item.get('lookahead_bars', 0) or 0)}d:{float(item.get('positive_rate', 0.0) or 0.0):.2f}"
                    for item in row.get("symbol_adoption_feedback_horizons", [])
                    if isinstance(item, dict)
                ]
            ),
            f"{float(row.get('atr_pct', 0.0) or 0.0):.4f}",
            row.get("atr_heat", ""),
            row.get("action_label", ""),
            row.get("candidate_type", ""),
            row.get("last_bar_date", ""),
            row.get("data_quality", ""),
            row.get("fundamental_data_quality", ""),
            f"{float(row.get('revenue_score', 0.5) or 0.5):.4f}",
            f"{float(row.get('quality_score', 0.5) or 0.5):.4f}",
            f"{float(row.get('valuation_or_growth_score', 0.5) or 0.5):.4f}",
            f"{float(row.get('news_risk_score', 0.5) or 0.5):.4f}",
            csv_join_pipe(row.get("reason_tags", [])),
            csv_join_pipe(row.get("blocker_tags", [])),
            investment_action_label_for_item(row),
            csv_join_pipe(action_queue_lanes_for_item(row)),
        ]
        lines.append(csv_join_row(values))
    csv_path.write_text("\n".join(lines), encoding="utf-8")

    diagnostic_alerts = diagnostics.get("alerts") if isinstance(diagnostics, dict) and isinstance(diagnostics.get("alerts"), list) else []
    decision_brief_lanes: list[dict[str, Any]] = []
    for lane_key in ("preview", "trigger", "risk"):
        lane = review_lanes.get(lane_key) if isinstance(review_lanes, dict) and isinstance(review_lanes.get(lane_key), dict) else {}
        lane_items = lane.get("items") if isinstance(lane.get("items"), list) else []
        alert_plan = lane.get("alert_plan") if isinstance(lane.get("alert_plan"), dict) else {}
        plan_checks = [brief_text(item) for item in review_checklist_text(alert_plan).split("|") if brief_text(item)]
        item_summaries = []
        for item in [row for row in lane_items if isinstance(row, dict)][:6]:
            checklist = [brief_text(part) for part in review_checklist_text(item).split("|") if brief_text(part)]
            radar_row = radar_by_symbol.get(str(item.get("symbol") or ""), {})
            trigger_plan = trigger_plan_payload(item, radar_row)
            watchlist_note = trigger_watchlist_note(item, trigger_plan, radar_row)
            trigger_metrics = trigger_plan_metric_text(trigger_plan)
            trigger_short = trigger_plan_short_metric_text(trigger_plan) or trigger_metrics
            action_grade = _daily_radar_action_grade(radar_row if isinstance(radar_row, dict) and radar_row else item)
            item_summaries.append(
                {
                    "symbol": brief_text(item.get("symbol")),
                    "name": brief_text(item.get("name")),
                    "reason": brief_text(item.get("reason") or item.get("next_step")),
                    "alert_rule": brief_text(item.get("alert_rule")),
                    "checklist": checklist,
                    "trigger_plan": trigger_plan,
                    "trigger_plan_text": trigger_plan_brief_text(trigger_plan),
                    "trigger_plan_metrics_text": trigger_metrics,
                    "trigger_plan_short_text": trigger_short,
                    "trigger_watchlist_note": watchlist_note,
                    "allocated": item.get("allocated", ""),
                    "monitor_only": item.get("monitor_only", ""),
                    "allocation_lane": item.get("allocation_lane", ""),
                    "allocation_status": review_allocation_status(item),
                    "suggested_value": item.get("suggested_value", ""),
                    "risk_quality_score": radar_row.get("risk_quality_score", ""),
                    "risk_quality_label": brief_text(radar_row.get("risk_quality_label")),
                    "risk_quality_reason": brief_text(radar_row.get("risk_quality_reason")),
                    "action_grade_key": action_grade["key"],
                    "action_grade_label": action_grade["label"],
                    "action_grade_reason": action_grade["reason"],
                }
            )
        decision_brief_lanes.append(
            {
                "lane": lane_key,
                "label": brief_text(lane.get("label") or review_labels.get(lane_key, lane_key)),
                "count": review_counts[lane_key],
                "hint": brief_text(lane.get("hint")),
                "alert_plan": {
                    "rule": brief_text(alert_plan.get("rule") or lane.get("hint")),
                    "checklist": plan_checks[:5],
                },
                "items": item_summaries,
            }
        )
    quality_brief_lanes = quality_lane_brief_items(quality_summary)
    risk_quality_rows = [item for item in safe_payload if isinstance(item, dict)]
    risk_quality_avg = (
        sum(float(item.get("risk_quality_score") or 0.0) for item in risk_quality_rows) / len(risk_quality_rows)
        if risk_quality_rows
        else 0.0
    )
    risk_quality_counts: dict[str, int] = {}
    for item in risk_quality_rows:
        label = str(item.get("risk_quality_label") or "未分類")
        risk_quality_counts[label] = risk_quality_counts.get(label, 0) + 1
    risk_quality_top = sorted(
        risk_quality_rows,
        key=lambda item: (
            float(item.get("risk_quality_score") or 0.0),
            float(item.get("risk_adjusted_score") or item.get("entry_score") or 0.0),
            -int(item.get("rank") or 9999),
        ),
        reverse=True,
    )
    action_grade_summary = _daily_radar_action_grade_summary(risk_quality_rows)
    decision_brief = {
        "title": "每日雷達盤前 Brief",
        "trade_date": str(report_date),
        "run_id": str(run_id),
        "candidate_count": len(safe_payload),
        "handoff": {
            "primary_lane": brief_text(review_handoff.get("primary_lane")),
            "primary_action": brief_text(review_handoff.get("primary_action") or review_focus),
            "next_session_note": brief_text(review_handoff.get("next_session_note") or review_focus),
            "top_symbols": [brief_text(symbol) for symbol in review_handoff.get("top_symbols", [])]
            if isinstance(review_handoff.get("top_symbols"), list)
            else [],
        },
        "diagnostics": {
            "verdict": brief_text(diagnostics.get("verdict") if isinstance(diagnostics, dict) else "") or "等待診斷",
            "review_counts": review_counts,
            "alerts": [
                {
                    "label": brief_text(alert.get("label")),
                    "detail": brief_text(alert.get("detail")),
                    "severity": brief_text(alert.get("severity")),
                }
                for alert in diagnostic_alerts[:4]
                if isinstance(alert, dict)
            ],
        },
        "quality": {
            "verdict": brief_text(quality_summary.get("verdict")),
            "risk_level": brief_text(quality_summary.get("risk_level")),
            "quality_score": quality_summary.get("quality_score", 0),
            "primary_action": brief_text(quality_summary.get("primary_action")),
            "top_symbols": [
                brief_text(item.get("symbol"))
                for item in quality_summary.get("top_picks", [])
                if isinstance(item, dict) and brief_text(item.get("symbol"))
            ][:6]
            if isinstance(quality_summary.get("top_picks"), list)
            else [],
            "alerts": [
                {
                    "label": brief_text(alert.get("label")),
                    "detail": brief_text(alert.get("detail")),
                    "severity": brief_text(alert.get("severity")),
                }
                for alert in quality_summary.get("alerts", [])[:4]
                if isinstance(alert, dict)
            ]
            if isinstance(quality_summary.get("alerts"), list)
            else [],
            "lanes": quality_brief_lanes,
        },
        "risk_quality": {
            "average_score": round(risk_quality_avg, 2),
            "counts": risk_quality_counts,
            "top_symbols": [
                brief_text(item.get("symbol"))
                for item in risk_quality_top[:6]
                if brief_text(item.get("symbol"))
            ],
            "top_items": [
                {
                    "symbol": brief_text(item.get("symbol")),
                    "name": brief_text(item.get("name")),
                    "label": brief_text(item.get("risk_quality_label")),
                    "score": item.get("risk_quality_score", ""),
                    "reason": brief_text(item.get("risk_quality_reason")),
                }
                for item in risk_quality_top[:5]
                if brief_text(item.get("symbol"))
            ],
        },
        "action_grade": action_grade_summary,
        "trading_feedback": {
            "verdict": brief_text(safe_trading_feedback.get("verdict")) or "尚無交易回饋",
            "risk_level": brief_text(safe_trading_feedback.get("risk_level")) or "neutral",
            "primary_action": brief_text(safe_trading_feedback.get("primary_action")) or "先保留雷達紀錄。",
            "affected_symbols": [
                brief_text(item.get("symbol"))
                for item in safe_trading_feedback.get("symbols", [])
                if isinstance(item, dict) and brief_text(item.get("symbol"))
            ][:6]
            if isinstance(safe_trading_feedback.get("symbols"), list)
            else [],
            "alerts": [
                {
                    "label": brief_text(alert.get("label")),
                    "detail": brief_text(alert.get("detail")),
                    "severity": brief_text(alert.get("severity")),
                }
                for alert in safe_trading_feedback.get("alerts", [])[:4]
                if isinstance(alert, dict)
            ]
            if isinstance(safe_trading_feedback.get("alerts"), list)
            else [],
        },
        "threshold_sweep": {
            "status": brief_text(threshold_sweep.get("status")),
            "verdict": brief_text(threshold_sweep.get("verdict")),
            "risk_level": brief_text(threshold_sweep.get("risk_level")),
            "robustness_score": threshold_sweep.get("robustness_score", 0),
            "base_profile_label": brief_text(threshold_sweep.get("base_profile_label")),
            "stable_symbols": [
                brief_text(symbol)
                for symbol in threshold_sweep.get("stable_symbols", [])
                if brief_text(symbol)
            ][:6]
            if isinstance(threshold_sweep.get("stable_symbols"), list)
            else [],
            "fragile_symbols": [
                brief_text(symbol)
                for symbol in threshold_sweep.get("fragile_symbols", [])
                if brief_text(symbol)
            ][:6]
            if isinstance(threshold_sweep.get("fragile_symbols"), list)
            else [],
            "next_step": brief_text(threshold_sweep.get("next_step")),
        },
        "lanes": decision_brief_lanes,
        "outcomes": {
            "verdict": brief_text(safe_allocation_outcomes.get("verdict") or safe_allocation_outcomes.get("reason"))
            if isinstance(safe_allocation_outcomes, dict)
            else "",
            "evaluated_run_count": safe_allocation_outcomes.get("evaluated_run_count")
            if isinstance(safe_allocation_outcomes, dict)
            else "",
        },
    }
    json_payload["decision_brief"] = decision_brief

    brief_lines = [
        f"# {decision_brief['title']}",
        "",
        f"- 日期: {decision_brief['trade_date']}",
        f"- Run ID: {decision_brief['run_id']}",
        f"- 候選檔數: {decision_brief['candidate_count']}",
        f"- 主線: {decision_brief['handoff']['primary_action']}",
        f"- 交接: {decision_brief['handoff']['next_session_note']}",
        "- 行動分級: "
        + (
            " / ".join(f"{label} {count} 檔" for label, count in decision_brief["action_grade"]["counts"].items())
            if decision_brief["action_grade"]["counts"]
            else "-"
        ),
        "",
        "## 候選品質",
        "",
        f"- 判讀: {decision_brief['quality']['verdict']} ({brief_text(decision_brief['quality']['quality_score'])}/100)",
        f"- 行動: {decision_brief['quality']['primary_action']}",
        f"- 品質首選: {', '.join(decision_brief['quality']['top_symbols']) if decision_brief['quality']['top_symbols'] else '-'}",
        f"- 風險品質: {brief_text(decision_brief['risk_quality']['average_score'])}/100；首選 {', '.join(decision_brief['risk_quality']['top_symbols']) if decision_brief['risk_quality']['top_symbols'] else '-'}",
        "- 風險層: "
        + (
            " / ".join(f"{label} {count} 檔" for label, count in decision_brief["risk_quality"]["counts"].items())
            if decision_brief["risk_quality"]["counts"]
            else "-"
        ),
    ]
    grade_insert_at = brief_lines.index("## 候選品質") - 1
    for grade_key, grade_label in (("ready", "可下手"), ("watch", "只觀察"), ("avoid", "不碰")):
        symbols = decision_brief["action_grade"].get(f"{grade_key}_symbols", [])
        if symbols:
            brief_lines.insert(grade_insert_at, f"- {grade_label}: {', '.join(symbols)}")
            grade_insert_at += 1
    for alert in decision_brief["quality"]["alerts"]:
        brief_lines.append(f"- 品質警示: {alert['label']} - {alert['detail']}")
    if decision_brief["quality"]["lanes"]:
        brief_lines.extend(["", "### 品質行動線"])
        for lane in decision_brief["quality"]["lanes"]:
            symbols = ", ".join(lane["symbols"][:6]) if lane["symbols"] else "目前沒有項目"
            action = f"；{lane['action']}" if lane["action"] else ""
            gap_summary = f"；缺口 {'、'.join(lane['data_gap_scope_summary'])}" if lane["data_gap_scope_summary"] else ""
            brief_lines.append(f"- {lane['label']}: {lane['count']} 檔；{symbols}{action}{gap_summary}")
    brief_lines.extend([
        "",
        "## 交易回饋",
        "",
        f"- 判讀: {decision_brief['trading_feedback']['verdict']}",
        f"- 行動: {decision_brief['trading_feedback']['primary_action']}",
        f"- 影響標的: {', '.join(decision_brief['trading_feedback']['affected_symbols']) if decision_brief['trading_feedback']['affected_symbols'] else '-'}",
    ])
    for alert in decision_brief["trading_feedback"]["alerts"]:
        brief_lines.append(f"- 交易警示: {alert['label']} - {alert['detail']}")
    if decision_brief["threshold_sweep"]["status"] == "ok":
        stable = ", ".join(decision_brief["threshold_sweep"]["stable_symbols"]) if decision_brief["threshold_sweep"]["stable_symbols"] else "-"
        fragile = ", ".join(decision_brief["threshold_sweep"]["fragile_symbols"]) if decision_brief["threshold_sweep"]["fragile_symbols"] else "-"
        brief_lines.extend(
            [
                "",
                "## 門檻穩健性",
                "",
                f"- 判讀: {decision_brief['threshold_sweep']['verdict']} ({brief_text(decision_brief['threshold_sweep']['robustness_score'])}/100)",
                f"- 基準風格: {decision_brief['threshold_sweep']['base_profile_label']}",
                f"- 穩定標的: {stable}",
                f"- 門檻邊緣: {fragile}",
                f"- 下一步: {decision_brief['threshold_sweep']['next_step']}",
            ]
        )
    brief_lines.extend([
        "",
        "## 配置診斷",
        "",
        f"- 判讀: {decision_brief['diagnostics']['verdict']}",
        f"- 行動線: Preview {review_counts['preview']} / Trigger {review_counts['trigger']} / Risk {review_counts['risk']}",
    ])
    brief_lines.append("")
    for alert in decision_brief["diagnostics"]["alerts"]:
        brief_lines.append(f"- 警示: {alert['label']} - {alert['detail']}")
    brief_lines.extend(["", "## 行動線"])
    for lane in decision_brief["lanes"]:
        brief_lines.extend(
            [
                "",
                f"### {lane['label']} ({lane['count']} 檔)",
                "",
                f"- 提醒: {lane['alert_plan']['rule']}",
            ]
        )
        for check in lane["alert_plan"]["checklist"][:3]:
            brief_lines.append(f"- 檢查: {check}")
        if lane["items"]:
            for item in lane["items"]:
                item_parts = dedupe_brief_parts([item.get("reason", ""), item.get("alert_rule", "")])
                detail = "；".join(item_parts) if item_parts else "-"
                trigger_suffix = f"；觸發: {item['trigger_plan_short_text']}" if item.get("trigger_plan_short_text") else ""
                watchlist_suffix = (
                    f"；盯盤: {item['trigger_watchlist_note']}"
                    if item.get("trigger_watchlist_note") and not item.get("trigger_plan_short_text")
                    else ""
                )
                risk_quality_suffix = (
                    f"；風險層: {item['risk_quality_label']} {brief_text(item['risk_quality_score'])}"
                    if item.get("risk_quality_label")
                    else ""
                )
                action_grade_suffix = f"；分級: {item['action_grade_label']}" if item.get("action_grade_label") else ""
                status = f" [{item['allocation_status']}]" if item.get("allocation_status") else ""
                brief_lines.append(
                    f"- {item['symbol']} {item['name']}{status}: "
                    f"{detail}{trigger_suffix}{watchlist_suffix}{risk_quality_suffix}{action_grade_suffix}"
                )
        else:
            brief_lines.append("- 目前沒有項目")
    if decision_brief["outcomes"]["verdict"]:
        brief_lines.extend(
            [
                "",
                "## 配置驗證",
                "",
                f"- 結論: {decision_brief['outcomes']['verdict']}",
                f"- 驗證 run: {brief_text(decision_brief['outcomes']['evaluated_run_count'])}",
            ]
        )
    brief_lines.append("")
    brief_path.write_text("\n".join(brief_lines), encoding="utf-8")
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    html_export = export_daily_decision_summary_report(
        safe_payload,
        output_dir=output_dir,
        report_date=report_date,
        run_id=run_id,
        allocation_plan=safe_allocation_plan,
        allocation_outcomes=safe_allocation_outcomes,
        allocation_calibration=safe_allocation_calibration,
        threshold_sweep=threshold_sweep,
        review_lane_exports={**review_lane_urls, **quality_lane_urls, "decision_brief_md": f"/reports/{brief_name}"},
    )
    return {
        "files": {
            "json": json_name,
            "csv": csv_name,
            "decision_brief_md": brief_name,
            **review_lane_files,
            **quality_lane_files,
            **html_export["files"],
        },
        "urls": {
            "json": f"/reports/{json_name}",
            "csv": f"/reports/{csv_name}",
            "decision_brief_md": f"/reports/{brief_name}",
            **review_lane_urls,
            **quality_lane_urls,
            **html_export["urls"],
        },
        "data": {"decision_brief": decision_brief},
    }
