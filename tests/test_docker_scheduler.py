from __future__ import annotations

from pathlib import Path

from app.scheduler.docker import (
    build_daily_radar_docker_compose,
    build_daily_radar_dockerfile,
    daily_radar_health_command,
    daily_radar_scheduler_command,
    docker_compose_command,
    intraday_quote_health_command,
    intraday_quote_service_command,
    ops_readiness_command,
    write_daily_radar_docker_assets,
)


def test_cli_parser_exposes_write_docker_compose_command():
    from app.cli import build_parser, cmd_write_docker_compose

    args = build_parser().parse_args(
        [
            "write-docker-compose",
            "--project-dir",
            "/opt/quant-trade",
            "--hour",
            "9",
            "--minute",
            "5",
            "--universe",
            "index:tip_tw50",
            "--notify-style",
            "postmarket",
            "--index-constituents-url",
            "https://backend.taiwanindex.com.tw/files/tip.xlsx",
            "--health-min-rows",
            "8",
            "--health-use-ops-readiness",
            "--health-ops-target-phase",
            "small_live",
            "--health-include-intraday-quote-review",
            "--health-intraday-quote-review-max-age-seconds",
            "7200",
            "--health-intraday-quote-review-min-heartbeat-count",
            "3",
            "--health-intraday-quote-review-min-quote-coverage",
            "0.6",
            "--health-interval",
            "4h",
            "--enable-trigger-monitor",
            "--trigger-monitor-hour",
            "9",
            "--trigger-monitor-minute",
            "20",
            "--trigger-monitor-notify-on",
            "near",
            "--trigger-monitor-price-source",
            "shioaji_streaming",
            "--trigger-monitor-shioaji-live",
            "--trigger-monitor-max-price-age-seconds",
            "45",
            "--trigger-monitor-streaming-seconds",
            "0.25",
            "--trigger-monitor-notification-state-path",
            "data/cache/custom-trigger-state.json",
            "--trigger-monitor-notification-cooldown-minutes",
            "120",
            "--trigger-monitor-force-notify",
            "--enable-intraday-quote-service",
            "--intraday-quote-service-name",
            "quote-feed",
            "--intraday-quote-symbols",
            "2330,2317",
            "--intraday-quote-shioaji-live",
            "--intraday-quote-no-bidask",
            "--intraday-quote-allow-outside-session",
            "--intraday-quote-health-max-age-seconds",
            "20",
        ]
    )

    assert args.func is cmd_write_docker_compose
    assert args.project_dir == "/opt/quant-trade"
    assert args.hour == 9
    assert args.minute == 5
    assert args.universe == "index:tip_tw50"
    assert args.notify_style == "postmarket"
    assert args.index_constituents_url == "https://backend.taiwanindex.com.tw/files/tip.xlsx"
    assert args.health_min_rows == 8
    assert args.health_use_ops_readiness is True
    assert args.health_ops_target_phase == "small_live"
    assert args.health_include_intraday_quote_review is True
    assert args.health_intraday_quote_review_max_age_seconds == 7200
    assert args.health_intraday_quote_review_min_heartbeat_count == 3
    assert args.health_intraday_quote_review_min_quote_coverage == 0.6
    assert args.health_interval == "4h"
    assert args.enable_trigger_monitor is True
    assert args.trigger_monitor_hour == 9
    assert args.trigger_monitor_minute == 20
    assert args.trigger_monitor_notify_on == "near"
    assert args.trigger_monitor_price_source == "shioaji_streaming"
    assert args.trigger_monitor_shioaji_live is True
    assert args.trigger_monitor_max_price_age_seconds == 45
    assert args.trigger_monitor_streaming_seconds == 0.25
    assert args.trigger_monitor_notification_state_path == "data/cache/custom-trigger-state.json"
    assert args.trigger_monitor_notification_cooldown_minutes == 120
    assert args.trigger_monitor_force_notify is True
    assert args.enable_intraday_quote_service is True
    assert args.intraday_quote_service_name == "quote-feed"
    assert args.intraday_quote_symbols == "2330,2317"
    assert args.intraday_quote_shioaji_live is True
    assert args.intraday_quote_no_bidask is True
    assert args.intraday_quote_allow_outside_session is True
    assert args.intraday_quote_health_max_age_seconds == 20


