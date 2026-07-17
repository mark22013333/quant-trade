from __future__ import annotations

import plistlib
import sys
from pathlib import Path


DEFAULT_LABEL = "com.quant-trade.daily-radar"
DEFAULT_TRIGGER_MONITOR_LABEL = "com.quant-trade.daily-radar-trigger-monitor"
DEFAULT_WEEKDAYS = (1, 2, 3, 4, 5)


def _abs_path(path: str | Path, *, base: Path | None = None) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (base or Path.cwd()) / candidate
    return str(candidate.absolute())


def _weekday_entries(hour: int, minute: int, weekdays: tuple[int, ...] = DEFAULT_WEEKDAYS) -> list[dict[str, int]]:
    safe_hour = max(0, min(23, int(hour)))
    safe_minute = max(0, min(59, int(minute)))
    safe_weekdays = tuple(day for day in weekdays if 0 <= int(day) <= 7) or DEFAULT_WEEKDAYS
    return [{"Weekday": int(day), "Hour": safe_hour, "Minute": safe_minute} for day in safe_weekdays]


def daily_radar_program_arguments(
    *,
    python_path: str | Path,
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
) -> list[str]:
    args = [
        _abs_path(python_path),
        "-m",
        "app.cli",
        "run-daily-radar-job",
        "--trade-date-mode",
        str(trade_date_mode or "T"),
        "--target-count",
        str(int(target_count)),
        "--risk-profile",
        str(risk_profile or "balanced"),
        "--threshold-offset",
        str(float(threshold_offset or 0.0)),
        "--universe",
        str(universe or "curated_watchlist"),
    ]
    clean_symbols = str(symbols or "").strip()
    if clean_symbols:
        args.extend(["--symbols", clean_symbols])
    clean_notify = str(notify or "none").strip().lower()
    if clean_notify and clean_notify != "none":
        args.extend(["--notify", clean_notify])
        clean_style = str(notify_style or "premarket").strip().lower()
        if clean_style and clean_style != "premarket":
            args.extend(["--notify-style", clean_style])
    clean_url = str(index_constituents_url or "").strip()
    if clean_url:
        args.extend(["--index-constituents-url", clean_url])
        clean_key = str(index_constituents_key or "").strip()
        if clean_key:
            args.extend(["--index-constituents-key", clean_key])
        clean_source = str(index_constituents_source or "").strip()
        if clean_source and clean_source != "official_url":
            args.extend(["--index-constituents-source", clean_source])
    if not data_freshness_required:
        args.append("--no-data-freshness-required")
    return args


def build_daily_radar_launchd_plist(
    *,
    project_dir: str | Path,
    python_path: str | Path | None = None,
    label: str = DEFAULT_LABEL,
    hour: int = 8,
    minute: int = 45,
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
    run_at_load: bool = False,
    stdout_path: str | Path | None = None,
    stderr_path: str | Path | None = None,
) -> dict:
    root = Path(project_dir).expanduser().absolute()
    logs_dir = root / "logs"
    resolved_python = python_path or (root / ".venv" / "bin" / "python")
    if not Path(resolved_python).expanduser().is_absolute() and str(resolved_python).startswith(".venv"):
        resolved_python = root / resolved_python
    if not Path(resolved_python).expanduser().exists():
        resolved_python = sys.executable
    out_path = stdout_path or (logs_dir / "daily-radar.launchd.out.log")
    err_path = stderr_path or (logs_dir / "daily-radar.launchd.err.log")
    return {
        "Label": str(label or DEFAULT_LABEL),
        "ProgramArguments": daily_radar_program_arguments(
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
        ),
        "WorkingDirectory": str(root),
        "StartCalendarInterval": _weekday_entries(hour=hour, minute=minute),
        "StandardOutPath": _abs_path(out_path, base=root),
        "StandardErrorPath": _abs_path(err_path, base=root),
        "RunAtLoad": bool(run_at_load),
    }


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


def build_daily_radar_trigger_monitor_launchd_plist(
    *,
    project_dir: str | Path,
    python_path: str | Path | None = None,
    label: str = DEFAULT_TRIGGER_MONITOR_LABEL,
    hour: int = 9,
    minute: int = 15,
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
    run_at_load: bool = False,
    stdout_path: str | Path | None = None,
    stderr_path: str | Path | None = None,
) -> dict:
    root = Path(project_dir).expanduser().absolute()
    logs_dir = root / "logs"
    resolved_python = python_path or (root / ".venv" / "bin" / "python")
    if not Path(resolved_python).expanduser().is_absolute() and str(resolved_python).startswith(".venv"):
        resolved_python = root / resolved_python
    if not Path(resolved_python).expanduser().exists():
        resolved_python = sys.executable
    out_path = stdout_path or (logs_dir / "daily-radar-trigger-monitor.launchd.out.log")
    err_path = stderr_path or (logs_dir / "daily-radar-trigger-monitor.launchd.err.log")
    return {
        "Label": str(label or DEFAULT_TRIGGER_MONITOR_LABEL),
        "ProgramArguments": daily_radar_trigger_monitor_program_arguments(
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
        ),
        "WorkingDirectory": str(root),
        "StartCalendarInterval": _weekday_entries(hour=hour, minute=minute),
        "StandardOutPath": _abs_path(out_path, base=root),
        "StandardErrorPath": _abs_path(err_path, base=root),
        "RunAtLoad": bool(run_at_load),
    }


def write_daily_radar_launchd_plist(
    output_path: str | Path,
    *,
    project_dir: str | Path,
    **kwargs,
) -> Path:
    output = Path(output_path).expanduser()
    if not output.is_absolute():
        output = Path(project_dir).expanduser().resolve() / output
    payload = build_daily_radar_launchd_plist(project_dir=project_dir, **kwargs)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        plistlib.dump(payload, fh, sort_keys=False)
    return output


def write_daily_radar_trigger_monitor_launchd_plist(
    output_path: str | Path,
    *,
    project_dir: str | Path,
    **kwargs,
) -> Path:
    output = Path(output_path).expanduser()
    if not output.is_absolute():
        output = Path(project_dir).expanduser().resolve() / output
    payload = build_daily_radar_trigger_monitor_launchd_plist(project_dir=project_dir, **kwargs)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        plistlib.dump(payload, fh, sort_keys=False)
    return output
