from __future__ import annotations

from pathlib import Path

from app.scheduler.systemd import (
    build_daily_radar_health_systemd_service,
    build_daily_radar_health_systemd_timer,
    build_daily_radar_systemd_service,
    build_daily_radar_systemd_timer,
    build_daily_radar_trigger_monitor_systemd_service,
    build_daily_radar_trigger_monitor_systemd_timer,
    build_intraday_quote_health_systemd_service,
    build_intraday_quote_health_systemd_timer,
    build_intraday_quote_systemd_service,
    ops_readiness_program_arguments,
    write_daily_radar_systemd_units,
)


def test_build_daily_radar_systemd_service_contains_daily_radar_command(tmp_path):
    python_path = tmp_path / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("FINMIND_API_KEY=test\n", encoding="utf-8")

    text = build_daily_radar_systemd_service(
        project_dir=tmp_path,
        python_path=python_path,
        env_file=env_file,
        user="quant",
        group="quant",
        trade_date_mode="T-1",
        target_count=12,
        risk_profile="conservative",
        threshold_offset=2.0,
        universe="custom",
        symbols="2330,2454",
        data_freshness_required=False,
        notify="line",
        notify_style="postmarket",
        index_constituents_url="https://backend.taiwanindex.com.tw/files/tip.xlsx",
        index_constituents_key="tip_tw50",
        index_constituents_source="tip_url",
    )

    assert "Type=oneshot" in text
    assert f"WorkingDirectory={tmp_path}" in text
    assert f"EnvironmentFile=-{env_file}" in text
    assert "ExecStart=" in text
    assert "run-daily-radar-job" in text
    assert "--trade-date-mode T-1" in text
    assert "--target-count 12" in text
    assert "--threshold-offset 2.0" in text
    assert "--symbols 2330,2454" in text
    assert "--notify line" in text
    assert "--notify-style postmarket" in text
    assert "--index-constituents-url https://backend.taiwanindex.com.tw/files/tip.xlsx" in text
    assert "--index-constituents-key tip_tw50" in text
    assert "--index-constituents-source tip_url" in text
    assert "--no-data-freshness-required" in text
    assert "User=quant" in text
    assert "Group=quant" in text


def test_build_daily_radar_systemd_timer_targets_weekday_calendar():
    text = build_daily_radar_systemd_timer(
        service_name="quant-trade-daily-radar.service",
        hour=8,
        minute=45,
    )

    assert "OnCalendar=Mon..Fri *-*-* 08:45:00" in text
    assert "Persistent=true" in text
    assert "Unit=quant-trade-daily-radar.service" in text


def test_build_daily_radar_health_systemd_service_contains_alert_command(tmp_path):
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    text = build_daily_radar_health_systemd_service(
        project_dir=tmp_path,
        python_path=python_path,
        notify="telegram",
        notify_on="warn",
        max_age_days=4,
        min_rows=8,
        fail_on_notify_error=True,
    )

    assert "Type=oneshot" in text
    assert "daily-radar-health" in text
    assert "--max-age-days 4" in text
    assert "--min-rows 8" in text
    assert "--notify telegram" in text
    assert "--notify-on warn" in text
    assert "--fail-on-danger" in text
    assert "--fail-on-notify-error" in text


def test_build_daily_radar_health_systemd_service_can_use_ops_readiness_with_quote_review(tmp_path):
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    text = build_daily_radar_health_systemd_service(
        project_dir=tmp_path,
        python_path=python_path,
        use_ops_readiness=True,
        ops_target_phase="small_live",
        include_intraday_quote_review=True,
        intraday_quote_state_path="data/cache/quote-state.json",
        intraday_quote_review_max_age_seconds=7200,
        intraday_quote_review_min_heartbeat_count=3,
        intraday_quote_review_min_quote_coverage=0.6,
    )

    assert "ops-readiness" in text
    assert "--target-phase small_live" in text
    assert "--include-intraday-quote-review" in text
    assert "--intraday-quote-state-path data/cache/quote-state.json" in text
    assert "--intraday-quote-review-max-age-seconds 7200" in text
    assert "--intraday-quote-review-min-heartbeat-count 3" in text
    assert "--intraday-quote-review-min-quote-coverage 0.6" in text
    assert "daily-radar-health" not in text