def test_daily_radar_scheduler_command_runs_radar_only_scheduler():
    args = daily_radar_scheduler_command(
        trade_date_mode="T-1",
        target_count=12,
        risk_profile="conservative",
        threshold_offset=2.0,
        universe="custom",
        symbols="2330,2454",
        data_freshness_required=False,
        notify="telegram",
        radar_notify_style="intraday",
        index_constituents_url="https://backend.taiwanindex.com.tw/files/tip.xlsx",
        index_constituents_key="tip_tw50",
        index_constituents_source="tip_url",
    )

    assert args[:4] == ["python", "-m", "app.cli", "run-scheduler"]
    assert "--disable-signal-job" in args
    assert "--enable-daily-radar" in args
    assert "--radar-target-count" in args
    assert "12" in args
    assert "--radar-threshold-offset" in args
    assert "2.0" in args
    assert "--radar-symbols" in args
    assert "2330,2454" in args
    assert "--radar-index-constituents-url" in args
    assert "https://backend.taiwanindex.com.tw/files/tip.xlsx" in args
    assert "--radar-index-constituents-key" in args
    assert "tip_tw50" in args
    assert "--radar-index-constituents-source" in args
    assert "tip_url" in args
    assert "--no-radar-data-freshness-required" in args
    assert "--notify" in args
    assert "telegram" in args
    assert "--radar-notify-style" in args
    assert "intraday" in args


def test_daily_radar_scheduler_command_can_include_trigger_monitor():
    args = daily_radar_scheduler_command(
        enable_trigger_monitor=True,
        trigger_monitor_hour=9,
        trigger_monitor_minute=20,
        trigger_monitor_count=60,
        trigger_monitor_limit=8,
        trigger_monitor_near_entry_gap=1.5,
        trigger_monitor_near_price_gap_pct=0.015,
        trigger_monitor_notify_on="near",
        trigger_monitor_price_source="shioaji_streaming",
        trigger_monitor_shioaji_simulation=False,
        trigger_monitor_max_price_age_seconds=45,
        trigger_monitor_streaming_seconds=0.25,
        trigger_monitor_notification_state_path="data/cache/custom-trigger-state.json",
        trigger_monitor_notification_cooldown_minutes=120,
        trigger_monitor_force_notify=True,
    )

    assert "--enable-trigger-monitor" in args
    assert args[args.index("--trigger-monitor-hour") + 1] == "9"
    assert args[args.index("--trigger-monitor-minute") + 1] == "20"
    assert args[args.index("--trigger-monitor-count") + 1] == "60"
    assert args[args.index("--trigger-monitor-limit") + 1] == "8"
    assert args[args.index("--trigger-monitor-near-entry-gap") + 1] == "1.5"
    assert args[args.index("--trigger-monitor-near-price-gap-pct") + 1] == "0.015"
    assert args[args.index("--trigger-monitor-notify-on") + 1] == "near"
    assert args[args.index("--trigger-monitor-price-source") + 1] == "shioaji_streaming"
    assert "--trigger-monitor-shioaji-live" in args
    assert args[args.index("--trigger-monitor-max-price-age-seconds") + 1] == "45"
    assert args[args.index("--trigger-monitor-streaming-seconds") + 1] == "0.25"
    assert args[args.index("--trigger-monitor-notification-state-path") + 1] == "data/cache/custom-trigger-state.json"
    assert args[args.index("--trigger-monitor-notification-cooldown-minutes") + 1] == "120"
    assert "--trigger-monitor-force-notify" in args


def test_daily_radar_health_command_runs_health_check_with_failure_code():
    args = daily_radar_health_command(
        max_age_days=4,
        warn_age_days=2,
        min_rows=8,
        min_entry_ready=0,
        fail_on_danger=True,
    )

    assert args[:4] == ["python", "-m", "app.cli", "daily-radar-health"]
    assert "--max-age-days" in args
    assert "4" in args
    assert "--warn-age-days" in args
    assert "2" in args
    assert "--min-rows" in args
    assert "8" in args
    assert "--min-entry-ready" in args
    assert "0" in args
    assert "--fail-on-danger" in args


def test_ops_readiness_docker_command_can_include_quote_review():
    args = ops_readiness_command(
        target_phase="small_live",
        max_age_days=4,
        min_rows=8,
        include_intraday_quote_review=True,
        intraday_quote_state_path="data/cache/quote-state.json",
        intraday_quote_review_max_age_seconds=7200,
        intraday_quote_review_min_heartbeat_count=3,
        intraday_quote_review_min_quote_coverage=0.6,
    )

    assert args[:4] == ["python", "-m", "app.cli", "ops-readiness"]
    assert args[args.index("--target-phase") + 1] == "small_live"
    assert "--include-intraday-quote-review" in args
    assert args[args.index("--intraday-quote-state-path") + 1] == "data/cache/quote-state.json"
    assert args[args.index("--intraday-quote-review-max-age-seconds") + 1] == "7200"
    assert args[args.index("--intraday-quote-review-min-heartbeat-count") + 1] == "3"
    assert args[args.index("--intraday-quote-review-min-quote-coverage") + 1] == "0.6"
    assert "--fail-on-danger" in args


