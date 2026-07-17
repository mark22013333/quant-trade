from __future__ import annotations

import plistlib

from app.scheduler.launchd import (
    build_daily_radar_launchd_plist,
    build_daily_radar_trigger_monitor_launchd_plist,
    write_daily_radar_launchd_plist,
    write_daily_radar_trigger_monitor_launchd_plist,
)


def test_build_daily_radar_launchd_plist_uses_structured_program_arguments(tmp_path):
    python_path = tmp_path / "python"
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    payload = build_daily_radar_launchd_plist(
        project_dir=tmp_path,
        python_path=python_path,
        label="com.example.quant-radar",
        hour=8,
        minute=35,
        trade_date_mode="T-1",
        target_count=12,
        risk_profile="conservative",
        threshold_offset=2.0,
        universe="custom",
        symbols="2330,2454",
        data_freshness_required=False,
        notify="telegram",
        notify_style="postmarket",
        index_constituents_url="https://backend.taiwanindex.com.tw/files/tip.xlsx",
        index_constituents_key="tip_tw50",
        index_constituents_source="tip_url",
        run_at_load=True,
    )

    assert payload["Label"] == "com.example.quant-radar"
    assert payload["WorkingDirectory"] == str(tmp_path.resolve())
    assert payload["RunAtLoad"] is True
    assert payload["StartCalendarInterval"] == [
        {"Weekday": 1, "Hour": 8, "Minute": 35},
        {"Weekday": 2, "Hour": 8, "Minute": 35},
        {"Weekday": 3, "Hour": 8, "Minute": 35},
        {"Weekday": 4, "Hour": 8, "Minute": 35},
        {"Weekday": 5, "Hour": 8, "Minute": 35},
    ]
    args = payload["ProgramArguments"]
    assert args[:4] == [str(python_path.resolve()), "-m", "app.cli", "run-daily-radar-job"]
    assert "--symbols" in args
    assert "2330,2454" in args
    assert "--threshold-offset" in args
    assert "2.0" in args
    assert "--notify" in args
    assert "telegram" in args
    assert "--notify-style" in args
    assert "postmarket" in args
    assert "--index-constituents-url" in args
    assert "https://backend.taiwanindex.com.tw/files/tip.xlsx" in args
    assert "--index-constituents-key" in args
    assert "tip_tw50" in args
    assert "--index-constituents-source" in args
    assert "tip_url" in args
    assert "--no-data-freshness-required" in args


def test_write_daily_radar_launchd_plist_roundtrips_with_plistlib(tmp_path):
    output = write_daily_radar_launchd_plist(
        "deploy/launchd/com.example.radar.plist",
        project_dir=tmp_path,
        label="com.example.radar",
        hour=9,
        minute=5,
    )

    assert output == tmp_path / "deploy" / "launchd" / "com.example.radar.plist"
    payload = plistlib.loads(output.read_bytes())
    assert payload["Label"] == "com.example.radar"
    assert payload["StartCalendarInterval"][0] == {"Weekday": 1, "Hour": 9, "Minute": 5}
    assert payload["StandardOutPath"].endswith("logs/daily-radar.launchd.out.log")


def test_build_daily_radar_trigger_monitor_launchd_plist_uses_structured_program_arguments(tmp_path):
    python_path = tmp_path / "python"
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    payload = build_daily_radar_trigger_monitor_launchd_plist(
        project_dir=tmp_path,
        python_path=python_path,
        label="com.example.quant-trigger",
        hour=9,
        minute=20,
        count=60,
        limit=8,
        near_entry_gap=1.5,
        near_price_gap_pct=0.015,
        price_source="shioaji_streaming",
        shioaji_simulation=False,
        max_price_age_seconds=45,
        streaming_seconds=0.25,
        notify="line",
        notify_on="near",
        notification_state_path="data/cache/custom-trigger-state.json",
        notification_cooldown_minutes=120,
        force_notify=True,
        fail_on_ready=True,
        fail_on_notify_error=True,
        run_at_load=True,
    )

    assert payload["Label"] == "com.example.quant-trigger"
    assert payload["WorkingDirectory"] == str(tmp_path.resolve())
    assert payload["RunAtLoad"] is True
    assert payload["StartCalendarInterval"][0] == {"Weekday": 1, "Hour": 9, "Minute": 20}
    args = payload["ProgramArguments"]
    assert args[:4] == [str(python_path.resolve()), "-m", "app.cli", "daily-radar-trigger-monitor"]
    assert "--count" in args
    assert "60" in args
    assert "--limit" in args
    assert "8" in args
    assert "--near-entry-gap" in args
    assert "1.5" in args
    assert "--near-price-gap-pct" in args
    assert "0.015" in args
    assert "--price-source" in args
    assert "shioaji_streaming" in args
    assert "--shioaji-live" in args
    assert "--max-price-age-seconds" in args
    assert "45" in args
    assert "--streaming-seconds" in args
    assert "0.25" in args
    assert "--notify" in args
    assert "line" in args
    assert "--notify-on" in args
    assert "near" in args
    assert "--notification-state-path" in args
    assert "data/cache/custom-trigger-state.json" in args
    assert "--notification-cooldown-minutes" in args
    assert "120" in args
    assert "--force-notify" in args
    assert "--fail-on-ready" in args
    assert "--fail-on-notify-error" in args
    assert payload["StandardOutPath"].endswith("logs/daily-radar-trigger-monitor.launchd.out.log")


def test_write_daily_radar_trigger_monitor_launchd_plist_roundtrips_with_plistlib(tmp_path):
    output = write_daily_radar_trigger_monitor_launchd_plist(
        "deploy/launchd/com.example.trigger.plist",
        project_dir=tmp_path,
        label="com.example.trigger",
        hour=10,
        minute=5,
        notify="telegram",
        notify_on="ready",
    )

    assert output == tmp_path / "deploy" / "launchd" / "com.example.trigger.plist"
    payload = plistlib.loads(output.read_bytes())
    assert payload["Label"] == "com.example.trigger"
    assert payload["StartCalendarInterval"][0] == {"Weekday": 1, "Hour": 10, "Minute": 5}
    assert "daily-radar-trigger-monitor" in payload["ProgramArguments"]


def test_cli_parser_exposes_trigger_monitor_launchd_command():
    from app.cli import build_parser, cmd_write_trigger_monitor_launchd_plist

    args = build_parser().parse_args(
        [
            "write-trigger-monitor-launchd-plist",
            "--hour",
            "9",
            "--minute",
            "20",
            "--notify",
            "telegram",
            "--notify-on",
            "near",
            "--price-source",
            "shioaji_streaming",
            "--shioaji-live",
            "--max-price-age-seconds",
            "45",
            "--streaming-seconds",
            "0.25",
            "--notification-state-path",
            "data/cache/custom-trigger-state.json",
            "--notification-cooldown-minutes",
            "120",
            "--force-notify",
        ]
    )

    assert args.func is cmd_write_trigger_monitor_launchd_plist
    assert args.hour == 9
    assert args.minute == 20
    assert args.notify == "telegram"
    assert args.notify_on == "near"
    assert args.price_source == "shioaji_streaming"
    assert args.shioaji_live is True
    assert args.max_price_age_seconds == 45
    assert args.streaming_seconds == 0.25
    assert args.notification_state_path == "data/cache/custom-trigger-state.json"
    assert args.notification_cooldown_minutes == 120
    assert args.force_notify is True