def test_ops_readiness_systemd_arguments_include_quote_review(tmp_path):
    args = ops_readiness_program_arguments(
        python_path=tmp_path / "python",
        target_phase="paper",
        include_intraday_quote_review=True,
        intraday_quote_state_path="data/cache/quote.json",
    )

    assert args[:4] == [str((tmp_path / "python").absolute()), "-m", "app.cli", "ops-readiness"]
    assert "--include-intraday-quote-review" in args
    assert args[args.index("--intraday-quote-state-path") + 1] == "data/cache/quote.json"


def test_build_daily_radar_health_systemd_timer_targets_health_service():
    text = build_daily_radar_health_systemd_timer(
        service_name="quant-trade-daily-radar-health.service",
        hour=9,
        minute=30,
    )

    assert "Description=Check Quant-Trade Daily Radar health on weekdays" in text
    assert "OnCalendar=Mon..Fri *-*-* 09:30:00" in text
    assert "Unit=quant-trade-daily-radar-health.service" in text


def test_build_daily_radar_trigger_monitor_systemd_service_contains_alert_command(tmp_path):
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    text = build_daily_radar_trigger_monitor_systemd_service(
        project_dir=tmp_path,
        python_path=python_path,
        notify="telegram",
        notify_on="near",
        notification_state_path="data/cache/custom-trigger-state.json",
        notification_cooldown_minutes=120,
        force_notify=True,
        count=60,
        limit=8,
        near_entry_gap=1.5,
        near_price_gap_pct=0.015,
        price_source="shioaji_streaming",
        shioaji_simulation=False,
        max_price_age_seconds=45,
        streaming_seconds=0.25,
        fail_on_ready=True,
        fail_on_notify_error=True,
    )

    assert "Type=oneshot" in text
    assert "daily-radar-trigger-monitor" in text
    assert "--count 60" in text
    assert "--limit 8" in text
    assert "--near-entry-gap 1.5" in text
    assert "--near-price-gap-pct 0.015" in text
    assert "--price-source shioaji_streaming" in text
    assert "--shioaji-live" in text
    assert "--max-price-age-seconds 45" in text
    assert "--streaming-seconds 0.25" in text
    assert "--notify telegram" in text
    assert "--notify-on near" in text
    assert "--notification-state-path data/cache/custom-trigger-state.json" in text
    assert "--notification-cooldown-minutes 120" in text
    assert "--force-notify" in text
    assert "--fail-on-ready" in text
    assert "--fail-on-notify-error" in text


def test_build_daily_radar_trigger_monitor_systemd_timer_targets_trigger_service():
    text = build_daily_radar_trigger_monitor_systemd_timer(
        service_name="quant-trade-daily-radar-trigger-monitor.service",
        hour=9,
        minute=20,
    )

    assert "Description=Run Quant-Trade Daily Radar trigger monitor on weekdays" in text
    assert "OnCalendar=Mon..Fri *-*-* 09:20:00" in text
    assert "Unit=quant-trade-daily-radar-trigger-monitor.service" in text


def test_build_intraday_quote_systemd_service_contains_long_running_command(tmp_path):
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    text = build_intraday_quote_systemd_service(
        project_dir=tmp_path,
        python_path=python_path,
        symbols="2330,2317",
        state_path="data/cache/quote-state.json",
        heartbeat_interval_seconds=2.5,
        shioaji_simulation=False,
        include_bidask=False,
        allow_outside_session=True,
        restart="on-failure",
        restart_sec=7,
    )

    assert "Type=simple" in text
    assert "run-intraday-quote-service" in text
    assert "--symbols 2330,2317" in text
    assert "--state-path data/cache/quote-state.json" in text
    assert "--heartbeat-interval-seconds 2.5" in text
    assert "--shioaji-live" in text
    assert "--no-bidask" in text
    assert "--allow-outside-session" in text
    assert "Restart=on-failure" in text
    assert "RestartSec=7" in text
    assert "WantedBy=multi-user.target" in text


