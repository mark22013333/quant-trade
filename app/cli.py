from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timedelta
from pathlib import Path


def _parse_date(text: str):
    return datetime.strptime(text, "%Y-%m-%d").date()


def _tw_tick_size(price: float) -> float:
    value = float(price)
    if value < 10:
        return 0.01
    if value < 50:
        return 0.05
    if value < 100:
        return 0.1
    if value < 500:
        return 0.5
    if value < 1000:
        return 1.0
    return 5.0


def _apply_tick_offset(price: float, ticks: int) -> float:
    out = float(price)
    steps = int(ticks)
    if steps > 0:
        for _ in range(steps):
            out += _tw_tick_size(out)
    elif steps < 0:
        for _ in range(abs(steps)):
            out = max(_tw_tick_size(out), out - _tw_tick_size(out))
    return float(out)


def _is_twse_session_open(now_dt: datetime | None = None) -> bool:
    now_dt = now_dt or datetime.now()
    if now_dt.weekday() >= 5:
        return False
    return time(9, 0) <= now_dt.time() <= time(13, 30)


def _load_db_modules():
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency: sqlalchemy. Please run `pip install -r requirements.txt`.") from exc
    return TradingRepository, get_session_factory, init_db


def cmd_init_db(_args) -> None:
    _, _, init_db = _load_db_modules()
    from app.config import load_config

    init_db()
    cfg = load_config()
    print(f"[ok] DB initialized: {cfg.database_url}")


def cmd_check_finmind(_args) -> None:
    from app.data.finmind_client import FinMindClient

    client = FinMindClient()
    info = client.check_auth()
    print(info)


def cmd_finmind_usage(_args) -> None:
    from app.data.finmind_client import FinMindClient

    client = FinMindClient()
    print(client.fetch_user_info())


