from __future__ import annotations

import argparse
import json
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
        result = service.sync_0050_universe(snapshot_date=snapshot_date)
        session.commit()
    print(result)


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
            include_fundamentals=bool(args.include_fundamentals),
            include_news=bool(args.include_news),
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
    TradingRepository, get_session_factory, _ = _load_db_modules()
    from app.scheduler.jobs import build_notifiers, start_scheduler

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
    result = service.get_account_snapshot(simulation=bool(args.simulation))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def cmd_order_preview(args) -> None:
    from app.backtest.costs import estimate_buy_total_cost
    from app.execution import OrderIntent
    from app.execution.order_preview import OrderPreviewService

    TradingRepository, get_session_factory, init_db = _load_db_modules()
    init_db()
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
        metadata={"strategy_version": args.strategy_version},
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

    p = sub.add_parser("sync-0050", help="Sync 0050 universe snapshot")
    p.add_argument("--snapshot-date", help="YYYY-MM-DD", default=None)
    p.set_defaults(func=cmd_sync_0050)

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
    p.add_argument("--include-fundamentals", dest="include_fundamentals", action="store_true", default=False)
    p.add_argument("--no-include-fundamentals", dest="include_fundamentals", action="store_false")
    p.add_argument("--include-news", dest="include_news", action="store_true", default=False)
    p.add_argument("--no-include-news", dest="include_news", action="store_false")
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

    p = sub.add_parser("run-scheduler", help="Start APScheduler for signal jobs")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--available-cash", type=float, default=10_000, help="cash budget used in sizing")
    p.add_argument("--hour", type=int, default=13, help="job hour (Asia/Taipei)")
    p.add_argument("--minute", type=int, default=40, help="job minute")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--rank-by", default="score", help="score | ensemble_score | risk_adjusted_score")
    p.add_argument("--max-open-positions", type=int, default=None, help="max number of suggestions to allocate")
    p.set_defaults(func=cmd_run_scheduler)

    p = sub.add_parser("run-signal-job", help="Run one-shot daily signal calculation + optional notify")
    p.add_argument("--notify", default="none", help="none | telegram | line | both")
    p.add_argument("--available-cash", type=float, default=10_000, help="cash budget used in sizing")
    p.add_argument("--trade-date-mode", default="T", help="T | T-1")
    p.add_argument("--min-data-timestamp", default=None, help="minimum last bar date (YYYY-MM-DD)")
    p.add_argument("--rank-by", default="score", help="score | ensemble_score | risk_adjusted_score")
    p.add_argument("--max-open-positions", type=int, default=None, help="max number of suggestions to allocate")
    p.set_defaults(func=cmd_run_signal_job)

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
