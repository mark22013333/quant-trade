from __future__ import annotations

from datetime import datetime, timedelta

from app.cli import build_parser, cmd_daily_radar_trigger_monitor
from app.reports.daily_radar_trigger_monitor import (
    DailyRadarTriggerMonitorService,
    daily_radar_trigger_monitor_notification_decision,
    record_daily_radar_trigger_monitor_notification_sent,
    daily_radar_trigger_monitor_should_notify,
    format_daily_radar_trigger_monitor_message,
)


class _Repo:
    def get_latest_daily_radar(self, count=50):  # noqa: ANN001
        assert count >= 4
        return [
            {
                "run_id": "radar_20260713",
                "run_date": "2026-07-13",
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 101.0,
                "entry_score": 75.0,
                "risk_score": 0.2,
                "risk_adjusted_score": 82.0,
                "data_quality": "fresh",
                "atr_heat": "normal",
                "trigger_plan": {
                    "target_entry_score": 74.0,
                    "breakout_price": 100.0,
                    "pullback_price": 95.0,
                    "trigger_readiness_score": 92.0,
                    "trigger_readiness_label": "接近觸發",
                    "atr_heat": "normal",
                },
            },
            {
                "run_id": "radar_20260713",
                "run_date": "2026-07-13",
                "rank": 2,
                "symbol": "2303",
                "name": "聯電",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 98.5,
                "entry_score": 73.0,
                "risk_score": 0.25,
                "risk_adjusted_score": 76.0,
                "data_quality": "fresh",
                "atr_heat": "elevated",
                "trigger_plan": {
                    "target_entry_score": 74.0,
                    "breakout_price": 100.0,
                    "pullback_price": 95.0,
                    "trigger_readiness_score": 68.0,
                    "trigger_readiness_label": "接近觸發",
                    "atr_heat": "elevated",
                },
            },
            {
                "run_id": "radar_20260713",
                "run_date": "2026-07-13",
                "rank": 3,
                "symbol": "2327",
                "name": "國巨",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 90.0,
                "entry_score": 68.0,
                "risk_score": 0.65,
                "risk_adjusted_score": 51.0,
                "data_quality": "fresh",
                "atr_heat": "hot",
                "trigger_plan": {
                    "target_entry_score": 75.0,
                    "breakout_price": 105.0,
                    "pullback_price": 85.0,
                    "trigger_readiness_score": 35.0,
                    "trigger_readiness_label": "還早",
                    "atr_heat": "hot",
                },
            },
            {
                "run_id": "radar_20260713",
                "run_date": "2026-07-13",
                "rank": 4,
                "symbol": "2881",
                "name": "富邦金",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 80.0,
                "entry_score": 75.0,
                "risk_score": 0.2,
                "risk_adjusted_score": 79.0,
                "data_quality": "fresh",
                "atr_heat": "normal",
                "blocker_tags": ["attention_stock"],
                "trigger_plan": {
                    "target_entry_score": 74.0,
                    "breakout_price": 79.0,
                    "pullback_price": 76.0,
                    "trigger_readiness_score": 91.0,
                    "trigger_readiness_label": "接近觸發",
                    "atr_heat": "normal",
                },
            },
        ]

    def get_daily_radar_allocation_plan(self, limit=12):  # noqa: ANN001
        assert limit >= 4
        return {
            "allocation_diagnostics": {
                "review_queue": {
                    "lanes": {
                        "trigger": {
                            "items": [
                                {
                                    "symbol": "2330",
                                    "monitor_only": True,
                                    "allocation_lane": "monitor_only",
                                    "trigger_watchlist_note": "2330 台積電 | 盯盤備選 | Entry>=74 | 突破 100",
                                }
                            ]
                        }
                    }
                }
            }
        }