def test_intraday_quote_docker_commands_manage_long_running_service():
    service_args = intraday_quote_service_command(
        symbols="2330,2317",
        state_path="data/cache/quote-state.json",
        heartbeat_interval_seconds=2.5,
        shioaji_simulation=False,
        include_bidask=False,
        allow_outside_session=True,
    )
    health_args = intraday_quote_health_command(
        state_path="data/cache/quote-state.json",
        max_age_seconds=20,
        fail_on_warn=True,
    )

    assert service_args[:4] == ["python", "-m", "app.cli", "run-intraday-quote-service"]
    assert service_args[service_args.index("--symbols") + 1] == "2330,2317"
    assert service_args[service_args.index("--state-path") + 1] == "data/cache/quote-state.json"
    assert service_args[service_args.index("--heartbeat-interval-seconds") + 1] == "2.5"
    assert "--shioaji-live" in service_args
    assert "--no-bidask" in service_args
    assert "--allow-outside-session" in service_args
    assert health_args[:4] == ["python", "-m", "app.cli", "intraday-quote-service-status"]
    assert health_args[health_args.index("--max-age-seconds") + 1] == "20"
    assert "--fail-on-warn" in health_args


def test_build_daily_radar_docker_compose_contains_scheduler_service(tmp_path):
    text = build_daily_radar_docker_compose(
        project_dir=tmp_path,
        service_name="quant-radar",
        image="example/radar:latest",
        hour=9,
        minute=5,
        trade_date_mode="T-1",
        target_count=12,
        risk_profile="conservative",
        threshold_offset=2.0,
        universe="tw0050",
        enable_trigger_monitor=True,
        trigger_monitor_hour=9,
        trigger_monitor_minute=20,
        trigger_monitor_notify_on="near",
        trigger_monitor_price_source="shioaji_streaming",
        trigger_monitor_shioaji_simulation=False,
        trigger_monitor_max_price_age_seconds=45,
        trigger_monitor_streaming_seconds=0.25,
        trigger_monitor_notification_state_path="data/cache/custom-trigger-state.json",
        trigger_monitor_notification_cooldown_minutes=120,
    )

    assert "quant-radar:" in text
    assert 'image: "example/radar:latest"' in text
    assert "run-scheduler" in text
    assert "--disable-signal-job" in text
    assert "--enable-daily-radar" in text
    assert "--radar-hour" in text
    lines = text.splitlines()
    assert lines[lines.index('      - "--radar-hour"') + 1] == '      - "9"'
    assert "--radar-minute" in text
    assert lines[lines.index('      - "--radar-minute"') + 1] == '      - "5"'
    assert "--radar-threshold-offset" in text
    assert lines[lines.index('      - "--radar-threshold-offset"') + 1] == '      - "2.0"'
    assert "--enable-trigger-monitor" in text
    assert lines[lines.index('      - "--trigger-monitor-hour"') + 1] == '      - "9"'
    assert lines[lines.index('      - "--trigger-monitor-minute"') + 1] == '      - "20"'
    assert lines[lines.index('      - "--trigger-monitor-notify-on"') + 1] == '      - "near"'
    assert lines[lines.index('      - "--trigger-monitor-price-source"') + 1] == '      - "shioaji_streaming"'
    assert '      - "--trigger-monitor-shioaji-live"' in lines
    assert lines[lines.index('      - "--trigger-monitor-max-price-age-seconds"') + 1] == '      - "45"'
    assert lines[lines.index('      - "--trigger-monitor-streaming-seconds"') + 1] == '      - "0.25"'
    assert lines[lines.index('      - "--trigger-monitor-notification-state-path"') + 1] == '      - "data/cache/custom-trigger-state.json"'
    assert lines[lines.index('      - "--trigger-monitor-notification-cooldown-minutes"') + 1] == '      - "120"'
    assert "healthcheck:" in text
    assert "daily-radar-health" in text
    assert "--fail-on-danger" in text
    assert 'interval: "6h"' in text
    assert f'"{tmp_path}/data":/app/data' in text
    assert f'"{tmp_path}/reports":/app/reports' in text
    assert f'"{tmp_path}/logs":/app/logs' in text


