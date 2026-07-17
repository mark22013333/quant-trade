from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.reports.daily_radar_health import _parse_date, _safe_float, _safe_int, _trigger_plan_for_row


TRIGGER_STATUS_RANK = {"empty": 0, "blocked": 0, "watch": 1, "near": 2, "ready": 3}
DEFAULT_TRIGGER_MONITOR_NOTIFICATION_STATE_PATH = Path("data/cache/daily_radar_trigger_monitor_state.json")
DEFAULT_TRIGGER_MONITOR_NOTIFICATION_COOLDOWN_MINUTES = 240
DEFAULT_TRIGGER_MONITOR_MAX_PRICE_AGE_SECONDS = 180
QUOTE_BUFFER_KEYS = {
    "quote_buffer_status",
    "quote_buffer_tick_count",
    "quote_buffer_bidask_count",
    "quote_buffer_retained_tick_count",
    "quote_buffer_retained_bidask_count",
    "quote_buffer_event_count",
    "quote_buffer_last_event",
    "quote_buffer",
    "quote_manager",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_price_symbol(value: Any) -> str:
    symbol = _clean_text(value).upper()
    for suffix in (".TW", ".TWO"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    return symbol


def _condition(
    key: str,
    label: str,
    status: str,
    *,
    observed: Any = "",
    target: Any = "",
    gap: Any = "",
    gap_pct: Any = "",
    detail: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "observed": observed,
        "target": target,
        "gap": gap,
        "gap_pct": gap_pct,
        "detail": detail,
    }


def _round_float(value: Any, digits: int = 2) -> float | None:
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _allocation_status(item: dict[str, Any]) -> str:
    if item.get("monitor_only") is True or _clean_text(item.get("allocation_lane")) == "monitor_only":
        return "盯盤備選"
    if item.get("allocated") is False or _clean_text(item.get("allocated")).lower() == "false":
        return "未配置"
    if "allocated" in item:
        return "已配置"
    return _clean_text(item.get("allocation_status")) or "未配置"


def _merge_non_empty(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if value in (None, ""):
            continue
        merged[key] = value
    return merged


def _copy_quote_buffer_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in QUOTE_BUFFER_KEYS:
        if key in source:
            target[key] = source[key]


def _quote_buffer_event(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "resp_code": value.get("resp_code"),
        "event_code": value.get("event_code"),
        "info": _clean_text(value.get("info")),
        "event": _clean_text(value.get("event")),
        "checked_at": _clean_text(value.get("checked_at")),
    }


def _quote_buffer_stats(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "latest_count": _safe_int(value.get("latest_count"), 0),
        "tick_count": _safe_int(value.get("tick_count"), 0),
        "bidask_count": _safe_int(value.get("bidask_count"), 0),
        "retained_tick_count": _safe_int(value.get("retained_tick_count"), 0),
        "retained_bidask_count": _safe_int(value.get("retained_bidask_count"), 0),
        "event_count": _safe_int(value.get("event_count"), 0),
        "symbols": [_clean_text(symbol) for symbol in value.get("symbols", []) if _clean_text(symbol)],
        "last_event": _quote_buffer_event(value.get("last_event")),
    }


def _quote_manager_status(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "state": _clean_text(value.get("state")),
        "connection_status": _clean_text(value.get("connection_status")),
        "started_at": _clean_text(value.get("started_at")),
        "stopped_at": _clean_text(value.get("stopped_at")),
        "subscribed_symbols": [_clean_text(symbol) for symbol in value.get("subscribed_symbols", []) if _clean_text(symbol)],
        "subscription_count": _safe_int(value.get("subscription_count"), 0),
        "subscription_call_count": _safe_int(value.get("subscription_call_count"), 0),
        "include_bidask": bool(value.get("include_bidask")),
        "event_callback_method": _clean_text(value.get("event_callback_method")),
        "last_heartbeat_at": _clean_text(value.get("last_heartbeat_at")),
        "last_reconnect_at": _clean_text(value.get("last_reconnect_at")),
        "last_resubscribe_at": _clean_text(value.get("last_resubscribe_at")),
        "reconnect_count": _safe_int(value.get("reconnect_count"), 0),
        "disconnect_count": _safe_int(value.get("disconnect_count"), 0),
        "resubscribe_count": _safe_int(value.get("resubscribe_count"), 0),
        "buffer": _quote_buffer_stats(value.get("buffer")),
    }


def _quote_status_summary(items: list[dict[str, Any]], price_snapshot: dict[str, Any]) -> dict[str, Any]:
    sources: dict[str, int] = {}
    freshness: dict[str, int] = {}
    streaming_count = 0
    fallback_count = 0
    tick_count = 0
    bidask_count = 0
    event_count = 0
    last_event: dict[str, Any] | None = None
    quote_manager: dict[str, Any] | None = None
    for item in items:
        source = _clean_text(item.get("price_source")) or "radar_snapshot"
        sources[source] = sources.get(source, 0) + 1
        freshness_status = _clean_text(item.get("price_freshness_status")) or "radar_snapshot"
        freshness[freshness_status] = freshness.get(freshness_status, 0) + 1
        buffer_status = _clean_text(item.get("quote_buffer_status"))
        if buffer_status.startswith("streaming"):
            streaming_count += 1
        if buffer_status == "fallback_snapshot" or source == "shioaji_streaming_fallback_snapshot":
            fallback_count += 1
        tick_count += _safe_int(item.get("quote_buffer_tick_count"), 0)
        bidask_count += _safe_int(item.get("quote_buffer_bidask_count"), 0)
        event_count += _safe_int(item.get("quote_buffer_event_count"), 0)
        event = item.get("quote_buffer_last_event")
        if isinstance(event, dict):
            last_event = _quote_buffer_event(event)
        manager = _quote_manager_status(item.get("quote_manager"))
        if manager:
            quote_manager = manager
    return {
        "enabled": bool(price_snapshot.get("enabled")),
        "source": _clean_text(price_snapshot.get("source")) or "radar_snapshot",
        "freshness_status": _clean_text(price_snapshot.get("freshness_status")) or "radar_snapshot",
        "updated_count": _safe_int(price_snapshot.get("updated_count"), 0),
        "fresh_count": _safe_int(price_snapshot.get("fresh_count"), 0),
        "stale_count": _safe_int(price_snapshot.get("stale_count"), 0),
        "unknown_count": _safe_int(price_snapshot.get("unknown_count"), 0),
        "streaming_count": streaming_count,
        "fallback_count": fallback_count,
        "tick_count": tick_count,
        "bidask_count": bidask_count,
        "event_count": event_count,
        "sources": sources,
        "freshness": freshness,
        "last_event": last_event,
        "quote_manager": quote_manager,
    }


def _call_latest_radar(repo: Any, *, count: int) -> list[dict[str, Any]]:
    method = getattr(repo, "get_latest_daily_radar", None)
    if not callable(method):
        return []
    try:
        rows = method(count=count)
    except TypeError:
        rows = method()
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def _call_allocation_plan(repo: Any, *, limit: int) -> dict[str, Any]:
    method = getattr(repo, "get_daily_radar_allocation_plan", None)
    if not callable(method):
        return {}
    try:
        payload = method(limit=limit)
    except TypeError:
        payload = method()
    return payload if isinstance(payload, dict) else {}


def _trigger_review_items(allocation_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    diagnostics = (
        allocation_plan.get("allocation_diagnostics")
        if isinstance(allocation_plan.get("allocation_diagnostics"), dict)
        else {}
    )
    review_queue = diagnostics.get("review_queue") if isinstance(diagnostics.get("review_queue"), dict) else {}
    lanes = review_queue.get("lanes") if isinstance(review_queue.get("lanes"), dict) else {}
    trigger_lane = lanes.get("trigger") if isinstance(lanes.get("trigger"), dict) else {}
    items = trigger_lane.get("items") if isinstance(trigger_lane.get("items"), list) else []
    output: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = _clean_text(item.get("symbol"))
        if symbol:
            output[symbol] = dict(item)
    return output


def _trigger_watchlist_note(item: dict[str, Any], plan: dict[str, Any], allocation_status: str) -> str:
    direct = _clean_text(item.get("trigger_watchlist_note"))
    if direct:
        return direct
    try:
        from app.db.repository import TradingRepository

        return TradingRepository._daily_radar_trigger_watchlist_note(
            item,
            plan,
            allocation_status=allocation_status,
        )
    except Exception:
        symbol = " ".join(part for part in [_clean_text(item.get("symbol")), _clean_text(item.get("name"))] if part)
        return " | ".join(part for part in [symbol, allocation_status] if part)


def _entry_condition(item: dict[str, Any], plan: dict[str, Any], near_entry_gap: float) -> tuple[dict[str, Any], bool, bool]:
    target = _safe_float(plan.get("target_entry_score"), 0.0)
    entry = _safe_float(item.get("entry_score"), 0.0)
    if target <= 0:
        return _condition("entry_score", "Entry Score", "missing", observed=entry, detail="尚無 Entry 目標"), True, False
    gap = round(target - entry, 2)
    if entry >= target:
        status = "met"
        detail = "Entry Score 已達標"
    elif gap <= near_entry_gap:
        status = "near"
        detail = f"Entry Score 差 {gap:.1f} 分"
    else:
        status = "wait"
        detail = f"Entry Score 仍差 {gap:.1f} 分"
    return (
        _condition(
            "entry_score",
            "Entry Score",
            status,
            observed=round(entry, 2),
            target=round(target, 2),
            gap=gap,
            detail=detail,
        ),
        status == "met",
        status == "near",
    )


def _price_conditions(
    item: dict[str, Any],
    plan: dict[str, Any],
    near_price_gap_pct: float,
) -> tuple[list[dict[str, Any]], bool, bool]:
    price = _safe_float(item.get("price"), 0.0)
    conditions: list[dict[str, Any]] = []
    met = False
    near = False
    breakout = _safe_float(plan.get("breakout_price"), 0.0)
    pullback = _safe_float(plan.get("pullback_price"), 0.0)

    if price <= 0:
        if breakout > 0 or pullback > 0:
            return (
                [
                    _condition(
                        "price",
                        "價格條件",
                        "missing",
                        target=round(breakout or pullback, 2),
                        detail="尚無有效最新價格",
                    )
                ],
                False,
                False,
            )
        return [], True, False

    if breakout > 0:
        gap = round(breakout - price, 2)
        gap_pct = (breakout - price) / price if price > 0 else None
        if price >= breakout:
            status = "met"
            detail = "已達突破參考價"
        elif gap_pct is not None and gap_pct <= near_price_gap_pct:
            status = "near"
            detail = f"距突破約 {gap_pct * 100:.1f}%"
        else:
            status = "wait"
            detail = f"距突破約 {(gap_pct or 0) * 100:.1f}%"
        met = met or status == "met"
        near = near or status == "near"
        conditions.append(
            _condition(
                "price_breakout",
                "突破價",
                status,
                observed=round(price, 2),
                target=round(breakout, 2),
                gap=gap,
                gap_pct=_round_float(gap_pct, 4),
                detail=detail,
            )
        )

    if pullback > 0:
        gap = round(price - pullback, 2)
        gap_pct = (price - pullback) / price if price > 0 else None
        if price <= pullback:
            status = "met"
            detail = "已達回測參考價"
        elif gap_pct is not None and gap_pct <= near_price_gap_pct:
            status = "near"
            detail = f"距回測約 {gap_pct * 100:.1f}%"
        else:
            status = "wait"
            detail = f"距回測約 {(gap_pct or 0) * 100:.1f}%"
        met = met or status == "met"
        near = near or status == "near"
        conditions.append(
            _condition(
                "price_pullback",
                "回測價",
                status,
                observed=round(price, 2),
                target=round(pullback, 2),
                gap=gap,
                gap_pct=_round_float(gap_pct, 4),
                detail=detail,
            )
        )

    return conditions, (met or not conditions), near


def _risk_condition(item: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any], bool, bool]:
    heat = _clean_text(item.get("atr_heat") or plan.get("atr_heat")).lower()
    if not heat:
        return _condition("atr_heat", "ATR 風險", "missing", detail="尚無 ATR 熱度"), True, False
    if heat == "hot":
        return (
            _condition("atr_heat", "ATR 風險", "wait", observed=heat, target="elevated/normal", detail="仍需降溫"),
            False,
            False,
        )
    if heat == "elevated":
        return (
            _condition("atr_heat", "ATR 風險", "met", observed=heat, target="不升高", detail="偏高但未過熱"),
            True,
            True,
        )
    return (
        _condition("atr_heat", "ATR 風險", "met", observed=heat, target="normal", detail="波動正常"),
        True,
        False,
    )


def _data_condition(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    quality = _clean_text(item.get("data_quality")).lower()
    if not quality or quality in {"fresh", "ok"}:
        return _condition("data_quality", "資料 freshness", "met", observed=quality or "unknown"), True
    return (
        _condition("data_quality", "資料 freshness", "wait", observed=quality, target="fresh", detail="觸發前需重新整理資料"),
        False,
    )


def _price_freshness_condition(item: dict[str, Any], max_age_seconds: int) -> tuple[dict[str, Any], bool]:
    source = _clean_text(item.get("price_source")) or "radar_snapshot"
    status = _clean_text(item.get("price_freshness_status")).lower()
    age = _safe_int(item.get("price_freshness_age_seconds"), 0)
    max_age = max(1, int(max_age_seconds or DEFAULT_TRIGGER_MONITOR_MAX_PRICE_AGE_SECONDS))
    if source == "radar_snapshot" and status not in {"stale", "unknown"}:
        return (
            _condition(
                "price_freshness",
                "報價 freshness",
                "met",
                observed="radar_snapshot",
                detail="使用雷達快照價；未啟用或未取得盤中報價",
            ),
            True,
        )
    if status == "fresh":
        return (
            _condition(
                "price_freshness",
                "報價 freshness",
                "met",
                observed=f"{age}s",
                target=f"<= {max_age}s",
                detail="盤中報價新鮮",
            ),
            True,
        )
    if status == "stale":
        return (
            _condition(
                "price_freshness",
                "報價 freshness",
                "wait",
                observed=f"{age}s",
                target=f"<= {max_age}s",
                detail="盤中報價過期，已降回雷達快照價",
            ),
            False,
        )
    if status == "unknown":
        return (
            _condition(
                "price_freshness",
                "報價 freshness",
                "wait",
                observed="unknown",
                target=f"<= {max_age}s",
                detail="盤中報價時間不可判讀，已降回雷達快照價",
            ),
            False,
        )
    return (
        _condition(
            "price_freshness",
            "報價 freshness",
            "met",
            observed=source,
            detail="報價來源未提供 freshness 狀態",
        ),
        True,
    )


def _blocker_condition(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
    blocker_count = _safe_int(item.get("blocker_count"), len(tags))
    feedback = _clean_text(item.get("trading_feedback_risk_level")).lower()
    blocked = blocker_count > 0 or bool(tags) or feedback in {"warn", "danger"}
    details = []
    if tags:
        details.append(" / ".join(_clean_text(tag) for tag in tags[:3]))
    if feedback in {"warn", "danger"}:
        details.append(f"交易回饋 {feedback}")
    return (
        _condition(
            "blockers",
            "阻擋條件",
            "blocked" if blocked else "met",
            observed=blocker_count or feedback or "clear",
            detail="；".join(part for part in details if part) or "無阻擋",
        ),
        not blocked,
    )


def _item_status(
    *,
    blocked_clear: bool,
    data_ready: bool,
    entry_met: bool,
    entry_near: bool,
    price_met: bool,
    price_near: bool,
    price_fresh: bool,
    risk_met: bool,
    risk_near: bool,
    readiness_score: float,
) -> str:
    if not blocked_clear:
        return "blocked"
    if data_ready and price_fresh and entry_met and price_met and risk_met:
        return "ready"
    near_score = readiness_score >= 65.0 and risk_met
    near_pair = risk_met and ((entry_near and (price_near or price_met)) or (entry_met and price_near))
    if data_ready and price_fresh and (near_pair or near_score):
        return "near"
    return "watch"


def _status_label(status: str) -> str:
    return {
        "ready": "已觸發",
        "near": "接近觸發",
        "watch": "等待中",
        "blocked": "先處理阻擋",
        "empty": "無觸發觀察",
    }.get(status, status)


def _next_action_for_status(status: str) -> str:
    return {
        "ready": "條件已成立，先人工檢查即時價格、部位與 promotion gate，再決定是否建立 Preview。",
        "near": "條件接近，排進開盤或盤中複查；未全部通過前先不建立 Preview。",
        "watch": "維持觀察，等待 Entry Score、價格或 ATR 條件更成熟。",
        "blocked": "先處理資料、阻擋標籤或交易回饋，再恢復觸發觀察。",
    }.get(status, "維持觀察。")


def format_daily_radar_trigger_monitor_message(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    status = _status_label(_clean_text(payload.get("status")) or "empty")
    lines = [
        f"[每日雷達觸發觀察] {status}",
        "Run: {run_id} · 日期 {run_date}".format(
            run_id=_clean_text(payload.get("latest_run_id")) or "-",
            run_date=_clean_text(payload.get("latest_run_date")) or "-",
        ),
        "已觸發 {ready} / 接近 {near} / 等待 {watch} / 阻擋 {blocked}".format(
            ready=int(summary.get("ready_count") or 0),
            near=int(summary.get("near_count") or 0),
            watch=int(summary.get("watch_count") or 0),
            blocked=int(summary.get("blocked_count") or 0),
        ),
    ]
    for item in [row for row in items if isinstance(row, dict)][:5]:
        symbol = " ".join(part for part in [_clean_text(item.get("symbol")), _clean_text(item.get("name"))] if part)
        label = _status_label(_clean_text(item.get("status")))
        note = _clean_text(item.get("trigger_watchlist_note"))
        action = _clean_text(item.get("next_action"))
        lines.append(f"- {symbol or '-'}: {label}" + (f"；{note}" if note else ""))
        if action:
            lines.append(f"  下一步: {action}")
    if not items:
        lines.append("目前沒有設觸發觀察標的。")
    return "\n".join(lines)


def daily_radar_trigger_monitor_should_notify(payload: dict[str, Any], notify_on: str = "ready") -> bool:
    mode = _clean_text(notify_on).lower()
    if mode in {"none", "never", "off"}:
        return False
    if mode == "always":
        return True
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    ready_count = int(summary.get("ready_count") or 0)
    near_count = int(summary.get("near_count") or 0)
    if mode == "near":
        return ready_count + near_count > 0
    return ready_count > 0


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _state_path(path: str | Path | None = None) -> Path:
    candidate = Path(path or DEFAULT_TRIGGER_MONITOR_NOTIFICATION_STATE_PATH).expanduser()
    return candidate


def _load_notification_state(path: str | Path | None) -> dict[str, Any]:
    state_file = _state_path(path)
    if not state_file.exists():
        return {"version": 1, "events": {}}
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "events": {}, "state_error": "invalid_json"}
    if not isinstance(payload, dict):
        return {"version": 1, "events": {}, "state_error": "invalid_payload"}
    events = payload.get("events")
    if not isinstance(events, dict):
        payload["events"] = {}
    payload.setdefault("version", 1)
    return payload


def _write_notification_state(path: str | Path | None, payload: dict[str, Any]) -> Path:
    state_file = _state_path(path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp_file.replace(state_file)
    return state_file


def _notification_items_for_mode(payload: dict[str, Any], notify_on: str) -> list[dict[str, Any]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    mode = _clean_text(notify_on).lower()
    output: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = _clean_text(item.get("status"))
        if mode == "ready" and status != "ready":
            continue
        if mode == "near" and status not in {"ready", "near"}:
            continue
        output.append(item)
    return output if mode != "always" else [item for item in items if isinstance(item, dict)]


def daily_radar_trigger_monitor_notification_event(payload: dict[str, Any], notify_on: str = "ready") -> dict[str, Any]:
    items = _notification_items_for_mode(payload, notify_on)
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    event_items = [
        {
            "symbol": _clean_text(item.get("symbol")),
            "status": _clean_text(item.get("status")),
            "readiness": _round_float(item.get("trigger_readiness_score"), 1),
            "entry": _round_float(item.get("entry_score"), 2),
            "price": _round_float(item.get("price"), 2),
        }
        for item in items
    ]
    event_items = [item for item in event_items if item["symbol"]]
    event_items.sort(key=lambda item: (item["symbol"], item["status"]))
    raw = {
        "notify_on": _clean_text(notify_on).lower() or "ready",
        "run_id": _clean_text(payload.get("latest_run_id")),
        "run_date": _clean_text(payload.get("latest_run_date")),
        "status": _clean_text(payload.get("status")),
        "summary": {
            "ready_count": int(summary.get("ready_count") or 0),
            "near_count": int(summary.get("near_count") or 0),
            "watch_count": int(summary.get("watch_count") or 0),
            "blocked_count": int(summary.get("blocked_count") or 0),
        },
        "items": event_items,
    }
    event_fingerprint = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    event_key = hashlib.sha256(event_fingerprint.encode("utf-8")).hexdigest()
    return {
        "event_key": event_key,
        "event_fingerprint": event_fingerprint,
        "notify_on": raw["notify_on"],
        "run_id": raw["run_id"],
        "run_date": raw["run_date"],
        "status": raw["status"],
        "symbols": [item["symbol"] for item in event_items],
        "items": event_items,
        "summary": raw["summary"],
    }


def daily_radar_trigger_monitor_notification_decision(
    payload: dict[str, Any],
    *,
    notify_on: str = "ready",
    state_path: str | Path | None = None,
    cooldown_minutes: int = DEFAULT_TRIGGER_MONITOR_NOTIFICATION_COOLDOWN_MINUTES,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    triggered = daily_radar_trigger_monitor_should_notify(payload, notify_on=notify_on)
    event = daily_radar_trigger_monitor_notification_event(payload, notify_on=notify_on)
    decision: dict[str, Any] = {
        "triggered": triggered,
        "should_send": triggered,
        "suppressed": False,
        "event_key": event["event_key"],
        "event_symbols": event["symbols"],
        "state_path": str(_state_path(state_path)),
        "cooldown_minutes": max(0, int(cooldown_minutes or 0)),
    }
    if not triggered:
        decision["should_send"] = False
        decision["skipped_reason"] = "status_below_notify_threshold"
        return decision
    if force or decision["cooldown_minutes"] <= 0:
        decision["force"] = bool(force)
        return decision

    state = _load_notification_state(state_path)
    if state.get("state_error"):
        decision["state_warning"] = _clean_text(state.get("state_error"))
        return decision
    previous = state.get("events", {}).get(event["event_key"]) if isinstance(state.get("events"), dict) else None
    if isinstance(previous, dict):
        last_sent_at = _parse_datetime(previous.get("last_sent_at"))
        checked_at = now or datetime.now()
        if last_sent_at is not None:
            next_allowed_at = last_sent_at + timedelta(minutes=int(decision["cooldown_minutes"]))
            if checked_at < next_allowed_at:
                decision.update(
                    {
                        "should_send": False,
                        "suppressed": True,
                        "skipped_reason": "cooldown_active",
                        "last_sent_at": last_sent_at.isoformat(timespec="seconds"),
                        "next_allowed_at": next_allowed_at.isoformat(timespec="seconds"),
                    }
                )
    return decision


def record_daily_radar_trigger_monitor_notification_sent(
    payload: dict[str, Any],
    *,
    notify_on: str = "ready",
    state_path: str | Path | None = None,
    sent_count: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    if int(sent_count or 0) <= 0:
        return {"recorded": False, "reason": "no_sent_notifications"}
    event = daily_radar_trigger_monitor_notification_event(payload, notify_on=notify_on)
    state = _load_notification_state(state_path)
    events = state.get("events") if isinstance(state.get("events"), dict) else {}
    checked_at = now or datetime.now()
    events[event["event_key"]] = {
        "last_sent_at": checked_at.isoformat(timespec="seconds"),
        "notify_on": event["notify_on"],
        "run_id": event["run_id"],
        "run_date": event["run_date"],
        "status": event["status"],
        "symbols": event["symbols"],
        "sent_count": int(sent_count or 0),
    }
    if len(events) > 200:
        ordered = sorted(
            events.items(),
            key=lambda item: _parse_datetime(item[1].get("last_sent_at")) or datetime.min,
            reverse=True,
        )
        events = dict(ordered[:200])
    state = {"version": 1, "updated_at": checked_at.isoformat(timespec="seconds"), "events": events}
    state_file = _write_notification_state(state_path, state)
    return {"recorded": True, "state_path": str(state_file), "event_key": event["event_key"]}


class DailyRadarTriggerMonitorService:
    def __init__(
        self,
        *,
        count: int = 50,
        limit: int = 10,
        near_entry_gap: float = 2.0,
        near_price_gap_pct: float = 0.02,
        max_price_age_seconds: int = DEFAULT_TRIGGER_MONITOR_MAX_PRICE_AGE_SECONDS,
        price_provider: Callable[[list[str]], dict[str, Any]] | None = None,
    ) -> None:
        self.count = max(1, int(count or 50))
        self.limit = max(1, int(limit or 10))
        self.near_entry_gap = max(0.0, float(near_entry_gap or 0.0))
        self.near_price_gap_pct = max(0.0, float(near_price_gap_pct or 0.0))
        self.max_price_age_seconds = max(1, int(max_price_age_seconds or DEFAULT_TRIGGER_MONITOR_MAX_PRICE_AGE_SECONDS))
        self.price_provider = price_provider

    def evaluate(self, repo: Any) -> dict[str, Any]:
        radar_rows = _call_latest_radar(repo, count=max(self.count, self.limit))
        allocation_plan = _call_allocation_plan(repo, limit=max(self.limit, 12))
        trigger_review = _trigger_review_items(allocation_plan)
        rows_by_symbol: dict[str, dict[str, Any]] = {}
        for row in radar_rows:
            symbol = _clean_text(row.get("symbol"))
            if symbol:
                rows_by_symbol[symbol] = row

        candidate_symbols = {
            symbol
            for symbol, row in rows_by_symbol.items()
            if _clean_text(row.get("action_label")).upper() == "WATCH_WAIT_TRIGGER"
        } | set(trigger_review)
        price_snapshot = self._load_price_snapshot(sorted(candidate_symbols))
        price_overrides = price_snapshot.get("prices") if isinstance(price_snapshot.get("prices"), dict) else {}

        candidates: list[dict[str, Any]] = []
        for symbol in sorted(candidate_symbols):
            base = rows_by_symbol.get(symbol, {})
            item = _merge_non_empty(base, trigger_review.get(symbol, {}))
            if _clean_text(item.get("action_label")).upper() not in {"", "WATCH_WAIT_TRIGGER"} and symbol not in trigger_review:
                continue
            item = self._apply_price_override(symbol, item, price_overrides.get(symbol))
            candidates.append(self._evaluate_item(item))

        items = sorted(
            candidates,
            key=lambda item: (
                -TRIGGER_STATUS_RANK.get(_clean_text(item.get("status")), 0),
                -_safe_float(item.get("trigger_readiness_score"), 0.0),
                _safe_int(item.get("rank"), 9999) or 9999,
                _clean_text(item.get("symbol")),
            ),
        )[: self.limit]
        summary = {
            "total_count": len(candidates),
            "ready_count": sum(1 for item in candidates if item.get("status") == "ready"),
            "near_count": sum(1 for item in candidates if item.get("status") == "near"),
            "watch_count": sum(1 for item in candidates if item.get("status") == "watch"),
            "blocked_count": sum(1 for item in candidates if item.get("status") == "blocked"),
        }
        status = "empty"
        if summary["ready_count"] > 0:
            status = "ready"
        elif summary["near_count"] > 0:
            status = "near"
        elif summary["watch_count"] > 0:
            status = "watch"
        elif summary["blocked_count"] > 0:
            status = "blocked"
        latest_row = radar_rows[0] if radar_rows else {}
        run_date = _parse_date(latest_row.get("run_date"))
        payload = {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "status_label": _status_label(status),
            "latest_run_id": _clean_text(latest_row.get("run_id")),
            "latest_run_date": run_date.isoformat() if run_date else _clean_text(latest_row.get("run_date")),
            "summary": summary,
            "thresholds": {
                "near_entry_gap": self.near_entry_gap,
                "near_price_gap_pct": self.near_price_gap_pct,
                "max_price_age_seconds": self.max_price_age_seconds,
            },
            "price_snapshot": {
                key: value
                for key, value in price_snapshot.items()
                if key != "prices"
            },
            "quote_status": _quote_status_summary(items, price_snapshot),
            "items": items,
        }
        payload["message"] = format_daily_radar_trigger_monitor_message(payload)
        return payload

    def _load_price_snapshot(self, symbols: list[str]) -> dict[str, Any]:
        loaded_at = datetime.now().isoformat(timespec="seconds")
        if not self.price_provider or not symbols:
            return {
                "source": "radar_snapshot",
                "enabled": False,
                "updated_count": 0,
                "symbols": [],
                "prices": {},
                "max_age_seconds": self.max_price_age_seconds,
                "fresh_count": 0,
                "stale_count": 0,
                "unknown_count": 0,
                "freshness_status": "radar_snapshot",
            }
        try:
            payload = self.price_provider(symbols)
        except Exception as exc:  # noqa: BLE001
            return {
                "source": "radar_snapshot",
                "enabled": True,
                "updated_count": 0,
                "symbols": [],
                "prices": {},
                "error": f"{type(exc).__name__}: {_clean_text(exc)}",
                "max_age_seconds": self.max_price_age_seconds,
                "fresh_count": 0,
                "stale_count": 0,
                "unknown_count": 0,
                "freshness_status": "fallback",
            }
        if not isinstance(payload, dict):
            return {
                "source": "radar_snapshot",
                "enabled": True,
                "updated_count": 0,
                "symbols": [],
                "prices": {},
                "error": "price_provider_returned_non_dict",
                "max_age_seconds": self.max_price_age_seconds,
                "fresh_count": 0,
                "stale_count": 0,
                "unknown_count": 0,
                "freshness_status": "fallback",
            }
        prices: dict[str, Any] = {}
        sources: set[str] = set()
        freshness_counts = {"fresh": 0, "stale": 0, "unknown": 0}
        for raw_symbol, item in payload.items():
            symbol = _normalize_price_symbol(raw_symbol)
            if symbol:
                normalized = dict(item) if isinstance(item, dict) else {"price": item}
                normalized.setdefault("source", "external_snapshot")
                normalized.setdefault("checked_at", loaded_at)
                freshness_status, age_seconds = self._price_freshness_status(normalized.get("checked_at"))
                normalized["price_freshness_status"] = freshness_status
                if age_seconds is not None:
                    normalized["price_freshness_age_seconds"] = age_seconds
                prices[symbol] = normalized
                source = _clean_text(normalized.get("source"))
                if source:
                    sources.add(source)
                freshness_counts[freshness_status] = freshness_counts.get(freshness_status, 0) + 1
        freshness_status = "fresh"
        if freshness_counts.get("stale", 0) > 0:
            freshness_status = "stale"
        elif freshness_counts.get("unknown", 0) > 0:
            freshness_status = "unknown"
        elif not prices:
            freshness_status = "empty"
        return {
            "source": sorted(sources)[0] if len(sources) == 1 else "external",
            "enabled": True,
            "updated_count": len(prices),
            "symbols": sorted(prices),
            "prices": prices,
            "checked_at": loaded_at,
            "max_age_seconds": self.max_price_age_seconds,
            "fresh_count": int(freshness_counts.get("fresh", 0)),
            "stale_count": int(freshness_counts.get("stale", 0)),
            "unknown_count": int(freshness_counts.get("unknown", 0)),
            "freshness_status": freshness_status,
        }

    def _apply_price_override(self, symbol: str, item: dict[str, Any], override: Any) -> dict[str, Any]:
        if override is None:
            merged = dict(item)
            merged.setdefault("price_source", "radar_snapshot")
            merged.setdefault("price_freshness_status", "radar_snapshot")
            return merged
        price = _safe_float(override.get("price") if isinstance(override, dict) else override, 0.0)
        if price <= 0:
            merged = dict(item)
            merged.setdefault("price_source", "radar_snapshot")
            merged.setdefault("price_freshness_status", "radar_snapshot")
            return merged
        merged = dict(item)
        radar_price = _safe_float(item.get("price"), 0.0)
        price_source = _clean_text(override.get("source")) if isinstance(override, dict) else "external_snapshot"
        price_checked_at = _clean_text(override.get("checked_at")) if isinstance(override, dict) else ""
        freshness_status = (
            _clean_text(override.get("price_freshness_status")).lower()
            if isinstance(override, dict)
            else "unknown"
        )
        age_seconds = _safe_int(override.get("price_freshness_age_seconds"), 0) if isinstance(override, dict) else 0
        if freshness_status in {"stale", "unknown"}:
            merged.setdefault("price_source", "radar_snapshot")
            merged["price_freshness_status"] = freshness_status
            merged["price_freshness_age_seconds"] = age_seconds
            merged["price_max_age_seconds"] = self.max_price_age_seconds
            merged["stale_intraday_price"] = float(price)
            merged["stale_price_source"] = price_source
            merged["stale_price_checked_at"] = price_checked_at
            if isinstance(override, dict):
                _copy_quote_buffer_fields(merged, override)
            return merged
        merged["radar_price"] = radar_price
        merged["price"] = float(price)
        merged["intraday_price"] = float(price)
        merged["price_source"] = price_source
        merged["price_checked_at"] = price_checked_at
        merged["price_freshness_status"] = freshness_status or "fresh"
        merged["price_freshness_age_seconds"] = age_seconds
        merged["price_max_age_seconds"] = self.max_price_age_seconds
        if radar_price > 0:
            merged["price_change_from_radar_pct"] = (float(price) / radar_price) - 1.0
        if isinstance(override, dict):
            _copy_quote_buffer_fields(merged, override)
        return merged

    def _price_freshness_status(self, checked_at: Any) -> tuple[str, int | None]:
        checked_at_dt = _parse_datetime(checked_at)
        if checked_at_dt is None:
            return "unknown", None
        now = datetime.now(checked_at_dt.tzinfo) if checked_at_dt.tzinfo is not None else datetime.now()
        age_seconds = max(0, int((now - checked_at_dt).total_seconds()))
        if age_seconds > self.max_price_age_seconds:
            return "stale", age_seconds
        return "fresh", age_seconds

    def _evaluate_item(self, item: dict[str, Any]) -> dict[str, Any]:
        plan = item.get("trigger_plan") if isinstance(item.get("trigger_plan"), dict) else _trigger_plan_for_row(item)
        entry_condition, entry_met, entry_near = _entry_condition(item, plan, self.near_entry_gap)
        price_freshness_condition, price_fresh = _price_freshness_condition(item, self.max_price_age_seconds)
        price_conditions, price_met, price_near = _price_conditions(item, plan, self.near_price_gap_pct)
        risk_condition, risk_met, risk_near = _risk_condition(item, plan)
        data_condition, data_ready = _data_condition(item)
        blocker_condition, blocked_clear = _blocker_condition(item)
        readiness_score = _safe_float(plan.get("trigger_readiness_score") or item.get("trigger_readiness_score"), 0.0)
        status = _item_status(
            blocked_clear=blocked_clear,
            data_ready=data_ready,
            entry_met=entry_met,
            entry_near=entry_near,
            price_met=price_met,
            price_near=price_near,
            price_fresh=price_fresh,
            risk_met=risk_met,
            risk_near=risk_near,
            readiness_score=readiness_score,
        )
        allocation_status = _allocation_status(item)
        conditions = [entry_condition, price_freshness_condition, *price_conditions, risk_condition, data_condition, blocker_condition]
        return {
            "symbol": _clean_text(item.get("symbol")),
            "name": _clean_text(item.get("name")),
            "rank": _safe_int(item.get("rank"), 0),
            "status": status,
            "status_label": _status_label(status),
            "next_action": _next_action_for_status(status),
            "allocation_status": allocation_status,
            "allocated": item.get("allocated", ""),
            "monitor_only": bool(item.get("monitor_only")),
            "allocation_lane": _clean_text(item.get("allocation_lane")),
            "entry_score": _round_float(item.get("entry_score"), 2),
            "risk_score": _round_float(item.get("risk_score"), 4),
            "risk_adjusted_score": _round_float(item.get("risk_adjusted_score"), 2),
            "priority_score": _round_float(item.get("priority_score") or item.get("risk_adjusted_score"), 2),
            "price": _round_float(item.get("price"), 2),
            "radar_price": _round_float(item.get("radar_price"), 2),
            "intraday_price": _round_float(item.get("intraday_price"), 2),
            "price_source": _clean_text(item.get("price_source")) or "radar_snapshot",
            "price_checked_at": _clean_text(item.get("price_checked_at")),
            "price_freshness_status": _clean_text(item.get("price_freshness_status")) or "radar_snapshot",
            "price_freshness_age_seconds": _round_float(item.get("price_freshness_age_seconds"), 0),
            "price_max_age_seconds": self.max_price_age_seconds,
            "price_change_from_radar_pct": _round_float(item.get("price_change_from_radar_pct"), 4),
            "stale_intraday_price": _round_float(item.get("stale_intraday_price"), 2),
            "stale_price_source": _clean_text(item.get("stale_price_source")),
            "stale_price_checked_at": _clean_text(item.get("stale_price_checked_at")),
            "quote_buffer_status": _clean_text(item.get("quote_buffer_status")),
            "quote_buffer_tick_count": _safe_int(item.get("quote_buffer_tick_count"), 0),
            "quote_buffer_bidask_count": _safe_int(item.get("quote_buffer_bidask_count"), 0),
            "quote_buffer_retained_tick_count": _safe_int(item.get("quote_buffer_retained_tick_count"), 0),
            "quote_buffer_retained_bidask_count": _safe_int(item.get("quote_buffer_retained_bidask_count"), 0),
            "quote_buffer_event_count": _safe_int(item.get("quote_buffer_event_count"), 0),
            "quote_buffer_last_event": _quote_buffer_event(item.get("quote_buffer_last_event")),
            "quote_buffer": _quote_buffer_stats(item.get("quote_buffer")),
            "quote_manager": _quote_manager_status(item.get("quote_manager")),
            "atr_heat": _clean_text(item.get("atr_heat") or plan.get("atr_heat")),
            "data_quality": _clean_text(item.get("data_quality")),
            "trigger_readiness_score": _round_float(readiness_score, 1),
            "trigger_readiness_label": _clean_text(plan.get("trigger_readiness_label")),
            "trigger_plan": plan,
            "trigger_watchlist_note": _trigger_watchlist_note(item, plan, allocation_status),
            "conditions": conditions,
        }