class _PriceOverrideRepo:
    def get_latest_daily_radar(self, count=50):  # noqa: ANN001
        return [
            {
                "run_id": "radar_20260713",
                "run_date": "2026-07-13",
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 97.0,
                "entry_score": 75.0,
                "risk_score": 0.2,
                "risk_adjusted_score": 82.0,
                "data_quality": "fresh",
                "atr_heat": "normal",
                "trigger_plan": {
                    "target_entry_score": 74.0,
                    "breakout_price": 100.0,
                    "pullback_price": 95.0,
                    "trigger_readiness_score": 40.0,
                    "trigger_readiness_label": "等待",
                    "atr_heat": "normal",
                },
            }
        ]

    def get_daily_radar_allocation_plan(self, limit=12):  # noqa: ANN001
        return {}


def test_trigger_monitor_classifies_ready_near_watch_and_blocked() -> None:
    payload = DailyRadarTriggerMonitorService(count=10, limit=10, near_entry_gap=2.0, near_price_gap_pct=0.02).evaluate(_Repo())

    assert payload["status"] == "ready"
    assert payload["summary"] == {
        "total_count": 4,
        "ready_count": 1,
        "near_count": 1,
        "watch_count": 1,
        "blocked_count": 1,
    }
    by_symbol = {item["symbol"]: item for item in payload["items"]}
    assert by_symbol["2330"]["status"] == "ready"
    assert by_symbol["2330"]["allocation_status"] == "盯盤備選"
    assert by_symbol["2330"]["trigger_watchlist_note"].startswith("2330 台積電 | 盯盤備選")
    assert by_symbol["2303"]["status"] == "near"
    assert by_symbol["2327"]["status"] == "watch"
    assert by_symbol["2881"]["status"] == "blocked"
    assert any(condition["key"] == "price_breakout" and condition["status"] == "met" for condition in by_symbol["2330"]["conditions"])
    assert any(condition["key"] == "blockers" and condition["status"] == "blocked" for condition in by_symbol["2881"]["conditions"])


