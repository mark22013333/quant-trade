from __future__ import annotations

import shlex
import sys
from pathlib import Path

from app.scheduler.launchd import daily_radar_program_arguments


DEFAULT_SERVICE_NAME = "quant-trade-daily-radar.service"
DEFAULT_TIMER_NAME = "quant-trade-daily-radar.timer"
DEFAULT_HEALTH_SERVICE_NAME = "quant-trade-daily-radar-health.service"
DEFAULT_HEALTH_TIMER_NAME = "quant-trade-daily-radar-health.timer"
DEFAULT_TRIGGER_MONITOR_SERVICE_NAME = "quant-trade-daily-radar-trigger-monitor.service"
DEFAULT_TRIGGER_MONITOR_TIMER_NAME = "quant-trade-daily-radar-trigger-monitor.timer"
DEFAULT_INTRADAY_QUOTE_SERVICE_NAME = "quant-trade-intraday-quote.service"
DEFAULT_INTRADAY_QUOTE_HEALTH_SERVICE_NAME = "quant-trade-intraday-quote-health.service"
DEFAULT_INTRADAY_QUOTE_HEALTH_TIMER_NAME = "quant-trade-intraday-quote-health.timer"


def _abs_path(path: str | Path, *, base: Path | None = None) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (base or Path.cwd()) / candidate
    return str(candidate.absolute())


