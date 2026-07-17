from __future__ import annotations

import json
import threading
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway


DEFAULT_INTRADAY_QUOTE_SERVICE_STATUS_PATH = Path("data/cache/intraday_quote_service_status.json")
HEALTH_SEVERITY_RANK = {"idle": 0, "ok": 1, "warn": 2, "danger": 3}


def _status_file_path(path: str | Path | None = None) -> Path:
    return Path(path or DEFAULT_INTRADAY_QUOTE_SERVICE_STATUS_PATH).expanduser()


def _write_status_file(path: str | Path, payload: dict[str, Any]) -> Path:
    status_file = _status_file_path(path)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = status_file.with_suffix(status_file.suffix + ".tmp")
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp_file.replace(status_file)
    return status_file


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _snapshot_unavailable_payload(
    *,
    state: str,
    code: str,
    label: str,
    summary: str,
    path: Path,
    severity: str = "warn",
    error: str = "",
) -> dict[str, Any]:
    return {
        "state": state,
        "running": False,
        "symbols": [],
        "symbol_count": 0,
        "simulation": True,
        "include_bidask": True,
        "last_error": error,
        "market_session": {},
        "health": {
            "status": severity,
            "label": label,
            "summary": summary,
            "issues": [{"code": code, "severity": severity, "message": summary}],
            "next_actions": ["啟動控制台盤中行情服務，或確認部署服務有持續寫入 health snapshot。"],
        },
        "manager": None,
        "snapshot": {
            "path": str(path),
            "exists": path.exists(),
            "freshness_status": "missing" if not path.exists() else "invalid",
            "age_seconds": None,
        },
    }