def test_build_daily_radar_docker_compose_can_use_ops_readiness_healthcheck_with_quote_review(tmp_path):
    text = build_daily_radar_docker_compose(
        project_dir=tmp_path,
        service_name="quant-radar",
        image="example/radar:latest",
        health_use_ops_readiness=True,
        health_ops_target_phase="small_live",
        health_include_intraday_quote_review=True,
        health_intraday_quote_review_max_age_seconds=7200,
        health_intraday_quote_review_min_heartbeat_count=3,
        health_intraday_quote_review_min_quote_coverage=0.6,
        intraday_quote_state_path="data/cache/quote-state.json",
    )
    lines = text.splitlines()

    assert "ops-readiness" in text
    assert "daily-radar-health" not in text
    assert lines[lines.index('        - "--target-phase"') + 1] == '        - "small_live"'
    assert '        - "--include-intraday-quote-review"' in lines
    assert lines[lines.index('        - "--intraday-quote-state-path"') + 1] == '        - "data/cache/quote-state.json"'
    assert lines[lines.index('        - "--intraday-quote-review-max-age-seconds"') + 1] == '        - "7200"'
    assert lines[lines.index('        - "--intraday-quote-review-min-heartbeat-count"') + 1] == '        - "3"'
    assert lines[lines.index('        - "--intraday-quote-review-min-quote-coverage"') + 1] == '        - "0.6"'


def test_build_daily_radar_docker_compose_can_include_intraday_quote_service(tmp_path):
    text = build_daily_radar_docker_compose(
        project_dir=tmp_path,
        service_name="quant-radar",
        image="example/radar:latest",
        enable_intraday_quote_service=True,
        intraday_quote_service_name="quote-feed",
        intraday_quote_symbols="2330,2317",
        intraday_quote_state_path="data/cache/quote-state.json",
        intraday_quote_heartbeat_interval_seconds=2.5,
        intraday_quote_shioaji_simulation=False,
        intraday_quote_include_bidask=False,
        intraday_quote_allow_outside_session=True,
        intraday_quote_health_max_age_seconds=20,
        intraday_quote_health_interval="20s",
        intraday_quote_health_timeout="8s",
        intraday_quote_health_retries=3,
        intraday_quote_health_start_period="25s",
    )
    lines = text.splitlines()

    assert "quote-feed:" in text
    assert 'container_name: "quote-feed"' in text
    assert "run-intraday-quote-service" in text
    assert lines[lines.index('      - "--symbols"') + 1] == '      - "2330,2317"'
    assert lines[lines.index('      - "--state-path"') + 1] == '      - "data/cache/quote-state.json"'
    assert lines[lines.index('      - "--heartbeat-interval-seconds"') + 1] == '      - "2.5"'
    assert '      - "--shioaji-live"' in lines
    assert '      - "--no-bidask"' in lines
    assert '      - "--allow-outside-session"' in lines
    assert "intraday-quote-service-status" in text
    assert lines[lines.index('        - "--max-age-seconds"') + 1] == '        - "20"'
    assert '        - "--fail-on-warn"' in lines
    assert '      interval: "20s"' in text
    assert '      timeout: "8s"' in text
    assert "      retries: 3" in text
    assert '      start_period: "25s"' in text


def test_write_daily_radar_docker_assets_writes_compose_and_dockerfile(tmp_path):
    paths = write_daily_radar_docker_assets(
        "deploy/docker",
        project_dir=tmp_path,
        service_name="quant-radar",
        compose_name="compose.yml",
        dockerfile_name="Dockerfile.radar",
        python_image="python:3.12-slim",
    )

    assert paths["compose"] == tmp_path / "deploy" / "docker" / "compose.yml"
    assert paths["dockerfile"] == tmp_path / "deploy" / "docker" / "Dockerfile.radar"
    assert "run-scheduler" in paths["compose"].read_text(encoding="utf-8")
    assert "Dockerfile.radar" in paths["compose"].read_text(encoding="utf-8")
    dockerfile = paths["dockerfile"].read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in dockerfile
    assert "pip install --no-cache-dir -r requirements.txt" in dockerfile
    assert docker_compose_command(paths["compose"], "up", "-d").endswith("compose.yml up -d")


def test_build_daily_radar_dockerfile_installs_runtime_requirements():
    text = build_daily_radar_dockerfile(python_image="python:3.11-slim")

    assert "FROM python:3.11-slim" in text
    assert "COPY requirements.txt ./" in text
    assert "COPY . ." in text


def test_docker_deploy_readme_documents_quote_review_runbook():
    text = (Path(__file__).resolve().parents[1] / "deploy" / "docker" / "README.md").read_text(encoding="utf-8")

    assert "--health-use-ops-readiness" in text
    assert "--health-include-intraday-quote-review" in text
    assert "intraday-quote-service-review" in text
    assert "ops-readiness" in text
    assert "盤中行情服務重啟 runbook" in text
    assert "隔日觸發監控先保守" in text
