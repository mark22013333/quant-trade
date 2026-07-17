from __future__ import annotations

from datetime import date, datetime
from typing import Any


STATUS_RANK = {"ok": 0, "warn": 1, "danger": 2}
PHASE1_BACKGROUND_DATA_ALERTS = {
    "universe",
    "market_data_coverage",
    "candidates",
    "disposition_periods",
}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _same_day(value: Any, target: date) -> bool:
    parsed = _parse_datetime(value)
    return parsed.date() == target if parsed else False


def _preview_effective_status(row: dict[str, Any], *, now: datetime) -> str:
    status = str(row.get("status") or "").strip().lower()
    expires_at = _parse_datetime(row.get("expires_at"))
    if status in {"created", "decision_only"} and expires_at is not None and expires_at <= now:
        return "expired"
    return status


def _status_worst(*statuses: str) -> str:
    return max((status or "warn" for status in statuses), key=lambda item: STATUS_RANK.get(item, 1))


def _safe_list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []


class EndOfDayReconciliationSummaryService:
    def __init__(self, *, limit: int = 100) -> None:
        self.limit = max(10, min(500, int(limit)))

    def build(self, repo: Any, *, as_of: date | None = None) -> dict[str, Any]:
        target_day = as_of or datetime.now().date()
        executions = _safe_list(getattr(repo, "list_recent_trading_execution_records", lambda limit=20: [])(limit=self.limit))
        previews = _safe_list(getattr(repo, "list_recent_order_preview_records", lambda limit=20: [])(limit=self.limit))
        promotion_gates = _safe_list(getattr(repo, "list_recent_promotion_gate_records", lambda limit=20: [])(limit=self.limit))
        reconciliations = _safe_list(getattr(repo, "list_recent_reconciliation_records", lambda limit=20: [])(limit=self.limit))
        data_overview = self._data_overview(repo, target_day)
        now_utc = datetime.utcnow()

        today_executions = [row for row in executions if _same_day(row.get("created_at"), target_day)]
        today_previews = [row for row in previews if _same_day(row.get("created_at"), target_day)]
        effective_today_previews = [
            {**row, "effective_status": _preview_effective_status(row, now=now_utc)}
            for row in today_previews
        ]
        latest_reconciliation = reconciliations[0] if reconciliations else None
        latest_gate = promotion_gates[0] if promotion_gates else None

        components = [
            self._reconciliation_component(latest_reconciliation, target_day),
            self._execution_component(today_executions),
            self._preview_component(effective_today_previews),
            self._promotion_component(latest_gate),
            self._data_component(data_overview),
        ]
        status = _status_worst(*(str(component["status"]) for component in components))
        failed = [component for component in components if str(component.get("status")) != "ok"]
        blocking_reasons = [str(component["key"]) for component in failed if str(component.get("status")) == "danger"]
        warnings = [str(component["key"]) for component in failed if str(component.get("status")) == "warn"]
        metrics = self._metrics(
            today_executions=today_executions,
            today_previews=effective_today_previews,
            latest_reconciliation=latest_reconciliation,
            latest_gate=latest_gate,
            data_overview=data_overview,
        )
        reconciliation_runbook = self._reconciliation_runbook(
            latest_reconciliation=latest_reconciliation,
            target_day=target_day,
            today_executions=today_executions,
            today_previews=today_previews,
        )
        preview_queue = self._preview_queue(
            previews=effective_today_previews,
            repo=repo,
        )
        return {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": target_day.isoformat(),
            "status": status,
            "status_text": {"ok": "正常", "warn": "需留意", "danger": "需處理"}.get(status, "需留意"),
            "metrics": metrics,
            "components": components,
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "next_steps": self._next_steps(blocking_reasons=blocking_reasons, warnings=warnings),
            "reconciliation_runbook": reconciliation_runbook,
            "preview_queue": preview_queue,
            "latest_reconciliation": latest_reconciliation,
            "latest_promotion_gate": latest_gate,
            "today_executions": today_executions[:20],
            "today_previews": today_previews[:20],
        }

    def _data_overview(self, repo: Any, target_day: date) -> dict[str, Any]:
        method = getattr(repo, "get_data_health_overview", None)
        if not callable(method):
            return {}
        try:
            payload = method(as_of=target_day)
        except TypeError:
            payload = method()
        return payload if isinstance(payload, dict) else {}

    def _reconciliation_component(self, row: dict[str, Any] | None, target_day: date) -> dict[str, Any]:
        if not row:
            return {
                "key": "reconciliation",
                "label": "收盤對帳",
                "status": "warn",
                "value": "尚無紀錄",
                "meta": "請執行 reconcile 或匯入券商帳務後再檢查。",
            }
        matched = bool(row.get("matched"))
        checked_at = row.get("checked_at")
        same_day = _same_day(checked_at, target_day)
        status = "ok" if matched and same_day else "danger" if not matched else "warn"
        if not same_day and matched:
            meta = f"最近對帳 {checked_at or '-'}，不是今日。"
        elif not matched:
            meta = f"阻擋：{', '.join(row.get('blocking_reasons') or ['reconciliation_mismatch'])}"
        else:
            meta = f"最近對帳 {checked_at or '-'}。"
        return {
            "key": "reconciliation",
            "label": "收盤對帳",
            "status": status,
            "value": "一致" if matched else "不一致",
            "meta": meta,
        }

    def _execution_component(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        errors = [row for row in rows if str(row.get("status") or "").lower() == "error"]
        rejected = [row for row in rows if str(row.get("status") or "").lower() == "rejected" or not bool(row.get("accepted"))]
        live_rows = [row for row in rows if str(row.get("environment") or "").lower() == "live"]
        if errors:
            status = "danger"
            meta = f"{len(errors)} 筆 execution error，需查 broker/API 回應。"
        elif rejected:
            status = "warn"
            meta = f"{len(rejected)} 筆被 gate 拒絕，請確認是否符合預期。"
        else:
            status = "ok"
            meta = "今日沒有 execution error。"
        return {
            "key": "executions",
            "label": "今日送單",
            "status": status,
            "value": f"{len(rows)} 筆",
            "meta": f"{meta} live {len(live_rows)} / simulation {len(rows) - len(live_rows)}",
        }

    def _preview_component(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        pending = [row for row in rows if str(row.get("effective_status") or row.get("status") or "").lower() in {"created", "decision_only"}]
        expired = [row for row in rows if str(row.get("effective_status") or row.get("status") or "").lower() == "expired"]
        rejected = [row for row in rows if str(row.get("status") or "").lower() == "rejected"]
        status = "warn" if pending else "ok"
        meta = f"待決策 {len(pending)}，已過期 {len(expired)}，已拒絕 {len(rejected)}。"
        return {
            "key": "previews",
            "label": "Preview 決策",
            "status": status,
            "value": f"{len(rows)} 筆",
            "meta": meta,
        }

    def _promotion_component(self, row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {
                "key": "promotion_gate",
                "label": "Promotion Gate",
                "status": "warn",
                "value": "尚無紀錄",
                "meta": "小額 live 前應保留最近 gate 判斷。",
            }
        accepted = bool(row.get("accepted"))
        return {
            "key": "promotion_gate",
            "label": "Promotion Gate",
            "status": "ok" if accepted else "warn",
            "value": "通過" if accepted else "未通過",
            "meta": row.get("reason") or ", ".join(row.get("blocking_reasons") or []) or "-",
        }

    def _data_component(self, overview: dict[str, Any]) -> dict[str, Any]:
        status = str(overview.get("status") or "warn")
        score = overview.get("score")
        normalized = status if status in STATUS_RANK else "warn"
        daily_decision_ready = self._daily_decision_data_ready(overview)
        if normalized == "danger" and daily_decision_ready:
            normalized = "warn"
        return {
            "key": "data_health",
            "label": "資料健康度",
            "status": normalized,
            "value": f"{score}/100" if score is not None else "尚無總覽",
            "meta": (
                "日常雷達可用；全市場/維運資料仍有 "
                f"{len(overview.get('freshness_alerts') or [])} 個提醒"
                if status == "danger" and daily_decision_ready
                else f"{len(overview.get('freshness_alerts') or [])} 個 freshness 提醒"
            ),
        }

    @staticmethod
    def _daily_decision_data_ready(overview: dict[str, Any]) -> bool:
        latest_radar = overview.get("latest_radar") if isinstance(overview.get("latest_radar"), dict) else {}
        daily_bars = overview.get("daily_bars") if isinstance(overview.get("daily_bars"), dict) else {}
        external_indices = overview.get("external_indices") if isinstance(overview.get("external_indices"), dict) else {}
        market_context = (
            overview.get("market_context_coverage")
            if isinstance(overview.get("market_context_coverage"), dict)
            else {}
        )
        if str(latest_radar.get("status") or "").lower() != "ok":
            return False
        if str(daily_bars.get("status") or "").lower() != "ok":
            return False
        hard_components = {
            str(item.get("key") or "")
            for item in overview.get("components", [])
            if isinstance(item, dict) and str(item.get("status") or "").lower() == "danger"
        }
        if hard_components - PHASE1_BACKGROUND_DATA_ALERTS:
            return False
        if external_indices and str(external_indices.get("status") or "").lower() == "danger":
            return False
        if market_context and str(market_context.get("status") or "").lower() == "danger":
            return False
        return True

    def _metrics(
        self,
        *,
        today_executions: list[dict[str, Any]],
        today_previews: list[dict[str, Any]],
        latest_reconciliation: dict[str, Any] | None,
        latest_gate: dict[str, Any] | None,
        data_overview: dict[str, Any],
    ) -> dict[str, Any]:
        live_executions = [row for row in today_executions if str(row.get("environment") or "").lower() == "live"]
        rejected = [row for row in today_executions if str(row.get("status") or "").lower() == "rejected" or not bool(row.get("accepted"))]
        errors = [row for row in today_executions if str(row.get("status") or "").lower() == "error"]
        pending_previews = [
            row
            for row in today_previews
            if str(row.get("effective_status") or row.get("status") or "").lower() in {"created", "decision_only"}
        ]
        expired_previews = [
            row
            for row in today_previews
            if str(row.get("effective_status") or row.get("status") or "").lower() == "expired"
        ]
        return {
            "today_execution_count": len(today_executions),
            "today_live_execution_count": len(live_executions),
            "today_rejected_count": len(rejected),
            "today_error_count": len(errors),
            "today_preview_count": len(today_previews),
            "pending_preview_count": len(pending_previews),
            "expired_preview_count": len(expired_previews),
            "latest_reconciliation_matched": bool(latest_reconciliation.get("matched")) if latest_reconciliation else None,
            "latest_promotion_gate_accepted": bool(latest_gate.get("accepted")) if latest_gate else None,
            "data_health_status": data_overview.get("status"),
            "data_health_score": data_overview.get("score"),
        }

    @staticmethod
    def _preview_queue(*, previews: list[dict[str, Any]], repo: Any) -> dict[str, Any]:
        latest_rows = _safe_list(getattr(repo, "get_latest_daily_radar", lambda count=50: [])(count=50))
        latest_by_symbol = {
            str(row.get("symbol") or "").strip().upper(): row
            for row in latest_rows
            if str(row.get("symbol") or "").strip()
        }
        active_statuses = {"created", "decision_only"}
        active_rows = [row for row in previews if str(row.get("effective_status") or "").lower() in active_statuses]
        counts_by_symbol: dict[str, int] = {}
        for row in active_rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol:
                counts_by_symbol[symbol] = counts_by_symbol.get(symbol, 0) + 1

        items: list[dict[str, Any]] = []
        for row in active_rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            radar = latest_by_symbol.get(symbol) or {}
            radar_action = str(radar.get("action_label") or "").upper()
            duplicate_count = counts_by_symbol.get(symbol, 0)
            if duplicate_count > 1:
                state = "duplicate"
                title = f"同代號待決策 {duplicate_count} 筆"
                detail = "先保留一筆人工確認，其餘先檢查是否為重複建立。"
            elif radar_action == "ENTRY_READY":
                state = "ready"
                title = "最新雷達仍可人工檢查"
                detail = "先重新確認價格、部位與交易 Gate。"
            elif radar_action == "WATCH_WAIT_TRIGGER":
                state = "wait_trigger"
                title = "最新雷達仍在等待觸發"
                detail = "不要直接送單，先等觸發條件成立後重建 Preview。"
            else:
                state = "stale_radar"
                title = "最新雷達不再支持這筆 Preview"
                detail = "建議人工檢查原因；不應沿用舊 Preview 進入送單流程。"
            items.append(
                {
                    "preview_id": str(row.get("preview_id") or ""),
                    "symbol": symbol,
                    "strategy_name": str(row.get("strategy_name") or ""),
                    "created_at": row.get("created_at"),
                    "expires_at": row.get("expires_at"),
                    "state": state,
                    "title": title,
                    "detail": detail,
                    "radar_action": radar_action or "unknown",
                }
            )
        expired_count = sum(1 for row in previews if str(row.get("effective_status") or "").lower() == "expired")
        return {
            "active_count": len(active_rows),
            "expired_count": expired_count,
            "items": items[:12],
            "summary": (
                f"{len(active_rows)} 筆仍有效待決策 Preview。"
                if active_rows
                else f"目前沒有仍有效的待決策 Preview；今日已過期 {expired_count} 筆。"
            ),
        }

    def _reconciliation_runbook(
        self,
        *,
        latest_reconciliation: dict[str, Any] | None,
        target_day: date,
        today_executions: list[dict[str, Any]],
        today_previews: list[dict[str, Any]],
    ) -> dict[str, Any]:
        command_template = (
            ".venv/bin/python -m app.cli reconcile "
            "--expected-cash <system_cash> "
            "--actual-cash <broker_cash> "
            "--expected-positions <symbol:shares,...> "
            "--actual-positions <symbol:shares,...>"
        )
        required_inputs = [
            "系統現金餘額（expected_cash）",
            "券商現金餘額（actual_cash）",
            "系統部位股數，格式如 2330:1000,2317:2000",
            "券商部位股數，格式如 2330:1000,2317:2000",
            "未成交委託差異，若有則先在交易驗收區確認",
        ]
        checklist = [
            "先用 account-snapshot 或券商後台確認現金、庫存與可賣股數。",
            "把系統與券商的部位都換成股數，不要混用張數與股數。",
            "執行 reconcile 後再重新整理 eod-summary / ops-readiness。",
            "若出現 cash_diff_exceeded、position_diff 或 open_order_diff，先修正紀錄再升級到送單流程。",
        ]
        has_today_reconciliation = bool(
            latest_reconciliation
            and bool(latest_reconciliation.get("matched"))
            and _same_day(latest_reconciliation.get("checked_at"), target_day)
        )
        if has_today_reconciliation:
            return {
                "status": "ok",
                "reason": "today_reconciliation_matched",
                "title": "今日對帳已完成",
                "summary": "最新對帳紀錄與今日一致，收盤後只需保留稽核紀錄。",
                "command_template": command_template,
                "required_inputs": required_inputs,
                "checklist": checklist,
                "position_format": "2330:1000,2317:2000",
            }
        if latest_reconciliation and not bool(latest_reconciliation.get("matched")):
            reason = "reconciliation_mismatch"
            summary = "最近對帳不一致，先處理差異再把候選標的升級到 Preview 或送單。"
        elif latest_reconciliation:
            reason = "reconciliation_stale"
            summary = "最近對帳不是今日紀錄，收盤後請重新對帳，避免用舊部位判斷明日風險。"
        elif today_executions or today_previews:
            reason = "reconciliation_missing_after_activity"
            summary = "今日已有交易或 Preview 紀錄，但尚無對帳紀錄。"
        else:
            reason = "reconciliation_missing"
            summary = "目前尚無對帳紀錄；正式進 paper/live 節奏前應先建立收盤後對帳習慣。"
        return {
            "status": "action_required",
            "reason": reason,
            "title": "收盤後請執行對帳",
            "summary": summary,
            "command_template": command_template,
            "required_inputs": required_inputs,
            "checklist": checklist,
            "position_format": "2330:1000,2317:2000",
        }

    def _next_steps(self, *, blocking_reasons: list[str], warnings: list[str]) -> list[str]:
        steps: list[str] = []
        names = set(blocking_reasons) | set(warnings)
        if "reconciliation" in names:
            steps.append("收盤後執行 reconcile，確認券商現金、部位與未成交委託是否和系統一致。")
        if "executions" in names:
            steps.append("查看今日 execution error / rejected 的 reason，確認是預期 gate 阻擋還是 broker/API 異常。")
        if "previews" in names:
            steps.append("清理仍停在 created 的 Preview：確認、婉拒，或等過期後重建。")
        if "promotion_gate" in names:
            steps.append("補齊 paper 觀察、對帳與 promotion gate 紀錄，再評估小額 live。")
        if "data_health" in names:
            steps.append("先處理資料健康度 freshness 提醒，再使用每日雷達或下單流程。")
        return steps or ["收盤後摘要正常；保留稽核紀錄即可。"]