def load_intraday_quote_service_status(
    path: str | Path | None = None,
    *,
    max_age_seconds: int = 300,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    status_file = _status_file_path(path)
    if not status_file.exists():
        return _snapshot_unavailable_payload(
            state="snapshot_missing",
            code="quote_status_snapshot_missing",
            label="尚無狀態",
            summary="尚未建立盤中行情服務 health snapshot。",
            path=status_file,
            severity="warn",
        )
    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _snapshot_unavailable_payload(
            state="snapshot_invalid",
            code="quote_status_snapshot_invalid",
            label="狀態檔異常",
            summary="盤中行情服務 health snapshot 無法解析。",
            path=status_file,
            severity="danger",
            error=str(exc),
        )
    if not isinstance(payload, dict):
        return _snapshot_unavailable_payload(
            state="snapshot_invalid",
            code="quote_status_snapshot_invalid",
            label="狀態檔異常",
            summary="盤中行情服務 health snapshot 格式不正確。",
            path=status_file,
            severity="danger",
            error="invalid_payload",
        )

    now = (now_fn or datetime.now)()
    recorded_at = _parse_iso_datetime(payload.get("snapshot_recorded_at"))
    age_seconds = int((now - recorded_at).total_seconds()) if recorded_at else None
    freshness_status = "fresh"
    if recorded_at is None:
        freshness_status = "unknown"
    elif age_seconds is not None and age_seconds > max(1, int(max_age_seconds)):
        freshness_status = "stale"

    output = dict(payload)
    snapshot = dict(output.get("snapshot") if isinstance(output.get("snapshot"), dict) else {})
    snapshot.update(
        {
            "path": str(status_file),
            "exists": True,
            "recorded_at": payload.get("snapshot_recorded_at"),
            "age_seconds": age_seconds,
            "freshness_status": freshness_status,
            "max_age_seconds": max(1, int(max_age_seconds)),
        }
    )
    output["snapshot"] = snapshot

    if freshness_status == "stale":
        health = dict(output.get("health") if isinstance(output.get("health"), dict) else {})
        issues = list(health.get("issues") if isinstance(health.get("issues"), list) else [])
        issues.append(
            {
                "code": "quote_status_snapshot_stale",
                "severity": "warn",
                "message": f"盤中行情服務 health snapshot 已超過 {max(1, int(max_age_seconds))} 秒未更新。",
            }
        )
        next_actions = list(health.get("next_actions") if isinstance(health.get("next_actions"), list) else [])
        next_actions.append("檢查盤中行情服務是否仍在執行，必要時重啟服務或控制台。")
        if str(health.get("status") or "idle") not in {"danger", "warn"}:
            health["status"] = "warn"
            health["label"] = "狀態過期"
            health["summary"] = "盤中行情服務 health snapshot 過期。"
        health["issues"] = issues
        health["next_actions"] = list(dict.fromkeys(str(item) for item in next_actions if item))
        output["health"] = health
    return output


def build_intraday_quote_service_review(
    path: str | Path | None = None,
    *,
    max_age_seconds: int = 3600,
    min_heartbeat_count: int = 1,
    min_quote_coverage: float = 0.8,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    payload = load_intraday_quote_service_status(path, max_age_seconds=max_age_seconds, now_fn=now_fn)
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    manager = payload.get("manager") if isinstance(payload.get("manager"), dict) else {}
    session = payload.get("session_summary") if isinstance(payload.get("session_summary"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []

    issues: list[dict[str, str]] = []
    next_actions: list[str] = []

    freshness = str(snapshot.get("freshness_status") or "unknown")
    if freshness in {"missing", "invalid"}:
        issues.append(
            {
                "code": f"snapshot_{freshness}",
                "severity": "danger",
                "message": "盤中行情服務狀態檔缺失或格式異常，無法完成盤後回顧。",
            }
        )
        next_actions.append("確認盤中行情服務有寫入 data/cache/intraday_quote_service_status.json。")
    elif freshness == "stale":
        issues.append(
            {
                "code": "snapshot_stale",
                "severity": "warn",
                "message": f"狀態檔超過 {max(1, int(max_age_seconds))} 秒未更新。",
            }
        )
        next_actions.append("檢查服務是否按預期收尾；若盤中應持續運作，請重啟服務。")

    health_status = str(health.get("status") or "").lower()
    if health_status in {"danger", "warn"}:
        issues.append(
            {
                "code": "latest_health_not_ok",
                "severity": "danger" if health_status == "danger" else "warn",
                "message": str(health.get("summary") or "最新行情服務 health 不是正常狀態。"),
            }
        )
    for issue in health.get("issues", []) if isinstance(health.get("issues"), list) else []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "").strip()
        if code:
            issues.append(
                {
                    "code": f"latest_{code}",
                    "severity": str(issue.get("severity") or "warn"),
                    "message": str(issue.get("message") or code),
                }
            )

    heartbeat_count = int(session.get("heartbeat_count") or 0)
    if heartbeat_count < max(1, int(min_heartbeat_count)):
        issues.append(
            {
                "code": "heartbeat_insufficient",
                "severity": "warn",
                "message": f"本輪只記錄 {heartbeat_count} 次行情服務 heartbeat。",
            }
        )
        next_actions.append("正式部署時讓行情服務跑滿盤中時段，再用盤後回顧判斷可靠度。")

    max_health_status = str(session.get("max_health_status") or "").lower()
    if max_health_status == "danger":
        issues.append({"code": "session_had_danger", "severity": "danger", "message": "本輪行情服務曾出現 danger。"})
        next_actions.append("回看服務 log、網路與 Shioaji 登入狀態，確認斷線或訂閱失敗原因。")
    elif max_health_status == "warn":
        issues.append({"code": "session_had_warn", "severity": "warn", "message": "本輪行情服務曾出現 warn。"})

    symbol_count = int(payload.get("symbol_count") or len(symbols))
    max_latest_count = int(session.get("max_latest_count") or 0)
    quote_coverage = float(session.get("max_quote_coverage") or (max_latest_count / symbol_count if symbol_count else 0.0))
    if symbol_count > 0 and max_latest_count <= 0:
        issues.append({"code": "no_quotes_during_session", "severity": "danger", "message": "本輪沒有留下任何串流報價證據。"})
        next_actions.append("下一輪先用少量代號測試訂閱，必要時暫時改用 Shioaji snapshot。")
    elif symbol_count > 0 and quote_coverage < max(0.0, min(1.0, float(min_quote_coverage))):
        issues.append(
            {
                "code": "quote_coverage_low",
                "severity": "warn",
                "message": f"本輪最高報價覆蓋率只有 {quote_coverage:.0%}。",
            }
        )
        next_actions.append("檢查未收到報價的代號是否停牌、流動性過低或訂閱失敗。")

    connection = str(manager.get("connection_status") or session.get("last_connection_status") or "")
    if connection == "disconnected":
        issues.append({"code": "connection_disconnected", "severity": "danger", "message": "最後行情連線狀態為 disconnected。"})
    elif connection == "reconnecting":
        issues.append({"code": "connection_reconnecting", "severity": "warn", "message": "最後行情連線狀態仍在重新連線。"})

    severity_rank = max((HEALTH_SEVERITY_RANK.get(str(issue.get("severity") or ""), 0) for issue in issues), default=0)
    if severity_rank >= HEALTH_SEVERITY_RANK["danger"]:
        status = "danger"
        label = "盤後需處理"
        summary = "盤中行情服務本輪可靠度不足，隔日觸發監控先保守。"
    elif severity_rank >= HEALTH_SEVERITY_RANK["warn"]:
        status = "warn"
        label = "盤後留意"
        summary = "盤中行情服務可用但仍有提醒，隔日開盤前建議再檢查。"
    else:
        status = "ok"
        label = "盤後正常"
        summary = "盤中行情服務本輪有可用 heartbeat 與報價證據。"

    if not next_actions:
        next_actions.append("保留目前服務設定；隔日開盤前再確認狀態檔 freshness。")

    evidence = {
        "heartbeat_count": heartbeat_count,
        "max_health_status": max_health_status or health_status or "unknown",
        "symbol_count": symbol_count,
        "max_latest_count": max_latest_count,
        "max_tick_count": int(session.get("max_tick_count") or 0),
        "max_bidask_count": int(session.get("max_bidask_count") or 0),
        "max_quote_coverage": round(quote_coverage, 4),
        "last_connection_status": connection,
        "snapshot_freshness": freshness,
    }
    return {
        "status": status,
        "label": label,
        "summary": summary,
        "state": payload.get("state"),
        "running": bool(payload.get("running")),
        "symbols": symbols,
        "symbol_count": symbol_count,
        "snapshot": snapshot,
        "health": health,
        "session_summary": session,
        "evidence": evidence,
        "issues": issues,
        "next_actions": list(dict.fromkeys(str(item) for item in next_actions if item)),
    }


def format_intraday_quote_service_review(review: dict[str, Any]) -> str:
    evidence = review.get("evidence") if isinstance(review.get("evidence"), dict) else {}
    lines = [
        f"[盤中行情服務盤後回顧] {review.get('label') or review.get('status')}",
        str(review.get("summary") or ""),
        (
            "證據: "
            f"heartbeat {evidence.get('heartbeat_count', 0)} 次 / "
            f"報價覆蓋 {float(evidence.get('max_quote_coverage') or 0.0):.0%} / "
            f"tick {evidence.get('max_tick_count', 0)} / "
            f"五檔 {evidence.get('max_bidask_count', 0)} / "
            f"snapshot {evidence.get('snapshot_freshness') or '-'}"
        ),
    ]
    issue_lines = [
        f"{issue.get('code')}: {issue.get('message')}"
        for issue in review.get("issues", [])
        if isinstance(issue, dict)
    ]
    if issue_lines:
        lines.append("問題: " + "；".join(issue_lines[:5]))
    actions = [str(item) for item in review.get("next_actions", []) if str(item).strip()]
    if actions:
        lines.append("下一步: " + "；".join(actions[:3]))
    symbols = [str(symbol) for symbol in review.get("symbols", []) if str(symbol)]
    if symbols:
        lines.append("代號: " + ",".join(symbols[:20]))
    return "\n".join(line for line in lines if line)


def run_intraday_quote_service(
    symbols: list[str] | tuple[str, ...],
    *,
    simulation: bool = True,
    include_bidask: bool = True,
    allow_outside_session: bool = False,
    status_path: str | Path | None = None,
    heartbeat_interval_seconds: float = 5.0,
    max_runtime_seconds: float | None = None,
    max_iterations: int | None = None,
    service_factory: Callable[[], "IntradayQuoteService"] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    service = service_factory() if service_factory is not None else IntradayQuoteService(status_path=status_path)
    interval = max(0.1, float(heartbeat_interval_seconds or 5.0))
    max_runtime = None if max_runtime_seconds is None or float(max_runtime_seconds) <= 0 else float(max_runtime_seconds)
    max_loops = None if max_iterations is None or int(max_iterations) < 0 else int(max_iterations)
    sleeper = sleep_fn or time_module.sleep
    started_monotonic = time_module.monotonic()
    iterations = 0
    exit_reason = "completed"
    latest_status = service.start(
        list(symbols),
        simulation=bool(simulation),
        include_bidask=bool(include_bidask),
        allow_outside_session=bool(allow_outside_session),
    )
    if str(latest_status.get("state") or "") == "blocked_outside_session":
        return {
            "status": latest_status,
            "stopped_status": latest_status,
            "iterations": iterations,
            "exit_reason": "blocked_outside_session",
            "status_path": str(_status_file_path(status_path)) if status_path else "",
        }

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                exit_reason = "stop_event"
                break
            if max_loops is not None and iterations >= max_loops:
                exit_reason = "max_iterations"
                break
            elapsed = time_module.monotonic() - started_monotonic
            if max_runtime is not None and elapsed >= max_runtime:
                exit_reason = "max_runtime"
                break
            sleep_for = interval
            if max_runtime is not None:
                sleep_for = min(sleep_for, max(0.0, max_runtime - elapsed))
            if sleep_for > 0:
                sleeper(sleep_for)
            latest_status = service.status()
            iterations += 1
    except KeyboardInterrupt:
        exit_reason = "keyboard_interrupt"
    finally:
        stopped_status = service.stop()

    return {
        "status": latest_status,
        "stopped_status": stopped_status,
        "iterations": iterations,
        "exit_reason": exit_reason,
        "status_path": str(_status_file_path(status_path)) if status_path else "",
    }


class IntradayQuoteService:
    """Process-local service for a reusable intraday Shioaji quote manager."""

    def __init__(
        self,
        *,
        gateway_factory: Callable[[bool], Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        status_path: str | Path | None = None,
    ):
        self._gateway_factory = gateway_factory or self._default_gateway_factory
        self._now_fn = now_fn or datetime.now
        self._status_path = _status_file_path(status_path) if status_path else None
        self._lock = threading.RLock()
        self._gateway: Any | None = None
        self._manager: Any | None = None
        self._state = "stopped"
        self._symbols: list[str] = []
        self._simulation = True
        self._include_bidask = True
        self._started_at = ""
        self._stopped_at = ""
        self._last_error = ""
        self._session_summary = self._new_session_summary()

    @staticmethod
    def _default_gateway_factory(simulation: bool) -> ShioajiGateway:
        return ShioajiGateway(ShioajiConfig(simulation=bool(simulation)))

    @staticmethod
    def _normalize_symbol(value: Any) -> str:
        return ShioajiGateway._normalize_symbol(str(value or ""))

    def start(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        simulation: bool = True,
        include_bidask: bool = True,
        allow_outside_session: bool = False,
    ) -> dict[str, Any]:
        clean_symbols = self._clean_symbols(symbols)
        if not clean_symbols:
            raise ValueError("symbols_required")
        with self._lock:
            session = self._market_session_payload()
            if not bool(allow_outside_session) and not bool(session["is_open"]):
                self._state = "blocked_outside_session"
                self._last_error = "outside_market_session"
                return self.status()
            if (
                self._manager is None
                or self._state not in {"running", "blocked_outside_session"}
                or bool(simulation) != self._simulation
                or bool(include_bidask) != self._include_bidask
            ):
                self._stop_locked(logout=True)
                self._gateway = self._gateway_factory(bool(simulation))
                self._manager = self._gateway.create_stock_streaming_quote_manager(include_bidask=bool(include_bidask))
                self._symbols = []
                self._started_at = datetime.now().isoformat(timespec="seconds")
                self._session_summary = self._new_session_summary()
            self._simulation = bool(simulation)
            self._include_bidask = bool(include_bidask)
            self._manager.start(clean_symbols)
            self._symbols = self._merge_symbols(self._symbols, clean_symbols)
            self._state = "running"
            self._last_error = ""
            return self.status()

    def stop(self, *, logout: bool = True) -> dict[str, Any]:
        with self._lock:
            self._stop_locked(logout=logout)
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            manager_status = self._manager.status() if self._manager is not None else None
            session = self._market_session_payload()
            health = self._health_payload(manager_status, session)
            session_summary = self._update_session_summary_locked(manager_status, health)
            payload = {
                "state": self._state,
                "running": self._state == "running",
                "simulation": self._simulation,
                "include_bidask": self._include_bidask,
                "symbols": list(self._symbols),
                "symbol_count": len(self._symbols),
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "last_error": self._last_error,
                "market_session": session,
                "health": health,
                "manager": manager_status,
                "session_summary": session_summary,
            }
            self._persist_status_locked(payload)
            return payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            items = self._manager.snapshot(self._symbols) if self._manager is not None else {}
            return {
                "status": self.status(),
                "items": items,
            }

    def _persist_status_locked(self, payload: dict[str, Any]) -> None:
        if self._status_path is None:
            return
        snapshot = dict(payload)
        snapshot["snapshot_recorded_at"] = self._now_fn().isoformat(timespec="seconds")
        snapshot["snapshot_source"] = "intraday_quote_service"
        snapshot["snapshot_schema_version"] = 1
        snapshot["snapshot"] = {
            "path": str(self._status_path),
            "exists": True,
            "freshness_status": "fresh",
        }
        _write_status_file(self._status_path, snapshot)

    def _stop_locked(self, *, logout: bool) -> None:
        if self._manager is not None:
            try:
                self._manager.stop()
            finally:
                self._manager = None
        if logout and self._gateway is not None:
            api = getattr(self._gateway, "api", None)
            if api is not None and hasattr(api, "logout"):
                try:
                    api.logout()
                except Exception:
                    pass
        self._gateway = None
        self._state = "stopped"
        self._stopped_at = datetime.now().isoformat(timespec="seconds")

    def _market_session_payload(self) -> dict[str, Any]:
        now = self._now_fn()
        is_open = ShioajiGateway._is_trading_session(now)
        return {
            "is_open": is_open,
            "status": "open" if is_open else "closed",
            "checked_at": now.isoformat(timespec="seconds"),
            "timezone": "local",
        }

    def _new_session_summary(self) -> dict[str, Any]:
        return {
            "heartbeat_count": 0,
            "max_health_status": "idle",
            "max_latest_count": 0,
            "max_tick_count": 0,
            "max_bidask_count": 0,
            "max_quote_coverage": 0.0,
            "last_connection_status": "",
            "issue_counts": {},
        }

    def _update_session_summary_locked(
        self,
        manager_status: dict[str, Any] | None,
        health: dict[str, Any],
    ) -> dict[str, Any]:
        summary = dict(self._session_summary)
        if self._state != "running":
            self._session_summary = summary
            return dict(summary)

        summary["heartbeat_count"] = int(summary.get("heartbeat_count") or 0) + 1
        current_health = str(health.get("status") or "idle").lower()
        previous_health = str(summary.get("max_health_status") or "idle").lower()
        if HEALTH_SEVERITY_RANK.get(current_health, 0) >= HEALTH_SEVERITY_RANK.get(previous_health, 0):
            summary["max_health_status"] = current_health

        issue_counts = dict(summary.get("issue_counts") if isinstance(summary.get("issue_counts"), dict) else {})
        for issue in health.get("issues", []) if isinstance(health.get("issues"), list) else []:
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code") or "").strip()
            if code:
                issue_counts[code] = int(issue_counts.get(code) or 0) + 1
        summary["issue_counts"] = issue_counts

        if isinstance(manager_status, dict):
            buffer = manager_status.get("buffer") if isinstance(manager_status.get("buffer"), dict) else {}
            latest_count = int(buffer.get("latest_count") or 0)
            tick_count = int(buffer.get("tick_count") or 0)
            bidask_count = int(buffer.get("bidask_count") or 0)
            symbol_count = max(1, len(self._symbols))
            summary["max_latest_count"] = max(int(summary.get("max_latest_count") or 0), latest_count)
            summary["max_tick_count"] = max(int(summary.get("max_tick_count") or 0), tick_count)
            summary["max_bidask_count"] = max(int(summary.get("max_bidask_count") or 0), bidask_count)
            summary["max_quote_coverage"] = round(
                max(float(summary.get("max_quote_coverage") or 0.0), latest_count / symbol_count),
                4,
            )
            summary["last_connection_status"] = str(manager_status.get("connection_status") or "")

        self._session_summary = summary
        return dict(summary)

    def _health_payload(self, manager_status: dict[str, Any] | None, session: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        next_actions: list[str] = []

        if self._state == "stopped":
            status = "idle"
            label = "未啟動"
            summary = "盤中行情服務目前未啟動。"
            if session.get("is_open") and self._symbols:
                status = "warn"
                label = "待啟動"
                issues.append({"code": "market_open_service_stopped", "severity": "warn", "message": "盤中服務未啟動。"})
                next_actions.append("若要盯觸發觀察標的，請啟動盤中行情服務。")
            return {"status": status, "label": label, "summary": summary, "issues": issues, "next_actions": next_actions}

        if self._state == "blocked_outside_session":
            return {
                "status": "warn",
                "label": "非盤中",
                "summary": "目前非台股一般交易時段，行情服務未啟動。",
                "issues": [{"code": "outside_market_session", "severity": "warn", "message": "非盤中預設不訂閱 Shioaji 行情。"}],
                "next_actions": ["等開盤後再啟動，或勾選允許非盤中測試。"],
            }

        if self._last_error:
            issues.append({"code": "last_error", "severity": "danger", "message": self._last_error})
            next_actions.append("檢查控制台 log 與 Shioaji 環境設定後再重啟行情服務。")

        if manager_status is None:
            issues.append({"code": "manager_missing", "severity": "danger", "message": "行情 manager 尚未建立。"})
            next_actions.append("停止後重新啟動盤中行情服務。")
        else:
            connection = str(manager_status.get("connection_status") or "")
            if connection == "disconnected":
                issues.append({"code": "quote_disconnected", "severity": "danger", "message": "Shioaji 行情連線已斷線。"})
                next_actions.append("停止並重新啟動行情服務；若持續斷線，檢查網路與 Shioaji 登入狀態。")
            elif connection == "reconnecting":
                issues.append({"code": "quote_reconnecting", "severity": "warn", "message": "Shioaji 行情正在重新連線。"})
                next_actions.append("短暫等待重連；若重連次數持續增加，改用 snapshot 或停止服務。")
            elif connection not in {"connected", ""}:
                issues.append({"code": "quote_connection_unknown", "severity": "warn", "message": f"行情連線狀態為 {connection}。"})

            subscribed_symbols = manager_status.get("subscribed_symbols") if isinstance(manager_status.get("subscribed_symbols"), list) else []
            missing_symbols = [symbol for symbol in self._symbols if symbol not in subscribed_symbols]
            if missing_symbols:
                issues.append(
                    {
                        "code": "missing_subscriptions",
                        "severity": "danger",
                        "message": f"尚未訂閱 {', '.join(missing_symbols[:5])}。",
                    }
                )
                next_actions.append("重新啟動行情服務，確認代號存在且未超過訂閱限制。")
            if self._symbols and int(manager_status.get("subscription_count") or 0) <= 0:
                issues.append({"code": "no_subscriptions", "severity": "danger", "message": "目前沒有有效訂閱路數。"})

            buffer = manager_status.get("buffer") if isinstance(manager_status.get("buffer"), dict) else {}
            latest_count = int(buffer.get("latest_count") or 0)
            tick_count = int(buffer.get("tick_count") or 0)
            bidask_count = int(buffer.get("bidask_count") or 0)
            if self._symbols and latest_count <= 0:
                issues.append({"code": "no_quotes_received", "severity": "warn", "message": "尚未收到任何串流報價。"})
                next_actions.append("等待第一筆 tick/bidask；若太久沒有報價，改用 snapshot 或檢查訂閱權限。")
            elif latest_count < len(self._symbols):
                issues.append(
                    {
                        "code": "partial_quotes",
                        "severity": "warn",
                        "message": f"已收到 {latest_count}/{len(self._symbols)} 檔報價。",
                    }
                )
            if self._include_bidask and self._symbols and bidask_count <= 0 and tick_count > 0:
                issues.append({"code": "no_bidask_received", "severity": "warn", "message": "已有 tick，但尚未收到五檔報價。"})

        if self._state == "running" and not session.get("is_open"):
            issues.append({"code": "running_outside_session", "severity": "warn", "message": "行情服務正在非交易時段執行。"})
            next_actions.append("若只是測試可保留；正式盯盤建議在開盤後啟動。")

        severity_order = {"danger": 3, "warn": 2}
        max_severity = max((severity_order.get(issue["severity"], 0) for issue in issues), default=0)
        if max_severity >= 3:
            status = "danger"
            label = "異常"
            summary = "盤中行情服務需要處理。"
        elif max_severity >= 2:
            status = "warn"
            label = "提醒"
            summary = "盤中行情服務可用，但有提醒事項。"
        else:
            status = "ok"
            label = "正常"
            summary = "盤中行情服務運作正常。"
        return {
            "status": status,
            "label": label,
            "summary": summary,
            "issues": issues,
            "next_actions": list(dict.fromkeys(next_actions)),
        }

    def _clean_symbols(self, symbols: list[str] | tuple[str, ...]) -> list[str]:
        clean: list[str] = []
        for symbol in symbols:
            code = self._normalize_symbol(symbol)
            if code and code not in clean:
                clean.append(code)
        return clean

    @staticmethod
    def _merge_symbols(existing: list[str], incoming: list[str]) -> list[str]:
        merged = list(existing)
        for symbol in incoming:
            if symbol and symbol not in merged:
                merged.append(symbol)
        return merged
