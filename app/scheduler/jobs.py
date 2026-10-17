from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.alerts.notifier_base import Notifier
from app.alerts.notifier_line import LineNotifyNotifier
from app.alerts.notifier_telegram import TelegramNotifier
from app.alerts.signal_engine import compute_daily_signal_suggestions
from app.data.sync_service import SyncService
from app.db.repository import TradingRepository
from app.pipeline.daily_radar import DailyRadarRequest, DailyRadarService


def build_notifiers(channels: tuple[str, ...] = ("telegram",)) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if "telegram" in channels:
        tg = TelegramNotifier()
        if tg.bot_token and tg.chat_id:
            notifiers.append(tg)
    if "line" in channels:
        line = LineNotifyNotifier()
        if line.token:
            notifiers.append(line)
    return notifiers


def _resolve_trade_date(trade_date_mode: str = "T") -> date:
    mode = str(trade_date_mode or "T").strip().upper()
    today = datetime.now().date()
    if mode in {"T-1", "T_MINUS_1", "PREV"}:
        day = today - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day
    return today


def _parse_min_timestamp(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_symbol_list(raw: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    if raw is None:
        return None
    values = raw if isinstance(raw, (list, tuple)) else str(raw or "").replace("\n", ",").split(",")
    symbols = [str(item or "").strip().upper() for item in values if str(item or "").strip()]
    return symbols or None


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _format_compact_number(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except Exception:
        return ""
    return f"{number:.{digits}f}"


def _normalize_notify_style(value: str | None) -> str:
    style = str(value or "premarket").strip().lower().replace("-", "_")
    aliases = {
        "pre": "premarket",
        "morning": "premarket",
        "盤前": "premarket",
        "day": "intraday",
        "midday": "intraday",
        "盤中": "intraday",
        "after": "postmarket",
        "post": "postmarket",
        "eod": "postmarket",
        "盤後": "postmarket",
    }
    style = aliases.get(style, style)
    if style not in {"premarket", "intraday", "postmarket"}:
        raise ValueError("notify_style must be one of: premarket, intraday, postmarket")
    return style


def _trigger_short_text(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    direct = _compact_text(item.get("trigger_plan_short_text") or item.get("trigger_plan_metrics_text"))
    if direct:
        return direct
    plan = item.get("trigger_plan") if isinstance(item.get("trigger_plan"), dict) else {}
    if plan:
        parts = []
        target = _format_compact_number(plan.get("target_entry_score"), 0)
        breakout = _format_compact_number(plan.get("breakout_price"), 2)
        pullback = _format_compact_number(plan.get("pullback_price"), 2)
        if target:
            parts.append(f"Entry>={target}")
        if breakout:
            parts.append(f"突破{breakout}")
        if pullback:
            parts.append(f"回測{pullback}")
        if parts:
            return " / ".join(parts)
    text = _compact_text(item.get("trigger_plan_text"))
    if "；" in text:
        text = text.split("；", 1)[0]
    return text


def format_daily_radar_brief_message(
    decision_brief: dict[str, Any],
    export_urls: dict[str, str] | None = None,
    *,
    notify_style: str = "premarket",
) -> str:
    if not isinstance(decision_brief, dict) or not decision_brief:
        return "[每日雷達] 盤前 Brief 尚未產生"
    style = _normalize_notify_style(notify_style)
    handoff = decision_brief.get("handoff") if isinstance(decision_brief.get("handoff"), dict) else {}
    diagnostics = decision_brief.get("diagnostics") if isinstance(decision_brief.get("diagnostics"), dict) else {}
    quality = decision_brief.get("quality") if isinstance(decision_brief.get("quality"), dict) else {}
    trading_feedback = (
        decision_brief.get("trading_feedback") if isinstance(decision_brief.get("trading_feedback"), dict) else {}
    )
    outcomes = decision_brief.get("outcomes") if isinstance(decision_brief.get("outcomes"), dict) else {}
    threshold_sweep = decision_brief.get("threshold_sweep") if isinstance(decision_brief.get("threshold_sweep"), dict) else {}
    action_grade = decision_brief.get("action_grade") if isinstance(decision_brief.get("action_grade"), dict) else {}
    counts = diagnostics.get("review_counts") if isinstance(diagnostics.get("review_counts"), dict) else {}
    quality_lanes = quality.get("lanes") if isinstance(quality.get("lanes"), list) else []
    lanes = decision_brief.get("lanes") if isinstance(decision_brief.get("lanes"), list) else []
    urls = export_urls if isinstance(export_urls, dict) else {}

    style_label = {"premarket": "盤前 Brief", "intraday": "盤中快訊", "postmarket": "盤後回顧"}[style]
    lines = [
        f"[每日雷達] {_compact_text(decision_brief.get('trade_date')) or '今日'} {style_label}",
        f"Run: {_compact_text(decision_brief.get('run_id')) or '-'} · 候選 {_compact_text(decision_brief.get('candidate_count')) or '0'} 檔",
    ]
    primary_action = _compact_text(handoff.get("primary_action"))
    next_note = _compact_text(handoff.get("next_session_note"))
    top_symbols = handoff.get("top_symbols") if isinstance(handoff.get("top_symbols"), list) else []
    if primary_action:
        lines.append(f"主線: {primary_action}")
    if next_note:
        lines.append(f"交接: {next_note}")
    if top_symbols:
        lines.append(f"代表: {', '.join(_compact_text(symbol) for symbol in top_symbols[:6] if _compact_text(symbol))}")
    grade_counts = action_grade.get("counts") if isinstance(action_grade.get("counts"), dict) else {}
    if grade_counts:
        lines.append(
            "分級: "
            + " / ".join(
                f"{_compact_text(label)} {_safe_int(count)}"
                for label, count in grade_counts.items()
                if _compact_text(label)
            )
        )
    ready_symbols = action_grade.get("ready_symbols") if isinstance(action_grade.get("ready_symbols"), list) else []
    watch_symbols = action_grade.get("watch_symbols") if isinstance(action_grade.get("watch_symbols"), list) else []
    avoid_symbols = action_grade.get("avoid_symbols") if isinstance(action_grade.get("avoid_symbols"), list) else []
    if ready_symbols:
        lines.append(f"可下手: {', '.join(_compact_text(symbol) for symbol in ready_symbols[:5] if _compact_text(symbol))}")
    if watch_symbols and style != "postmarket":
        lines.append(f"只觀察: {', '.join(_compact_text(symbol) for symbol in watch_symbols[:5] if _compact_text(symbol))}")
    if avoid_symbols and style == "premarket":
        lines.append(f"不碰: {', '.join(_compact_text(symbol) for symbol in avoid_symbols[:5] if _compact_text(symbol))}")

    if style == "intraday":
        if counts:
            lines.append(
                "行動線: Preview {preview} / Trigger {trigger} / Risk {risk}".format(
                    preview=_safe_int(counts.get("preview")),
                    trigger=_safe_int(counts.get("trigger")),
                    risk=_safe_int(counts.get("risk")),
                )
            )
        for lane in [item for item in lanes if isinstance(item, dict)][:2]:
            lane_items = lane.get("items") if isinstance(lane.get("items"), list) else []
            symbols = []
            for item in [row for row in lane_items if isinstance(row, dict)][:3]:
                symbol = _compact_text(item.get("symbol"))
                trigger_short = _trigger_short_text(item)
                symbols.append(f"{symbol} ({trigger_short})" if trigger_short and symbol else symbol)
            symbols = [item for item in symbols if item]
            suffix = f": {', '.join(symbols)}" if symbols else ""
            lines.append(f"- {_compact_text(lane.get('label')) or _compact_text(lane.get('lane'))}: {_safe_int(lane.get('count'))} 檔{suffix}")
        if _compact_text(urls.get("html_summary")):
            lines.append(f"HTML: {_compact_text(urls.get('html_summary'))}")
        return "\n".join(lines)

    quality_verdict = _compact_text(quality.get("verdict"))
    if quality_verdict:
        quality_score = _compact_text(quality.get("quality_score"))
        suffix = f" ({quality_score}/100)" if quality_score else ""
        lines.append(f"品質: {quality_verdict}{suffix}")
    if quality_lanes:
        lane_parts = []
        repair_symbols: list[str] = []
        repair_scope_summary: list[str] = []
        research_symbols: list[str] = []
        for lane in [item for item in quality_lanes if isinstance(item, dict)][:4]:
            label = _compact_text(lane.get("label") or lane.get("key"))
            if label:
                lane_parts.append(f"{label} {_safe_int(lane.get('count'))}")
            symbols = lane.get("symbols") if isinstance(lane.get("symbols"), list) else []
            clean_symbols = [_compact_text(symbol) for symbol in symbols if _compact_text(symbol)]
            lane_key = _compact_text(lane.get("key"))
            if lane_key == "repair_first":
                repair_symbols = clean_symbols[:5]
                raw_scope_summary = lane.get("data_gap_scope_summary") if isinstance(lane.get("data_gap_scope_summary"), list) else []
                repair_scope_summary = [_compact_text(item) for item in raw_scope_summary if _compact_text(item)][:4]
            elif lane_key == "research_now":
                research_symbols = clean_symbols[:5]
        if lane_parts:
            lines.append(f"品質分流: {' / '.join(lane_parts)}")
        if research_symbols:
            lines.append(f"可先研究: {', '.join(research_symbols)}")
        if repair_symbols:
            lines.append(f"先修資料: {', '.join(repair_symbols)}")
        if repair_scope_summary:
            lines.append(f"資料缺口: {' / '.join(repair_scope_summary)}")

    verdict = _compact_text(diagnostics.get("verdict"))
    if verdict:
        lines.append(f"判讀: {verdict}")
    if counts:
        lines.append(
            "行動線: Preview {preview} / Trigger {trigger} / Risk {risk}".format(
                preview=_safe_int(counts.get("preview")),
                trigger=_safe_int(counts.get("trigger")),
                risk=_safe_int(counts.get("risk")),
            )
        )
    if _compact_text(threshold_sweep.get("verdict")):
        score = _compact_text(threshold_sweep.get("robustness_score"))
        score_text = f" {score}/100" if score else ""
        stable_symbols = threshold_sweep.get("stable_symbols") if isinstance(threshold_sweep.get("stable_symbols"), list) else []
        stable_text = ", ".join(_compact_text(symbol) for symbol in stable_symbols[:5] if _compact_text(symbol))
        suffix = f"；穩定: {stable_text}" if stable_text else ""
        lines.append(f"門檻: {_compact_text(threshold_sweep.get('verdict'))}{score_text}{suffix}")

    for lane in [item for item in lanes if isinstance(item, dict)][:3]:
        lane_items = lane.get("items") if isinstance(lane.get("items"), list) else []
        symbols = []
        for item in [row for row in lane_items if isinstance(row, dict)][:3]:
            symbol = _compact_text(item.get("symbol"))
            name = _compact_text(item.get("name"))
            title = f"{symbol} {name}".strip()
            trigger_short = _trigger_short_text(item)
            if trigger_short:
                title = f"{title} ({trigger_short})".strip()
            symbols.append(title)
        suffix = f": {', '.join(symbols)}" if symbols else ""
        lines.append(f"- {_compact_text(lane.get('label')) or _compact_text(lane.get('lane'))}: {_safe_int(lane.get('count'))} 檔{suffix}")

    outcome_verdict = _compact_text(outcomes.get("verdict"))
    if outcome_verdict:
        lines.append(f"驗證: {outcome_verdict}")
    if style == "postmarket":
        feedback_verdict = _compact_text(trading_feedback.get("verdict"))
        if feedback_verdict:
            lines.append(f"交易回饋: {feedback_verdict}")
        feedback_action = _compact_text(trading_feedback.get("primary_action"))
        if feedback_action:
            lines.append(f"盤後動作: {feedback_action}")
    if _compact_text(urls.get("html_summary")):
        lines.append(f"HTML: {_compact_text(urls.get('html_summary'))}")
    if _compact_text(urls.get("decision_brief_md")):
        lines.append(f"Brief: {_compact_text(urls.get('decision_brief_md'))}")
    return "\n".join(lines)


def send_daily_radar_brief_notification(
    export: dict[str, Any],
    notifiers: list[Notifier] | None = None,
    *,
    notify_style: str = "premarket",
) -> dict[str, Any]:
    active_notifiers = notifiers or []
    style = _normalize_notify_style(notify_style)
    notification: dict[str, Any] = {
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "channels": [type(item).__name__ for item in active_notifiers],
        "notify_style": style,
    }
    if not active_notifiers:
        notification["skipped_reason"] = "no_configured_notifiers"
        return notification

    export_data = export.get("data") if isinstance(export.get("data"), dict) else {}
    decision_brief = export_data.get("decision_brief") if isinstance(export_data.get("decision_brief"), dict) else {}
    urls = export.get("urls") if isinstance(export.get("urls"), dict) else {}
    message = format_daily_radar_brief_message(decision_brief, export_urls=urls, notify_style=style)
    notification["message_preview"] = message.splitlines()[0] if message else ""
    for notifier in active_notifiers:
        notification["attempted"] += 1
        try:
            notifier.send(message)
            notification["sent"] += 1
        except Exception as exc:  # noqa: BLE001
            notification["failed"] += 1
            notification.setdefault("errors", []).append(str(exc))
    return notification


def _resolve_index_constituents_key(universe: str, index_key: str | None = None) -> str:
    explicit = str(index_key or "").strip()
    if explicit:
        return explicit
    text = str(universe or "").strip()
    if text.lower().startswith("index:"):
        key = text.split(":", 1)[1].strip()
        if key:
            return key
    return "custom"


def _sync_index_constituents_url_for_job(
    *,
    url: str | None,
    universe: str,
    index_key: str | None = None,
    source: str = "official_url",
) -> dict | None:
    clean_url = str(url or "").strip()
    if not clean_url:
        return None
    from app.data.index_constituents import sync_index_constituents_url

    key = _resolve_index_constituents_key(universe, index_key=index_key)
    return sync_index_constituents_url(clean_url, index_key=key, source=str(source or "official_url"))


def run_daily_signal_job(
    repo: TradingRepository,
    notifiers: list[Notifier] | None = None,
    available_cash: float = 10_000,
    *,
    trade_date_mode: str = "T",
    min_data_timestamp: date | datetime | str | None = None,
    rank_by: str = "score",
    max_open_positions: int | None = None,
) -> dict:
    trade_date = _resolve_trade_date(trade_date_mode)
    suggestions = compute_daily_signal_suggestions(
        repo=repo,
        trade_date=trade_date,
        available_cash=float(available_cash),
        require_chip=True,
        block_disposition=True,
        rank_by=rank_by,
        max_open_positions=max_open_positions,
        data_freshness_required=True,
        chip_threshold_mode="zscore",
    )
    min_data_date = _parse_min_timestamp(min_data_timestamp)
    rejected_by_timestamp = 0
    if min_data_date is not None:
        filtered = []
        for item in suggestions:
            try:
                last_day = datetime.strptime(str(item.last_bar_date), "%Y-%m-%d").date()
            except Exception:
                last_day = date.min
            if last_day < min_data_date:
                rejected_by_timestamp += 1
                continue
            filtered.append(item)
        suggestions = filtered

    sent = 0
    failed = 0
    notifiers = notifiers or []
    if notifiers:
        for item in suggestions:
            name = repo.get_instrument_name(item.symbol)
            symbol_text = f"{item.symbol} {name}".strip()
            message = (
                f"[訊號] {symbol_text}, 現價 {item.price:.2f}, 符合進場, "
                f"建議買進 {item.qty} 股, 預估總成本 {item.estimated_total_cost:.0f} 元 (含預估手續費), "
                f"RSI3={item.rsi3:.2f}, K={item.k:.2f}, D={item.d:.2f}, "
                f"法人5日淨買超={item.chip_net_buy_5d:.0f}, 籌碼連3增={item.chip_concentration_up3}, "
                f"score={item.score:.2f}, risk_score={item.risk_score:.2f}"
            )
            for notifier in notifiers:
                try:
                    notifier.send(message)
                    sent += 1
                except Exception:
                    failed += 1
    return {
        "suggestions": len(suggestions),
        "sent": sent,
        "failed": failed,
        "channels": [type(n).__name__ for n in notifiers],
        "available_cash": float(available_cash),
        "trade_date_mode": str(trade_date_mode or "T"),
        "trade_date": trade_date.isoformat(),
        "rank_by": rank_by,
        "max_open_positions": max_open_positions,
        "min_data_timestamp": min_data_date.isoformat() if min_data_date else None,
        "rejected_by_min_data_timestamp": rejected_by_timestamp,
    }


def run_daily_radar_job(
    repo: TradingRepository,
    *,
    sync_service: SyncService | None = None,
    notifiers: list[Notifier] | None = None,
    trade_date_mode: str = "T",
    target_count: int = 15,
    risk_profile: str = "balanced",
    threshold_offset: float = 0.0,
    universe: str = "curated_watchlist",
    data_freshness_required: bool = True,
    include_attention: bool = True,
    include_extended_data: bool = False,
    prefer_official_bulk: bool = True,
    symbols: str | list[str] | tuple[str, ...] | None = None,
    index_constituents_url: str | None = None,
    index_constituents_key: str | None = None,
    index_constituents_source: str = "official_url",
    threshold_adoption: dict[str, Any] | None = None,
    notify_style: str = "premarket",
) -> dict:
    trade_date = _resolve_trade_date(trade_date_mode)
    index_sync = _sync_index_constituents_url_for_job(
        url=index_constituents_url,
        universe=universe,
        index_key=index_constituents_key,
        source=index_constituents_source,
    )
    service = DailyRadarService(repo=repo, sync_service=sync_service or SyncService(repo=repo))
    request = DailyRadarRequest(
        trade_date=trade_date,
        target_count=int(target_count),
        risk_profile=str(risk_profile or "balanced"),
        threshold_offset=float(threshold_offset or 0.0),
        universe=str(universe or "curated_watchlist"),
        data_freshness_required=bool(data_freshness_required),
        include_attention=bool(include_attention),
        include_extended_data=bool(include_extended_data),
        prefer_official_bulk=bool(prefer_official_bulk),
        symbols=_parse_symbol_list(symbols),
        threshold_adoption=threshold_adoption,
    )
    session = getattr(repo, "session", None)
    try:
        result = service.run(request)
        commit = getattr(session, "commit", None)
        if callable(commit):
            commit()
    except Exception:
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            rollback()
        raise
    export = result.get("export", {})
    notification = send_daily_radar_brief_notification(export, notifiers=notifiers, notify_style=notify_style) if isinstance(export, dict) else {
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "channels": [type(item).__name__ for item in (notifiers or [])],
        "notify_style": _normalize_notify_style(notify_style),
        "skipped_reason": "export_unavailable",
    }
    return {
        "run_id": result.get("run_id", ""),
        "trade_date": result.get("trade_date", trade_date.isoformat()),
        "target_count": result.get("target_count", int(target_count)),
        "risk_profile": result.get("risk_profile", risk_profile),
        "threshold_offset": result.get("threshold_offset", threshold_offset),
        "threshold_adoption": result.get("threshold_adoption", {}),
        "universe": result.get("universe", universe),
        "items": len(result.get("items") or []),
        "top_items": _compact_daily_radar_items(result.get("items") or [], limit=5),
        "label_counts": result.get("label_counts", {}),
        "degraded": bool(result.get("degraded", False)),
        "degrade_reasons": result.get("degrade_reasons", []),
        "prefer_official_bulk": bool(prefer_official_bulk),
        "sync_result": result.get("sync_result", {}),
        "export": export,
        "notification": notification,
        "index_constituents_sync": index_sync,
    }


def _run_shortlist_stage(job_name: str, callback, *, force: bool = False) -> dict[str, Any]:
    from app.db.models import ScheduledJobRun
    from app.db.session import get_session_factory, init_db
    from sqlalchemy import select

    init_db()
    session_factory = get_session_factory()
    today = datetime.now().date().isoformat()
    run_key = f"{job_name}:{today}"
    if force:
        run_key = f"{run_key}:{datetime.now().strftime('%H%M%S%f')}"

    with session_factory() as session:
        existing = session.execute(
            select(ScheduledJobRun).where(ScheduledJobRun.run_key == run_key).limit(1)
        ).scalar_one_or_none()
        if existing is not None and existing.status == "ok":
            return {"status": "skipped", "reason": "already_completed", "run_key": run_key}
        if existing is None:
            existing = ScheduledJobRun(job_name=job_name, run_key=run_key, status="running")
            session.add(existing)
        else:
            existing.status = "running"
            existing.attempt_count = int(existing.attempt_count or 0) + 1
            existing.started_at = datetime.utcnow()
            existing.finished_at = None
        session.commit()

    started = datetime.utcnow()
    try:
        payload = callback()
        status = "ok"
        message = ""
        rows = int(payload.get("items") or payload.get("selected_count") or payload.get("updated") or 0)
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
        status = "error"
        message = str(exc)[:1200]
        rows = 0

    finished = datetime.utcnow()
    with session_factory() as session:
        record = session.execute(
            select(ScheduledJobRun).where(ScheduledJobRun.run_key == run_key).limit(1)
        ).scalar_one()
        record.status = status
        record.finished_at = finished
        record.duration_seconds = max(0.0, (finished - started).total_seconds())
        record.rows_affected = rows
        record.message = message
        session.commit()
    if status == "error":
        raise RuntimeError(message)
    return {"status": "ok", "run_key": run_key, "data": payload}


def run_shortlist_quant_job(*, force: bool = False) -> dict[str, Any]:
    def callback() -> dict[str, Any]:
        from app.db.session import get_session_factory
        from app.pipeline.shortlist import ShortlistService

        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            radar = run_daily_radar_job(
                repo,
                trade_date_mode="T",
                target_count=30,
                risk_profile="balanced",
                universe="twse_tpex",
                data_freshness_required=True,
                include_attention=True,
                include_extended_data=True,
                prefer_official_bulk=True,
            )
            result = ShortlistService(repo).prepare(radar_run_id=str(radar.get("run_id") or ""))
            session.commit()
            return result

    return _run_shortlist_stage("shortlist_quant", callback, force=force)


def run_shortlist_ai_job(*, force: bool = False) -> dict[str, Any]:
    def callback() -> dict[str, Any]:
        from app.db.session import get_session_factory
        from app.pipeline.shortlist import ShortlistService

        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = ShortlistService(repo).enrich(force=force)
            session.commit()
            return result

    return _run_shortlist_stage("shortlist_ai", callback, force=force)


def run_shortlist_finalize_job(*, force: bool = False) -> dict[str, Any]:
    def callback() -> dict[str, Any]:
        from app.db.session import get_session_factory
        from app.pipeline.shortlist import ShortlistService

        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = ShortlistService(repo).finalize()
            session.commit()
            return result

    return _run_shortlist_stage("shortlist_finalize", callback, force=force)


def run_shortlist_premarket_job(*, force: bool = False) -> dict[str, Any]:
    def callback() -> dict[str, Any]:
        from app.db.session import get_session_factory
        from app.pipeline.shortlist import ShortlistService

        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = ShortlistService(repo).refresh_premarket()
            session.commit()
            return result

    return _run_shortlist_stage("shortlist_premarket", callback, force=force)


def run_shortlist_outcome_job(*, force: bool = False) -> dict[str, Any]:
    def callback() -> dict[str, Any]:
        from app.db.session import get_session_factory
        from app.pipeline.shortlist import ShortlistService

        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = ShortlistService(repo).update_outcomes()
            session.commit()
            return result

    return _run_shortlist_stage("shortlist_outcomes", callback, force=force)


def _compact_daily_radar_items(items: list[dict] | tuple[dict, ...], *, limit: int = 5) -> list[dict]:
    rows = [item for item in items if isinstance(item, dict)]
    priority = {"ENTRY_READY": 3, "WATCH_WAIT_TRIGGER": 2, "NOT_RECOMMENDED": 1}
    rows.sort(
        key=lambda item: (
            priority.get(str(item.get("action_label") or ""), 0),
            float(item.get("risk_adjusted_score") or item.get("entry_score") or 0.0),
            float(item.get("entry_score") or 0.0),
        ),
        reverse=True,
    )
    out: list[dict] = []
    for item in rows[: max(1, int(limit or 5))]:
        out.append(
            {
                "rank": item.get("rank"),
                "symbol": str(item.get("symbol") or ""),
                "name": str(item.get("name") or ""),
                "industry_group": str(item.get("industry_group") or ""),
                "action_label": str(item.get("action_label") or ""),
                "entry_score": item.get("entry_score"),
                "risk_adjusted_score": item.get("risk_adjusted_score"),
                "risk_score": item.get("risk_score"),
                "reason_tags": list(item.get("reason_tags") or [])[:6] if isinstance(item.get("reason_tags"), list) else [],
                "blocker_tags": list(item.get("blocker_tags") or [])[:6] if isinstance(item.get("blocker_tags"), list) else [],
            }
        )
    return out


def run_daily_radar_trigger_monitor_job(
    repo: TradingRepository,
    notifiers: list[Notifier] | None = None,
    *,
    count: int = 50,
    limit: int = 10,
    near_entry_gap: float = 2.0,
    near_price_gap_pct: float = 0.02,
    notify_on: str = "ready",
    price_source: str = "radar_snapshot",
    shioaji_simulation: bool = True,
    max_price_age_seconds: int = 180,
    streaming_seconds: float = 3.0,
    notification_state_path: str | None = None,
    notification_cooldown_minutes: int = 240,
    force_notify: bool = False,
) -> dict:
    from app.reports.daily_radar_trigger_monitor import DailyRadarTriggerMonitorService

    price_provider = build_daily_radar_trigger_price_provider(
        price_source=price_source,
        shioaji_simulation=bool(shioaji_simulation),
        streaming_seconds=float(streaming_seconds),
    )
    payload = DailyRadarTriggerMonitorService(
        count=int(count),
        limit=int(limit),
        near_entry_gap=float(near_entry_gap),
        near_price_gap_pct=float(near_price_gap_pct),
        max_price_age_seconds=int(max_price_age_seconds),
        price_provider=price_provider,
    ).evaluate(repo)
    payload["notification"] = send_daily_radar_trigger_monitor_notification(
        payload,
        notifiers=notifiers,
        notify_on=notify_on,
        state_path=notification_state_path,
        cooldown_minutes=notification_cooldown_minutes,
        force=force_notify,
    )
    return payload


def build_daily_radar_trigger_price_provider(
    *,
    price_source: str = "radar_snapshot",
    shioaji_simulation: bool = True,
    streaming_seconds: float = 3.0,
) -> Any | None:
    source = str(price_source or "radar_snapshot").strip().lower()
    if source in {"", "radar", "radar_snapshot", "snapshot"}:
        return None
    if source not in {"shioaji_snapshot", "shioaji_streaming"}:
        raise ValueError(f"unsupported trigger monitor price source: {price_source}")

    def provider(symbols: list[str]) -> dict[str, Any]:
        from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway

        gateway = ShioajiGateway(ShioajiConfig(simulation=bool(shioaji_simulation)))
        try:
            if source == "shioaji_streaming":
                return gateway.get_stock_streaming_quotes(
                    symbols,
                    collect_seconds=max(0.0, float(streaming_seconds or 0.0)),
                    fallback_to_snapshot=True,
                )
            return gateway.get_stock_snapshots(symbols)
        finally:
            api = getattr(gateway, "api", None)
            if api is not None and hasattr(api, "logout"):
                try:
                    api.logout()
                except Exception:
                    pass

    return provider


def send_daily_radar_trigger_monitor_notification(
    payload: dict[str, Any],
    notifiers: list[Notifier] | None = None,
    *,
    notify_on: str = "ready",
    state_path: str | None = None,
    cooldown_minutes: int = 240,
    force: bool = False,
) -> dict[str, Any]:
    from app.reports.daily_radar_trigger_monitor import (
        daily_radar_trigger_monitor_notification_decision,
        format_daily_radar_trigger_monitor_message,
        record_daily_radar_trigger_monitor_notification_sent,
    )

    active_notifiers = notifiers or []
    decision = daily_radar_trigger_monitor_notification_decision(
        payload,
        notify_on=str(notify_on or "ready"),
        state_path=state_path,
        cooldown_minutes=int(cooldown_minutes),
        force=bool(force),
    )
    notification: dict[str, Any] = {
        "notify_on": str(notify_on or "ready"),
        "triggered": bool(decision.get("triggered")),
        "suppressed": bool(decision.get("suppressed")),
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "channels": [type(item).__name__ for item in active_notifiers],
        "event_key": str(decision.get("event_key") or ""),
        "event_symbols": list(decision.get("event_symbols") or []),
        "notification_state_path": str(decision.get("state_path") or ""),
        "cooldown_minutes": int(decision.get("cooldown_minutes") or 0),
    }
    if decision.get("last_sent_at"):
        notification["last_sent_at"] = decision["last_sent_at"]
    if decision.get("next_allowed_at"):
        notification["next_allowed_at"] = decision["next_allowed_at"]
    if decision.get("state_warning"):
        notification["state_warning"] = decision["state_warning"]

    if active_notifiers and decision.get("should_send"):
        message = format_daily_radar_trigger_monitor_message(payload)
        notification["message_preview"] = message.splitlines()[0] if message else ""
        for notifier in active_notifiers:
            notification["attempted"] += 1
            try:
                notifier.send(message)
                notification["sent"] += 1
            except Exception as exc:  # noqa: BLE001
                notification["failed"] += 1
                notification.setdefault("errors", []).append(str(exc))
        if notification["sent"] > 0:
            notification["state_record"] = record_daily_radar_trigger_monitor_notification_sent(
                payload,
                notify_on=str(notify_on or "ready"),
                state_path=state_path,
                sent_count=int(notification["sent"]),
            )
    elif active_notifiers:
        notification["skipped_reason"] = str(decision.get("skipped_reason") or "status_below_notify_threshold")
    else:
        notification["skipped_reason"] = "notify_disabled"
    return notification


def start_scheduler(
    repo: TradingRepository,
    notifiers: list[Notifier] | None = None,
    available_cash: float = 10_000,
    hour: int = 13,
    minute: int = 40,
    trade_date_mode: str = "T",
    rank_by: str = "score",
    max_open_positions: int | None = None,
    enable_signal_job: bool = True,
    enable_daily_radar: bool = False,
    enable_trigger_monitor: bool = False,
    enable_shortlist: bool = False,
    radar_hour: int = 8,
    radar_minute: int = 45,
    radar_target_count: int = 15,
    radar_risk_profile: str = "balanced",
    radar_threshold_offset: float = 0.0,
    radar_universe: str = "curated_watchlist",
    radar_data_freshness_required: bool = True,
    radar_include_extended_data: bool = False,
    radar_prefer_official_bulk: bool = True,
    radar_symbols: str | list[str] | tuple[str, ...] | None = None,
    radar_index_constituents_url: str | None = None,
    radar_index_constituents_key: str | None = None,
    radar_index_constituents_source: str = "official_url",
    radar_notify_style: str = "premarket",
    trigger_monitor_hour: int = 9,
    trigger_monitor_minute: int = 15,
    trigger_monitor_count: int = 50,
    trigger_monitor_limit: int = 10,
    trigger_monitor_near_entry_gap: float = 2.0,
    trigger_monitor_near_price_gap_pct: float = 0.02,
    trigger_monitor_notify_on: str = "ready",
    trigger_monitor_price_source: str = "radar_snapshot",
    trigger_monitor_shioaji_simulation: bool = True,
    trigger_monitor_max_price_age_seconds: int = 180,
    trigger_monitor_streaming_seconds: float = 3.0,
    trigger_monitor_notification_state_path: str | None = None,
    trigger_monitor_notification_cooldown_minutes: int = 240,
    trigger_monitor_force_notify: bool = False,
    scheduler_cls: Any | None = None,
) -> None:
    if not enable_signal_job and not enable_daily_radar and not enable_trigger_monitor and not enable_shortlist:
        raise ValueError("at least one scheduled job must be enabled")
    if scheduler_cls is None:
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("apscheduler is not installed. Please `pip install apscheduler`.") from exc
        scheduler_cls = BlockingScheduler

    scheduler = scheduler_cls(timezone="Asia/Taipei")
    if enable_signal_job:
        scheduler.add_job(
            lambda: run_daily_signal_job(
                repo=repo,
                notifiers=notifiers,
                available_cash=available_cash,
                trade_date_mode=trade_date_mode,
                rank_by=rank_by,
                max_open_positions=max_open_positions,
            ),
            trigger="cron",
            day_of_week="mon-fri",
            hour=int(hour),
            minute=int(minute),
            id="daily_signal_job",
            replace_existing=True,
        )
    if enable_daily_radar:
        scheduler.add_job(
            lambda: run_daily_radar_job(
                repo=repo,
                notifiers=notifiers,
                trade_date_mode=trade_date_mode,
                target_count=int(radar_target_count),
                risk_profile=str(radar_risk_profile),
                threshold_offset=float(radar_threshold_offset or 0.0),
                universe=str(radar_universe),
                data_freshness_required=bool(radar_data_freshness_required),
                include_extended_data=bool(radar_include_extended_data),
                prefer_official_bulk=bool(radar_prefer_official_bulk),
                symbols=radar_symbols,
                index_constituents_url=radar_index_constituents_url,
                index_constituents_key=radar_index_constituents_key,
                index_constituents_source=radar_index_constituents_source,
                notify_style=radar_notify_style,
            ),
            trigger="cron",
            day_of_week="mon-fri",
            hour=int(radar_hour),
            minute=int(radar_minute),
            id="daily_radar_job",
            replace_existing=True,
        )
    if enable_trigger_monitor:
        scheduler.add_job(
            lambda: run_daily_radar_trigger_monitor_job(
                repo=repo,
                notifiers=notifiers,
                count=int(trigger_monitor_count),
                limit=int(trigger_monitor_limit),
                near_entry_gap=float(trigger_monitor_near_entry_gap),
                near_price_gap_pct=float(trigger_monitor_near_price_gap_pct),
                notify_on=str(trigger_monitor_notify_on),
                price_source=str(trigger_monitor_price_source),
                shioaji_simulation=bool(trigger_monitor_shioaji_simulation),
                max_price_age_seconds=int(trigger_monitor_max_price_age_seconds),
                streaming_seconds=float(trigger_monitor_streaming_seconds),
                notification_state_path=trigger_monitor_notification_state_path,
                notification_cooldown_minutes=int(trigger_monitor_notification_cooldown_minutes),
                force_notify=bool(trigger_monitor_force_notify),
            ),
            trigger="cron",
            day_of_week="mon-fri",
            hour=int(trigger_monitor_hour),
            minute=int(trigger_monitor_minute),
            id="daily_radar_trigger_monitor",
            replace_existing=True,
        )
    if enable_shortlist:
        common = {
            "replace_existing": True,
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": 3600,
        }
        scheduler.add_job(
            run_shortlist_premarket_job,
            trigger="cron",
            day_of_week="mon-fri",
            hour=7,
            minute=30,
            id="shortlist_premarket",
            **common,
        )
        scheduler.add_job(
            run_shortlist_quant_job,
            trigger="cron",
            day_of_week="mon-fri",
            hour=14,
            minute=10,
            id="shortlist_quant",
            **common,
        )
        scheduler.add_job(
            run_shortlist_ai_job,
            trigger="cron",
            day_of_week="mon-fri",
            hour=14,
            minute=40,
            id="shortlist_ai",
            **common,
        )
        scheduler.add_job(
            run_shortlist_finalize_job,
            trigger="cron",
            day_of_week="mon-fri",
            hour=15,
            minute=20,
            id="shortlist_finalize",
            **common,
        )
        scheduler.add_job(
            run_shortlist_outcome_job,
            trigger="cron",
            day_of_week="sun",
            hour=10,
            minute=0,
            id="shortlist_outcomes",
            **common,
        )
    scheduler.start()