def test_trigger_monitor_uses_intraday_price_provider_for_trigger_status() -> None:
    checked_at = datetime.now().isoformat(timespec="seconds")
    payload = DailyRadarTriggerMonitorService(
        count=10,
        limit=10,
        max_price_age_seconds=180,
        price_provider=lambda symbols: {
            "2330.TW": {
                "price": 100.5,
                "source": "shioaji_snapshot",
                "checked_at": checked_at,
                "quote_buffer_status": "streaming_tick",
                "quote_buffer_tick_count": 2,
                "quote_buffer_bidask_count": 1,
                "quote_buffer_retained_tick_count": 2,
                "quote_buffer_retained_bidask_count": 1,
                "quote_buffer_event_count": 3,
                "quote_buffer_last_event": {
                    "resp_code": 0,
                    "event_code": 16,
                    "info": "subscribe 2330",
                    "event": "quote",
                    "checked_at": checked_at,
                },
                "quote_buffer": {
                    "latest_count": 1,
                    "tick_count": 2,
                    "bidask_count": 1,
                    "retained_tick_count": 2,
                    "retained_bidask_count": 1,
                    "event_count": 3,
                    "symbols": ["2330"],
                    "last_event": {
                        "resp_code": 0,
                        "event_code": 16,
                        "info": "subscribe 2330",
                        "event": "quote",
                        "checked_at": checked_at,
                    },
                },
                "quote_manager": {
                    "state": "stopped",
                    "connection_status": "connected",
                    "started_at": checked_at,
                    "stopped_at": checked_at,
                    "subscribed_symbols": ["2330"],
                    "subscription_count": 0,
                    "subscription_call_count": 2,
                    "include_bidask": True,
                    "event_callback_method": "on_event",
                    "last_heartbeat_at": checked_at,
                    "last_reconnect_at": checked_at,
                    "last_resubscribe_at": checked_at,
                    "reconnect_count": 1,
                    "disconnect_count": 1,
                    "resubscribe_count": 1,
                    "buffer": {
                        "latest_count": 1,
                        "tick_count": 2,
                        "bidask_count": 1,
                        "retained_tick_count": 2,
                        "retained_bidask_count": 1,
                        "event_count": 3,
                        "symbols": ["2330"],
                    },
                },
            }
        },
    ).evaluate(_PriceOverrideRepo())

    item = payload["items"][0]
    assert payload["status"] == "ready"
    assert payload["price_snapshot"]["enabled"] is True
    assert payload["price_snapshot"]["source"] == "shioaji_snapshot"
    assert payload["price_snapshot"]["updated_count"] == 1
    assert payload["price_snapshot"]["symbols"] == ["2330"]
    assert payload["price_snapshot"]["max_age_seconds"] == 180
    assert payload["price_snapshot"]["fresh_count"] == 1
    assert item["status"] == "ready"
    assert item["price"] == 100.5
    assert item["radar_price"] == 97.0
    assert item["intraday_price"] == 100.5
    assert item["price_source"] == "shioaji_snapshot"
    assert item["price_checked_at"] == checked_at
    assert item["price_freshness_status"] == "fresh"
    assert item["price_max_age_seconds"] == 180
    assert item["price_change_from_radar_pct"] == 0.0361
    assert item["quote_buffer_status"] == "streaming_tick"
    assert item["quote_buffer_tick_count"] == 2
    assert item["quote_buffer_bidask_count"] == 1
    assert item["quote_buffer_retained_tick_count"] == 2
    assert item["quote_buffer_retained_bidask_count"] == 1
    assert item["quote_buffer_event_count"] == 3
    assert item["quote_buffer_last_event"]["event_code"] == 16
    assert item["quote_buffer"]["tick_count"] == 2
    assert item["quote_manager"]["connection_status"] == "connected"
    assert item["quote_manager"]["reconnect_count"] == 1
    assert item["quote_manager"]["resubscribe_count"] == 1
    assert payload["quote_status"]["enabled"] is True
    assert payload["quote_status"]["source"] == "shioaji_snapshot"
    assert payload["quote_status"]["streaming_count"] == 1
    assert payload["quote_status"]["tick_count"] == 2
    assert payload["quote_status"]["bidask_count"] == 1
    assert payload["quote_status"]["event_count"] == 3
    assert payload["quote_status"]["sources"] == {"shioaji_snapshot": 1}
    assert payload["quote_status"]["last_event"]["event_code"] == 16
    assert payload["quote_status"]["quote_manager"]["connection_status"] == "connected"
    assert payload["quote_status"]["quote_manager"]["disconnect_count"] == 1


def test_trigger_monitor_rejects_stale_intraday_price_for_trigger_status() -> None:
    payload = DailyRadarTriggerMonitorService(
        count=10,
        limit=10,
        max_price_age_seconds=1,
        price_provider=lambda symbols: {
            "2330": {
                "price": 100.5,
                "source": "shioaji_snapshot",
                "checked_at": "2000-01-01T09:15:00",
            }
        },
    ).evaluate(_PriceOverrideRepo())

    item = payload["items"][0]
    assert payload["status"] == "watch"
    assert payload["price_snapshot"]["freshness_status"] == "stale"
    assert payload["price_snapshot"]["stale_count"] == 1
    assert item["status"] == "watch"
    assert item["price"] == 97.0
    assert item["price_source"] == "radar_snapshot"
    assert item["price_freshness_status"] == "stale"
    assert item["stale_intraday_price"] == 100.5
    assert item["stale_price_source"] == "shioaji_snapshot"
    assert any(condition["key"] == "price_freshness" and condition["status"] == "wait" for condition in item["conditions"])


def test_trigger_monitor_falls_back_to_radar_price_when_price_provider_fails() -> None:
    def failing_provider(symbols):  # noqa: ANN001
        raise RuntimeError("quote unavailable")

    payload = DailyRadarTriggerMonitorService(count=10, limit=10, price_provider=failing_provider).evaluate(
        _PriceOverrideRepo()
    )

    item = payload["items"][0]
    assert payload["status"] == "watch"
    assert payload["price_snapshot"]["enabled"] is True
    assert payload["price_snapshot"]["updated_count"] == 0
    assert "RuntimeError" in payload["price_snapshot"]["error"]
    assert item["price"] == 97.0
    assert item["price_source"] == "radar_snapshot"