def test_build_intraday_quote_health_systemd_units_check_snapshot(tmp_path):
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    service = build_intraday_quote_health_systemd_service(
        project_dir=tmp_path,
        python_path=python_path,
        state_path="data/cache/quote-state.json",
        max_age_seconds=20,
        fail_on_warn=True,
    )
    timer = build_intraday_quote_health_systemd_timer(
        service_name="quant-trade-intraday-quote-health.service",
        interval_seconds=20,
    )

    assert "intraday-quote-service-status" in service
    assert "--state-path data/cache/quote-state.json" in service
    assert "--max-age-seconds 20" in service
    assert "--fail-on-warn" in service
    assert "OnUnitActiveSec=20s" in timer
    assert "Unit=quant-trade-intraday-quote-health.service" in timer


def test_write_daily_radar_systemd_units_writes_service_and_timer(tmp_path):
    paths = write_daily_radar_systemd_units(
        "deploy/systemd",
        project_dir=tmp_path,
        service_name="example-radar.service",
        timer_name="example-radar.timer",
        hour=9,
        minute=5,
    )

    assert paths["service"] == tmp_path / "deploy" / "systemd" / "example-radar.service"
    assert paths["timer"] == tmp_path / "deploy" / "systemd" / "example-radar.timer"
    assert "ExecStart=" in paths["service"].read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 09:05:00" in paths["timer"].read_text(encoding="utf-8")


def test_write_daily_radar_systemd_units_can_include_health_monitor(tmp_path):
    paths = write_daily_radar_systemd_units(
        "deploy/systemd",
        project_dir=tmp_path,
        service_name="example-radar.service",
        timer_name="example-radar.timer",
        include_health_monitor=True,
        health_service_name="example-radar-health.service",
        health_timer_name="example-radar-health.timer",
        health_hour=10,
        health_minute=15,
        health_notify="line",
    )

    assert paths["health_service"] == tmp_path / "deploy" / "systemd" / "example-radar-health.service"
    assert paths["health_timer"] == tmp_path / "deploy" / "systemd" / "example-radar-health.timer"
    assert "daily-radar-health" in paths["health_service"].read_text(encoding="utf-8")
    assert "--notify line" in paths["health_service"].read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 10:15:00" in paths["health_timer"].read_text(encoding="utf-8")


def test_write_daily_radar_systemd_units_can_include_trigger_monitor(tmp_path):
    paths = write_daily_radar_systemd_units(
        "deploy/systemd",
        project_dir=tmp_path,
        service_name="example-radar.service",
        timer_name="example-radar.timer",
        include_trigger_monitor=True,
        trigger_monitor_service_name="example-radar-trigger.service",
        trigger_monitor_timer_name="example-radar-trigger.timer",
        trigger_monitor_hour=9,
        trigger_monitor_minute=20,
        trigger_monitor_notify="telegram",
        trigger_monitor_notify_on="near",
        trigger_monitor_price_source="shioaji_streaming",
        trigger_monitor_shioaji_simulation=False,
        trigger_monitor_max_price_age_seconds=45,
        trigger_monitor_streaming_seconds=0.25,
        trigger_monitor_notification_state_path="data/cache/custom-trigger-state.json",
        trigger_monitor_notification_cooldown_minutes=120,
        trigger_monitor_force_notify=True,
    )

    assert paths["trigger_monitor_service"] == tmp_path / "deploy" / "systemd" / "example-radar-trigger.service"
    assert paths["trigger_monitor_timer"] == tmp_path / "deploy" / "systemd" / "example-radar-trigger.timer"
    assert "daily-radar-trigger-monitor" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--notify telegram" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--notify-on near" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--price-source shioaji_streaming" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--shioaji-live" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--max-price-age-seconds 45" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--streaming-seconds 0.25" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--notification-state-path data/cache/custom-trigger-state.json" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--notification-cooldown-minutes 120" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "--force-notify" in paths["trigger_monitor_service"].read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 09:20:00" in paths["trigger_monitor_timer"].read_text(encoding="utf-8")


