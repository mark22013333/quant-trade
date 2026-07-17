from __future__ import annotations

import shlex
from pathlib import Path


DEFAULT_COMPOSE_NAME = "docker-compose.daily-radar.yml"
DEFAULT_DOCKERFILE_NAME = "Dockerfile.daily-radar"
DEFAULT_SERVICE_NAME = "daily-radar"
DEFAULT_IMAGE = "quant-trade-daily-radar:latest"
DEFAULT_INTRADAY_QUOTE_SERVICE_NAME = "intraday-quote"


def _abs_path(path: str | Path, *, base: Path | None = None) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (base or Path.cwd()) / candidate
    return str(candidate.absolute())


def _yaml_quote(value: str | Path) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def daily_radar_scheduler_command(
    *,
    trade_date_mode: str = "T",
    target_count: int = 15,
    risk_profile: str = "balanced",
    threshold_offset: float = 0.0,
    universe: str = "curated_watchlist",
    symbols: str = "",
    data_freshness_required: bool = True,
    notify: str = "none",
    radar_notify_style: str = "premarket",
    enable_trigger_monitor: bool = False,
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
    trigger_monitor_notification_state_path: str = "data/cache/daily_radar_trigger_monitor_state.json",
    trigger_monitor_notification_cooldown_minutes: int = 240,
    trigger_monitor_force_notify: bool = False,
    index_constituents_url: str = "",
    index_constituents_key: str = "",
    index_constituents_source: str = "official_url",
) -> list[str]:
    args = [
        "python",
        "-m",
        "app.cli",
        "run-scheduler",
        "--notify",
        str(notify or "none"),
        "--disable-signal-job",
        "--enable-daily-radar",
        "--trade-date-mode",
        str(trade_date_mode or "T"),
        "--radar-target-count",
        str(int(target_count)),
        "--radar-risk-profile",
        str(risk_profile or "balanced"),
        "--radar-threshold-offset",
        str(float(threshold_offset or 0.0)),
        "--radar-universe",
        str(universe or "curated_watchlist"),
    ]
    clean_notify_style = str(radar_notify_style or "premarket").strip().lower()
    if clean_notify_style and clean_notify_style != "premarket":
        args.extend(["--radar-notify-style", clean_notify_style])
    clean_symbols = str(symbols or "").strip()
    if clean_symbols:
        args.extend(["--radar-symbols", clean_symbols])
    clean_url = str(index_constituents_url or "").strip()
    if clean_url:
        args.extend(["--radar-index-constituents-url", clean_url])
        clean_key = str(index_constituents_key or "").strip()
        if clean_key:
            args.extend(["--radar-index-constituents-key", clean_key])
        clean_source = str(index_constituents_source or "").strip()
        if clean_source and clean_source != "official_url":
            args.extend(["--radar-index-constituents-source", clean_source])
    if not data_freshness_required:
        args.append("--no-radar-data-freshness-required")
    if enable_trigger_monitor:
        args.extend(
            [
                "--enable-trigger-monitor",
                "--trigger-monitor-hour",
                str(max(0, min(23, int(trigger_monitor_hour)))),
                "--trigger-monitor-minute",
                str(max(0, min(59, int(trigger_monitor_minute)))),
                "--trigger-monitor-count",
                str(int(trigger_monitor_count)),
                "--trigger-monitor-limit",
                str(int(trigger_monitor_limit)),
                "--trigger-monitor-near-entry-gap",
                str(float(trigger_monitor_near_entry_gap)),
                "--trigger-monitor-near-price-gap-pct",
                str(float(trigger_monitor_near_price_gap_pct)),
                "--trigger-monitor-notify-on",
                str(trigger_monitor_notify_on or "ready"),
                "--trigger-monitor-price-source",
                str(trigger_monitor_price_source or "radar_snapshot"),
                "--trigger-monitor-max-price-age-seconds",
                str(int(trigger_monitor_max_price_age_seconds)),
                "--trigger-monitor-streaming-seconds",
                str(float(trigger_monitor_streaming_seconds)),
                "--trigger-monitor-notification-state-path",
                str(trigger_monitor_notification_state_path or "data/cache/daily_radar_trigger_monitor_state.json"),
                "--trigger-monitor-notification-cooldown-minutes",
                str(int(trigger_monitor_notification_cooldown_minutes)),
            ]
        )
        if not trigger_monitor_shioaji_simulation:
            args.append("--trigger-monitor-shioaji-live")
        if trigger_monitor_force_notify:
            args.append("--trigger-monitor-force-notify")
    return args


