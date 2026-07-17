from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Mapping

from app.execution.promotion_readiness import PromotionReadinessService
from app.reports.daily_radar_health import DailyRadarDeploymentHealthService
from app.reports.eod_reconciliation import EndOfDayReconciliationSummaryService


STATUS_RANK = {"ok": 0, "warn": 1, "danger": 2}


def _status_worst(*statuses: str) -> str:
    return max((status or "warn" for status in statuses), key=lambda item: STATUS_RANK.get(item, 1))


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _component(
    *,
    key: str,
    label: str,
    status: str,
    value: Any,
    meta: str = "",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = str(status or "warn")
    if normalized not in STATUS_RANK:
        normalized = "warn"
    return {
        "key": key,
        "label": label,
        "status": normalized,
        "value": value,
        "meta": meta,
        "details": dict(details or {}),
    }


class OpsReadinessService:
    """Aggregate the daily operational gates needed before using the research cockpit."""

    def __init__(
        self,
        *,
        max_age_days: int = 3,
        warn_age_days: int = 1,
        min_rows: int = 5,
        min_entry_ready: int = 1,
        eod_limit: int = 100,
        min_observation_days: int = 3,
        promotion_gate_limit: int = 100,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.max_age_days = max(0, int(max_age_days))
        self.warn_age_days = max(0, int(warn_age_days))
        self.min_rows = max(1, int(min_rows))
        self.min_entry_ready = max(0, int(min_entry_ready))
        self.eod_limit = max(10, min(500, int(eod_limit)))
        self.min_observation_days = max(1, int(min_observation_days))
        self.promotion_gate_limit = max(1, int(promotion_gate_limit))
        self.env = env if env is not None else os.environ

    def build(
        self,
        repo: Any,
        *,
        as_of: date | None = None,
        advisor_name: str = "stub",
        strategy_name: str = "manual",
        strategy_version: str = "manual",
        target_phase: str = "paper",
    ) -> dict[str, Any]:
        target_day = as_of or datetime.now().date()
        normalized_phase = self._normalize_phase(target_phase)
        daily_radar = DailyRadarDeploymentHealthService(
            max_age_days=self.max_age_days,
            warn_age_days=self.warn_age_days,
            min_rows=self.min_rows,
            min_entry_ready=self.min_entry_ready,
        ).evaluate(repo, as_of=target_day)
        eod = EndOfDayReconciliationSummaryService(limit=self.eod_limit).build(repo, as_of=target_day)
        promotion = PromotionReadinessService(
            min_observation_days=self.min_observation_days,
            promotion_gate_limit=self.promotion_gate_limit,
        ).evaluate(
            repository=repo,
            advisor_name=advisor_name,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            target_phase=normalized_phase,
        ).to_dict()
        paper = promotion
        if normalized_phase != "paper":
            paper = PromotionReadinessService(
                min_observation_days=self.min_observation_days,
                promotion_gate_limit=self.promotion_gate_limit,
            ).evaluate(
                repository=repo,
                advisor_name=advisor_name,
                strategy_name=strategy_name,
                strategy_version=strategy_version,
                target_phase="paper",
            ).to_dict()

        components = [
            self._daily_radar_component(daily_radar),
            self._eod_component(eod),
            self._promotion_component(promotion),
            self._environment_component(paper_ready=bool(paper.get("accepted")), target_ready=bool(promotion.get("accepted")), target_phase=normalized_phase),
        ]
        status = _status_worst(*(str(item.get("status")) for item in components))
        failed = [item for item in components if str(item.get("status")) != "ok"]
        next_steps = self._next_steps(
            daily_radar=daily_radar,
            eod=eod,
            promotion=promotion,
            components=components,
        )
        return {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": target_day.isoformat(),
            "target_phase": normalized_phase,
            "status": status,
            "status_text": {"ok": "正常", "warn": "需留意", "danger": "需處理"}.get(status, "需留意"),
            "components": components,
            "blocking_reasons": [str(item.get("key")) for item in failed if str(item.get("status")) == "danger"],
            "warnings": [str(item.get("key")) for item in failed if str(item.get("status")) == "warn"],
            "next_steps": next_steps,
            "decision_brief": self._decision_brief(
                status=status,
                daily_radar=daily_radar,
                eod=eod,
                components=components,
                next_steps=next_steps,
            ),
            "metrics": {
                "daily_radar_rows": daily_radar.get("row_count"),
                "daily_radar_entry_ready": daily_radar.get("entry_ready_count"),
                "daily_radar_age_days": daily_radar.get("age_days"),
                "today_execution_count": (eod.get("metrics") or {}).get("today_execution_count"),
                "pending_preview_count": (eod.get("metrics") or {}).get("pending_preview_count"),
                "target_readiness": promotion.get("readiness"),
                "target_ready": bool(promotion.get("accepted")),
                "live_orders_enabled": _enabled(self.env.get("SHIOAJI_ENABLE_LIVE_ORDERS")),
                "codex_advisor_enabled": _enabled(self.env.get("CODEX_ADVISOR_ENABLED")),
            },
            "daily_radar": self._compact_daily_radar(daily_radar),
            "eod_summary": self._compact_eod(eod),
            "promotion_readiness": self._compact_promotion(promotion),
        }

    @staticmethod
    def _decision_brief(
        *,
        status: str,
        daily_radar: dict[str, Any],
        eod: dict[str, Any],
        components: list[dict[str, Any]],
        next_steps: list[str],
    ) -> dict[str, Any]:
        entry_ready = _safe_int(daily_radar.get("entry_ready_count"))
        watch_count = _safe_int(daily_radar.get("watch_trigger_count"))
        row_count = _safe_int(daily_radar.get("row_count"))
        watch_candidates = (
            daily_radar.get("watch_trigger_candidates")
            if isinstance(daily_radar.get("watch_trigger_candidates"), list)
            else []
        )
        focus_candidate = dict(watch_candidates[0]) if watch_candidates and isinstance(watch_candidates[0], dict) else None
        diagnosis = daily_radar.get("entry_ready_diagnosis") if isinstance(daily_radar.get("entry_ready_diagnosis"), dict) else {}
        eod_metrics = eod.get("metrics") if isinstance(eod.get("metrics"), dict) else {}
        danger_components = [
            str(item.get("label") or item.get("key") or "")
            for item in components
            if str(item.get("status")) == "danger"
        ]

        if status == "danger":
            state = "blocked"
            title = "今天先暫停建立新部位"
            summary = f"{('、'.join(danger_components[:2]) or '營運 Gate')} 尚未通過；先排除阻擋，再重新判讀雷達。"
            primary_action = {"key": "ops-readiness", "label": "查看阻擋項目"}
            decision_next_step = next_steps[0] if next_steps else "先排除營運阻擋，再重新整理總檢。"
        elif entry_ready > 0:
            state = "review_ready"
            title = f"有 {entry_ready} 檔可進入人工檢查"
            summary = "先檢查價格、風險、部位集中度與資料品質；通過後才能建立 Preview。"
            primary_action = {"key": "daily-radar", "label": "檢查可進場標的"}
            decision_next_step = "打開可進場標的逐檔人工檢查；未通過風險 Gate 前不要建立 Preview。"
        elif watch_count > 0:
            state = "wait_trigger"
            title = "今天先觀察，不追價"
            focus_label = ""
            if focus_candidate:
                focus_label = f"優先看 {focus_candidate.get('symbol') or '-'} {focus_candidate.get('name') or ''}".strip()
            wait_label = str((daily_radar.get("watch_trigger_summary") or {}).get("primary_wait_label") or "等待條件成立")
            summary = f"{focus_label or f'目前有 {watch_count} 檔待觸發'}；主要等待「{wait_label}」。"
            primary_action = {"key": "daily-radar", "label": "查看觸發條件"}
            decision_next_step = "確認觸發價、Entry Score 與 ATR 降溫條件，條件未成立前維持觀察。"
        else:
            state = "stand_down"
            title = "今天沒有合格進場標的"
            diagnosis_title = str(diagnosis.get("title") or "市場條件尚未通過")
            summary = f"{row_count} 檔候選中沒有可進場或待觸發標的；{diagnosis_title}。"
            primary_action = {"key": "daily-radar", "label": "查看阻擋原因"}
            decision_next_step = "今天不建立新 Preview；先確認阻擋原因與資料完整性，再等下一次雷達。"

        reconciliation = eod.get("reconciliation_runbook") if isinstance(eod.get("reconciliation_runbook"), dict) else {}
        return {
            "state": state,
            "title": title,
            "summary": summary,
            "primary_action": primary_action,
            "next_step": decision_next_step,
            "facts": [
                {"label": "可進場", "value": entry_ready, "unit": "檔"},
                {"label": "待觸發", "value": watch_count, "unit": "檔"},
                {"label": "資料健康", "value": eod_metrics.get("data_health_score"), "unit": "/100"},
                {
                    "label": "收盤對帳",
                    "value": "完成" if (eod.get("latest_reconciliation") or {}).get("matched") is True else "待完成",
                    "unit": "",
                },
            ],
            "focus_candidate": focus_candidate,
            "reconciliation_status": reconciliation.get("status"),
        }

    def _daily_radar_component(self, payload: dict[str, Any]) -> dict[str, Any]:
        sync_issues = payload.get("sync_issues") if isinstance(payload.get("sync_issues"), list) else []
        issue_labels = [
            str(label)
            for issue in sync_issues
            if isinstance(issue, dict)
            for label in (issue.get("reason_labels") if isinstance(issue.get("reason_labels"), list) else [])
            if label
        ]
        unique_issue_labels = list(dict.fromkeys(issue_labels))
        if len(unique_issue_labels) > 1:
            unique_issue_labels = [label for label in unique_issue_labels if label != "資料同步部分完成"] or unique_issue_labels
        meta = f"{payload.get('row_count', 0)} 檔候選，{payload.get('entry_ready_count', 0)} 檔可進場，距今 {payload.get('age_days', '-')} 天。"
        if unique_issue_labels:
            meta = f"{meta} 資料源：{'、'.join(unique_issue_labels[:3])}。"
        return _component(
            key="daily_radar",
            label="盤前每日雷達",
            status=str(payload.get("status") or "warn"),
            value=payload.get("status_text") or payload.get("status") or "未知",
            meta=meta,
            details={
                "blocking_reasons": payload.get("blocking_reasons") or [],
                "warnings": payload.get("warnings") or [],
                "sync_issues": sync_issues,
            },
        )

    def _eod_component(self, payload: dict[str, Any]) -> dict[str, Any]:
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        return _component(
            key="eod_summary",
            label="收盤營運摘要",
            status=str(payload.get("status") or "warn"),
            value=payload.get("status_text") or payload.get("status") or "未知",
            meta=f"今日送單 {metrics.get('today_execution_count', 0)}，待決策 Preview {metrics.get('pending_preview_count', 0)}。",
            details={
                "blocking_reasons": payload.get("blocking_reasons") or [],
                "warnings": payload.get("warnings") or [],
            },
        )

    def _promotion_component(self, payload: dict[str, Any]) -> dict[str, Any]:
        accepted = bool(payload.get("accepted"))
        return _component(
            key="promotion_readiness",
            label="升級 Gate",
            status="ok" if accepted else "danger",
            value=payload.get("readiness") or "blocked",
            meta=payload.get("recommendation") or "",
            details={
                "target_phase": payload.get("target_phase"),
                "blocking_reasons": payload.get("blocking_reasons") or [],
            },
        )

    def _environment_component(self, *, paper_ready: bool, target_ready: bool, target_phase: str) -> dict[str, Any]:
        live_enabled = _enabled(self.env.get("SHIOAJI_ENABLE_LIVE_ORDERS"))
        codex_enabled = _enabled(self.env.get("CODEX_ADVISOR_ENABLED"))
        problems: list[str] = []
        warnings: list[str] = []
        if live_enabled and (target_phase != "small_live" or not target_ready):
            problems.append("SHIOAJI_ENABLE_LIVE_ORDERS=1 早於小額 live readiness")
        if codex_enabled and not paper_ready:
            warnings.append("CODEX_ADVISOR_ENABLED=1 早於 paper readiness")
        status = "danger" if problems else "warn" if warnings else "ok"
        flags: list[str] = []
        if live_enabled:
            flags.append("live_orders")
        if codex_enabled:
            flags.append("codex_advisor")
        return _component(
            key="environment_safety",
            label="環境安全旗標",
            status=status,
            value=", ".join(flags) if flags else "安全預設",
            meta="；".join(problems + warnings) or "未提前啟用 live orders 或 CodexAdvisor。",
            details={"problems": problems, "warnings": warnings},
        )

    def _next_steps(
        self,
        *,
        daily_radar: dict[str, Any],
        eod: dict[str, Any],
        promotion: dict[str, Any],
        components: list[dict[str, Any]],
    ) -> list[str]:
        steps: list[str] = []
        if str(next((item.get("status") for item in components if item.get("key") == "daily_radar"), "")) != "ok":
            steps.extend(str(item) for item in (daily_radar.get("next_steps") or []) if item)
        if str(next((item.get("status") for item in components if item.get("key") == "eod_summary"), "")) != "ok":
            steps.extend(str(item) for item in (eod.get("next_steps") or []) if item)
        if str(next((item.get("status") for item in components if item.get("key") == "promotion_readiness"), "")) != "ok":
            recommendation = str(promotion.get("recommendation") or "").strip()
            if recommendation:
                steps.append(recommendation)
        env_component = next((item for item in components if item.get("key") == "environment_safety"), {})
        if str(env_component.get("status")) == "danger":
            steps.append("先關閉正式下單環境旗標，等小額 live readiness 通過後再依切換計畫啟用。")
        elif str(env_component.get("status")) == "warn":
            steps.append("先完成 paper readiness，再啟用 CodexAdvisor 觀察。")
        return list(dict.fromkeys(steps)) or ["營運總覽正常；可依今日雷達與人工 gate 繼續日常流程。"]

    @staticmethod
    def _compact_daily_radar(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "checked_at": payload.get("checked_at"),
            "as_of": payload.get("as_of"),
            "status": payload.get("status"),
            "status_text": payload.get("status_text"),
            "latest_run_id": payload.get("latest_run_id"),
            "latest_run_date": payload.get("latest_run_date"),
            "age_days": payload.get("age_days"),
            "row_count": payload.get("row_count"),
            "entry_ready_count": payload.get("entry_ready_count"),
            "checks": payload.get("checks") or [],
            "blocking_reasons": payload.get("blocking_reasons") or [],
            "warnings": payload.get("warnings") or [],
            "sync_issues": payload.get("sync_issues") or [],
            "next_steps": payload.get("next_steps") or [],
        }

    @staticmethod
    def _compact_eod(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "checked_at": payload.get("checked_at"),
            "as_of": payload.get("as_of"),
            "status": payload.get("status"),
            "status_text": payload.get("status_text"),
            "metrics": payload.get("metrics") or {},
            "components": payload.get("components") or [],
            "blocking_reasons": payload.get("blocking_reasons") or [],
            "warnings": payload.get("warnings") or [],
            "next_steps": payload.get("next_steps") or [],
            "latest_reconciliation": payload.get("latest_reconciliation"),
            "latest_promotion_gate": payload.get("latest_promotion_gate"),
            "reconciliation_runbook": payload.get("reconciliation_runbook") or {},
        }

    @staticmethod
    def _compact_promotion(payload: dict[str, Any]) -> dict[str, Any]:
        output = dict(payload)
        summary = output.get("observation_summary")
        if isinstance(summary, dict):
            compact_summary = dict(summary)
            recent = compact_summary.pop("recent_records", None)
            if isinstance(recent, list):
                compact_summary["recent_record_count"] = len(recent)
            output["observation_summary"] = compact_summary
        return output

    @staticmethod
    def _normalize_phase(value: str) -> str:
        text = str(value or "paper").strip().lower().replace("-", "_")
        if text in {"live", "small_live", "smalllive"}:
            return "small_live"
        return "paper"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