def test_trigger_monitor_notification_thresholds_and_message() -> None:
    payload = DailyRadarTriggerMonitorService().evaluate(_Repo())

    assert daily_radar_trigger_monitor_should_notify(payload, notify_on="ready") is True
    assert daily_radar_trigger_monitor_should_notify({"summary": {"ready_count": 0, "near_count": 1}}, notify_on="ready") is False
    assert daily_radar_trigger_monitor_should_notify({"summary": {"ready_count": 0, "near_count": 1}}, notify_on="near") is True
    assert daily_radar_trigger_monitor_should_notify(payload, notify_on="always") is True
    assert daily_radar_trigger_monitor_should_notify(payload, notify_on="none") is False

    message = format_daily_radar_trigger_monitor_message(payload)
    assert "[每日雷達觸發觀察] 已觸發" in message
    assert "2330 台積電" in message
    assert "已觸發 1 / 接近 1 / 等待 1 / 阻擋 1" in message


def test_trigger_monitor_notification_state_suppresses_duplicate_event(tmp_path) -> None:
    payload = DailyRadarTriggerMonitorService().evaluate(_Repo())
    state_path = tmp_path / "trigger-state.json"
    now = datetime(2026, 7, 13, 9, 0, 0)

    first = daily_radar_trigger_monitor_notification_decision(
        payload,
        notify_on="near",
        state_path=state_path,
        cooldown_minutes=240,
        now=now,
    )
    assert first["triggered"] is True
    assert first["should_send"] is True
    assert first["suppressed"] is False

    record = record_daily_radar_trigger_monitor_notification_sent(
        payload,
        notify_on="near",
        state_path=state_path,
        sent_count=1,
        now=now,
    )
    assert record["recorded"] is True
    assert state_path.exists()

    duplicate = daily_radar_trigger_monitor_notification_decision(
        payload,
        notify_on="near",
        state_path=state_path,
        cooldown_minutes=240,
        now=now + timedelta(minutes=30),
    )
    assert duplicate["should_send"] is False
    assert duplicate["suppressed"] is True
    assert duplicate["skipped_reason"] == "cooldown_active"

    after_cooldown = daily_radar_trigger_monitor_notification_decision(
        payload,
        notify_on="near",
        state_path=state_path,
        cooldown_minutes=240,
        now=now + timedelta(minutes=241),
    )
    assert after_cooldown["should_send"] is True
    assert after_cooldown["suppressed"] is False


def test_trigger_monitor_cli_parser_exposes_options() -> None:
    args = build_parser().parse_args(
        [
            "daily-radar-trigger-monitor",
            "--count",
            "60",
            "--limit",
            "6",
            "--near-entry-gap",
            "1.5",
            "--near-price-gap-pct",
            "0.015",
            "--price-source",
            "shioaji_streaming",
            "--shioaji-live",
            "--max-price-age-seconds",
            "45",
            "--streaming-seconds",
            "0.25",
            "--notify",
            "telegram",
            "--notify-on",
            "near",
            "--notification-state-path",
            "data/cache/custom-trigger-state.json",
            "--notification-cooldown-minutes",
            "120",
            "--force-notify",
            "--fail-on-ready",
            "--fail-on-notify-error",
        ]
    )

    assert args.func is cmd_daily_radar_trigger_monitor
    assert args.count == 60
    assert args.limit == 6
    assert args.near_entry_gap == 1.5
    assert args.near_price_gap_pct == 0.015
    assert args.price_source == "shioaji_streaming"
    assert args.shioaji_live is True
    assert args.max_price_age_seconds == 45
    assert args.streaming_seconds == 0.25
    assert args.notify == "telegram"
    assert args.notify_on == "near"
    assert args.notification_state_path == "data/cache/custom-trigger-state.json"
    assert args.notification_cooldown_minutes == 120
    assert args.force_notify is True
    assert args.fail_on_ready is True
    assert args.fail_on_notify_error is True