def daily_radar_health_command(
    *,
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
    fail_on_danger: bool = True,
) -> list[str]:
    args = [
        "python",
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
    ]
    if fail_on_danger:
        args.append("--fail-on-danger")
    return args


def ops_readiness_command(
    *,
    target_phase: str = "paper",
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
    eod_limit: int = 100,
    include_intraday_quote_review: bool = False,
    intraday_quote_state_path: str = "data/cache/intraday_quote_service_status.json",
    intraday_quote_review_max_age_seconds: int = 3600,
    intraday_quote_review_min_heartbeat_count: int = 1,
    intraday_quote_review_min_quote_coverage: float = 0.8,
    fail_on_danger: bool = True,
) -> list[str]:
    args = [
        "python",
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


def intraday_quote_service_command(
    *,
    symbols: str,
    state_path: str = "data/cache/intraday_quote_service_status.json",
    heartbeat_interval_seconds: float = 5.0,
    shioaji_simulation: bool = True,
    include_bidask: bool = True,
    allow_outside_session: bool = False,
) -> list[str]:
    args = [
        "python",
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


def intraday_quote_health_command(
    *,
    state_path: str = "data/cache/intraday_quote_service_status.json",
    max_age_seconds: int = 30,
    fail_on_warn: bool = True,
    fail_on_danger: bool = True,
) -> list[str]:
    args = [
        "python",
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


def build_daily_radar_dockerfile(*, python_image: str = "python:3.11-slim") -> str:
    return "\n".join(
        [
            f"FROM {python_image}",
            "",
            "WORKDIR /app",
            "ENV PYTHONUNBUFFERED=1",
            "ENV TZ=Asia/Taipei",
            "",
            "RUN python -m pip install --upgrade pip",
            "COPY requirements.txt ./",
            "RUN pip install --no-cache-dir -r requirements.txt",
            "COPY . .",
            "",
        ]
    )


def build_daily_radar_docker_compose(
    *,
    project_dir: str | Path,
    dockerfile: str | Path = f"deploy/docker/{DEFAULT_DOCKERFILE_NAME}",
    service_name: str = DEFAULT_SERVICE_NAME,
    image: str = DEFAULT_IMAGE,
    env_file: str | Path | None = None,
    container_workdir: str = "/app",
    hour: int = 8,
    minute: int = 45,
    trade_date_mode: str = "T",
    target_count: int = 15,
    risk_profile: str = "balanced",
    threshold_offset: float = 0.0,
    universe: str = "curated_watchlist",
    symbols: str = "",
    data_freshness_required: bool = True,
    index_constituents_url: str = "",
    index_constituents_key: str = "",
    index_constituents_source: str = "official_url",
    notify: str = "none",
    radar_notify_style: str = "premarket",
    enable_trigger_monitor: bool = False,
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
    trigger_monitor_notification_state_path: str = "data/cache/daily_radar_trigger_monitor_state.json",
    trigger_monitor_notification_cooldown_minutes: int = 240,
    trigger_monitor_force_notify: bool = False,
    restart: str = "unless-stopped",
    include_healthcheck: bool = True,
    health_max_age_days: int = 3,
    health_warn_age_days: int = 1,
    health_min_rows: int = 5,
    health_min_entry_ready: int = 1,
    health_interval: str = "6h",
    health_timeout: str = "60s",
    health_retries: int = 1,
    health_start_period: str = "2h",
    health_use_ops_readiness: bool = False,
    health_ops_target_phase: str = "paper",
    health_include_intraday_quote_review: bool = False,
    health_intraday_quote_review_max_age_seconds: int = 3600,
    health_intraday_quote_review_min_heartbeat_count: int = 1,
    health_intraday_quote_review_min_quote_coverage: float = 0.8,
    enable_intraday_quote_service: bool = False,
    intraday_quote_service_name: str = DEFAULT_INTRADAY_QUOTE_SERVICE_NAME,
    intraday_quote_symbols: str = "",
    intraday_quote_state_path: str = "data/cache/intraday_quote_service_status.json",
    intraday_quote_heartbeat_interval_seconds: float = 5.0,
    intraday_quote_shioaji_simulation: bool = True,
    intraday_quote_include_bidask: bool = True,
    intraday_quote_allow_outside_session: bool = False,
    intraday_quote_health_max_age_seconds: int = 30,
    intraday_quote_health_interval: str = "30s",
    intraday_quote_health_timeout: str = "10s",
    intraday_quote_health_retries: int = 2,
    intraday_quote_health_start_period: str = "30s",
) -> str:
    root = Path(project_dir).expanduser().absolute()
    resolved_env = env_file if env_file is not None else (root / ".env")
    command = daily_radar_scheduler_command(
        trade_date_mode=trade_date_mode,
        target_count=target_count,
        risk_profile=risk_profile,
        threshold_offset=threshold_offset,
        universe=universe,
        symbols=symbols,
        data_freshness_required=data_freshness_required,
        notify=notify,
        radar_notify_style=radar_notify_style,
        enable_trigger_monitor=enable_trigger_monitor,
        trigger_monitor_hour=trigger_monitor_hour,
        trigger_monitor_minute=trigger_monitor_minute,
        trigger_monitor_count=trigger_monitor_count,
        trigger_monitor_limit=trigger_monitor_limit,
        trigger_monitor_near_entry_gap=trigger_monitor_near_entry_gap,
        trigger_monitor_near_price_gap_pct=trigger_monitor_near_price_gap_pct,
        trigger_monitor_notify_on=trigger_monitor_notify_on,
        trigger_monitor_price_source=trigger_monitor_price_source,
        trigger_monitor_shioaji_simulation=trigger_monitor_shioaji_simulation,
        trigger_monitor_max_price_age_seconds=trigger_monitor_max_price_age_seconds,
        trigger_monitor_streaming_seconds=trigger_monitor_streaming_seconds,
        trigger_monitor_notification_state_path=trigger_monitor_notification_state_path,
        trigger_monitor_notification_cooldown_minutes=trigger_monitor_notification_cooldown_minutes,
        trigger_monitor_force_notify=trigger_monitor_force_notify,
        index_constituents_url=index_constituents_url,
        index_constituents_key=index_constituents_key,
        index_constituents_source=index_constituents_source,
    )
    command.extend(["--radar-hour", str(max(0, min(23, int(hour))))])
    command.extend(["--radar-minute", str(max(0, min(59, int(minute))))])
    if health_use_ops_readiness:
        health_command = ops_readiness_command(
            target_phase=health_ops_target_phase,
            max_age_days=health_max_age_days,
            warn_age_days=health_warn_age_days,
            min_rows=health_min_rows,
            min_entry_ready=health_min_entry_ready,
            include_intraday_quote_review=health_include_intraday_quote_review,
            intraday_quote_state_path=intraday_quote_state_path,
            intraday_quote_review_max_age_seconds=health_intraday_quote_review_max_age_seconds,
            intraday_quote_review_min_heartbeat_count=health_intraday_quote_review_min_heartbeat_count,
            intraday_quote_review_min_quote_coverage=health_intraday_quote_review_min_quote_coverage,
            fail_on_danger=True,
        )
    else:
        health_command = daily_radar_health_command(
            max_age_days=health_max_age_days,
            warn_age_days=health_warn_age_days,
            min_rows=health_min_rows,
            min_entry_ready=health_min_entry_ready,
            fail_on_danger=True,
        )

    dockerfile_path = Path(dockerfile)
    if dockerfile_path.is_absolute():
        dockerfile_value = str(dockerfile_path)
    else:
        dockerfile_value = dockerfile_path.as_posix()

    lines = [
        "services:",
        f"  {service_name}:",
        "    build:",
        f"      context: {_yaml_quote(root)}",
        f"      dockerfile: {_yaml_quote(dockerfile_value)}",
        f"    image: {_yaml_quote(image)}",
        f"    container_name: {_yaml_quote(service_name)}",
        f"    working_dir: {_yaml_quote(container_workdir)}",
        "    env_file:",
        f"      - {_yaml_quote(_abs_path(resolved_env, base=root))}",
        "    environment:",
        "      TZ: Asia/Taipei",
        '      PYTHONUNBUFFERED: "1"',
        "    volumes:",
        f"      - {_yaml_quote(root / 'data')}:{container_workdir}/data",
        f"      - {_yaml_quote(root / 'reports')}:{container_workdir}/reports",
        f"      - {_yaml_quote(root / 'logs')}:{container_workdir}/logs",
        "    command:",
    ]
    lines.extend([f"      - {_yaml_quote(item)}" for item in command])
    if include_healthcheck:
        lines.append("    healthcheck:")
        lines.append("      test:")
        lines.append("        - CMD")
        lines.extend([f"        - {_yaml_quote(item)}" for item in health_command])
        lines.extend(
            [
                f"      interval: {_yaml_quote(str(health_interval))}",
                f"      timeout: {_yaml_quote(str(health_timeout))}",
                f"      retries: {max(1, int(health_retries))}",
                f"      start_period: {_yaml_quote(str(health_start_period))}",
            ]
        )
    lines.extend(
        [
            f"    restart: {_yaml_quote(restart)}",
            "",
        ]
    )
    if enable_intraday_quote_service:
        quote_command = intraday_quote_service_command(
            symbols=intraday_quote_symbols,
            state_path=intraday_quote_state_path,
            heartbeat_interval_seconds=intraday_quote_heartbeat_interval_seconds,
            shioaji_simulation=intraday_quote_shioaji_simulation,
            include_bidask=intraday_quote_include_bidask,
            allow_outside_session=intraday_quote_allow_outside_session,
        )
        quote_health = intraday_quote_health_command(
            state_path=intraday_quote_state_path,
            max_age_seconds=intraday_quote_health_max_age_seconds,
            fail_on_warn=True,
            fail_on_danger=True,
        )
        lines.extend(
            [
                f"  {intraday_quote_service_name}:",
                f"    image: {_yaml_quote(image)}",
                f"    container_name: {_yaml_quote(intraday_quote_service_name)}",
                f"    working_dir: {_yaml_quote(container_workdir)}",
                "    env_file:",
                f"      - {_yaml_quote(_abs_path(resolved_env, base=root))}",
                "    environment:",
                "      TZ: Asia/Taipei",
                '      PYTHONUNBUFFERED: "1"',
                "    volumes:",
                f"      - {_yaml_quote(root / 'data')}:{container_workdir}/data",
                f"      - {_yaml_quote(root / 'reports')}:{container_workdir}/reports",
                f"      - {_yaml_quote(root / 'logs')}:{container_workdir}/logs",
                "    command:",
            ]
        )
        lines.extend([f"      - {_yaml_quote(item)}" for item in quote_command])
        lines.append("    healthcheck:")
        lines.append("      test:")
        lines.append("        - CMD")
        lines.extend([f"        - {_yaml_quote(item)}" for item in quote_health])
        lines.extend(
            [
                f"      interval: {_yaml_quote(str(intraday_quote_health_interval))}",
                f"      timeout: {_yaml_quote(str(intraday_quote_health_timeout))}",
                f"      retries: {max(1, int(intraday_quote_health_retries))}",
                f"      start_period: {_yaml_quote(str(intraday_quote_health_start_period))}",
                f"    restart: {_yaml_quote(restart)}",
                "",
            ]
        )
    return "\n".join(lines)


def write_daily_radar_docker_assets(
    output_dir: str | Path,
    *,
    project_dir: str | Path,
    compose_name: str = DEFAULT_COMPOSE_NAME,
    dockerfile_name: str = DEFAULT_DOCKERFILE_NAME,
    python_image: str = "python:3.11-slim",
    **kwargs,
) -> dict[str, Path]:
    root = Path(project_dir).expanduser().absolute()
    output = Path(output_dir).expanduser()
    if not output.is_absolute():
        output = root / output
    output.mkdir(parents=True, exist_ok=True)

    compose_path = output / compose_name
    dockerfile_path = output / dockerfile_name
    try:
        dockerfile_ref = dockerfile_path.relative_to(root)
    except ValueError:
        dockerfile_ref = dockerfile_path
    compose_path.write_text(
        build_daily_radar_docker_compose(
            project_dir=root,
            dockerfile=dockerfile_ref,
            **kwargs,
        ),
        encoding="utf-8",
    )
    dockerfile_path.write_text(build_daily_radar_dockerfile(python_image=python_image), encoding="utf-8")
    return {"compose": compose_path, "dockerfile": dockerfile_path}


def docker_compose_command(compose_path: str | Path, *args: str) -> str:
    return shlex.join(["docker", "compose", "-f", str(compose_path), *args])