def resolve_python_path(project_dir: str | Path, python_path: str | Path | None = None) -> str:
    root = Path(project_dir).expanduser().absolute()
    if python_path:
        return _abs_path(python_path, base=root)
    for candidate in (root / "venv" / "bin" / "python", root / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def build_daily_radar_systemd_service(
    *,
    project_dir: str | Path,
    python_path: str | Path | None = None,
    env_file: str | Path | None = None,
    user: str = "root",
    group: str = "root",
    trade_date_mode: str = "T",
    target_count: int = 15,
    risk_profile: str = "balanced",
    threshold_offset: float = 0.0,
    universe: str = "curated_watchlist",
    symbols: str = "",
    data_freshness_required: bool = True,
    notify: str = "none",
    notify_style: str = "premarket",
    index_constituents_url: str = "",
    index_constituents_key: str = "",
    index_constituents_source: str = "official_url",
) -> str:
    root = Path(project_dir).expanduser().absolute()
    resolved_python = resolve_python_path(root, python_path)
    resolved_env = env_file if env_file is not None else (root / ".env")
    args = daily_radar_program_arguments(
        python_path=resolved_python,
        trade_date_mode=trade_date_mode,
        target_count=target_count,
        risk_profile=risk_profile,
        threshold_offset=threshold_offset,
        universe=universe,
        symbols=symbols,
        data_freshness_required=data_freshness_required,
        notify=notify,
        notify_style=notify_style,
        index_constituents_url=index_constituents_url,
        index_constituents_key=index_constituents_key,
        index_constituents_source=index_constituents_source,
    )
    lines = [
        "[Unit]",
        "Description=Quant-Trade Daily Radar",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"WorkingDirectory={root}",
        f"EnvironmentFile=-{_abs_path(resolved_env, base=root)}",
        f"ExecStart={shlex.join(args)}",
    ]
    if user:
        lines.append(f"User={user}")
    if group:
        lines.append(f"Group={group}")
    return "\n".join(lines) + "\n"


def build_daily_radar_systemd_timer(
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
    hour: int = 8,
    minute: int = 45,
    persistent: bool = True,
) -> str:
    safe_hour = max(0, min(23, int(hour)))
    safe_minute = max(0, min(59, int(minute)))
    return "\n".join(
        [
            "[Unit]",
            "Description=Run Quant-Trade Daily Radar on weekdays",
            "",
            "[Timer]",
            f"OnCalendar=Mon..Fri *-*-* {safe_hour:02d}:{safe_minute:02d}:00",
            f"Persistent={'true' if persistent else 'false'}",
            f"Unit={service_name}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def daily_radar_health_program_arguments(
    *,
    python_path: str | Path,
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
    notify: str = "none",
    notify_on: str = "danger",
    fail_on_danger: bool = True,
    fail_on_notify_error: bool = False,
) -> list[str]:
    args = [
        _abs_path(python_path),
        "-m",
        "app.cli",
        "daily-radar-health",
        "--max-age-days",
        str(int(max_age_days)),
        "--warn-age-days",
        str(int(warn_age_days)),
        "--min-rows",
        str(int(min_rows)),
        "--min-entry-ready",
        str(int(min_entry_ready)),
        "--notify",
        str(notify or "none"),
        "--notify-on",
        str(notify_on or "danger"),
    ]
    if fail_on_danger:
        args.append("--fail-on-danger")
    if fail_on_notify_error:
        args.append("--fail-on-notify-error")
    return args


def ops_readiness_program_arguments(
    *,
    python_path: str | Path,
    target_phase: str = "paper",
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
    eod_limit: int = 100,
    include_intraday_quote_review: bool = False,
    intraday_quote_state_path: str | Path = "data/cache/intraday_quote_service_status.json",
    intraday_quote_review_max_age_seconds: int = 3600,
    intraday_quote_review_min_heartbeat_count: int = 1,
    intraday_quote_review_min_quote_coverage: float = 0.8,
    fail_on_danger: bool = True,
) -> list[str]:
    args = [
        _abs_path(python_path),
        "-m",
        "app.cli",
        "ops-readiness",
        "--target-phase",
        str(target_phase or "paper"),
        "--max-age-days",
        str(int(max_age_days)),
        "--warn-age-days",
        str(int(warn_age_days)),
        "--min-rows",
        str(int(min_rows)),
        "--min-entry-ready",
        str(int(min_entry_ready)),
        "--eod-limit",
        str(int(eod_limit)),
    ]
    if include_intraday_quote_review:
        args.extend(
            [
                "--include-intraday-quote-review",
                "--intraday-quote-state-path",
                str(intraday_quote_state_path or "data/cache/intraday_quote_service_status.json"),
                "--intraday-quote-review-max-age-seconds",
                str(int(intraday_quote_review_max_age_seconds)),
                "--intraday-quote-review-min-heartbeat-count",
                str(int(intraday_quote_review_min_heartbeat_count)),
                "--intraday-quote-review-min-quote-coverage",
                str(float(intraday_quote_review_min_quote_coverage)),
            ]
        )
    if fail_on_danger:
        args.append("--fail-on-danger")
    return args


def build_daily_radar_health_systemd_service(
    *,
    project_dir: str | Path,
    python_path: str | Path | None = None,
    env_file: str | Path | None = None,
    user: str = "root",
    group: str = "root",
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
    notify: str = "none",
    notify_on: str = "danger",
    fail_on_danger: bool = True,
    fail_on_notify_error: bool = False,
    use_ops_readiness: bool = False,
    ops_target_phase: str = "paper",
    include_intraday_quote_review: bool = False,
    intraday_quote_state_path: str | Path = "data/cache/intraday_quote_service_status.json",
    intraday_quote_review_max_age_seconds: int = 3600,
    intraday_quote_review_min_heartbeat_count: int = 1,
    intraday_quote_review_min_quote_coverage: float = 0.8,
) -> str:
    root = Path(project_dir).expanduser().absolute()
    resolved_python = resolve_python_path(root, python_path)
    resolved_env = env_file if env_file is not None else (root / ".env")
    if use_ops_readiness:
        args = ops_readiness_program_arguments(
            python_path=resolved_python,
            target_phase=ops_target_phase,
            max_age_days=max_age_days,
            warn_age_days=warn_age_days,
            min_rows=min_rows,
            min_entry_ready=min_entry_ready,
            include_intraday_quote_review=include_intraday_quote_review,
            intraday_quote_state_path=intraday_quote_state_path,
            intraday_quote_review_max_age_seconds=intraday_quote_review_max_age_seconds,
            intraday_quote_review_min_heartbeat_count=intraday_quote_review_min_heartbeat_count,
            intraday_quote_review_min_quote_coverage=intraday_quote_review_min_quote_coverage,
            fail_on_danger=fail_on_danger,
        )
    else:
        args = daily_radar_health_program_arguments(
            python_path=resolved_python,
            max_age_days=max_age_days,
            warn_age_days=warn_age_days,
            min_rows=min_rows,
            min_entry_ready=min_entry_ready,
            notify=notify,
            notify_on=notify_on,
            fail_on_danger=fail_on_danger,
            fail_on_notify_error=fail_on_notify_error,
        )
    lines = [
        "[Unit]",
        "Description=Quant-Trade Daily Radar Health Check",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"WorkingDirectory={root}",
        f"EnvironmentFile=-{_abs_path(resolved_env, base=root)}",
        f"ExecStart={shlex.join(args)}",
    ]
    if user:
        lines.append(f"User={user}")
    if group:
        lines.append(f"Group={group}")
    return "\n".join(lines) + "\n"


def build_daily_radar_health_systemd_timer(
    *,
    service_name: str = DEFAULT_HEALTH_SERVICE_NAME,
    hour: int = 9,
    minute: int = 30,
    persistent: bool = True,
) -> str:
    return build_daily_radar_systemd_timer(
        service_name=service_name,
        hour=hour,
        minute=minute,
        persistent=persistent,
    ).replace("Description=Run Quant-Trade Daily Radar on weekdays", "Description=Check Quant-Trade Daily Radar health on weekdays")


def daily_radar_trigger_monitor_program_arguments(
    *,
    python_path: str | Path,
    count: int = 50,
    limit: int = 10,
    near_entry_gap: float = 2.0,
    near_price_gap_pct: float = 0.02,
    price_source: str = "radar_snapshot",
    shioaji_simulation: bool = True,
    max_price_age_seconds: int = 180,
    streaming_seconds: float = 3.0,
    notify: str = "none",
    notify_on: str = "ready",
    notification_state_path: str | Path = "data/cache/daily_radar_trigger_monitor_state.json",
    notification_cooldown_minutes: int = 240,
    force_notify: bool = False,
    fail_on_ready: bool = False,
    fail_on_notify_error: bool = False,
) -> list[str]:
    args = [
        _abs_path(python_path),
        "-m",
        "app.cli",
        "daily-radar-trigger-monitor",
        "--count",
        str(int(count)),
        "--limit",
        str(int(limit)),
        "--near-entry-gap",
        str(float(near_entry_gap)),
        "--near-price-gap-pct",
        str(float(near_price_gap_pct)),
        "--price-source",
        str(price_source or "radar_snapshot"),
        "--max-price-age-seconds",
        str(int(max_price_age_seconds)),
        "--streaming-seconds",
        str(float(streaming_seconds)),
        "--notify",
        str(notify or "none"),
        "--notify-on",
        str(notify_on or "ready"),
        "--notification-state-path",
        str(notification_state_path or "data/cache/daily_radar_trigger_monitor_state.json"),
        "--notification-cooldown-minutes",
        str(int(notification_cooldown_minutes)),
    ]
    if not shioaji_simulation:
        args.append("--shioaji-live")
    if force_notify:
        args.append("--force-notify")
    if fail_on_ready:
        args.append("--fail-on-ready")
    if fail_on_notify_error:
        args.append("--fail-on-notify-error")
    return args


def build_daily_radar_trigger_monitor_systemd_service(
    *,
    project_dir: str | Path,
    python_path: str | Path | None = None,
    env_file: str | Path | None = None,
    user: str = "root",
    group: str = "root",
    count: int = 50,
    limit: int = 10,
    near_entry_gap: float = 2.0,
    near_price_gap_pct: float = 0.02,
    price_source: str = "radar_snapshot",
    shioaji_simulation: bool = True,
    max_price_age_seconds: int = 180,
    streaming_seconds: float = 3.0,
    notify: str = "none",
    notify_on: str = "ready",
    notification_state_path: str | Path = "data/cache/daily_radar_trigger_monitor_state.json",
    notification_cooldown_minutes: int = 240,
    force_notify: bool = False,
    fail_on_ready: bool = False,
    fail_on_notify_error: bool = False,
) -> str:
    root = Path(project_dir).expanduser().absolute()
    resolved_python = resolve_python_path(root, python_path)
    resolved_env = env_file if env_file is not None else (root / ".env")
    args = daily_radar_trigger_monitor_program_arguments(
        python_path=resolved_python,
        count=count,
        limit=limit,
        near_entry_gap=near_entry_gap,
        near_price_gap_pct=near_price_gap_pct,
        price_source=price_source,
        shioaji_simulation=shioaji_simulation,
        max_price_age_seconds=max_price_age_seconds,
        streaming_seconds=streaming_seconds,
        notify=notify,
        notify_on=notify_on,
        notification_state_path=notification_state_path,
        notification_cooldown_minutes=notification_cooldown_minutes,
        force_notify=force_notify,
        fail_on_ready=fail_on_ready,
        fail_on_notify_error=fail_on_notify_error,
    )
    lines = [
        "[Unit]",
        "Description=Quant-Trade Daily Radar Trigger Monitor",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"WorkingDirectory={root}",
        f"EnvironmentFile=-{_abs_path(resolved_env, base=root)}",
        f"ExecStart={shlex.join(args)}",
    ]
    if user:
        lines.append(f"User={user}")
    if group:
        lines.append(f"Group={group}")
    return "\n".join(lines) + "\n"


def build_daily_radar_trigger_monitor_systemd_timer(
    *,
    service_name: str = DEFAULT_TRIGGER_MONITOR_SERVICE_NAME,
    hour: int = 9,
    minute: int = 15,
    persistent: bool = True,
) -> str:
    return build_daily_radar_systemd_timer(
        service_name=service_name,
        hour=hour,
        minute=minute,
        persistent=persistent,
    ).replace("Description=Run Quant-Trade Daily Radar on weekdays", "Description=Run Quant-Trade Daily Radar trigger monitor on weekdays")


def intraday_quote_service_program_arguments(
    *,
    python_path: str | Path,
    symbols: str,
    state_path: str | Path = "data/cache/intraday_quote_service_status.json",
    heartbeat_interval_seconds: float = 5.0,
    shioaji_simulation: bool = True,
    include_bidask: bool = True,
    allow_outside_session: bool = False,
) -> list[str]:
    args = [
        _abs_path(python_path),
        "-m",
        "app.cli",
        "run-intraday-quote-service",
        "--symbols",
        str(symbols or ""),
        "--state-path",
        str(state_path or "data/cache/intraday_quote_service_status.json"),
        "--heartbeat-interval-seconds",
        str(float(heartbeat_interval_seconds)),
    ]
    if not shioaji_simulation:
        args.append("--shioaji-live")
    if not include_bidask:
        args.append("--no-bidask")
    if allow_outside_session:
        args.append("--allow-outside-session")
    return args


def intraday_quote_health_program_arguments(
    *,
    python_path: str | Path,
    state_path: str | Path = "data/cache/intraday_quote_service_status.json",
    max_age_seconds: int = 30,
    fail_on_warn: bool = True,
    fail_on_danger: bool = True,
) -> list[str]:
    args = [
        _abs_path(python_path),
        "-m",
        "app.cli",
        "intraday-quote-service-status",
        "--state-path",
        str(state_path or "data/cache/intraday_quote_service_status.json"),
        "--max-age-seconds",
        str(int(max_age_seconds)),
    ]
    if fail_on_warn:
        args.append("--fail-on-warn")
    elif fail_on_danger:
        args.append("--fail-on-danger")
    return args


def build_intraday_quote_systemd_service(
    *,
    project_dir: str | Path,
    python_path: str | Path | None = None,
    env_file: str | Path | None = None,
    user: str = "root",
    group: str = "root",
    symbols: str = "",
    state_path: str | Path = "data/cache/intraday_quote_service_status.json",
    heartbeat_interval_seconds: float = 5.0,
    shioaji_simulation: bool = True,
    include_bidask: bool = True,
    allow_outside_session: bool = False,
    restart: str = "always",
    restart_sec: int = 10,
) -> str:
    root = Path(project_dir).expanduser().absolute()
    resolved_python = resolve_python_path(root, python_path)
    resolved_env = env_file if env_file is not None else (root / ".env")
    args = intraday_quote_service_program_arguments(
        python_path=resolved_python,
        symbols=symbols,
        state_path=state_path,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        shioaji_simulation=shioaji_simulation,
        include_bidask=include_bidask,
        allow_outside_session=allow_outside_session,
    )
    lines = [
        "[Unit]",
        "Description=Quant-Trade Intraday Shioaji Quote Service",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={root}",
        f"EnvironmentFile=-{_abs_path(resolved_env, base=root)}",
        f"ExecStart={shlex.join(args)}",
        f"Restart={restart}",
        f"RestartSec={max(1, int(restart_sec))}",
    ]
    if user:
        lines.append(f"User={user}")
    if group:
        lines.append(f"Group={group}")
    lines.extend(["", "[Install]", "WantedBy=multi-user.target", ""])
    return "\n".join(lines)


def build_intraday_quote_health_systemd_service(
    *,
    project_dir: str | Path,
    python_path: str | Path | None = None,
    env_file: str | Path | None = None,
    user: str = "root",
    group: str = "root",
    state_path: str | Path = "data/cache/intraday_quote_service_status.json",
    max_age_seconds: int = 30,
    fail_on_warn: bool = True,
    fail_on_danger: bool = True,
) -> str:
    root = Path(project_dir).expanduser().absolute()
    resolved_python = resolve_python_path(root, python_path)
    resolved_env = env_file if env_file is not None else (root / ".env")
    args = intraday_quote_health_program_arguments(
        python_path=resolved_python,
        state_path=state_path,
        max_age_seconds=max_age_seconds,
        fail_on_warn=fail_on_warn,
        fail_on_danger=fail_on_danger,
    )
    lines = [
        "[Unit]",
        "Description=Quant-Trade Intraday Quote Health Check",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"WorkingDirectory={root}",
        f"EnvironmentFile=-{_abs_path(resolved_env, base=root)}",
        f"ExecStart={shlex.join(args)}",
    ]
    if user:
        lines.append(f"User={user}")
    if group:
        lines.append(f"Group={group}")
    return "\n".join(lines) + "\n"


def build_intraday_quote_health_systemd_timer(
    *,
    service_name: str = DEFAULT_INTRADAY_QUOTE_HEALTH_SERVICE_NAME,
    interval_seconds: int = 30,
    persistent: bool = True,
) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Check Quant-Trade Intraday Quote health",
            "",
            "[Timer]",
            "OnBootSec=30s",
            f"OnUnitActiveSec={max(10, int(interval_seconds))}s",
            f"Persistent={'true' if persistent else 'false'}",
            f"Unit={service_name}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def write_daily_radar_systemd_units(
    output_dir: str | Path,
    *,
    project_dir: str | Path,
    service_name: str = DEFAULT_SERVICE_NAME,
    timer_name: str = DEFAULT_TIMER_NAME,
    health_service_name: str = DEFAULT_HEALTH_SERVICE_NAME,
    health_timer_name: str = DEFAULT_HEALTH_TIMER_NAME,
    trigger_monitor_service_name: str = DEFAULT_TRIGGER_MONITOR_SERVICE_NAME,
    trigger_monitor_timer_name: str = DEFAULT_TRIGGER_MONITOR_TIMER_NAME,
    intraday_quote_service_name: str = DEFAULT_INTRADAY_QUOTE_SERVICE_NAME,
    intraday_quote_health_service_name: str = DEFAULT_INTRADAY_QUOTE_HEALTH_SERVICE_NAME,
    intraday_quote_health_timer_name: str = DEFAULT_INTRADAY_QUOTE_HEALTH_TIMER_NAME,
    hour: int = 8,
    minute: int = 45,
    health_hour: int = 9,
    health_minute: int = 30,
    include_health_monitor: bool = False,
    include_trigger_monitor: bool = False,
    include_intraday_quote_service: bool = False,
    health_max_age_days: int = 3,
    health_warn_age_days: int = 1,
    health_min_rows: int = 5,
    health_min_entry_ready: int = 1,
    health_notify: str = "none",
    health_notify_on: str = "danger",
    health_fail_on_danger: bool = True,
    health_fail_on_notify_error: bool = False,
    health_use_ops_readiness: bool = False,
    health_ops_target_phase: str = "paper",
    health_include_intraday_quote_review: bool = False,
    health_intraday_quote_review_max_age_seconds: int = 3600,
    health_intraday_quote_review_min_heartbeat_count: int = 1,
    health_intraday_quote_review_min_quote_coverage: float = 0.8,
    trigger_monitor_hour: int = 9,
    trigger_monitor_minute: int = 15,
    trigger_monitor_count: int = 50,
    trigger_monitor_limit: int = 10,
    trigger_monitor_near_entry_gap: float = 2.0,
    trigger_monitor_near_price_gap_pct: float = 0.02,
    trigger_monitor_notify: str = "none",
    trigger_monitor_notify_on: str = "ready",
    trigger_monitor_price_source: str = "radar_snapshot",
    trigger_monitor_shioaji_simulation: bool = True,
    trigger_monitor_max_price_age_seconds: int = 180,
    trigger_monitor_streaming_seconds: float = 3.0,
    trigger_monitor_notification_state_path: str | Path = "data/cache/daily_radar_trigger_monitor_state.json",
    trigger_monitor_notification_cooldown_minutes: int = 240,
    trigger_monitor_force_notify: bool = False,
    trigger_monitor_fail_on_ready: bool = False,
    trigger_monitor_fail_on_notify_error: bool = False,
    intraday_quote_symbols: str = "",
    intraday_quote_state_path: str | Path = "data/cache/intraday_quote_service_status.json",
    intraday_quote_heartbeat_interval_seconds: float = 5.0,
    intraday_quote_shioaji_simulation: bool = True,
    intraday_quote_include_bidask: bool = True,
    intraday_quote_allow_outside_session: bool = False,
    intraday_quote_restart: str = "always",
    intraday_quote_restart_sec: int = 10,
    intraday_quote_health_max_age_seconds: int = 30,
    intraday_quote_health_interval_seconds: int = 30,
    intraday_quote_health_fail_on_warn: bool = True,
    intraday_quote_health_fail_on_danger: bool = True,
    **kwargs,
) -> dict[str, Path]:
    output = Path(output_dir).expanduser()
    if not output.is_absolute():
        output = Path(project_dir).expanduser().absolute() / output
    output.mkdir(parents=True, exist_ok=True)
    service_path = output / service_name
    timer_path = output / timer_name
    service_path.write_text(
        build_daily_radar_systemd_service(project_dir=project_dir, **kwargs),
        encoding="utf-8",
    )
    timer_path.write_text(
        build_daily_radar_systemd_timer(service_name=service_name, hour=hour, minute=minute),
        encoding="utf-8",
    )
    paths = {"service": service_path, "timer": timer_path}
    if include_health_monitor:
        health_service_path = output / health_service_name
        health_timer_path = output / health_timer_name
        health_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in {"python_path", "env_file", "user", "group"}
        }
        health_service_path.write_text(
            build_daily_radar_health_systemd_service(
                project_dir=project_dir,
                max_age_days=health_max_age_days,
                warn_age_days=health_warn_age_days,
                min_rows=health_min_rows,
                min_entry_ready=health_min_entry_ready,
                notify=health_notify,
                notify_on=health_notify_on,
                fail_on_danger=health_fail_on_danger,
                fail_on_notify_error=health_fail_on_notify_error,
                use_ops_readiness=health_use_ops_readiness,
                ops_target_phase=health_ops_target_phase,
                include_intraday_quote_review=health_include_intraday_quote_review,
                intraday_quote_state_path=intraday_quote_state_path,
                intraday_quote_review_max_age_seconds=health_intraday_quote_review_max_age_seconds,
                intraday_quote_review_min_heartbeat_count=health_intraday_quote_review_min_heartbeat_count,
                intraday_quote_review_min_quote_coverage=health_intraday_quote_review_min_quote_coverage,
                **health_kwargs,
            ),
            encoding="utf-8",
        )
        health_timer_path.write_text(
            build_daily_radar_health_systemd_timer(
                service_name=health_service_name,
                hour=health_hour,
                minute=health_minute,
            ),
            encoding="utf-8",
        )
        paths["health_service"] = health_service_path
        paths["health_timer"] = health_timer_path
    if include_trigger_monitor:
        trigger_service_path = output / trigger_monitor_service_name
        trigger_timer_path = output / trigger_monitor_timer_name
        trigger_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in {"python_path", "env_file", "user", "group"}
        }
        trigger_service_path.write_text(
            build_daily_radar_trigger_monitor_systemd_service(
                project_dir=project_dir,
                count=trigger_monitor_count,
                limit=trigger_monitor_limit,
                near_entry_gap=trigger_monitor_near_entry_gap,
                near_price_gap_pct=trigger_monitor_near_price_gap_pct,
                price_source=trigger_monitor_price_source,
                shioaji_simulation=trigger_monitor_shioaji_simulation,
                max_price_age_seconds=trigger_monitor_max_price_age_seconds,
                streaming_seconds=trigger_monitor_streaming_seconds,
                notify=trigger_monitor_notify,
                notify_on=trigger_monitor_notify_on,
                notification_state_path=trigger_monitor_notification_state_path,
                notification_cooldown_minutes=trigger_monitor_notification_cooldown_minutes,
                force_notify=trigger_monitor_force_notify,
                fail_on_ready=trigger_monitor_fail_on_ready,
                fail_on_notify_error=trigger_monitor_fail_on_notify_error,
                **trigger_kwargs,
            ),
            encoding="utf-8",
        )
        trigger_timer_path.write_text(
            build_daily_radar_trigger_monitor_systemd_timer(
                service_name=trigger_monitor_service_name,
                hour=trigger_monitor_hour,
                minute=trigger_monitor_minute,
            ),
            encoding="utf-8",
        )
        paths["trigger_monitor_service"] = trigger_service_path
        paths["trigger_monitor_timer"] = trigger_timer_path
    if include_intraday_quote_service:
        quote_service_path = output / intraday_quote_service_name
        quote_health_service_path = output / intraday_quote_health_service_name
        quote_health_timer_path = output / intraday_quote_health_timer_name
        quote_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in {"python_path", "env_file", "user", "group"}
        }
        quote_service_path.write_text(
            build_intraday_quote_systemd_service(
                project_dir=project_dir,
                symbols=intraday_quote_symbols,
                state_path=intraday_quote_state_path,
                heartbeat_interval_seconds=intraday_quote_heartbeat_interval_seconds,
                shioaji_simulation=intraday_quote_shioaji_simulation,
                include_bidask=intraday_quote_include_bidask,
                allow_outside_session=intraday_quote_allow_outside_session,
                restart=intraday_quote_restart,
                restart_sec=intraday_quote_restart_sec,
                **quote_kwargs,
            ),
            encoding="utf-8",
        )
        quote_health_service_path.write_text(
            build_intraday_quote_health_systemd_service(
                project_dir=project_dir,
                state_path=intraday_quote_state_path,
                max_age_seconds=intraday_quote_health_max_age_seconds,
                fail_on_warn=intraday_quote_health_fail_on_warn,
                fail_on_danger=intraday_quote_health_fail_on_danger,
                **quote_kwargs,
            ),
            encoding="utf-8",
        )
        quote_health_timer_path.write_text(
            build_intraday_quote_health_systemd_timer(
                service_name=intraday_quote_health_service_name,
                interval_seconds=intraday_quote_health_interval_seconds,
            ),
            encoding="utf-8",
        )
        paths["intraday_quote_service"] = quote_service_path
        paths["intraday_quote_health_service"] = quote_health_service_path
        paths["intraday_quote_health_timer"] = quote_health_timer_path
    return paths