def test_write_daily_radar_systemd_units_can_include_intraday_quote_service(tmp_path):
    paths = write_daily_radar_systemd_units(
        "deploy/systemd",
        project_dir=tmp_path,
        service_name="example-radar.service",
        timer_name="example-radar.timer",
        include_intraday_quote_service=True,
        intraday_quote_service_name="example-quote.service",
        intraday_quote_health_service_name="example-quote-health.service",
        intraday_quote_health_timer_name="example-quote-health.timer",
        intraday_quote_symbols="2330,2317",
        intraday_quote_state_path="data/cache/quote-state.json",
        intraday_quote_heartbeat_interval_seconds=2,
        intraday_quote_shioaji_simulation=False,
        intraday_quote_include_bidask=False,
        intraday_quote_allow_outside_session=True,
        intraday_quote_health_max_age_seconds=20,
        intraday_quote_health_interval_seconds=20,
    )

    assert paths["intraday_quote_service"] == tmp_path / "deploy" / "systemd" / "example-quote.service"
    assert paths["intraday_quote_health_service"] == tmp_path / "deploy" / "systemd" / "example-quote-health.service"
    assert paths["intraday_quote_health_timer"] == tmp_path / "deploy" / "systemd" / "example-quote-health.timer"
    quote_service = paths["intraday_quote_service"].read_text(encoding="utf-8")
    quote_health = paths["intraday_quote_health_service"].read_text(encoding="utf-8")
    quote_timer = paths["intraday_quote_health_timer"].read_text(encoding="utf-8")
    assert "run-intraday-quote-service" in quote_service
    assert "--symbols 2330,2317" in quote_service
    assert "--shioaji-live" in quote_service
    assert "--no-bidask" in quote_service
    assert "--allow-outside-session" in quote_service
    assert "intraday-quote-service-status" in quote_health
    assert "--max-age-seconds 20" in quote_health
    assert "OnUnitActiveSec=20s" in quote_timer


def test_write_daily_radar_systemd_units_can_use_ops_readiness_health_with_quote_review(tmp_path):
    paths = write_daily_radar_systemd_units(
        "deploy/systemd",
        project_dir=tmp_path,
        include_health_monitor=True,
        health_use_ops_readiness=True,
        health_ops_target_phase="small_live",
        health_include_intraday_quote_review=True,
        intraday_quote_state_path="data/cache/quote-state.json",
        health_intraday_quote_review_max_age_seconds=7200,
        health_intraday_quote_review_min_heartbeat_count=3,
        health_intraday_quote_review_min_quote_coverage=0.6,
    )

    health_service = paths["health_service"].read_text(encoding="utf-8")
    assert "ops-readiness" in health_service
    assert "--target-phase small_live" in health_service
    assert "--include-intraday-quote-review" in health_service
    assert "--intraday-quote-state-path data/cache/quote-state.json" in health_service
    assert "--intraday-quote-review-max-age-seconds 7200" in health_service
    assert "--intraday-quote-review-min-heartbeat-count 3" in health_service
    assert "--intraday-quote-review-min-quote-coverage 0.6" in health_service


def test_systemd_deploy_readme_documents_quote_review_runbook():
    text = (Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "README.md").read_text(encoding="utf-8")

    assert "--health-use-ops-readiness" in text
    assert "--health-include-intraday-quote-review" in text
    assert "intraday-quote-service-review" in text
    assert "ops-readiness" in text
    assert "盤中行情服務重啟 runbook" in text
    assert "隔日觸發監控先保守" in text