def cmd_sync_0050(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    snapshot_date = _parse_date(args.snapshot_date) if args.snapshot_date else datetime.now().date()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_0050_universe(snapshot_date=snapshot_date, prefer_remote=not args.local_cache)
        session.commit()
    print(result)


def cmd_sync_mid100(_args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_mid100_universe()
        session.commit()
    print(result)


def cmd_sync_twse_tpex_universe(_args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.data.sync_service import SyncService

    init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        before = repo.get_instrument_industry_coverage()
        service = SyncService(repo=repo)
        result = service.sync_twse_tpex_universe()
        session.commit()
        after = repo.get_instrument_industry_coverage()
    print(json.dumps({"before": before, "result": result, "after": after}, ensure_ascii=False, indent=2))


def cmd_import_index_constituents(args) -> None:
    from app.data.index_constituents import import_index_constituents_file

    result = import_index_constituents_file(
        args.file,
        index_key=str(args.index_key),
        source=str(args.source),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_sync_index_constituents_url(args) -> None:
    from app.data.index_constituents import sync_index_constituents_url

    result = sync_index_constituents_url(
        str(args.url),
        index_key=str(args.index_key),
        source=str(args.source),
        timeout_sec=int(args.timeout_sec),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_list_index_constituents(_args) -> None:
    from app.data.index_constituents import list_index_constituent_caches

    print(json.dumps({"items": list_index_constituent_caches()}, ensure_ascii=False, indent=2))


def cmd_sync_bars(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date) if args.end_date else datetime.now().date()
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_daily_bars(start_date=start_date, end_date=end_date, symbols=symbols)
        session.commit()
    print(result)


def cmd_sync_chip(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date) if args.end_date else datetime.now().date()
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_institutional_chip(start_date=start_date, end_date=end_date, symbols=symbols)
        session.commit()
    print(result)


def cmd_sync_broker_agg(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date) if args.end_date else datetime.now().date()
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_broker_agg_chip(start_date=start_date, end_date=end_date, symbols=symbols)
        session.commit()
    print(result)


def cmd_sync_disposition(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date) if args.end_date else datetime.now().date()
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_disposition_periods(start_date=start_date, end_date=end_date, symbols=symbols)
        session.commit()
    print(result)


def cmd_sync_attention(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    trade_date = _parse_date(args.trade_date) if args.trade_date else datetime.now().date()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_attention_stocks(trade_date=trade_date)
        session.commit()
    print(result)


def cmd_sync_market_bundle(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.data.sync_service import SyncService

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date) if args.end_date else datetime.now().date()
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = SyncService(repo=repo)
        result = service.sync_market_bundle(
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            include_bars=bool(args.include_bars),
            include_institutional=bool(args.include_chip),
            include_broker_agg=bool(args.include_broker_agg),
            include_disposition=bool(args.include_disposition),
            include_attention=bool(args.include_attention),
            include_fundamentals=bool(args.include_fundamentals),
            include_news=bool(args.include_news),
            include_industry_indices=bool(args.include_industry_indices),
            prefer_official_bulk=bool(args.prefer_official_bulk),
        )
        session.commit()
    print(result)


def cmd_list_0050(_args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()

    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        symbols = repo.get_latest_0050_symbols()
    print({"count": len(symbols), "symbols": symbols})


def cmd_backtest(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.backtest.run_backtest import run_backtest

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = run_backtest(
            repo=repo,
            symbol=args.symbol,
            start_date=start_date,
            end_date=end_date,
            cash=float(args.cash),
        )
    print(result)


def cmd_run_scheduler(_args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.scheduler.jobs import build_notifiers, start_scheduler

    init_db()
    channels = _parse_notify_channels(_args.notify)
    notifiers = build_notifiers(channels=channels)
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        start_scheduler(
            repo=repo,
            notifiers=notifiers,
            available_cash=float(_args.available_cash),
            hour=int(_args.hour),
            minute=int(_args.minute),
            trade_date_mode=str(_args.trade_date_mode),
            rank_by=str(_args.rank_by),
            max_open_positions=(
                int(_args.max_open_positions)
                if _args.max_open_positions is not None and int(_args.max_open_positions) > 0
                else None
            ),
            enable_signal_job=bool(_args.enable_signal_job),
            enable_daily_radar=bool(_args.enable_daily_radar),
            enable_trigger_monitor=bool(_args.enable_trigger_monitor),
            enable_shortlist=bool(_args.enable_shortlist),
            radar_hour=int(_args.radar_hour),
            radar_minute=int(_args.radar_minute),
            radar_target_count=int(_args.radar_target_count),
            radar_risk_profile=str(_args.radar_risk_profile),
            radar_threshold_offset=float(_args.radar_threshold_offset),
            radar_universe=str(_args.radar_universe),
            radar_data_freshness_required=bool(_args.radar_data_freshness_required),
            radar_include_extended_data=bool(_args.radar_include_extended_data),
            radar_prefer_official_bulk=bool(_args.radar_prefer_official_bulk),
            radar_symbols=str(_args.radar_symbols or ""),
            radar_index_constituents_url=str(_args.radar_index_constituents_url or ""),
            radar_index_constituents_key=str(_args.radar_index_constituents_key or ""),
            radar_index_constituents_source=str(_args.radar_index_constituents_source or "official_url"),
            radar_notify_style=str(_args.radar_notify_style),
            trigger_monitor_hour=int(_args.trigger_monitor_hour),
            trigger_monitor_minute=int(_args.trigger_monitor_minute),
            trigger_monitor_count=int(_args.trigger_monitor_count),
            trigger_monitor_limit=int(_args.trigger_monitor_limit),
            trigger_monitor_near_entry_gap=float(_args.trigger_monitor_near_entry_gap),
            trigger_monitor_near_price_gap_pct=float(_args.trigger_monitor_near_price_gap_pct),
            trigger_monitor_notify_on=str(_args.trigger_monitor_notify_on),
            trigger_monitor_price_source=str(_args.trigger_monitor_price_source),
            trigger_monitor_shioaji_simulation=not bool(_args.trigger_monitor_shioaji_live),
            trigger_monitor_max_price_age_seconds=int(_args.trigger_monitor_max_price_age_seconds),
            trigger_monitor_streaming_seconds=float(_args.trigger_monitor_streaming_seconds),
            trigger_monitor_notification_state_path=str(_args.trigger_monitor_notification_state_path or ""),
            trigger_monitor_notification_cooldown_minutes=int(_args.trigger_monitor_notification_cooldown_minutes),
            trigger_monitor_force_notify=bool(_args.trigger_monitor_force_notify),
        )


def _parse_notify_channels(raw: str) -> tuple[str, ...]:
    value = (raw or "none").strip().lower()
    if value in {"none", ""}:
        return tuple()
    if value == "both":
        return ("telegram", "line")
    if value not in {"telegram", "line"}:
        raise RuntimeError("notify must be one of: none, telegram, line, both")
    return (value,)


def cmd_run_signal_job(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.scheduler.jobs import build_notifiers, run_daily_signal_job

    channels = _parse_notify_channels(args.notify)
    notifiers = build_notifiers(channels=channels)
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = run_daily_signal_job(
            repo=repo,
            notifiers=notifiers,
            available_cash=float(args.available_cash),
            trade_date_mode=str(args.trade_date_mode),
            min_data_timestamp=args.min_data_timestamp,
            rank_by=str(args.rank_by),
            max_open_positions=(
                int(args.max_open_positions)
                if args.max_open_positions is not None and int(args.max_open_positions) > 0
                else None
            ),
        )
    print(result)


def cmd_run_daily_radar_job(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.scheduler.jobs import build_notifiers, run_daily_radar_job

    init_db()
    channels = _parse_notify_channels(args.notify)
    notifiers = build_notifiers(channels=channels)
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = run_daily_radar_job(
            repo=repo,
            notifiers=notifiers,
            trade_date_mode=str(args.trade_date_mode),
            target_count=int(args.target_count),
            risk_profile=str(args.risk_profile),
            threshold_offset=float(args.threshold_offset),
            universe=str(args.universe),
            data_freshness_required=bool(args.data_freshness_required),
            include_attention=bool(args.include_attention),
            include_extended_data=bool(args.include_extended_data),
            prefer_official_bulk=bool(args.prefer_official_bulk),
            symbols=str(args.symbols or ""),
            index_constituents_url=str(args.index_constituents_url or ""),
            index_constituents_key=str(args.index_constituents_key or ""),
            index_constituents_source=str(args.index_constituents_source or "official_url"),
            notify_style=str(args.notify_style),
        )
    print(result)
    notification = result.get("notification") if isinstance(result.get("notification"), dict) else {}
    if bool(args.fail_on_notify_error) and int(notification.get("failed", 0) or 0) > 0:
        sys.exit(3)


def cmd_run_daily_radar_threshold_optimization(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.reports.daily_radar_threshold_optimization import run_daily_radar_threshold_optimization_batch

    horizons: list[int] = []
    for raw in str(args.horizons or "5,10,20").replace(";", ",").split(","):
        text = raw.strip()
        if not text:
            continue
        try:
            horizons.append(max(1, min(60, int(text))))
        except ValueError:
            continue
    if not horizons:
        horizons = [5, 10, 20]

    init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = run_daily_radar_threshold_optimization_batch(
            repo,
            execute=bool(args.execute),
            max_runs=int(args.max_runs),
            lookback_runs=int(args.lookback_runs),
            lookahead_bars_list=tuple(horizons),
            count=int(args.count),
            base_profile=args.base_profile,
            trade_date_mode=str(args.trade_date_mode),
            grid_universes=tuple(_csv_items(args.grid_universes)),
            grid_profiles=tuple(_csv_items(args.grid_profiles)),
            grid_offsets=tuple(_csv_floats(args.grid_offsets)),
            candidate_source=str(args.candidate_source),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_run_invest_pipeline(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.data.sync_service import SyncService
    from app.pipeline import InvestPipelineService
    from app.pipeline import OneClickPipelineRequest as PipelineRequest

    init_db()
    trade_date = _parse_date(args.trade_date) if args.trade_date else None
    symbols = _csv_items(args.symbols)
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = InvestPipelineService(repo=repo, sync_service=SyncService(repo=repo))
        result = service.run(
            PipelineRequest(
                trade_date=trade_date,
                target_count=int(args.target_count),
                risk_profile=str(args.risk_profile),
                universe=str(args.universe),
                data_freshness_required=bool(args.data_freshness_required),
                include_attention=bool(args.include_attention),
                include_extended_chip=bool(args.include_extended_chip),
                symbols=symbols or None,
            )
        )
        session.commit()
    if bool(args.json):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result)
    if bool(args.fail_on_degraded) and bool(result.get("degraded")):
        sys.exit(2)


def cmd_daily_radar_health(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.reports.daily_radar_health import (
        DailyRadarDeploymentHealthService,
        daily_radar_health_should_notify,
        format_daily_radar_health_message,
    )
    from app.scheduler.jobs import build_notifiers

    init_db()
    as_of = _parse_date(args.as_of) if args.as_of else None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = DailyRadarDeploymentHealthService(
            max_age_days=int(args.max_age_days),
            warn_age_days=int(args.warn_age_days),
            min_rows=int(args.min_rows),
            min_entry_ready=int(args.min_entry_ready),
        ).evaluate(repo, as_of=as_of)
    channels = _parse_notify_channels(args.notify)
    notification = {
        "notify": str(args.notify),
        "notify_on": str(args.notify_on),
        "triggered": daily_radar_health_should_notify(payload, notify_on=str(args.notify_on)),
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "channels": [],
    }
    if channels and notification["triggered"]:
        notifiers = build_notifiers(channels=channels)
        notification["channels"] = [type(item).__name__ for item in notifiers]
        if notifiers:
            message = format_daily_radar_health_message(payload)
            for notifier in notifiers:
                notification["attempted"] += 1
                try:
                    notifier.send(message)
                    notification["sent"] += 1
                except Exception as exc:  # noqa: BLE001
                    notification["failed"] += 1
                    notification.setdefault("errors", []).append(str(exc))
        else:
            notification["skipped_reason"] = "no_configured_notifiers"
    elif channels:
        notification["skipped_reason"] = "status_below_notify_threshold"
    else:
        notification["skipped_reason"] = "notify_disabled"
    payload["notification"] = notification
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if bool(args.fail_on_notify_error) and int(notification["failed"]) > 0:
        sys.exit(3)
    if bool(args.fail_on_danger) and payload.get("status") == "danger":
        sys.exit(2)


def cmd_intraday_quote_service_status(args) -> None:
    from app.services.intraday_quote_service import load_intraday_quote_service_status

    payload = load_intraday_quote_service_status(
        args.state_path,
        max_age_seconds=int(args.max_age_seconds),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    health_status = str(health.get("status") or "").lower()
    if bool(args.fail_on_warn) and health_status in {"warn", "danger"}:
        sys.exit(2)
    if bool(args.fail_on_danger) and health_status == "danger":
        sys.exit(2)


def cmd_intraday_quote_service_review(args) -> None:
    from app.services.intraday_quote_service import (
        build_intraday_quote_service_review,
        format_intraday_quote_service_review,
    )

    payload = build_intraday_quote_service_review(
        args.state_path,
        max_age_seconds=int(args.max_age_seconds),
        min_heartbeat_count=int(args.min_heartbeat_count),
        min_quote_coverage=float(args.min_quote_coverage),
    )
    if bool(args.json):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_intraday_quote_service_review(payload))
    status = str(payload.get("status") or "").lower()
    if bool(args.fail_on_warn) and status in {"warn", "danger"}:
        sys.exit(2)
    if bool(args.fail_on_danger) and status == "danger":
        sys.exit(2)


def _intraday_quote_symbols_from_args(args) -> list[str]:
    raw = str(getattr(args, "symbols", "") or "")
    symbols_file = str(getattr(args, "symbols_file", "") or "").strip()
    if symbols_file:
        raw = f"{raw},{Path(symbols_file).expanduser().read_text(encoding='utf-8')}"
    items = raw.replace("\n", ",").replace(" ", ",").split(",")
    symbols: list[str] = []
    for item in items:
        symbol = item.strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def cmd_run_intraday_quote_service(args) -> None:
    from app.services.intraday_quote_service import run_intraday_quote_service

    symbols = _intraday_quote_symbols_from_args(args)
    if not symbols:
        print(json.dumps({"status": "error", "error": "symbols_required"}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)
    payload = run_intraday_quote_service(
        symbols,
        simulation=not bool(args.shioaji_live),
        include_bidask=not bool(args.no_bidask),
        allow_outside_session=bool(args.allow_outside_session),
        status_path=str(args.state_path or ""),
        heartbeat_interval_seconds=float(args.heartbeat_interval_seconds),
        max_runtime_seconds=float(args.max_runtime_seconds or 0.0),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    health_status = str(health.get("status") or "").lower()
    state = str(status.get("state") or "").lower()
    if bool(args.fail_on_not_running) and state != "running":
        sys.exit(2)
    if bool(args.fail_on_warn) and health_status in {"warn", "danger"}:
        sys.exit(2)
    if bool(args.fail_on_danger) and health_status == "danger":
        sys.exit(2)


def cmd_daily_radar_trigger_monitor(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.reports.daily_radar_trigger_monitor import (
        DailyRadarTriggerMonitorService,
    )
    from app.scheduler.jobs import (
        build_daily_radar_trigger_price_provider,
        build_notifiers,
        send_daily_radar_trigger_monitor_notification,
    )

    init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = DailyRadarTriggerMonitorService(
            count=int(args.count),
            limit=int(args.limit),
            near_entry_gap=float(args.near_entry_gap),
            near_price_gap_pct=float(args.near_price_gap_pct),
            max_price_age_seconds=int(args.max_price_age_seconds),
            price_provider=build_daily_radar_trigger_price_provider(
                price_source=str(args.price_source),
                shioaji_simulation=not bool(args.shioaji_live),
                streaming_seconds=float(args.streaming_seconds),
            ),
        ).evaluate(repo)
    channels = _parse_notify_channels(args.notify)
    notifiers = build_notifiers(channels=channels) if channels else []
    notification = send_daily_radar_trigger_monitor_notification(
        payload,
        notifiers=notifiers,
        notify_on=str(args.notify_on),
        state_path=str(args.notification_state_path or ""),
        cooldown_minutes=int(args.notification_cooldown_minutes),
        force=bool(args.force_notify),
    )
    notification["notify"] = str(args.notify)
    if channels and not notifiers:
        notification["skipped_reason"] = "no_configured_notifiers"
    payload["notification"] = notification
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if bool(args.fail_on_notify_error) and int(notification["failed"]) > 0:
        sys.exit(3)
    if bool(args.fail_on_ready) and payload.get("status") == "ready":
        sys.exit(2)


def _format_pct(value) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except Exception:
        return "-"


def format_data_health_overview_message(payload: dict) -> str:
    status = str(payload.get("status") or "unknown")
    status_text = {"ok": "正常", "warn": "需留意", "danger": "需處理"}.get(status, status)
    score = payload.get("score")
    lines = [f"[資料健康度] {status_text}" + (f" · {score}/100" if score is not None else "")]
    daily = payload.get("daily_bars") if isinstance(payload.get("daily_bars"), dict) else {}
    universe = payload.get("universe") if isinstance(payload.get("universe"), dict) else {}
    lines.append(
        "日線: "
        f"{daily.get('latest_date') or '無資料'}"
        f"（{daily.get('status') or 'unknown'}，距今 {daily.get('age_days') if daily.get('age_days') is not None else '-'} 天）"
    )
    lines.append(
        "標的池: "
        f"{universe.get('recent_symbols', 0)}/{universe.get('active_symbols', 0)}"
        f"（覆蓋 {_format_pct(universe.get('coverage'))}）"
    )
    industry_coverage = (
        payload.get("instrument_industry_coverage") if isinstance(payload.get("instrument_industry_coverage"), dict) else {}
    )
    if industry_coverage:
        lines.append(
            "產業分類: "
            f"{industry_coverage.get('official_industry_symbols', 0)}/{industry_coverage.get('active_symbols', 0)}"
            f"（正式覆蓋 {float(industry_coverage.get('coverage_pct') or 0):.0f}%；"
            f"缺 {int(industry_coverage.get('missing_industry_count') or 0)}，不足時使用代號 proxy）"
        )
    market_coverage = payload.get("market_data_coverage") if isinstance(payload.get("market_data_coverage"), dict) else {}
    datasets = market_coverage.get("datasets") if isinstance(market_coverage.get("datasets"), list) else []
    if datasets:
        scope_label = "（普通股）" if str(market_coverage.get("scope") or "") == "common_stock" else ""
        lines.append(f"市場資料覆蓋{scope_label}:")
        for dataset in datasets[:6]:
            markets = dataset.get("markets") if isinstance(dataset.get("markets"), list) else []
            market_text = " / ".join(
                f"{market.get('label') or market.get('market') or '-'} {float(market.get('coverage_pct') or 0):.0f}%"
                + (f" 缺 {int(market.get('missing_count') or 0)}" if int(market.get("missing_count") or 0) > 0 else "")
                for market in markets[:4]
            )
            missing_symbols = [str(symbol) for symbol in dataset.get("missing_symbols", []) if symbol][:6]
            missing_text = f"；缺口 {','.join(missing_symbols)}" if missing_symbols else ""
            unavailable_count = int(dataset.get("unavailable_symbol_count") or 0)
            unavailable_symbols = [str(symbol) for symbol in dataset.get("unavailable_symbols", []) if symbol][:6]
            unavailable_text = (
                f"；不可重試暫排 {unavailable_count}"
                + (f"（{','.join(unavailable_symbols)}）" if unavailable_symbols else "")
                if unavailable_count > 0
                else ""
            )
            optional_text = "；加值資料" if dataset.get("optional") else ""
            provider = dataset.get("provider_requirement") if isinstance(dataset.get("provider_requirement"), dict) else {}
            provider_text = f"；需 {provider.get('plan')}" if provider.get("plan") else ""
            lines.append(
                f"- {dataset.get('label') or dataset.get('key')}: "
                f"{float(dataset.get('coverage_pct') or 0):.0f}%"
                f"（{dataset.get('status') or 'unknown'}"
                f"{optional_text}{provider_text}"
                f"{'；' + market_text if market_text else ''}{missing_text}{unavailable_text}）"
            )
    alerts = payload.get("freshness_alerts") if isinstance(payload.get("freshness_alerts"), list) else []
    if alerts:
        lines.append("提醒:")
        for alert in alerts[:5]:
            lines.append(f"- {alert.get('label') or alert.get('key')}: {alert.get('message') or alert.get('status') or '-'}")
    return "\n".join(lines)


def cmd_data_health_overview(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()

    init_db()
    as_of = _parse_date(args.as_of) if args.as_of else None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = repo.get_data_health_overview(as_of=as_of, freshness_days=int(args.freshness_days))
    if bool(args.json):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_data_health_overview_message(payload))
    if bool(args.fail_on_danger) and payload.get("status") == "danger":
        sys.exit(2)


MARKET_DATASET_FLAGS = {
    "daily_bars": {
        "label": "日線資料",
        "include_bars": True,
        "include_institutional": False,
        "include_broker_agg": False,
    },
    "institutional_chip": {
        "label": "法人買賣超",
        "include_bars": False,
        "include_institutional": True,
        "include_broker_agg": False,
    },
    "broker_agg_chip": {
        "label": "分點聚合",
        "include_bars": False,
        "include_institutional": False,
        "include_broker_agg": True,
    },
}

MARKET_DATASET_SCOPES = {
    "daily_bars": {"daily_bars"},
    "institutional_chip": {"institutional_chip"},
    "broker_agg_chip": {"broker_agg_chip"},
}

MARKET_DATA_SKIP_REASON_CODES = {
    "upstream_bad_request",
    "upstream_forbidden",
    "upstream_no_data",
    "upstream_not_found",
}


def _csv_items(text: str | None) -> list[str]:
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


def _csv_floats(text: str | None) -> list[float]:
    values: list[float] = []
    for item in _csv_items(text):
        try:
            values.append(float(item))
        except ValueError:
            continue
    return values


def _recent_unavailable_market_data_symbols(overview: dict, selected_datasets: set[str]) -> dict[str, list[str]]:
    selected_scopes = set().union(*(MARKET_DATASET_SCOPES.get(key, {key}) for key in selected_datasets)) if selected_datasets else set()
    unavailable: dict[str, list[str]] = {}
    summaries = []
    recent_failures = overview.get("recent_sync_failures") if isinstance(overview.get("recent_sync_failures"), dict) else {}
    if recent_failures:
        summaries.append(recent_failures)
    sync_jobs = overview.get("sync_jobs") if isinstance(overview.get("sync_jobs"), list) else []
    for job in sync_jobs:
        if not isinstance(job, dict):
            continue
        summary = job.get("failure_summary") if isinstance(job.get("failure_summary"), dict) else {}
        if summary:
            summaries.append(summary)

    def scope_had_official_attempt(summary: dict, scopes: set[str]) -> bool:
        components = summary.get("components") if isinstance(summary.get("components"), list) else []
        by_name = {
            str(component.get("name") or "").strip(): component
            for component in components
            if isinstance(component, dict) and str(component.get("name") or "").strip()
        }
        for scope in scopes:
            component = by_name.get(scope)
            if not isinstance(component, dict):
                continue
            if bool(component.get("prefer_official_bulk")):
                return True
            if str(component.get("api_mode") or "") == "official_bulk":
                return True
        return False

    for summary in summaries:
        failures = summary.get("symbol_failures") if isinstance(summary.get("symbol_failures"), list) else []
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            symbol = str(failure.get("symbol") or "").strip().upper()
            scopes = {str(scope or "").strip() for scope in failure.get("scopes", []) if str(scope or "").strip()}
            reason_codes = {str(code or "").strip() for code in failure.get("reason_codes", []) if str(code or "").strip()}
            if not symbol:
                continue
            if selected_scopes and scopes and not (scopes & selected_scopes):
                continue
            skip_codes = sorted(reason_codes & MARKET_DATA_SKIP_REASON_CODES)
            if "upstream_payment_required" in reason_codes and scope_had_official_attempt(summary, scopes or selected_scopes):
                skip_codes.append("upstream_payment_required_after_official")
            if not skip_codes:
                continue
            bucket = unavailable.setdefault(symbol, [])
            for code in skip_codes:
                if code not in bucket:
                    bucket.append(code)
    return unavailable


def _coverage_unavailable_market_data_symbols(overview: dict, selected_datasets: set[str]) -> dict[str, list[str]]:
    unavailable: dict[str, list[str]] = {}
    market_coverage = overview.get("market_data_coverage") if isinstance(overview.get("market_data_coverage"), dict) else {}
    datasets = market_coverage.get("datasets") if isinstance(market_coverage.get("datasets"), list) else []
    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        key = str(dataset.get("key") or "").strip()
        if selected_datasets and key not in selected_datasets:
            continue
        reason_map = dataset.get("unavailable_symbol_reasons")
        if not isinstance(reason_map, dict):
            reason_map = {}
        symbols = [str(symbol or "").strip().upper() for symbol in dataset.get("unavailable_symbols", []) if symbol]
        for symbol in symbols:
            if not symbol:
                continue
            bucket = unavailable.setdefault(symbol, [])
            reasons = [str(reason or "").strip() for reason in reason_map.get(symbol, []) if str(reason or "").strip()]
            if not reasons:
                reasons = ["recent_unavailable"]
            for reason in reasons:
                if reason not in bucket:
                    bucket.append(reason)
        markets = dataset.get("markets") if isinstance(dataset.get("markets"), list) else []
        for market in markets:
            if not isinstance(market, dict):
                continue
            market_reason_map = market.get("unavailable_symbol_reasons")
            if not isinstance(market_reason_map, dict):
                market_reason_map = {}
            for symbol in market.get("unavailable_symbols", []) if isinstance(market.get("unavailable_symbols"), list) else []:
                symbol_text = str(symbol or "").strip().upper()
                if not symbol_text:
                    continue
                bucket = unavailable.setdefault(symbol_text, [])
                reasons = [
                    str(reason or "").strip()
                    for reason in market_reason_map.get(symbol_text, [])
                    if str(reason or "").strip()
                ]
                if not reasons:
                    reasons = ["recent_unavailable"]
                for reason in reasons:
                    if reason not in bucket:
                        bucket.append(reason)
    return unavailable


def build_market_data_repair_plan(
    overview: dict,
    *,
    datasets: list[str] | None = None,
    markets: list[str] | None = None,
    symbols: list[str] | None = None,
    as_of=None,
    freshness_days: int = 3,
    lookback_days: int | None = None,
    max_symbols: int = 20,
    exclude_symbols: list[str] | set[str] | tuple[str, ...] | None = None,
) -> dict:
    selected_datasets = {
        key
        for key in (datasets or list(MARKET_DATASET_FLAGS))
        if key in MARKET_DATASET_FLAGS
    }
    selected_markets = {str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()}
    limit = max(1, int(max_symbols or 20))
    end_date = as_of or datetime.now().date()
    window_days = max(1, int(lookback_days if lookback_days is not None else max(7, int(freshness_days or 3) * 3)))
    start_date = end_date - timedelta(days=window_days)
    excluded_symbols = {
        str(item or "").strip().upper()
        for item in (exclude_symbols or [])
        if str(item or "").strip()
    }
    manual_symbols = [
        symbol
        for symbol in dict.fromkeys(str(item).strip().upper() for item in (symbols or []) if str(item).strip())
        if symbol not in excluded_symbols
    ]
    unavailable_symbol_reasons = {}
    if not manual_symbols:
        for source in (
            _coverage_unavailable_market_data_symbols(overview, selected_datasets),
            _recent_unavailable_market_data_symbols(overview, selected_datasets),
        ):
            for symbol, reasons in source.items():
                bucket = unavailable_symbol_reasons.setdefault(symbol, [])
                for reason in reasons:
                    if reason not in bucket:
                        bucket.append(reason)
    unavailable_symbols = set(unavailable_symbol_reasons)
    skipped_symbols: list[str] = []

    market_coverage = overview.get("market_data_coverage") if isinstance(overview.get("market_data_coverage"), dict) else {}
    raw_datasets = market_coverage.get("datasets") if isinstance(market_coverage.get("datasets"), list) else []
    plan_items: list[dict] = []
    selected_symbols: list[str] = []
    skipped_datasets: list[dict] = []
    include_bars = False
    include_institutional = False
    include_broker_agg = False

    for dataset in raw_datasets:
        if not isinstance(dataset, dict):
            continue
        key = str(dataset.get("key") or "").strip()
        if key not in selected_datasets:
            continue
        if not manual_symbols and dataset.get("repairable") is False:
            skipped_datasets.append(
                {
                    "key": key,
                    "label": dataset.get("label") or MARKET_DATASET_FLAGS[key]["label"],
                    "reason": (dataset.get("provider_requirement") or {}).get("reason")
                    or "資料集目前標記為不可用於自動修復",
                }
            )
            continue
        missing_count = int(dataset.get("missing_count") or 0)
        status = str(dataset.get("status") or "").lower()
        if not manual_symbols and status == "ok" and missing_count <= 0:
            continue
        flags = MARKET_DATASET_FLAGS[key]
        dataset_symbols: list[str] = []
        if manual_symbols:
            dataset_symbols = manual_symbols[:limit]
        else:
            markets_payload = dataset.get("markets") if isinstance(dataset.get("markets"), list) else []
            for market in markets_payload:
                if not isinstance(market, dict):
                    continue
                market_key = str(market.get("market") or "").strip().upper()
                if selected_markets and market_key not in selected_markets:
                    continue
                for symbol in market.get("missing_symbols") if isinstance(market.get("missing_symbols"), list) else []:
                    symbol_text = str(symbol or "").strip().upper()
                    if symbol_text in excluded_symbols:
                        continue
                    if symbol_text in unavailable_symbols:
                        if symbol_text not in skipped_symbols:
                            skipped_symbols.append(symbol_text)
                        continue
                    if symbol_text and symbol_text not in dataset_symbols:
                        dataset_symbols.append(symbol_text)
            if not dataset_symbols:
                for symbol in dataset.get("missing_symbols") if isinstance(dataset.get("missing_symbols"), list) else []:
                    symbol_text = str(symbol or "").strip().upper()
                    if symbol_text in excluded_symbols:
                        continue
                    if symbol_text in unavailable_symbols:
                        if symbol_text not in skipped_symbols:
                            skipped_symbols.append(symbol_text)
                        continue
                    if symbol_text and symbol_text not in dataset_symbols:
                        dataset_symbols.append(symbol_text)
        dataset_symbols = dataset_symbols[:limit]
        for symbol in dataset_symbols:
            if symbol not in selected_symbols:
                selected_symbols.append(symbol)
        include_bars = include_bars or bool(flags["include_bars"])
        include_institutional = include_institutional or bool(flags["include_institutional"])
        include_broker_agg = include_broker_agg or bool(flags["include_broker_agg"])
        plan_items.append(
            {
                "key": key,
                "label": dataset.get("label") or flags["label"],
                "status": status or "unknown",
                "coverage_pct": dataset.get("coverage_pct"),
                "missing_count": missing_count,
                "symbols": dataset_symbols,
            }
        )

    selected_symbols = selected_symbols[:limit]
    command = (
        f".venv/bin/python -m app.cli sync-market-bundle --start-date {start_date.isoformat()} --end-date {end_date.isoformat()}"
        f"{' --symbols ' + ','.join(selected_symbols) if selected_symbols else ''}"
        f"{' --include-bars' if include_bars else ' --no-include-bars'}"
        f"{' --include-chip' if include_institutional else ' --no-include-chip'}"
        f"{' --include-broker-agg' if include_broker_agg else ' --no-include-broker-agg'}"
        " --prefer-official-bulk"
        " --no-include-disposition --no-include-attention --no-include-industry-indices"
    )
    return {
        "status": "ready" if plan_items and selected_symbols else "empty",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "freshness_days": int(freshness_days or 3),
        "lookback_days": window_days,
        "max_symbols": limit,
        "selected_datasets": sorted(selected_datasets),
        "selected_markets": sorted(selected_markets),
        "symbols": selected_symbols,
        "excluded_symbols": sorted(excluded_symbols),
        "skipped_symbols": skipped_symbols[:20],
        "skipped_symbol_reasons": {symbol: unavailable_symbol_reasons.get(symbol, []) for symbol in skipped_symbols[:20]},
        "skipped_datasets": skipped_datasets[:6],
        "include_bars": include_bars,
        "include_institutional": include_institutional,
        "include_broker_agg": include_broker_agg,
        "items": plan_items,
        "command": command,
    }


def format_market_data_repair_plan(plan: dict, *, executed: bool = False) -> str:
    title = "[市場資料覆蓋修復]" + (" 已執行" if executed else " 乾跑")
    lines = [
        title,
        f"期間: {plan.get('start_date')} -> {plan.get('end_date')}",
        f"資料集: {', '.join(item.get('label') or item.get('key') or '-' for item in plan.get('items', [])) or '-'}",
        f"代號: {', '.join(plan.get('symbols') or []) or '-'}",
    ]
    skipped_symbols = [str(symbol) for symbol in plan.get("skipped_symbols", []) if symbol]
    if skipped_symbols:
        reason_map = plan.get("skipped_symbol_reasons") if isinstance(plan.get("skipped_symbol_reasons"), dict) else {}
        reason_label = {
            "upstream_bad_request": "參數不支援",
            "upstream_forbidden": "權限不足",
            "upstream_no_data": "查無資料",
            "upstream_not_found": "來源無此代號",
            "upstream_payment_required_after_official": "付費限制且官方重試未補到",
        }
        symbol_texts = []
        for symbol in skipped_symbols:
            reasons = [reason_label.get(str(code), str(code)) for code in reason_map.get(symbol, []) if str(code)]
            symbol_texts.append(f"{symbol}（{'/'.join(reasons)}）" if reasons else symbol)
        lines.append(f"略過近期不可重試: {', '.join(symbol_texts)}")
    skipped_datasets = [item for item in plan.get("skipped_datasets", []) if isinstance(item, dict)]
    if skipped_datasets:
        label_text = ", ".join(str(item.get("label") or item.get("key") or "-") for item in skipped_datasets)
        lines.append(f"略過加值/不可自動修復資料集: {label_text}")
    if plan.get("status") == "empty":
        lines.append("目前沒有可用的市場資料覆蓋缺口樣本可重試。")
    else:
        lines.append(f"建議同步指令: {plan.get('command')}")
        if not executed:
            lines.append("加上 --execute 才會實際同步。")
    return "\n".join(lines)


def format_market_data_repair_result_summary(result: dict | None) -> str:
    if not isinstance(result, dict):
        return "結果: 尚未執行。"
    quality = result.get("sync_quality") if isinstance(result.get("sync_quality"), dict) else {}
    rows = int(result.get("rows_upserted_total") or quality.get("rows_upserted") or 0)
    failed = int(result.get("failed_count_total") or quality.get("failed_count") or 0)
    degraded = int(result.get("degraded_count") or quality.get("degraded_count") or 0)
    status = str(quality.get("status") or ("partial" if result.get("partial_failure") else "complete"))
    lines = [f"結果: {status}，寫入 {rows} 筆，失敗 {failed}，降級 {degraded}"]

    components = quality.get("components") if isinstance(quality.get("components"), dict) else {}
    for key, flags in MARKET_DATASET_FLAGS.items():
        component = components.get(key) if isinstance(components.get(key), dict) else None
        if not component:
            continue
        label = flags["label"]
        component_status = str(component.get("status") or "unknown")
        component_rows = int(component.get("rows_upserted") or 0)
        component_failed = int(component.get("failed_count") or 0)
        official_rows = int(component.get("official_fallback_rows") or 0)
        api_mode = str(component.get("api_mode") or "")
        official_text = f"，官方 {official_rows}" if official_rows else ""
        mode_text = f"，{api_mode}" if api_mode else ""
        lines.append(f"- {label}: {component_status}，寫入 {component_rows}{official_text}，失敗 {component_failed}{mode_text}")

    failure_texts: list[str] = []
    bundle = result.get("bundle") if isinstance(result.get("bundle"), dict) else {}
    for key, payload in bundle.items():
        if not isinstance(payload, dict):
            continue
        label = MARKET_DATASET_FLAGS.get(str(key), {}).get("label", str(key))
        for sample in payload.get("failed_samples", []) if isinstance(payload.get("failed_samples"), list) else []:
            if not isinstance(sample, dict):
                continue
            symbol = str(sample.get("symbol") or "").strip()
            message = str(sample.get("error") or sample.get("error_type") or "").strip()
            if symbol:
                failure_texts.append(f"{symbol} {label}: {message or '失敗'}")
            if len(failure_texts) >= 6:
                break
        if len(failure_texts) >= 6:
            break
    if failure_texts:
        lines.append(f"失敗樣本: {'；'.join(failure_texts)}")
    return "\n".join(lines)


def format_market_data_repair_after_summary(after: dict | None) -> str:
    if not isinstance(after, dict):
        return ""
    lines = ["修復後健康摘要:"]
    universe = after.get("universe") if isinstance(after.get("universe"), dict) else {}
    if universe:
        lines.append(
            "標的池: "
            f"{int(universe.get('recent_symbols') or 0)}/{int(universe.get('active_symbols') or 0)}"
            f"（覆蓋 {_format_pct(universe.get('coverage'))}）"
        )
    market_coverage = after.get("market_data_coverage") if isinstance(after.get("market_data_coverage"), dict) else {}
    datasets = market_coverage.get("datasets") if isinstance(market_coverage.get("datasets"), list) else []
    dataset_texts = []
    for dataset in datasets[:6]:
        if not isinstance(dataset, dict):
            continue
        dataset_texts.append(
            f"{dataset.get('label') or dataset.get('key')}: "
            f"{float(dataset.get('coverage_pct') or 0):.0f}%（{dataset.get('status') or 'unknown'}）"
        )
    if dataset_texts:
        lines.append("市場資料: " + " / ".join(dataset_texts))
    return "\n".join(lines)


def cmd_repair_market_data_coverage(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.data.sync_service import SyncService

    init_db()
    as_of = _parse_date(args.as_of) if args.as_of else datetime.now().date()
    dataset_items = _csv_items(args.datasets)
    selected_datasets = list(MARKET_DATASET_FLAGS) if not dataset_items or "all" in dataset_items else dataset_items
    selected_markets = _csv_items(args.markets)
    manual_symbols = _csv_items(args.symbols)
    batch_limit = max(1, int(getattr(args, "batches", 1) or 1))
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        overview = repo.get_data_health_overview(as_of=as_of, freshness_days=int(args.freshness_days))
        plan = build_market_data_repair_plan(
            overview,
            datasets=selected_datasets,
            markets=selected_markets,
            symbols=manual_symbols,
            as_of=as_of,
            freshness_days=int(args.freshness_days),
            lookback_days=int(args.lookback_days),
            max_symbols=int(args.max_symbols),
        )
        result = None
        after = None
        batch_runs: list[dict] = []
        attempted_symbols: set[str] = set()
        if bool(args.execute) and plan["status"] == "ready":
            service = SyncService(repo=repo)
            current_plan = plan
            for batch_index in range(batch_limit):
                if current_plan["status"] != "ready":
                    break
                current_result = service.sync_market_bundle(
                    start_date=_parse_date(current_plan["start_date"]),
                    end_date=_parse_date(current_plan["end_date"]),
                    symbols=list(current_plan["symbols"]),
                    include_bars=bool(current_plan["include_bars"]),
                    include_institutional=bool(current_plan["include_institutional"]),
                    include_broker_agg=bool(current_plan["include_broker_agg"]),
                    include_disposition=False,
                    include_attention=False,
                    include_industry_indices=False,
                    prefer_official_bulk=True,
                )
                session.commit()
                batch_runs.append({"batch": batch_index + 1, "plan": current_plan, "result": current_result})
                attempted_symbols.update(str(symbol).strip().upper() for symbol in current_plan["symbols"] if str(symbol).strip())
                result = current_result
                after = repo.get_data_health_overview(as_of=as_of, freshness_days=int(args.freshness_days))
                if batch_index >= batch_limit - 1:
                    break
                current_plan = build_market_data_repair_plan(
                    after,
                    datasets=selected_datasets,
                    markets=selected_markets,
                    symbols=manual_symbols,
                    as_of=as_of,
                    freshness_days=int(args.freshness_days),
                    lookback_days=int(args.lookback_days),
                    max_symbols=int(args.max_symbols),
                    exclude_symbols=attempted_symbols,
                )
    payload = {
        "dry_run": not bool(args.execute),
        "plan": plan,
        "result": result,
        "after": after,
        "batches": batch_runs,
        "batch_count": len(batch_runs),
    }
    if bool(args.json):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if batch_runs:
            for batch in batch_runs:
                print(format_market_data_repair_plan(batch["plan"], executed=True))
                print(format_market_data_repair_result_summary(batch["result"]))
            after_summary = format_market_data_repair_after_summary(after)
            if after_summary:
                print(after_summary)
        else:
            print(format_market_data_repair_plan(plan, executed=bool(args.execute)))
            if result is not None:
                print(format_market_data_repair_result_summary(result))
                after_summary = format_market_data_repair_after_summary(after)
                if after_summary:
                    print(after_summary)
    if bool(args.fail_on_empty) and plan["status"] == "empty":
        sys.exit(2)


OPS_READINESS_CLI_STATUS_RANK = {"ok": 0, "warn": 1, "danger": 2}
OPS_READINESS_CLI_STATUS_TEXT = {"ok": "正常", "warn": "需留意", "danger": "需處理"}


def _ops_readiness_cli_status_worst(*statuses) -> str:  # noqa: ANN001
    normalized = [str(status or "warn").lower() for status in statuses]
    return max(normalized or ["warn"], key=lambda item: OPS_READINESS_CLI_STATUS_RANK.get(item, 1))


def _merge_intraday_quote_review_readiness(payload: dict, review: dict) -> dict:
    output = dict(payload)
    review_status = str(review.get("status") or "warn").lower()
    if review_status not in OPS_READINESS_CLI_STATUS_RANK:
        review_status = "warn"
    evidence = review.get("evidence") if isinstance(review.get("evidence"), dict) else {}
    issues = review.get("issues") if isinstance(review.get("issues"), list) else []
    blocking_reasons = [
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and str(issue.get("severity") or "").lower() == "danger" and issue.get("code")
    ]
    warnings = [
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and str(issue.get("severity") or "").lower() == "warn" and issue.get("code")
    ]
    component = {
        "key": "intraday_quote_review",
        "label": "盤後行情回顧",
        "status": review_status,
        "value": review.get("label") or OPS_READINESS_CLI_STATUS_TEXT.get(review_status, "需留意"),
        "meta": (
            f"heartbeat {evidence.get('heartbeat_count', 0)} 次，"
            f"報價覆蓋 {float(evidence.get('max_quote_coverage') or 0.0):.0%}，"
            f"snapshot {evidence.get('snapshot_freshness') or '-'}。"
        ),
        "details": {
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "summary": review.get("summary"),
            "issues": issues,
            "next_actions": review.get("next_actions") if isinstance(review.get("next_actions"), list) else [],
            "evidence": evidence,
        },
    }

    components = list(output.get("components") if isinstance(output.get("components"), list) else [])
    components.append(component)
    output["components"] = components

    old_status = str(output.get("status") or "warn").lower()
    new_status = _ops_readiness_cli_status_worst(old_status, review_status)
    output["status"] = new_status
    output["status_text"] = OPS_READINESS_CLI_STATUS_TEXT.get(new_status, "需留意")

    output_blocking = list(output.get("blocking_reasons") if isinstance(output.get("blocking_reasons"), list) else [])
    output_warnings = list(output.get("warnings") if isinstance(output.get("warnings"), list) else [])
    if review_status == "danger" and "intraday_quote_review" not in output_blocking:
        output_blocking.append("intraday_quote_review")
    elif review_status == "warn" and "intraday_quote_review" not in output_warnings:
        output_warnings.append("intraday_quote_review")
    output["blocking_reasons"] = output_blocking
    output["warnings"] = output_warnings

    next_steps = list(output.get("next_steps") if isinstance(output.get("next_steps"), list) else [])
    if review_status != "ok":
        next_steps.extend(str(action) for action in component["details"]["next_actions"] if action)
    output["next_steps"] = list(dict.fromkeys(next_steps))

    metrics = dict(output.get("metrics") if isinstance(output.get("metrics"), dict) else {})
    metrics.update(
        {
            "intraday_quote_review_status": review_status,
            "intraday_quote_review_coverage": evidence.get("max_quote_coverage"),
            "intraday_quote_review_heartbeat_count": evidence.get("heartbeat_count"),
        }
    )
    output["metrics"] = metrics
    output["intraday_quote_review"] = review

    if old_status != "danger" and review_status == "danger":
        brief = dict(output.get("decision_brief") if isinstance(output.get("decision_brief"), dict) else {})
        brief.update(
            {
                "state": "blocked",
                "title": "盤後行情回顧需要先處理",
                "summary": review.get("summary") or "盤中行情服務本輪可靠度不足；先檢查後再信任觸發監控。",
                "primary_action": {"key": "intraday_quote_review", "label": "查看盤後行情回顧"},
                "next_step": next_steps[0] if next_steps else "先檢查盤中行情服務狀態檔與報價覆蓋。",
            }
        )
        output["decision_brief"] = brief
    return output


def cmd_ops_readiness(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.reports.ops_readiness import OpsReadinessService

    init_db()
    as_of = _parse_date(args.as_of) if args.as_of else None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = OpsReadinessService(
            max_age_days=int(args.max_age_days),
            warn_age_days=int(args.warn_age_days),
            min_rows=int(args.min_rows),
            min_entry_ready=int(args.min_entry_ready),
            eod_limit=int(args.eod_limit),
            min_observation_days=int(args.min_observation_days),
            promotion_gate_limit=int(args.promotion_gate_limit),
        ).build(
            repo,
            as_of=as_of,
            advisor_name=str(args.advisor_name),
            strategy_name=str(args.strategy_name),
            strategy_version=str(args.strategy_version),
            target_phase=str(args.target_phase),
        )
    if bool(args.include_intraday_quote_review):
        from app.services.intraday_quote_service import build_intraday_quote_service_review

        review = build_intraday_quote_service_review(
            str(args.intraday_quote_state_path or ""),
            max_age_seconds=int(args.intraday_quote_review_max_age_seconds),
            min_heartbeat_count=int(args.intraday_quote_review_min_heartbeat_count),
            min_quote_coverage=float(args.intraday_quote_review_min_quote_coverage),
        )
        payload = _merge_intraday_quote_review_readiness(payload, review)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if bool(args.fail_on_danger) and payload.get("status") == "danger":
        sys.exit(2)


def cmd_decision_radar_phase1_acceptance(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.reports.decision_radar_phase1_acceptance import (
        DecisionRadarPhase1AcceptanceService,
        format_decision_radar_phase1_acceptance_message,
    )

    init_db()
    as_of = _parse_date(args.as_of) if args.as_of else None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = DecisionRadarPhase1AcceptanceService(
            max_age_days=int(args.max_age_days),
            warn_age_days=int(args.warn_age_days),
            min_rows=int(args.min_rows),
            min_entry_ready=int(args.min_entry_ready),
        ).build(repo, as_of=as_of)
    if bool(args.json):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_decision_radar_phase1_acceptance_message(payload))
    if bool(args.fail_on_danger) and payload.get("status") == "danger":
        sys.exit(2)


def cmd_write_launchd_plist(args) -> None:
    from app.scheduler.launchd import write_daily_radar_launchd_plist

    project_dir = Path(args.project_dir or Path.cwd()).expanduser().resolve()
    output = write_daily_radar_launchd_plist(
        args.output,
        project_dir=project_dir,
        python_path=args.python,
        label=str(args.label),
        hour=int(args.hour),
        minute=int(args.minute),
        trade_date_mode=str(args.trade_date_mode),
        target_count=int(args.target_count),
        risk_profile=str(args.risk_profile),
        threshold_offset=float(args.threshold_offset),
        universe=str(args.universe),
        symbols=str(args.symbols or ""),
        data_freshness_required=bool(args.data_freshness_required),
        index_constituents_url=str(args.index_constituents_url or ""),
        index_constituents_key=str(args.index_constituents_key or ""),
        index_constituents_source=str(args.index_constituents_source or "official_url"),
        notify=str(args.notify),
        notify_style=str(args.notify_style),
        run_at_load=bool(args.run_at_load),
        stdout_path=args.stdout_path,
        stderr_path=args.stderr_path,
    )
    print(
        {
            "plist": str(output),
            "install_hint": f"launchctl bootstrap gui/$(id -u) {output}",
            "uninstall_hint": f"launchctl bootout gui/$(id -u) {output}",
        }
    )


def cmd_write_trigger_monitor_launchd_plist(args) -> None:
    from app.scheduler.launchd import write_daily_radar_trigger_monitor_launchd_plist

    project_dir = Path(args.project_dir or Path.cwd()).expanduser().resolve()
    output = write_daily_radar_trigger_monitor_launchd_plist(
        args.output,
        project_dir=project_dir,
        python_path=args.python,
        label=str(args.label),
        hour=int(args.hour),
        minute=int(args.minute),
        count=int(args.count),
        limit=int(args.limit),
        near_entry_gap=float(args.near_entry_gap),
        near_price_gap_pct=float(args.near_price_gap_pct),
        notify=str(args.notify),
        notify_on=str(args.notify_on),
        price_source=str(args.price_source),
        shioaji_simulation=not bool(args.shioaji_live),
        max_price_age_seconds=int(args.max_price_age_seconds),
        streaming_seconds=float(args.streaming_seconds),
        notification_state_path=str(args.notification_state_path or ""),
        notification_cooldown_minutes=int(args.notification_cooldown_minutes),
        force_notify=bool(args.force_notify),
        fail_on_ready=bool(args.fail_on_ready),
        fail_on_notify_error=bool(args.fail_on_notify_error),
        run_at_load=bool(args.run_at_load),
        stdout_path=args.stdout_path,
        stderr_path=args.stderr_path,
    )
    print(
        {
            "plist": str(output),
            "install_hint": f"launchctl bootstrap gui/$(id -u) {output}",
            "uninstall_hint": f"launchctl bootout gui/$(id -u) {output}",
        }
    )


def cmd_write_systemd_units(args) -> None:
    from app.scheduler.systemd import write_daily_radar_systemd_units

    project_dir = Path(args.project_dir or Path.cwd()).expanduser().absolute()
    paths = write_daily_radar_systemd_units(
        args.output_dir,
        project_dir=project_dir,
        service_name=str(args.service_name),
        timer_name=str(args.timer_name),
        hour=int(args.hour),
        minute=int(args.minute),
        python_path=args.python,
        env_file=args.env_file,
        user=str(args.user),
        group=str(args.group),
        trade_date_mode=str(args.trade_date_mode),
        target_count=int(args.target_count),
        risk_profile=str(args.risk_profile),
        threshold_offset=float(args.threshold_offset),
        universe=str(args.universe),
        symbols=str(args.symbols or ""),
        data_freshness_required=bool(args.data_freshness_required),
        index_constituents_url=str(args.index_constituents_url or ""),
        index_constituents_key=str(args.index_constituents_key or ""),
        index_constituents_source=str(args.index_constituents_source or "official_url"),
        notify=str(args.notify),
        notify_style=str(args.notify_style),
        include_health_monitor=bool(args.include_health_monitor),
        health_service_name=str(args.health_service_name),
        health_timer_name=str(args.health_timer_name),
        health_hour=int(args.health_hour),
        health_minute=int(args.health_minute),
        health_max_age_days=int(args.health_max_age_days),
        health_warn_age_days=int(args.health_warn_age_days),
        health_min_rows=int(args.health_min_rows),
        health_min_entry_ready=int(args.health_min_entry_ready),
        health_notify=str(args.health_notify),
        health_notify_on=str(args.health_notify_on),
        health_fail_on_danger=bool(args.health_fail_on_danger),
        health_fail_on_notify_error=bool(args.health_fail_on_notify_error),
        health_use_ops_readiness=bool(args.health_use_ops_readiness),
        health_ops_target_phase=str(args.health_ops_target_phase),
        health_include_intraday_quote_review=bool(args.health_include_intraday_quote_review),
        health_intraday_quote_review_max_age_seconds=int(args.health_intraday_quote_review_max_age_seconds),
        health_intraday_quote_review_min_heartbeat_count=int(args.health_intraday_quote_review_min_heartbeat_count),
        health_intraday_quote_review_min_quote_coverage=float(args.health_intraday_quote_review_min_quote_coverage),
        include_trigger_monitor=bool(args.include_trigger_monitor),
        trigger_monitor_service_name=str(args.trigger_monitor_service_name),
        trigger_monitor_timer_name=str(args.trigger_monitor_timer_name),
        trigger_monitor_hour=int(args.trigger_monitor_hour),
        trigger_monitor_minute=int(args.trigger_monitor_minute),
        trigger_monitor_count=int(args.trigger_monitor_count),
        trigger_monitor_limit=int(args.trigger_monitor_limit),
        trigger_monitor_near_entry_gap=float(args.trigger_monitor_near_entry_gap),
        trigger_monitor_near_price_gap_pct=float(args.trigger_monitor_near_price_gap_pct),
        trigger_monitor_notify=str(args.trigger_monitor_notify),
        trigger_monitor_notify_on=str(args.trigger_monitor_notify_on),
        trigger_monitor_price_source=str(args.trigger_monitor_price_source),
        trigger_monitor_shioaji_simulation=not bool(args.trigger_monitor_shioaji_live),
        trigger_monitor_max_price_age_seconds=int(args.trigger_monitor_max_price_age_seconds),
        trigger_monitor_streaming_seconds=float(args.trigger_monitor_streaming_seconds),
        trigger_monitor_notification_state_path=str(args.trigger_monitor_notification_state_path or ""),
        trigger_monitor_notification_cooldown_minutes=int(args.trigger_monitor_notification_cooldown_minutes),
        trigger_monitor_force_notify=bool(args.trigger_monitor_force_notify),
        trigger_monitor_fail_on_ready=bool(args.trigger_monitor_fail_on_ready),
        trigger_monitor_fail_on_notify_error=bool(args.trigger_monitor_fail_on_notify_error),
        include_intraday_quote_service=bool(args.include_intraday_quote_service),
        intraday_quote_service_name=str(args.intraday_quote_service_name),
        intraday_quote_health_service_name=str(args.intraday_quote_health_service_name),
        intraday_quote_health_timer_name=str(args.intraday_quote_health_timer_name),
        intraday_quote_symbols=str(args.intraday_quote_symbols or ""),
        intraday_quote_state_path=str(args.intraday_quote_state_path or ""),
        intraday_quote_heartbeat_interval_seconds=float(args.intraday_quote_heartbeat_interval_seconds),
        intraday_quote_shioaji_simulation=not bool(args.intraday_quote_shioaji_live),
        intraday_quote_include_bidask=not bool(args.intraday_quote_no_bidask),
        intraday_quote_allow_outside_session=bool(args.intraday_quote_allow_outside_session),
        intraday_quote_restart=str(args.intraday_quote_restart),
        intraday_quote_restart_sec=int(args.intraday_quote_restart_sec),
        intraday_quote_health_max_age_seconds=int(args.intraday_quote_health_max_age_seconds),
        intraday_quote_health_interval_seconds=int(args.intraday_quote_health_interval_seconds),
        intraday_quote_health_fail_on_warn=not bool(args.no_intraday_quote_health_fail_on_warn),
        intraday_quote_health_fail_on_danger=bool(args.intraday_quote_health_fail_on_danger),
    )
    install_files = [str(paths["service"]), str(paths["timer"])]
    enable_timers = [str(args.timer_name)]
    enable_services = []
    if "health_service" in paths and "health_timer" in paths:
        install_files.extend([str(paths["health_service"]), str(paths["health_timer"])])
        enable_timers.append(str(args.health_timer_name))
    if "trigger_monitor_service" in paths and "trigger_monitor_timer" in paths:
        install_files.extend([str(paths["trigger_monitor_service"]), str(paths["trigger_monitor_timer"])])
        enable_timers.append(str(args.trigger_monitor_timer_name))
    if "intraday_quote_service" in paths:
        install_files.append(str(paths["intraday_quote_service"]))
        enable_services.append(str(args.intraday_quote_service_name))
    if "intraday_quote_health_service" in paths and "intraday_quote_health_timer" in paths:
        install_files.extend([str(paths["intraday_quote_health_service"]), str(paths["intraday_quote_health_timer"])])
        enable_timers.append(str(args.intraday_quote_health_timer_name))
    enable_units = enable_timers + enable_services
    print(
        {
            "service": str(paths["service"]),
            "timer": str(paths["timer"]),
            "health_service": str(paths["health_service"]) if "health_service" in paths else None,
            "health_timer": str(paths["health_timer"]) if "health_timer" in paths else None,
            "trigger_monitor_service": str(paths["trigger_monitor_service"]) if "trigger_monitor_service" in paths else None,
            "trigger_monitor_timer": str(paths["trigger_monitor_timer"]) if "trigger_monitor_timer" in paths else None,
            "intraday_quote_service": str(paths["intraday_quote_service"]) if "intraday_quote_service" in paths else None,
            "intraday_quote_health_service": str(paths["intraday_quote_health_service"]) if "intraday_quote_health_service" in paths else None,
            "intraday_quote_health_timer": str(paths["intraday_quote_health_timer"]) if "intraday_quote_health_timer" in paths else None,
            "install_hint": "sudo cp {files} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now {timers}".format(
                files=" ".join(install_files),
                timers=" ".join(enable_units),
            ),
            "status_hint": "systemctl status {timers} --no-pager".format(timers=" ".join(enable_units)),
            "logs_hint": "journalctl -u {service_name} -u {health_service_name} -u {trigger_service_name} -u {quote_service_name} -u {quote_health_service_name} -n 100 --no-pager".format(
                service_name=str(args.service_name),
                health_service_name=str(args.health_service_name),
                trigger_service_name=str(args.trigger_monitor_service_name),
                quote_service_name=str(args.intraday_quote_service_name),
                quote_health_service_name=str(args.intraday_quote_health_service_name),
            ),
        }
    )


def cmd_write_docker_compose(args) -> None:
    from app.scheduler.docker import docker_compose_command, write_daily_radar_docker_assets

    project_dir = Path(args.project_dir or Path.cwd()).expanduser().absolute()
    paths = write_daily_radar_docker_assets(
        args.output_dir,
        project_dir=project_dir,
        compose_name=str(args.compose_name),
        dockerfile_name=str(args.dockerfile_name),
        python_image=str(args.python_image),
        service_name=str(args.service_name),
        image=str(args.image),
        env_file=args.env_file,
        hour=int(args.hour),
        minute=int(args.minute),
        trade_date_mode=str(args.trade_date_mode),
        target_count=int(args.target_count),
        risk_profile=str(args.risk_profile),
        threshold_offset=float(args.threshold_offset),
        universe=str(args.universe),
        symbols=str(args.symbols or ""),
        data_freshness_required=bool(args.data_freshness_required),
        index_constituents_url=str(args.index_constituents_url or ""),
        index_constituents_key=str(args.index_constituents_key or ""),
        index_constituents_source=str(args.index_constituents_source or "official_url"),
        notify=str(args.notify),
        radar_notify_style=str(args.notify_style),
        enable_trigger_monitor=bool(args.enable_trigger_monitor),
        trigger_monitor_hour=int(args.trigger_monitor_hour),
        trigger_monitor_minute=int(args.trigger_monitor_minute),
        trigger_monitor_count=int(args.trigger_monitor_count),
        trigger_monitor_limit=int(args.trigger_monitor_limit),
        trigger_monitor_near_entry_gap=float(args.trigger_monitor_near_entry_gap),
        trigger_monitor_near_price_gap_pct=float(args.trigger_monitor_near_price_gap_pct),
        trigger_monitor_notify_on=str(args.trigger_monitor_notify_on),
        trigger_monitor_price_source=str(args.trigger_monitor_price_source),
        trigger_monitor_shioaji_simulation=not bool(args.trigger_monitor_shioaji_live),
        trigger_monitor_max_price_age_seconds=int(args.trigger_monitor_max_price_age_seconds),
        trigger_monitor_streaming_seconds=float(args.trigger_monitor_streaming_seconds),
        trigger_monitor_notification_state_path=str(args.trigger_monitor_notification_state_path or ""),
        trigger_monitor_notification_cooldown_minutes=int(args.trigger_monitor_notification_cooldown_minutes),
        trigger_monitor_force_notify=bool(args.trigger_monitor_force_notify),
        restart=str(args.restart),
        include_healthcheck=bool(args.include_healthcheck),
        health_max_age_days=int(args.health_max_age_days),
        health_warn_age_days=int(args.health_warn_age_days),
        health_min_rows=int(args.health_min_rows),
        health_min_entry_ready=int(args.health_min_entry_ready),
        health_interval=str(args.health_interval),
        health_timeout=str(args.health_timeout),
        health_retries=int(args.health_retries),
        health_start_period=str(args.health_start_period),
        health_use_ops_readiness=bool(args.health_use_ops_readiness),
        health_ops_target_phase=str(args.health_ops_target_phase),
        health_include_intraday_quote_review=bool(args.health_include_intraday_quote_review),
        health_intraday_quote_review_max_age_seconds=int(args.health_intraday_quote_review_max_age_seconds),
        health_intraday_quote_review_min_heartbeat_count=int(args.health_intraday_quote_review_min_heartbeat_count),
        health_intraday_quote_review_min_quote_coverage=float(args.health_intraday_quote_review_min_quote_coverage),
        enable_intraday_quote_service=bool(args.enable_intraday_quote_service),
        intraday_quote_service_name=str(args.intraday_quote_service_name),
        intraday_quote_symbols=str(args.intraday_quote_symbols or ""),
        intraday_quote_state_path=str(args.intraday_quote_state_path or ""),
        intraday_quote_heartbeat_interval_seconds=float(args.intraday_quote_heartbeat_interval_seconds),
        intraday_quote_shioaji_simulation=not bool(args.intraday_quote_shioaji_live),
        intraday_quote_include_bidask=not bool(args.intraday_quote_no_bidask),
        intraday_quote_allow_outside_session=bool(args.intraday_quote_allow_outside_session),
        intraday_quote_health_max_age_seconds=int(args.intraday_quote_health_max_age_seconds),
        intraday_quote_health_interval=str(args.intraday_quote_health_interval),
        intraday_quote_health_timeout=str(args.intraday_quote_health_timeout),
        intraday_quote_health_retries=int(args.intraday_quote_health_retries),
        intraday_quote_health_start_period=str(args.intraday_quote_health_start_period),
    )
    log_services = [str(args.service_name)]
    if bool(args.enable_intraday_quote_service):
        log_services.append(str(args.intraday_quote_service_name))
    print(
        {
            "compose": str(paths["compose"]),
            "dockerfile": str(paths["dockerfile"]),
            "build_hint": docker_compose_command(paths["compose"], "build"),
            "start_hint": docker_compose_command(paths["compose"], "up", "-d"),
            "logs_hint": docker_compose_command(paths["compose"], "logs", "-f", *log_services),
            "stop_hint": docker_compose_command(paths["compose"], "down"),
        }
    )


def cmd_rebuild_features(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.features.snapshot_builder import rebuild_feature_snapshots

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date) if args.end_date else datetime.now().date()
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or None

    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = rebuild_feature_snapshots(
            repo=repo,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
        )
        session.commit()
    print(result)


def cmd_live_buy(args) -> None:
    from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway
    from app.execution import OrderIntent, TradingExecutionService

    if bool(args.enforce_session_check) and not _is_twse_session_open():
        raise RuntimeError("TWSE session closed (09:00~13:30 Asia/Taipei). Use --enforce-session-check only during session.")

    TradingRepository, get_session_factory, init_db = _load_db_modules()
    init_db()
    gateway = ShioajiGateway(
        ShioajiConfig(
            simulation=not args.live,
            enforce_session_check=bool(args.enforce_session_check),
            allow_live_order=bool(args.live),
            live_order_nonce=str(args.live_order_nonce or ""),
        )
    )
    explicit_price = float(args.price) if args.price is not None else None
    base_price = gateway.resolve_order_price(symbol=args.symbol, explicit_price=explicit_price, price_offset_ticks=0)
    final_price = _apply_tick_offset(base_price, int(args.price_offset_ticks))
    intent = OrderIntent(
        source="cli",
        environment="live" if args.live else "simulation",
        symbol=args.symbol,
        side="buy",
        price=float(final_price),
        order_lot="Common" if bool(args.common_lot) else "IntradayOdd",
        metadata={
            "command": "live-buy",
            "price_offset_ticks": int(args.price_offset_ticks),
            "price_source": "manual" if explicit_price is not None else "reference",
        },
    )
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        service = TradingExecutionService(gateway=gateway, repository=repo)
        result = service.execute_intent(intent)
        session.commit()
    payload = result.to_dict()
    payload["price_offset_ticks"] = int(args.price_offset_ticks)
    payload["resolved_price"] = float(final_price)
    payload["price_source"] = "manual" if explicit_price is not None else "reference"
    print(payload)


def cmd_shioaji_doctor(args) -> None:
    from app.broker.shioaji_account_service import ShioajiAccountService

    service = ShioajiAccountService()
    result = service.run_health_check(simulation=bool(args.simulation))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def cmd_account_snapshot(args) -> None:
    from app.broker.shioaji_account_service import ShioajiAccountService

    service = ShioajiAccountService()
    try:
        result = service.get_account_snapshot(simulation=bool(args.simulation))
    except Exception as exc:  # noqa: BLE001
        # Shioaji may include person/account identifiers and network details in
        # login errors. Keep CLI failures machine-readable without echoing the
        # broker response or a traceback containing sensitive fields.
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "account_snapshot_failed",
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def cmd_order_preview(args) -> None:
    from app.backtest.costs import estimate_buy_total_cost
    from app.execution import OrderIntent
    from app.execution.order_preview import OrderPreviewService

    TradingRepository, get_session_factory, init_db = _load_db_modules()
    init_db()
    metadata = {
        "strategy_version": args.strategy_version,
        "position_quantity": int(args.position_before),
        "max_price_deviation_ticks": int(args.max_price_deviation_ticks),
        "max_price_deviation_pct": float(args.max_price_deviation_pct),
    }
    if args.reference_price is not None:
        metadata["reference_price"] = float(args.reference_price)
    intent = OrderIntent(
        source="cli",
        environment="simulation",
        symbol=args.symbol,
        side=args.side,
        price=float(args.price),
        quantity=int(args.quantity),
        order_lot="Common" if bool(args.common_lot) else "IntradayOdd",
        strategy_name=args.strategy_name,
        signal_id=args.signal_id,
        metadata=metadata,
    )
    estimated_total_cost = estimate_buy_total_cost(float(args.price) * int(args.quantity)) if args.side == "buy" else 0.0
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        preview = OrderPreviewService(ttl_seconds=int(args.ttl_seconds), repository=repo).create_preview(
            intent=intent,
            estimated_total_cost=estimated_total_cost,
            available_cash=float(args.available_cash),
            position_before=int(args.position_before),
            checks=[{"name": "cli_preview", "passed": True}],
            strategy_version=args.strategy_version,
            signal_id=args.signal_id,
        )
        session.commit()
    print(json.dumps(preview.to_dict(), ensure_ascii=False, indent=2))


def cmd_reconcile(args) -> None:
    from app.portfolio.reconciliation import ReconciliationService

    TradingRepository, get_session_factory, init_db = _load_db_modules()
    init_db()
    expected_positions = _parse_position_map(args.expected_positions)
    actual_positions = _parse_position_map(args.actual_positions)
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = ReconciliationService(repository=repo).reconcile(
            expected_cash=float(args.expected_cash),
            actual_cash=float(args.actual_cash),
            expected_positions=expected_positions,
            actual_positions=actual_positions,
        )
        session.commit()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def cmd_trading_audit(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()

    init_db()
    limit = max(1, min(100, int(args.limit)))
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = {
            "executions": repo.list_recent_trading_execution_records(limit=limit),
            "previews": repo.list_recent_order_preview_records(limit=limit),
            "promotion_gates": repo.list_recent_promotion_gate_records(limit=limit),
            "reconciliations": repo.list_recent_reconciliation_records(limit=limit),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_eod_summary(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.reports.eod_reconciliation import EndOfDayReconciliationSummaryService

    init_db()
    as_of = _parse_date(args.as_of) if args.as_of else None
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = EndOfDayReconciliationSummaryService(limit=int(args.limit)).build(repo, as_of=as_of)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_advisor_observation_add(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()

    init_db()
    blocker_reasons = [item for item in (args.blocker_reason or []) if str(item).strip()]
    if bool(args.failed) and not blocker_reasons:
        blocker_reasons = ["manual_marked_failed"]
    meta = {}
    if args.meta_json:
        try:
            payload = json.loads(args.meta_json)
            if isinstance(payload, dict):
                meta = payload
        except json.JSONDecodeError as exc:
            raise RuntimeError("--meta-json must be a JSON object") from exc
    record = {
        "observation_date": args.observation_date or datetime.now().date().isoformat(),
        "symbol": args.symbol,
        "advisor_name": args.advisor_name,
        "advisor_version": args.advisor_version,
        "preview_created": bool(args.preview_created),
        "human_action": args.human_action,
        "simulation_result": args.simulation_result,
        "data_quality": args.data_quality,
        "passed": not bool(args.failed),
        "blocker_reasons": blocker_reasons,
        "notes": args.notes,
        "meta": meta,
    }
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        saved = repo.add_advisor_observation_record(record=record)
        session.commit()
    print(json.dumps(saved, ensure_ascii=False, indent=2))


def cmd_advisor_observation_summary(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()

    init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        payload = repo.summarize_advisor_observation_period(
            advisor_name=args.advisor_name,
            min_days=int(args.min_days),
            limit=int(args.limit),
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_promotion_readiness(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.execution.promotion_readiness import PromotionReadinessService

    init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        decision = PromotionReadinessService(
            min_observation_days=int(args.min_observation_days),
            promotion_gate_limit=int(args.promotion_gate_limit),
        ).evaluate(
            repository=repo,
            advisor_name=args.advisor_name,
            strategy_name=args.strategy_name,
            strategy_version=args.strategy_version,
            target_phase=args.target_phase,
        )
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))


def _parse_position_map(text: str | None) -> dict[str, int]:
    output: dict[str, int] = {}
    for item in str(text or "").split(","):
        if not item.strip():
            continue
        symbol, _, qty = item.partition(":")
        if symbol.strip():
            output[symbol.strip().upper()] = int(qty or 0)
    return output


def cmd_paper_ledger(args) -> None:
    TradingRepository, get_session_factory, init_db = _load_db_modules()
    from app.paper.ledger import PaperLedgerConfig, export_paper_ledger_report, run_symbol_paper_ledger

    init_db()

    end_date = _parse_date(args.end_date) if args.end_date else datetime.now().date()
    start_date = _parse_date(args.start_date) if args.start_date else (end_date - timedelta(days=365))
    cfg = PaperLedgerConfig(
        initial_cash=float(args.initial_cash),
        hold_days=int(args.hold_days),
        settlement_days=int(args.settlement_days),
        force_close_end=bool(args.force_close_end),
    )

    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = run_symbol_paper_ledger(
            repo=repo,
            symbol=args.symbol,
            start_date=start_date,
            end_date=end_date,
            config=cfg,
        )
    if not result.get("passed"):
        print(result)
        return

    export = export_paper_ledger_report(
        result,
        output_dir=Path(args.output_dir).resolve(),
        symbol=args.symbol,
    )
    print(
        {
            "passed": True,
            "message": "paper ledger report generated",
            "summary": result.get("summary", {}),
            "export": export,
        }
    )


def cmd_signal_preview(args) -> None:
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.services.trading_workflow import SignalPreviewWorkflow, build_signal_preview_payload

    trade_date = _parse_date(args.trade_date) if args.trade_date else datetime.now().date()
    session_factory = get_session_factory()
    with session_factory() as session:
        repo = TradingRepository(session)
        result = build_signal_preview_payload(
            repo=repo,
            request=SignalPreviewWorkflow(
                trade_date=trade_date,
                available_cash=float(args.available_cash),
                max_symbols=int(args.max_symbols),
                require_chip=bool(args.require_chip),
                block_disposition=bool(args.block_disposition),
                rank_by=str(args.rank_by),
                max_open_positions=(
                    int(args.max_open_positions)
                    if args.max_open_positions is not None and int(args.max_open_positions) > 0
                    else None
                ),
                data_freshness_required=bool(args.data_freshness_required),
                chip_threshold_mode=str(args.chip_threshold_mode),
            ),
        )
    print({"trade_date": result["trade_date"], "count": result["suggestions_count"], "suggestions": result["suggestions"]})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TW swing trading CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="Initialize SQLite schema")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("check-finmind", help="Validate FinMind API key from .env")
    p.set_defaults(func=cmd_check_finmind)

    p = sub.add_parser("finmind-usage", help="Check FinMind API quota / usage")
    p.set_defaults(func=cmd_finmind_usage)

    p = sub.add_parser("sync-0050", help="Sync Yuanta 0050 PCF constituents into the Taiwan 50 universe snapshot")
    p.add_argument("--snapshot-date", help="YYYY-MM-DD", default=None)
    p.add_argument("--local-cache", action="store_true", help="Use local cache/fallback list instead of Yuanta 0050 PCF")
    p.set_defaults(func=cmd_sync_0050)

    p = sub.add_parser("sync-mid100", help="Sync 0051 PCF constituents into the mid-cap universe cache")
    p.set_defaults(func=cmd_sync_mid100)

    p = sub.add_parser("sync-twse-tpex-universe", help="Sync TWSE/TPEX stock metadata, names, markets, and industries")
    p.set_defaults(func=cmd_sync_twse_tpex_universe)

    p = sub.add_parser("import-index-constituents", help="Import official/custom index constituents CSV/TXT/XLSX into local cache")
    p.add_argument("--index-key", required=True, help="cache key used by universe index:<key>, e.g. custom or tw_quality_50")
    p.add_argument("--file", required=True, help="CSV/TSV/TXT/XLSX file exported from an index provider")
    p.add_argument("--source", default="manual_file", help="source label stored in the cache")
    p.set_defaults(func=cmd_import_index_constituents)

    p = sub.add_parser("sync-index-constituents-url", help="Download official index constituents CSV/TXT/XLSX URL into local cache")
    p.add_argument("--index-key", required=True, help="cache key used by universe index:<key>, e.g. tip_tw50")
    p.add_argument("--url", required=True, help="HTTPS download URL from an official/licensed provider")
    p.add_argument("--source", default="official_url", help="source label stored in the cache")
    p.add_argument("--timeout-sec", type=int, default=15, help="download timeout in seconds")
    p.set_defaults(func=cmd_sync_index_constituents_url)

    p = sub.add_parser("list-index-constituents", help="List imported index constituent caches")
    p.set_defaults(func=cmd_list_index_constituents)

    p = sub.add_parser("sync-bars", help="Sync Taiwan daily bars")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--symbols", required=False, help="Comma-separated symbols")
    p.set_defaults(func=cmd_sync_bars)

    p = sub.add_parser("sync-chip", help="Sync institutional chip flow daily")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--symbols", required=False, help="Comma-separated symbols")
    p.set_defaults(func=cmd_sync_chip)

    p = sub.add_parser("sync-broker-agg", help="Sync broker aggregation chip data (Sponsor)")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--symbols", required=False, help="Comma-separated symbols")
    p.set_defaults(func=cmd_sync_broker_agg)

    p = sub.add_parser("sync-disposition", help="Sync disposition securities periods")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--symbols", required=False, help="Comma-separated symbols")
    p.set_defaults(func=cmd_sync_disposition)

    p = sub.add_parser("sync-attention", help="Sync current attention stocks from Shioaji notice API")
    p.add_argument("--trade-date", required=False, help="YYYY-MM-DD; defaults to today")
    p.set_defaults(func=cmd_sync_attention)

    p = sub.add_parser("sync-market-bundle", help="Sync bars + chip + broker agg + disposition")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--symbols", required=False, help="Comma-separated symbols")
    p.add_argument("--include-bars", dest="include_bars", action="store_true", default=True)
    p.add_argument("--no-include-bars", dest="include_bars", action="store_false")
    p.add_argument("--include-chip", dest="include_chip", action="store_true", default=True)
    p.add_argument("--no-include-chip", dest="include_chip", action="store_false")
    p.add_argument("--include-broker-agg", dest="include_broker_agg", action="store_true", default=True)
    p.add_argument("--no-include-broker-agg", dest="include_broker_agg", action="store_false")
    p.add_argument("--include-disposition", dest="include_disposition", action="store_true", default=True)
    p.add_argument("--no-include-disposition", dest="include_disposition", action="store_false")
    p.add_argument("--include-attention", dest="include_attention", action="store_true", default=False)
    p.add_argument("--no-include-attention", dest="include_attention", action="store_false")
    p.add_argument("--include-fundamentals", dest="include_fundamentals", action="store_true", default=False)
    p.add_argument("--no-include-fundamentals", dest="include_fundamentals", action="store_false")
    p.add_argument("--include-news", dest="include_news", action="store_true", default=False)
    p.add_argument("--no-include-news", dest="include_news", action="store_false")
    p.add_argument("--include-industry-indices", dest="include_industry_indices", action="store_true", default=False)
    p.add_argument("--no-include-industry-indices", dest="include_industry_indices", action="store_false")
    p.add_argument("--prefer-official-bulk", action="store_true", default=False, help="prefer official by-date bulk sources for bars/chip")
    p.set_defaults(func=cmd_sync_market_bundle)

    p = sub.add_parser("list-0050", help="List latest 0050 symbols in DB")
    p.set_defaults(func=cmd_list_0050)

    p = sub.add_parser("rebuild-features", help="Rebuild feature snapshots from synced data")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--symbols", required=False, help="Comma-separated symbols")
    p.set_defaults(func=cmd_rebuild_features)

    p = sub.add_parser("backtest", help="Run TW swing backtest (Backtrader)")
    p.add_argument("--symbol", required=True, help="e.g. 2330")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--cash", type=float, default=10_000)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("run-scheduler", help="Start APScheduler for signal and optional daily radar jobs")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--available-cash", type=float, default=10_000, help="cash budget used in sizing")
    p.add_argument("--hour", type=int, default=13, help="job hour (Asia/Taipei)")
    p.add_argument("--minute", type=int, default=40, help="job minute")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--rank-by", default="score", help="score | ensemble_score | risk_adjusted_score")
    p.add_argument("--max-open-positions", type=int, default=None, help="max number of suggestions to allocate")
    p.add_argument("--disable-signal-job", dest="enable_signal_job", action="store_false", default=True)
    p.add_argument("--enable-daily-radar", action="store_true", default=False, help="also schedule daily radar sync + ranking")
    p.add_argument("--enable-trigger-monitor", action="store_true", default=False, help="also schedule daily radar trigger monitor")
    p.add_argument("--enable-shortlist", action="store_true", default=False, help="schedule full-market Top 10 shortlist and Codex subscription research")
    p.add_argument("--radar-hour", type=int, default=8, help="daily radar job hour (Asia/Taipei)")
    p.add_argument("--radar-minute", type=int, default=45, help="daily radar job minute")
    p.add_argument("--radar-target-count", type=int, default=15, help="daily radar target count")
    p.add_argument("--radar-risk-profile", default="balanced", help="balanced | conservative | aggressive")
    p.add_argument("--radar-threshold-offset", type=float, default=0.0, help="daily radar entry/watch score offset, -10 to 10")
    p.add_argument("--radar-universe", default="curated_watchlist", help="curated_watchlist | tw0050 | tw_mid100 | twse_tpex | custom")
    p.add_argument("--radar-symbols", default="", help="comma-separated symbols when radar universe is custom")
    p.add_argument("--radar-index-constituents-url", default="", help="official HTTPS CSV/TXT/XLSX URL to sync before radar")
    p.add_argument("--radar-index-constituents-key", default="", help="cache key for the synced index constituents; defaults to index:<key> universe")
    p.add_argument("--radar-index-constituents-source", default="official_url", help="source label for synced index constituents")
    p.add_argument("--radar-notify-style", choices=["premarket", "intraday", "postmarket"], default="premarket", help="daily radar notification density")
    p.add_argument("--no-radar-data-freshness-required", dest="radar_data_freshness_required", action="store_false", default=True)
    p.add_argument("--radar-include-extended-data", dest="radar_include_extended_data", action="store_true", default=False, help="also sync premium/extended radar data such as broker agg, disposition, fundamentals and news")
    p.add_argument("--no-radar-include-extended-data", dest="radar_include_extended_data", action="store_false")
    p.add_argument("--radar-prefer-official-bulk", dest="radar_prefer_official_bulk", action="store_true", default=True, help="prefer official by-date bulk sources for daily radar core data")
    p.add_argument("--no-radar-prefer-official-bulk", dest="radar_prefer_official_bulk", action="store_false")
    p.add_argument("--trigger-monitor-hour", type=int, default=9, help="trigger monitor job hour (Asia/Taipei)")
    p.add_argument("--trigger-monitor-minute", type=int, default=15, help="trigger monitor job minute")
    p.add_argument("--trigger-monitor-count", type=int, default=50, help="latest radar rows to inspect")
    p.add_argument("--trigger-monitor-limit", type=int, default=10, help="max trigger watch items to return")
    p.add_argument("--trigger-monitor-near-entry-gap", type=float, default=2.0, help="mark near when Entry Score gap is within this")
    p.add_argument("--trigger-monitor-near-price-gap-pct", type=float, default=0.02, help="mark near when price is within this pct of breakout/pullback")
    p.add_argument("--trigger-monitor-notify-on", choices=["ready", "near", "always"], default="ready")
    p.add_argument("--trigger-monitor-price-source", choices=["radar_snapshot", "shioaji_snapshot", "shioaji_streaming"], default="radar_snapshot", help="price source used by trigger monitor")
    p.add_argument("--trigger-monitor-shioaji-live", action="store_true", default=False, help="use live Shioaji environment for trigger monitor snapshots")
    p.add_argument("--trigger-monitor-max-price-age-seconds", type=int, default=180, help="max accepted trigger monitor external quote age")
    p.add_argument("--trigger-monitor-streaming-seconds", type=float, default=3.0, help="seconds to collect Shioaji streaming quotes before falling back to snapshot")
    p.add_argument("--trigger-monitor-notification-state-path", default="data/cache/daily_radar_trigger_monitor_state.json", help="state file used to suppress duplicate trigger monitor notifications")
    p.add_argument("--trigger-monitor-notification-cooldown-minutes", type=int, default=240, help="suppress duplicate trigger monitor notifications within this window")
    p.add_argument("--trigger-monitor-force-notify", action="store_true", default=False, help="bypass trigger monitor duplicate notification cooldown")
    p.set_defaults(func=cmd_run_scheduler)

    p = sub.add_parser("run-signal-job", help="Run one-shot daily signal calculation + optional notify")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--available-cash", type=float, default=10_000, help="cash budget used in sizing")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--min-data-timestamp", default=None, help="minimum last bar date (YYYY-MM-DD)")
    p.add_argument("--rank-by", default="score", help="score | ensemble_score | risk_adjusted_score")
    p.add_argument("--max-open-positions", type=int, default=None, help="max number of suggestions to allocate")
    p.set_defaults(func=cmd_run_signal_job)

    p = sub.add_parser("run-daily-radar-job", help="Run one-shot daily radar sync + ranking")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--target-count", type=int, default=15, help="daily radar target count")
    p.add_argument("--risk-profile", default="balanced", help="balanced | conservative | aggressive")
    p.add_argument("--threshold-offset", type=float, default=0.0, help="entry/watch score offset, -10 to 10")
    p.add_argument("--universe", default="curated_watchlist", help="curated_watchlist | tw0050 | tw_mid100 | twse_tpex | custom")
    p.add_argument("--symbols", default="", help="comma-separated symbols when universe is custom")
    p.add_argument("--index-constituents-url", default="", help="official HTTPS CSV/TXT/XLSX URL to sync before radar")
    p.add_argument("--index-constituents-key", default="", help="cache key for the synced index constituents; defaults to index:<key> universe")
    p.add_argument("--index-constituents-source", default="official_url", help="source label for synced index constituents")
    p.add_argument("--include-attention", dest="include_attention", action="store_true", default=True)
    p.add_argument("--no-include-attention", dest="include_attention", action="store_false")
    p.add_argument("--include-extended-data", dest="include_extended_data", action="store_true", default=False, help="also sync premium/extended radar data such as broker agg, disposition, fundamentals and news")
    p.add_argument("--no-include-extended-data", dest="include_extended_data", action="store_false")
    p.add_argument("--prefer-official-bulk", dest="prefer_official_bulk", action="store_true", default=True, help="prefer official by-date bulk sources for daily radar core data")
    p.add_argument("--no-prefer-official-bulk", dest="prefer_official_bulk", action="store_false")
    p.add_argument("--notify", default="none", help="send盤前 Brief: none | telegram | line | both")
    p.add_argument("--notify-style", choices=["premarket", "intraday", "postmarket"], default="premarket", help="message density for notification")
    p.add_argument("--fail-on-notify-error", action="store_true", default=False, help="exit with code 3 when Brief notification fails")
    p.add_argument("--no-data-freshness-required", dest="data_freshness_required", action="store_false", default=True)
    p.set_defaults(func=cmd_run_daily_radar_job)

    p = sub.add_parser(
        "run-daily-radar-threshold-optimization",
        help="Build or execute the threshold-backtest daily radar rerun batch",
    )
    p.add_argument("--execute", action="store_true", default=False, help="actually execute selected reruns; default is dry-run")
    p.add_argument("--max-runs", type=int, default=3, help="max optimization candidates to run, 1-6")
    p.add_argument("--lookback-runs", type=int, default=8, help="historical daily radar runs to inspect")
    p.add_argument("--horizons", default="5,10,20", help="comma-separated lookahead bars, e.g. 5,10,20")
    p.add_argument("--count", type=int, default=50, help="daily radar rows per historical run")
    p.add_argument("--base-profile", default=None, help="balanced | conservative | aggressive")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--grid-universes", default="", help="optional comma-separated universe grid, e.g. research_ready,dynamic_balanced,tw0050")
    p.add_argument("--grid-profiles", default="", help="optional comma-separated profile grid, e.g. balanced,conservative")
    p.add_argument("--grid-offsets", default="", help="optional comma-separated threshold offset grid, e.g. -2,0,2")
    p.add_argument("--candidate-source", default="mixed", choices=("mixed", "plan", "grid", "exploration"), help="candidate source to select from: mixed, plan, grid, or exploration")
    p.set_defaults(func=cmd_run_daily_radar_threshold_optimization)

    p = sub.add_parser("run-invest-pipeline", help="Run one-shot one-click candidate pipeline")
    p.add_argument("--trade-date", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--target-count", type=int, default=8, help="one-click candidate target count")
    p.add_argument("--risk-profile", default="balanced", help="balanced | conservative | aggressive")
    p.add_argument("--universe", default="curated_watchlist", help="curated_watchlist | tw0050 | tw_mid100 | twse_tpex | custom")
    p.add_argument("--symbols", default="", help="comma-separated symbols when universe is custom")
    p.add_argument("--include-attention", dest="include_attention", action="store_true", default=True)
    p.add_argument("--no-include-attention", dest="include_attention", action="store_false")
    p.add_argument("--include-extended-chip", dest="include_extended_chip", action="store_true", default=False)
    p.add_argument("--no-include-extended-chip", dest="include_extended_chip", action="store_false")
    p.add_argument("--no-data-freshness-required", dest="data_freshness_required", action="store_false", default=True)
    p.add_argument("--json", action="store_true", default=False, help="print full JSON result")
    p.add_argument("--fail-on-degraded", action="store_true", default=False, help="exit with code 2 when pipeline is degraded")
    p.set_defaults(func=cmd_run_invest_pipeline)

    p = sub.add_parser("daily-radar-health", help="Check daily radar deployment freshness and latest run health")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--max-age-days", type=int, default=3, help="danger when latest radar is older than this")
    p.add_argument("--warn-age-days", type=int, default=1, help="warn when latest radar is older than this")
    p.add_argument("--min-rows", type=int, default=5, help="minimum latest radar row count")
    p.add_argument("--min-entry-ready", type=int, default=1, help="warn when ENTRY_READY count is below this")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--notify-on", choices=["danger", "warn", "always"], default="danger")
    p.add_argument("--fail-on-danger", action="store_true", default=False, help="exit with code 2 when status is danger")
    p.add_argument("--fail-on-notify-error", action="store_true", default=False, help="exit with code 3 when notification fails")
    p.set_defaults(func=cmd_daily_radar_health)

    p = sub.add_parser("intraday-quote-service-status", help="Read the latest persisted intraday quote service health snapshot")
    p.add_argument("--state-path", default="data/cache/intraday_quote_service_status.json", help="persisted quote service status JSON path")
    p.add_argument("--max-age-seconds", type=int, default=300, help="warn when the persisted status is older than this")
    p.add_argument("--fail-on-danger", action="store_true", default=False, help="exit with code 2 when persisted health is danger")
    p.add_argument("--fail-on-warn", action="store_true", default=False, help="exit with code 2 when persisted health is warn or danger")
    p.set_defaults(func=cmd_intraday_quote_service_status)

    p = sub.add_parser("intraday-quote-service-review", help="Build a post-session review from the persisted intraday quote service snapshot")
    p.add_argument("--state-path", default="data/cache/intraday_quote_service_status.json", help="persisted quote service status JSON path")
    p.add_argument("--max-age-seconds", type=int, default=3600, help="warn when the persisted status is older than this")
    p.add_argument("--min-heartbeat-count", type=int, default=1, help="minimum expected heartbeats for a usable session review")
    p.add_argument("--min-quote-coverage", type=float, default=0.8, help="minimum acceptable max quote coverage ratio, 0-1")
    p.add_argument("--json", action="store_true", default=False, help="print full JSON review instead of text summary")
    p.add_argument("--fail-on-danger", action="store_true", default=False, help="exit with code 2 when review status is danger")
    p.add_argument("--fail-on-warn", action="store_true", default=False, help="exit with code 2 when review status is warn or danger")
    p.set_defaults(func=cmd_intraday_quote_service_review)

    p = sub.add_parser("run-intraday-quote-service", help="Run a long-lived Shioaji streaming quote service and persist health snapshots")
    p.add_argument("--symbols", default="", help="comma or space separated symbols to subscribe")
    p.add_argument("--symbols-file", default="", help="optional file containing symbols separated by comma, space, or newline")
    p.add_argument("--state-path", default="data/cache/intraday_quote_service_status.json", help="persisted quote service status JSON path")
    p.add_argument("--heartbeat-interval-seconds", type=float, default=5.0, help="seconds between persisted health updates")
    p.add_argument("--max-runtime-seconds", type=float, default=0.0, help="optional runtime limit; 0 means run until stopped")
    p.add_argument("--shioaji-live", action="store_true", default=False, help="use live Shioaji environment instead of simulation")
    p.add_argument("--no-bidask", action="store_true", default=False, help="subscribe tick only")
    p.add_argument("--allow-outside-session", action="store_true", default=False, help="allow starting outside regular Taiwan stock session")
    p.add_argument("--fail-on-not-running", action="store_true", default=False, help="exit with code 2 when the service did not reach running state")
    p.add_argument("--fail-on-danger", action="store_true", default=False, help="exit with code 2 when health is danger")
    p.add_argument("--fail-on-warn", action="store_true", default=False, help="exit with code 2 when health is warn or danger")
    p.set_defaults(func=cmd_run_intraday_quote_service)

    p = sub.add_parser("daily-radar-trigger-monitor", help="Check latest WATCH_WAIT_TRIGGER conditions and optional notify")
    p.add_argument("--count", type=int, default=50, help="latest radar rows to inspect")
    p.add_argument("--limit", type=int, default=10, help="max trigger watch items to return")
    p.add_argument("--near-entry-gap", type=float, default=2.0, help="mark near when Entry Score gap is within this")
    p.add_argument("--near-price-gap-pct", type=float, default=0.02, help="mark near when price is within this pct of breakout/pullback")
    p.add_argument("--price-source", choices=["radar_snapshot", "shioaji_snapshot", "shioaji_streaming"], default="radar_snapshot", help="price source used for trigger checks")
    p.add_argument("--shioaji-live", action="store_true", default=False, help="use live Shioaji environment for snapshots")
    p.add_argument("--max-price-age-seconds", type=int, default=180, help="max accepted external quote age for trigger checks")
    p.add_argument("--streaming-seconds", type=float, default=3.0, help="seconds to collect Shioaji streaming quotes before falling back to snapshot")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--notify-on", choices=["ready", "near", "always"], default="ready")
    p.add_argument("--notification-state-path", default="data/cache/daily_radar_trigger_monitor_state.json", help="state file used to suppress duplicate notifications")
    p.add_argument("--notification-cooldown-minutes", type=int, default=240, help="suppress duplicate notifications within this window")
    p.add_argument("--force-notify", action="store_true", default=False, help="bypass duplicate notification cooldown")
    p.add_argument("--fail-on-ready", action="store_true", default=False, help="exit with code 2 when at least one trigger is ready")
    p.add_argument("--fail-on-notify-error", action="store_true", default=False, help="exit with code 3 when notification fails")
    p.set_defaults(func=cmd_daily_radar_trigger_monitor)

    p = sub.add_parser("data-health-overview", help="Print data freshness and market coverage overview")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--freshness-days", type=int, default=3, help="recent-data window used for coverage checks")
    p.add_argument("--json", action="store_true", default=False, help="print full JSON payload instead of a short summary")
    p.add_argument("--fail-on-danger", action="store_true", default=False, help="exit with code 2 when status is danger")
    p.set_defaults(func=cmd_data_health_overview)

    p = sub.add_parser("repair-market-data-coverage", help="Plan or run a small sync for market data coverage gaps")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--freshness-days", type=int, default=3, help="recent-data window used for coverage checks")
    p.add_argument("--lookback-days", type=int, default=10, help="sync start date lookback from as-of")
    p.add_argument("--datasets", default="all", help="all or comma-separated: daily_bars,institutional_chip,broker_agg_chip")
    p.add_argument("--markets", default="", help="optional comma-separated market filter: TWSE,TPEX")
    p.add_argument("--symbols", default="", help="optional comma-separated symbols to repair instead of coverage samples")
    p.add_argument("--max-symbols", type=int, default=20, help="maximum symbols in one repair batch")
    p.add_argument("--batches", type=int, default=1, help="number of sequential repair batches to run with --execute")
    p.add_argument("--execute", action="store_true", default=False, help="run the sync; default is dry-run")
    p.add_argument("--json", action="store_true", default=False, help="print JSON payload")
    p.add_argument("--fail-on-empty", action="store_true", default=False, help="exit with code 2 when no repair symbols are available")
    p.set_defaults(func=cmd_repair_market_data_coverage)

    p = sub.add_parser("ops-readiness", help="Summarize daily radar, EOD, promotion and environment readiness")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--target-phase", choices=["paper", "small_live", "live"], default="paper")
    p.add_argument("--advisor-name", default="stub")
    p.add_argument("--strategy-name", default="manual")
    p.add_argument("--strategy-version", default="manual")
    p.add_argument("--max-age-days", type=int, default=3, help="danger when latest radar is older than this")
    p.add_argument("--warn-age-days", type=int, default=1, help="warn when latest radar is older than this")
    p.add_argument("--min-rows", type=int, default=5, help="minimum latest radar row count")
    p.add_argument("--min-entry-ready", type=int, default=1, help="warn when ENTRY_READY count is below this")
    p.add_argument("--eod-limit", type=int, default=100, help="recent trading records to inspect")
    p.add_argument("--min-observation-days", type=int, default=3)
    p.add_argument("--promotion-gate-limit", type=int, default=100)
    p.add_argument("--include-intraday-quote-review", action="store_true", default=False, help="merge persisted intraday quote post-session review into readiness")
    p.add_argument("--intraday-quote-state-path", default="data/cache/intraday_quote_service_status.json", help="persisted quote service status JSON path")
    p.add_argument("--intraday-quote-review-max-age-seconds", type=int, default=3600, help="warn when quote review snapshot is older than this")
    p.add_argument("--intraday-quote-review-min-heartbeat-count", type=int, default=1, help="minimum expected quote service heartbeats for review")
    p.add_argument("--intraday-quote-review-min-quote-coverage", type=float, default=0.8, help="minimum acceptable quote coverage ratio, 0-1")
    p.add_argument("--fail-on-danger", action="store_true", default=False, help="exit with code 2 when status is danger")
    p.set_defaults(func=cmd_ops_readiness)

    p = sub.add_parser("decision-radar-phase1-acceptance", help="Check whether Decision Radar Phase 1 is ready for user testing")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--max-age-days", type=int, default=3, help="danger when latest radar is older than this")
    p.add_argument("--warn-age-days", type=int, default=1, help="warn when latest radar is older than this")
    p.add_argument("--min-rows", type=int, default=5, help="minimum latest radar row count")
    p.add_argument("--min-entry-ready", type=int, default=0, help="minimum ENTRY_READY count expected for acceptance")
    p.add_argument("--json", action="store_true", default=False, help="print full JSON payload")
    p.add_argument("--fail-on-danger", action="store_true", default=False, help="exit with code 2 when status is danger")
    p.set_defaults(func=cmd_decision_radar_phase1_acceptance)

    p = sub.add_parser("write-launchd-plist", help="Write a macOS launchd plist for daily radar automation")
    p.add_argument("--output", default="deploy/launchd/com.quant-trade.daily-radar.plist", help="plist output path")
    p.add_argument("--project-dir", default=str(Path.cwd()), help="project working directory")
    p.add_argument("--python", default="", help="absolute Python path; defaults to .venv/bin/python when present")
    p.add_argument("--label", default="com.quant-trade.daily-radar", help="launchd label")
    p.add_argument("--hour", type=int, default=8, help="job hour (Asia/Taipei)")
    p.add_argument("--minute", type=int, default=45, help="job minute")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--target-count", type=int, default=15, help="daily radar target count")
    p.add_argument("--risk-profile", default="balanced", help="balanced | conservative | aggressive")
    p.add_argument("--threshold-offset", type=float, default=0.0, help="entry/watch score offset, -10 to 10")
    p.add_argument("--universe", default="curated_watchlist", help="curated_watchlist | tw0050 | tw_mid100 | twse_tpex | custom")
    p.add_argument("--symbols", default="", help="comma-separated symbols when universe is custom")
    p.add_argument("--index-constituents-url", default="", help="official HTTPS CSV/TXT/XLSX URL to sync before radar")
    p.add_argument("--index-constituents-key", default="", help="cache key for the synced index constituents; defaults to index:<key> universe")
    p.add_argument("--index-constituents-source", default="official_url", help="source label for synced index constituents")
    p.add_argument("--notify", default="none", help="send盤前 Brief after the one-shot radar job: none | telegram | line | both")
    p.add_argument("--notify-style", choices=["premarket", "intraday", "postmarket"], default="premarket", help="message density for daily radar notification")
    p.add_argument("--no-data-freshness-required", dest="data_freshness_required", action="store_false", default=True)
    p.add_argument("--run-at-load", action="store_true", default=False, help="run once when the plist is loaded")
    p.add_argument("--stdout-path", default=None, help="optional StandardOutPath")
    p.add_argument("--stderr-path", default=None, help="optional StandardErrorPath")
    p.set_defaults(func=cmd_write_launchd_plist)

    p = sub.add_parser("write-trigger-monitor-launchd-plist", help="Write a macOS launchd plist for daily radar trigger monitoring")
    p.add_argument("--output", default="deploy/launchd/com.quant-trade.daily-radar-trigger-monitor.plist", help="plist output path")
    p.add_argument("--project-dir", default=str(Path.cwd()), help="project working directory")
    p.add_argument("--python", default="", help="absolute Python path; defaults to .venv/bin/python when present")
    p.add_argument("--label", default="com.quant-trade.daily-radar-trigger-monitor", help="launchd label")
    p.add_argument("--hour", type=int, default=9, help="job hour (Asia/Taipei)")
    p.add_argument("--minute", type=int, default=15, help="job minute")
    p.add_argument("--count", type=int, default=50, help="latest radar rows to inspect")
    p.add_argument("--limit", type=int, default=10, help="max trigger watch items to return")
    p.add_argument("--near-entry-gap", type=float, default=2.0, help="mark near when Entry Score gap is within this")
    p.add_argument("--near-price-gap-pct", type=float, default=0.02, help="mark near when price is within this pct of breakout/pullback")
    p.add_argument("--price-source", choices=["radar_snapshot", "shioaji_snapshot", "shioaji_streaming"], default="radar_snapshot", help="price source used for trigger checks")
    p.add_argument("--shioaji-live", action="store_true", default=False, help="use live Shioaji environment for snapshots")
    p.add_argument("--max-price-age-seconds", type=int, default=180, help="max accepted external quote age for trigger checks")
    p.add_argument("--streaming-seconds", type=float, default=3.0, help="seconds to collect Shioaji streaming quotes before falling back to snapshot")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--notify-on", choices=["ready", "near", "always"], default="ready")
    p.add_argument("--notification-state-path", default="data/cache/daily_radar_trigger_monitor_state.json", help="state file used to suppress duplicate notifications")
    p.add_argument("--notification-cooldown-minutes", type=int, default=240, help="suppress duplicate notifications within this window")
    p.add_argument("--force-notify", action="store_true", default=False, help="bypass duplicate notification cooldown")
    p.add_argument("--fail-on-ready", action="store_true", default=False)
    p.add_argument("--fail-on-notify-error", action="store_true", default=False)
    p.add_argument("--run-at-load", action="store_true", default=False, help="run once when the plist is loaded")
    p.add_argument("--stdout-path", default=None, help="optional StandardOutPath")
    p.add_argument("--stderr-path", default=None, help="optional StandardErrorPath")
    p.set_defaults(func=cmd_write_trigger_monitor_launchd_plist)

    p = sub.add_parser("write-systemd-units", help="Write systemd service/timer units for daily radar automation")
    p.add_argument("--output-dir", default="deploy/systemd", help="directory for generated service/timer files")
    p.add_argument("--project-dir", default=str(Path.cwd()), help="project working directory")
    p.add_argument("--python", default=None, help="absolute Python path; defaults to venv/bin/python or .venv/bin/python")
    p.add_argument("--env-file", default=None, help="EnvironmentFile path; defaults to <project-dir>/.env")
    p.add_argument("--service-name", default="quant-trade-daily-radar.service", help="systemd service filename")
    p.add_argument("--timer-name", default="quant-trade-daily-radar.timer", help="systemd timer filename")
    p.add_argument("--user", default="root", help="service User")
    p.add_argument("--group", default="root", help="service Group")
    p.add_argument("--hour", type=int, default=8, help="job hour")
    p.add_argument("--minute", type=int, default=45, help="job minute")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--target-count", type=int, default=15, help="daily radar target count")
    p.add_argument("--risk-profile", default="balanced", help="balanced | conservative | aggressive")
    p.add_argument("--threshold-offset", type=float, default=0.0, help="entry/watch score offset, -10 to 10")
    p.add_argument("--universe", default="curated_watchlist", help="curated_watchlist | tw0050 | tw_mid100 | twse_tpex | custom")
    p.add_argument("--symbols", default="", help="comma-separated symbols when universe is custom")
    p.add_argument("--index-constituents-url", default="", help="official HTTPS CSV/TXT/XLSX URL to sync before radar")
    p.add_argument("--index-constituents-key", default="", help="cache key for the synced index constituents; defaults to index:<key> universe")
    p.add_argument("--index-constituents-source", default="official_url", help="source label for synced index constituents")
    p.add_argument("--notify", default="none", help="send盤前 Brief after the daily radar service runs: none | telegram | line | both")
    p.add_argument("--notify-style", choices=["premarket", "intraday", "postmarket"], default="premarket", help="message density for daily radar notification")
    p.add_argument("--no-data-freshness-required", dest="data_freshness_required", action="store_false", default=True)
    p.add_argument("--include-health-monitor", action="store_true", default=False, help="also write daily-radar-health service/timer")
    p.add_argument("--health-service-name", default="quant-trade-daily-radar-health.service", help="systemd health service filename")
    p.add_argument("--health-timer-name", default="quant-trade-daily-radar-health.timer", help="systemd health timer filename")
    p.add_argument("--health-hour", type=int, default=9, help="health check hour")
    p.add_argument("--health-minute", type=int, default=30, help="health check minute")
    p.add_argument("--health-max-age-days", type=int, default=3, help="daily radar health max age before danger")
    p.add_argument("--health-warn-age-days", type=int, default=1, help="daily radar health age before warn")
    p.add_argument("--health-min-rows", type=int, default=5, help="daily radar health minimum row count")
    p.add_argument("--health-min-entry-ready", type=int, default=1, help="daily radar health minimum ENTRY_READY count")
    p.add_argument("--health-notify", default="none", help="none | telegram | line | both")
    p.add_argument("--health-notify-on", choices=["danger", "warn", "always"], default="danger")
    p.add_argument("--no-health-fail-on-danger", dest="health_fail_on_danger", action="store_false", default=True)
    p.add_argument("--health-fail-on-notify-error", action="store_true", default=False)
    p.add_argument("--health-use-ops-readiness", action="store_true", default=False, help="use ops-readiness instead of daily-radar-health for the generated health service")
    p.add_argument("--health-ops-target-phase", choices=["paper", "small_live", "live"], default="paper", help="target phase passed to ops-readiness health service")
    p.add_argument("--health-include-intraday-quote-review", action="store_true", default=False, help="include intraday quote post-session review in generated ops-readiness health service")
    p.add_argument("--health-intraday-quote-review-max-age-seconds", type=int, default=3600)
    p.add_argument("--health-intraday-quote-review-min-heartbeat-count", type=int, default=1)
    p.add_argument("--health-intraday-quote-review-min-quote-coverage", type=float, default=0.8)
    p.add_argument("--include-trigger-monitor", action="store_true", default=False, help="also write daily-radar-trigger-monitor service/timer")
    p.add_argument("--trigger-monitor-service-name", default="quant-trade-daily-radar-trigger-monitor.service", help="systemd trigger monitor service filename")
    p.add_argument("--trigger-monitor-timer-name", default="quant-trade-daily-radar-trigger-monitor.timer", help="systemd trigger monitor timer filename")
    p.add_argument("--trigger-monitor-hour", type=int, default=9, help="trigger monitor hour")
    p.add_argument("--trigger-monitor-minute", type=int, default=15, help="trigger monitor minute")
    p.add_argument("--trigger-monitor-count", type=int, default=50, help="latest radar rows to inspect")
    p.add_argument("--trigger-monitor-limit", type=int, default=10, help="max trigger watch items to return")
    p.add_argument("--trigger-monitor-near-entry-gap", type=float, default=2.0, help="mark near when Entry Score gap is within this")
    p.add_argument("--trigger-monitor-near-price-gap-pct", type=float, default=0.02, help="mark near when price is within this pct of breakout/pullback")
    p.add_argument("--trigger-monitor-notify", default="none", help="none | telegram | line | both")
    p.add_argument("--trigger-monitor-notify-on", choices=["ready", "near", "always"], default="ready")
    p.add_argument("--trigger-monitor-price-source", choices=["radar_snapshot", "shioaji_snapshot", "shioaji_streaming"], default="radar_snapshot", help="price source used by trigger monitor")
    p.add_argument("--trigger-monitor-shioaji-live", action="store_true", default=False, help="use live Shioaji environment for trigger monitor snapshots")
    p.add_argument("--trigger-monitor-max-price-age-seconds", type=int, default=180, help="max accepted trigger monitor external quote age")
    p.add_argument("--trigger-monitor-streaming-seconds", type=float, default=3.0, help="seconds to collect Shioaji streaming quotes before falling back to snapshot")
    p.add_argument("--trigger-monitor-notification-state-path", default="data/cache/daily_radar_trigger_monitor_state.json", help="state file used to suppress duplicate trigger monitor notifications")
    p.add_argument("--trigger-monitor-notification-cooldown-minutes", type=int, default=240, help="suppress duplicate trigger monitor notifications within this window")
    p.add_argument("--trigger-monitor-force-notify", action="store_true", default=False, help="bypass trigger monitor duplicate notification cooldown")
    p.add_argument("--trigger-monitor-fail-on-ready", action="store_true", default=False)
    p.add_argument("--trigger-monitor-fail-on-notify-error", action="store_true", default=False)
    p.add_argument("--include-intraday-quote-service", action="store_true", default=False, help="also write long-running intraday quote service units")
    p.add_argument("--intraday-quote-service-name", default="quant-trade-intraday-quote.service", help="systemd intraday quote service filename")
    p.add_argument("--intraday-quote-health-service-name", default="quant-trade-intraday-quote-health.service", help="systemd intraday quote health service filename")
    p.add_argument("--intraday-quote-health-timer-name", default="quant-trade-intraday-quote-health.timer", help="systemd intraday quote health timer filename")
    p.add_argument("--intraday-quote-symbols", default="", help="comma-separated symbols for the long-running quote service")
    p.add_argument("--intraday-quote-state-path", default="data/cache/intraday_quote_service_status.json", help="health snapshot path for intraday quote service")
    p.add_argument("--intraday-quote-heartbeat-interval-seconds", type=float, default=5.0, help="seconds between quote service health snapshots")
    p.add_argument("--intraday-quote-shioaji-live", action="store_true", default=False, help="use live Shioaji environment for intraday quote service")
    p.add_argument("--intraday-quote-no-bidask", action="store_true", default=False, help="subscribe tick only for intraday quote service")
    p.add_argument("--intraday-quote-allow-outside-session", action="store_true", default=False, help="allow quote service to start outside regular Taiwan stock session")
    p.add_argument("--intraday-quote-restart", default="always", help="systemd Restart policy for intraday quote service")
    p.add_argument("--intraday-quote-restart-sec", type=int, default=10, help="systemd RestartSec for intraday quote service")
    p.add_argument("--intraday-quote-health-max-age-seconds", type=int, default=30, help="max health snapshot age before quote health check fails")
    p.add_argument("--intraday-quote-health-interval-seconds", type=int, default=30, help="systemd quote health timer interval")
    p.add_argument("--no-intraday-quote-health-fail-on-warn", dest="no_intraday_quote_health_fail_on_warn", action="store_true", default=False)
    p.add_argument("--no-intraday-quote-health-fail-on-danger", dest="intraday_quote_health_fail_on_danger", action="store_false", default=True)
    p.set_defaults(func=cmd_write_systemd_units)

    p = sub.add_parser("write-docker-compose", help="Write Docker Compose assets for daily radar scheduler")
    p.add_argument("--output-dir", default="deploy/docker", help="directory for generated compose/Dockerfile assets")
    p.add_argument("--project-dir", default=str(Path.cwd()), help="project working directory")
    p.add_argument("--compose-name", default="docker-compose.daily-radar.yml", help="compose filename")
    p.add_argument("--dockerfile-name", default="Dockerfile.daily-radar", help="Dockerfile filename")
    p.add_argument("--python-image", default="python:3.11-slim", help="base Python image for generated Dockerfile")
    p.add_argument("--service-name", default="daily-radar", help="compose service/container name")
    p.add_argument("--image", default="quant-trade-daily-radar:latest", help="scheduler image tag")
    p.add_argument("--env-file", default=None, help="env_file path; defaults to <project-dir>/.env")
    p.add_argument("--restart", default="unless-stopped", help="compose restart policy")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--notify-style", choices=["premarket", "intraday", "postmarket"], default="premarket", help="message density for daily radar notification")
    p.add_argument("--hour", type=int, default=8, help="daily radar job hour")
    p.add_argument("--minute", type=int, default=45, help="daily radar job minute")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--target-count", type=int, default=15, help="daily radar target count")
    p.add_argument("--risk-profile", default="balanced", help="balanced | conservative | aggressive")
    p.add_argument("--threshold-offset", type=float, default=0.0, help="entry/watch score offset, -10 to 10")
    p.add_argument("--universe", default="curated_watchlist", help="curated_watchlist | tw0050 | tw_mid100 | twse_tpex | custom")
    p.add_argument("--symbols", default="", help="comma-separated symbols when universe is custom")
    p.add_argument("--index-constituents-url", default="", help="official HTTPS CSV/TXT/XLSX URL to sync before radar")
    p.add_argument("--index-constituents-key", default="", help="cache key for the synced index constituents; defaults to index:<key> universe")
    p.add_argument("--index-constituents-source", default="official_url", help="source label for synced index constituents")
    p.add_argument("--no-data-freshness-required", dest="data_freshness_required", action="store_false", default=True)
    p.add_argument("--enable-trigger-monitor", action="store_true", default=False, help="also schedule daily radar trigger monitor")
    p.add_argument("--trigger-monitor-hour", type=int, default=9, help="trigger monitor job hour")
    p.add_argument("--trigger-monitor-minute", type=int, default=15, help="trigger monitor job minute")
    p.add_argument("--trigger-monitor-count", type=int, default=50, help="latest radar rows to inspect")
    p.add_argument("--trigger-monitor-limit", type=int, default=10, help="max trigger watch items to return")
    p.add_argument("--trigger-monitor-near-entry-gap", type=float, default=2.0, help="mark near when Entry Score gap is within this")
    p.add_argument("--trigger-monitor-near-price-gap-pct", type=float, default=0.02, help="mark near when price is within this pct of breakout/pullback")
    p.add_argument("--trigger-monitor-notify-on", choices=["ready", "near", "always"], default="ready")
    p.add_argument("--trigger-monitor-price-source", choices=["radar_snapshot", "shioaji_snapshot", "shioaji_streaming"], default="radar_snapshot", help="price source used by trigger monitor")
    p.add_argument("--trigger-monitor-shioaji-live", action="store_true", default=False, help="use live Shioaji environment for trigger monitor snapshots")
    p.add_argument("--trigger-monitor-max-price-age-seconds", type=int, default=180, help="max accepted trigger monitor external quote age")
    p.add_argument("--trigger-monitor-streaming-seconds", type=float, default=3.0, help="seconds to collect Shioaji streaming quotes before falling back to snapshot")
    p.add_argument("--trigger-monitor-notification-state-path", default="data/cache/daily_radar_trigger_monitor_state.json", help="state file used to suppress duplicate trigger monitor notifications")
    p.add_argument("--trigger-monitor-notification-cooldown-minutes", type=int, default=240, help="suppress duplicate trigger monitor notifications within this window")
    p.add_argument("--trigger-monitor-force-notify", action="store_true", default=False, help="bypass trigger monitor duplicate notification cooldown")
    p.add_argument("--no-healthcheck", dest="include_healthcheck", action="store_false", default=True)
    p.add_argument("--health-max-age-days", type=int, default=3, help="Docker healthcheck max radar age before unhealthy")
    p.add_argument("--health-warn-age-days", type=int, default=1, help="Docker healthcheck warning age")
    p.add_argument("--health-min-rows", type=int, default=5, help="Docker healthcheck minimum radar row count")
    p.add_argument("--health-min-entry-ready", type=int, default=1, help="Docker healthcheck minimum ENTRY_READY count")
    p.add_argument("--health-interval", default="6h", help="Docker healthcheck interval")
    p.add_argument("--health-timeout", default="60s", help="Docker healthcheck timeout")
    p.add_argument("--health-retries", type=int, default=1, help="Docker healthcheck retries")
    p.add_argument("--health-start-period", default="2h", help="Docker healthcheck start_period")
    p.add_argument("--health-use-ops-readiness", action="store_true", default=False, help="use ops-readiness instead of daily-radar-health for Docker healthcheck")
    p.add_argument("--health-ops-target-phase", choices=["paper", "small_live", "live"], default="paper", help="target phase passed to ops-readiness healthcheck")
    p.add_argument("--health-include-intraday-quote-review", action="store_true", default=False, help="include intraday quote post-session review in Docker ops-readiness healthcheck")
    p.add_argument("--health-intraday-quote-review-max-age-seconds", type=int, default=3600)
    p.add_argument("--health-intraday-quote-review-min-heartbeat-count", type=int, default=1)
    p.add_argument("--health-intraday-quote-review-min-quote-coverage", type=float, default=0.8)
    p.add_argument("--enable-intraday-quote-service", action="store_true", default=False, help="also run long-lived intraday quote service in compose")
    p.add_argument("--intraday-quote-service-name", default="intraday-quote", help="compose service/container name for intraday quote service")
    p.add_argument("--intraday-quote-symbols", default="", help="comma-separated symbols for the long-running quote service")
    p.add_argument("--intraday-quote-state-path", default="data/cache/intraday_quote_service_status.json", help="health snapshot path for intraday quote service")
    p.add_argument("--intraday-quote-heartbeat-interval-seconds", type=float, default=5.0, help="seconds between quote service health snapshots")
    p.add_argument("--intraday-quote-shioaji-live", action="store_true", default=False, help="use live Shioaji environment for intraday quote service")
    p.add_argument("--intraday-quote-no-bidask", action="store_true", default=False, help="subscribe tick only for intraday quote service")
    p.add_argument("--intraday-quote-allow-outside-session", action="store_true", default=False, help="allow quote service to start outside regular Taiwan stock session")
    p.add_argument("--intraday-quote-health-max-age-seconds", type=int, default=30, help="max health snapshot age before compose healthcheck fails")
    p.add_argument("--intraday-quote-health-interval", default="30s", help="Docker intraday quote healthcheck interval")
    p.add_argument("--intraday-quote-health-timeout", default="10s", help="Docker intraday quote healthcheck timeout")
    p.add_argument("--intraday-quote-health-retries", type=int, default=2, help="Docker intraday quote healthcheck retries")
    p.add_argument("--intraday-quote-health-start-period", default="30s", help="Docker intraday quote healthcheck start_period")
    p.set_defaults(func=cmd_write_docker_compose)

    p = sub.add_parser("live-buy", help="Place guarded Shioaji stock buy order (phase 3)")
    p.add_argument("--symbol", required=True, help="e.g. 2330")
    p.add_argument("--price", type=float, required=False, help="limit price; omit to use reference price")
    p.add_argument("--price-offset-ticks", type=int, default=0, help="apply +/- ticks on resolved price")
    p.add_argument("--enforce-session-check", action="store_true", help="reject order when session is closed")
    p.add_argument("--live", action="store_true", help="set simulation=False")
    p.add_argument("--live-order-nonce", default="", help="nonce required when SHIOAJI_LIVE_ORDER_NONCE is configured")
    p.add_argument("--common-lot", action="store_true", help="use common-lot order instead of intraday odd-lot")
    p.set_defaults(func=cmd_live_buy)

    p = sub.add_parser("shioaji-doctor", help="Run Shioaji API readiness checks")
    p.add_argument("--simulation", action="store_true", default=True)
    p.add_argument("--live", dest="simulation", action="store_false")
    p.set_defaults(func=cmd_shioaji_doctor)

    p = sub.add_parser("account-snapshot", help="Query Shioaji account snapshot")
    p.add_argument("--simulation", action="store_true", default=True)
    p.add_argument("--live", dest="simulation", action="store_false")
    p.set_defaults(func=cmd_account_snapshot)

    p = sub.add_parser("order-preview", help="Create a guarded stock order preview")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", choices=["buy", "sell"], default="buy")
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--quantity", type=int, required=True)
    p.add_argument("--available-cash", type=float, default=10_000)
    p.add_argument("--position-before", type=int, default=0)
    p.add_argument("--strategy-name", default="manual")
    p.add_argument("--strategy-version", default="manual")
    p.add_argument("--signal-id", default="manual")
    p.add_argument("--reference-price", type=float, default=None, help="reference price used by live price deviation guard")
    p.add_argument("--max-price-deviation-ticks", type=int, default=3, help="max ticks away from reference price")
    p.add_argument("--max-price-deviation-pct", type=float, default=0.01, help="max percentage away from reference price")
    p.add_argument("--ttl-seconds", type=int, default=120)
    p.add_argument("--common-lot", action="store_true")
    p.set_defaults(func=cmd_order_preview)

    p = sub.add_parser("reconcile", help="Compare expected and actual cash/positions")
    p.add_argument("--expected-cash", type=float, required=True)
    p.add_argument("--actual-cash", type=float, required=True)
    p.add_argument("--expected-positions", default="", help="e.g. 2330:3,2317:1")
    p.add_argument("--actual-positions", default="", help="e.g. 2330:3,2317:1")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("trading-audit", help="List recent trading audit records")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_trading_audit)

    p = sub.add_parser("eod-summary", help="Summarize after-close reconciliation and trading operations")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_eod_summary)

    p = sub.add_parser("advisor-observation-add", help="Record one stub advisor observation day")
    p.add_argument("--observation-date", default=None, help="YYYY-MM-DD; default=today")
    p.add_argument("--symbol", required=True, help="e.g. 2330")
    p.add_argument("--advisor-name", default="stub")
    p.add_argument("--advisor-version", default="")
    p.add_argument("--preview-created", action="store_true")
    p.add_argument("--human-action", choices=["rejected", "confirmed", "skipped"], default="skipped")
    p.add_argument(
        "--simulation-result",
        choices=["submitted", "skipped", "rejected", "failed"],
        default="skipped",
    )
    p.add_argument("--data-quality", default="unknown")
    p.add_argument("--failed", action="store_true", help="mark this observation as failed")
    p.add_argument("--blocker-reason", action="append", default=[], help="repeatable failure/blocking reason")
    p.add_argument("--notes", default="")
    p.add_argument("--meta-json", default="", help="optional JSON object")
    p.set_defaults(func=cmd_advisor_observation_add)

    p = sub.add_parser("advisor-observation-summary", help="Summarize stub advisor observation readiness")
    p.add_argument("--advisor-name", default="stub")
    p.add_argument("--min-days", type=int, default=3)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_advisor_observation_summary)

    p = sub.add_parser("promotion-readiness", help="Evaluate observe -> paper -> small-live readiness")
    p.add_argument("--advisor-name", default="stub")
    p.add_argument("--strategy-name", default="manual")
    p.add_argument("--strategy-version", default="manual")
    p.add_argument("--target-phase", choices=["paper", "small_live", "live"], default="paper")
    p.add_argument("--min-observation-days", type=int, default=3)
    p.add_argument("--promotion-gate-limit", type=int, default=100)
    p.set_defaults(func=cmd_promotion_readiness)

    p = sub.add_parser("paper-ledger", help="Simulate T+2 paper ledger and export HTML/CSV/JSON")
    p.add_argument("--symbol", required=True, help="e.g. 2330")
    p.add_argument("--start-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--initial-cash", type=float, default=10_000, help="virtual initial cash")
    p.add_argument("--hold-days", type=int, default=5, help="time exit bars")
    p.add_argument("--settlement-days", type=int, default=2, help="T+n settlement lag")
    p.add_argument("--force-close-end", dest="force_close_end", action="store_true", help="force close position on end date")
    p.add_argument("--no-force-close-end", dest="force_close_end", action="store_false", help="keep open position at end date")
    p.set_defaults(force_close_end=True)
    p.add_argument("--output-dir", default="reports", help="output directory for report files")
    p.set_defaults(func=cmd_paper_ledger)

    p = sub.add_parser("signal-preview", help="Preview entry suggestions using chip + disposition filters")
    p.add_argument("--trade-date", required=False, help="YYYY-MM-DD")
    p.add_argument("--available-cash", type=float, default=10_000)
    p.add_argument("--max-symbols", type=int, default=50)
    p.add_argument("--require-chip", dest="require_chip", action="store_true", default=True)
    p.add_argument("--no-require-chip", dest="require_chip", action="store_false")
    p.add_argument("--block-disposition", dest="block_disposition", action="store_true", default=True)
    p.add_argument("--no-block-disposition", dest="block_disposition", action="store_false")
    p.add_argument("--rank-by", default="score", help="score | ensemble_score | risk_adjusted_score")
    p.add_argument("--max-open-positions", type=int, default=None, help="max number of suggestions to allocate")
    p.add_argument("--data-freshness-required", action="store_true", help="require latest bar date >= trade-date")
    p.add_argument("--chip-threshold-mode", default="absolute", help="absolute | zscore")
    p.set_defaults(func=cmd_signal_preview)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"[error] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
