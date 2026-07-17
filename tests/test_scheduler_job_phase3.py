from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.scheduler.jobs import (
    build_daily_radar_trigger_price_provider,
    format_daily_radar_brief_message,
    run_daily_radar_job,
    run_daily_radar_trigger_monitor_job,
    run_daily_signal_job,
    start_scheduler,
)


def _make_rows(closes: list[float], volumes: list[float], start: date) -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    for idx, close in enumerate(closes):
        rows.append(
            SimpleNamespace(
                date=date.fromordinal(start.toordinal() + idx),
                open=float(close) * 0.995,
                high=float(close) * 1.01,
                low=float(close) * 0.99,
                close=float(close),
                volume=float(volumes[idx]),
            )
        )
    return rows


class DummyRepo:
    def __init__(self):
        start = date(2024, 1, 1)
        closes = [100 + i * 0.4 for i in range(80)]
        closes[-4:] = [130, 126, 123, 122]
        volumes = [1200] * 79 + [7000]
        self.rows = _make_rows(closes, volumes, start)
        self.trade_date = self.rows[-1].date

    def get_latest_0050_symbols(self) -> list[str]:
        return ["2330"]

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return self.rows

    def get_institutional_chip_rollup(self, symbol: str, end_date, lookback_days=5):  # noqa: ANN001, ARG002
        return {"institutional_net_buy_sum": 2000.0}

    def is_chip_concentration_up(self, symbol: str, end_date, days=3):  # noqa: ANN001, ARG002
        return True

    def get_active_disposition(self, symbol: str, on_date):  # noqa: ANN001, ARG002
        return {"active": False}

    def get_institutional_chip(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        rows = []
        for idx in range(60):
            # rising tail to pass z-score check
            rows.append(
                SimpleNamespace(
                    foreign_net_buy=float(idx * 30),
                    investment_trust_net_buy=float(idx * 10),
                )
            )
        return rows

    def get_instrument_name(self, symbol: str) -> str:
        return "台積電"


class DummySession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class DummyNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


def test_format_daily_radar_brief_message_summarizes_handoff_and_lanes():
    message = format_daily_radar_brief_message(
        {
            "trade_date": "2026-06-25",
            "run_id": "radar_test",
            "candidate_count": 3,
            "handoff": {
                "primary_action": "先檢查可建立 Preview",
                "next_session_note": "可先建立 Preview 1 檔：2330",
                "top_symbols": ["2330"],
            },
            "diagnostics": {
                "verdict": "可小額研究，先補檢查",
                "review_counts": {"preview": 1, "trigger": 1, "risk": 1},
            },
            "threshold_sweep": {
                "verdict": "穩健區間",
                "robustness_score": 82,
                "stable_symbols": ["2330", "2454"],
            },
            "quality": {
                "verdict": "可優先研究",
                "quality_score": 72,
                "lanes": [
                    {"key": "research_now", "label": "可先研究", "count": 1, "symbols": ["2330"]},
                    {"key": "observe_only", "label": "只觀察", "count": 1, "symbols": ["2454"]},
                    {
                        "key": "repair_first",
                        "label": "修資料後再看",
                        "count": 1,
                        "symbols": ["2317"],
                        "data_gap_scope_summary": ["daily_bars 1 檔", "institutional_chip 1 檔"],
                    },
                    {"key": "skip_for_now", "label": "先略過", "count": 0, "symbols": []},
                ],
            },
            "action_grade": {
                "counts": {"可下手": 1, "只觀察": 1, "不碰": 1},
                "ready_symbols": ["2330"],
                "watch_symbols": ["2454"],
                "avoid_symbols": ["2317"],
            },
            "lanes": [
                {"label": "可先建立 Preview", "count": 1, "items": [{"symbol": "2330", "name": "台積電"}]},
                {
                    "label": "設觸發觀察",
                    "count": 1,
                    "items": [
                        {
                            "symbol": "2454",
                            "name": "聯發科",
                            "trigger_plan_short_text": "Entry>=80 / 突破900",
                        }
                    ],
                },
                {"label": "先處理風險", "count": 1, "items": [{"symbol": "2317", "name": "鴻海"}]},
            ],
            "outcomes": {"verdict": "配置優於排名基準"},
        },
        export_urls={"html_summary": "/reports/radar.html", "decision_brief_md": "/reports/radar_brief.md"},
    )

    assert "[每日雷達] 2026-06-25 盤前 Brief" in message
    assert "主線: 先檢查可建立 Preview" in message
    assert "分級: 可下手 1 / 只觀察 1 / 不碰 1" in message
    assert "可下手: 2330" in message
    assert "只觀察: 2454" in message
    assert "不碰: 2317" in message
    assert "品質: 可優先研究 (72/100)" in message
    assert "品質分流: 可先研究 1 / 只觀察 1 / 修資料後再看 1 / 先略過 0" in message
    assert "可先研究: 2330" in message
    assert "先修資料: 2317" in message
    assert "資料缺口: daily_bars 1 檔 / institutional_chip 1 檔" in message
    assert "行動線: Preview 1 / Trigger 1 / Risk 1" in message
    assert "門檻: 穩健區間 82/100；穩定: 2330, 2454" in message
    assert "- 可先建立 Preview: 1 檔: 2330 台積電" in message
    assert "- 設觸發觀察: 1 檔: 2454 聯發科 (Entry>=80 / 突破900)" in message
    assert "Brief: /reports/radar_brief.md" in message


def test_format_daily_radar_brief_message_supports_intraday_and_postmarket_styles():
    decision_brief = {
        "trade_date": "2026-06-25",
        "run_id": "radar_test",
        "candidate_count": 3,
        "handoff": {
            "primary_action": "盤中只盯接近觸發標的",
            "next_session_note": "先看 2454 是否突破",
            "top_symbols": ["2454", "2330"],
        },
        "diagnostics": {"review_counts": {"preview": 1, "trigger": 2, "risk": 0}},
        "quality": {"verdict": "可優先研究", "quality_score": 76},
        "lanes": [
            {
                "label": "設觸發觀察",
                "count": 2,
                "items": [
                    {"symbol": "2454", "name": "聯發科", "trigger_plan_short_text": "Entry>=80 / 突破900"},
                    {"symbol": "2330", "name": "台積電", "trigger_plan_short_text": "回測650"},
                ],
            },
            {"label": "先處理風險", "count": 1, "items": [{"symbol": "2317", "name": "鴻海"}]},
        ],
        "trading_feedback": {"verdict": "今日沒有新增拒單", "primary_action": "盤後保留觀察清單"},
        "outcomes": {"verdict": "採用後樣本仍偏少"},
    }

    intraday = format_daily_radar_brief_message(
        decision_brief,
        export_urls={"html_summary": "/reports/radar.html"},
        notify_style="intraday",
    )
    postmarket = format_daily_radar_brief_message(
        decision_brief,
        export_urls={"html_summary": "/reports/radar.html"},
        notify_style="postmarket",
    )

    assert "[每日雷達] 2026-06-25 盤中快訊" in intraday
    assert "行動線: Preview 1 / Trigger 2 / Risk 0" in intraday
    assert "- 設觸發觀察: 2 檔: 2454 (Entry>=80 / 突破900), 2330 (回測650)" in intraday
    assert "品質:" not in intraday
    assert "[每日雷達] 2026-06-25 盤後回顧" in postmarket
    assert "品質: 可優先研究 (76/100)" in postmarket
    assert "驗證: 採用後樣本仍偏少" in postmarket
    assert "交易回饋: 今日沒有新增拒單" in postmarket
    assert "盤後動作: 盤後保留觀察清單" in postmarket


def test_run_daily_signal_job_supports_new_filters(monkeypatch):
    repo = DummyRepo()
    monkeypatch.setattr("app.scheduler.jobs._resolve_trade_date", lambda mode="T": repo.trade_date)
    result = run_daily_signal_job(
        repo=repo,
        notifiers=[],
        available_cash=10_000,
        trade_date_mode="T",
        min_data_timestamp=repo.trade_date.isoformat(),
        rank_by="risk_adjusted_score",
        max_open_positions=1,
    )

    assert result["suggestions"] >= 1
    assert result["trade_date_mode"] == "T"
    assert result["rank_by"] == "risk_adjusted_score"
    assert result["max_open_positions"] == 1
    assert result["rejected_by_min_data_timestamp"] == 0


def test_run_daily_radar_job_runs_service_and_commits(monkeypatch):
    captured = {}

    class _RadarRepo:
        def __init__(self):
            self.session = DummySession()

    class _RadarService:
        def __init__(self, repo, sync_service):  # noqa: ANN001
            captured["repo"] = repo
            captured["sync_service"] = sync_service

        def run(self, request):  # noqa: ANN001
            captured["request"] = request
            return {
                "run_id": "radar_20240401_test",
                "trade_date": request.trade_date.isoformat(),
                "target_count": request.target_count,
                "risk_profile": request.risk_profile,
                "threshold_offset": request.threshold_offset,
                "universe": request.universe,
                "items": [
                    {
                        "rank": 1,
                        "symbol": "2330",
                        "name": "台積電",
                        "action_label": "ENTRY_READY",
                        "entry_score": 88.0,
                        "risk_adjusted_score": 86.0,
                    },
                    {
                        "rank": 2,
                        "symbol": "2454",
                        "name": "聯發科",
                        "action_label": "WATCH_WAIT_TRIGGER",
                        "entry_score": 74.0,
                        "risk_adjusted_score": 72.0,
                    },
                ],
                "label_counts": {"ENTRY_READY": 1, "WATCH_WAIT_TRIGGER": 1},
                "degraded": False,
                "degrade_reasons": [],
                "export": {
                    "files": {"json": "daily.json"},
                    "urls": {"html_summary": "/reports/daily.html", "decision_brief_md": "/reports/daily_brief.md"},
                    "data": {
                        "decision_brief": {
                            "trade_date": request.trade_date.isoformat(),
                            "run_id": "radar_20240401_test",
                            "candidate_count": 2,
                            "handoff": {
                                "primary_action": "先檢查可建立 Preview",
                                "next_session_note": "可先建立 Preview 1 檔：2330",
                                "top_symbols": ["2330"],
                            },
                            "diagnostics": {"verdict": "可小額研究", "review_counts": {"preview": 1, "trigger": 1, "risk": 0}},
                            "lanes": [
                                {
                                    "label": "可先建立 Preview",
                                    "count": 1,
                                    "items": [{"symbol": "2330", "name": "台積電"}],
                                }
                            ],
                            "outcomes": {"verdict": ""},
                        }
                    },
                },
            }

    repo = _RadarRepo()
    sync_service = object()
    notifier = DummyNotifier()
    monkeypatch.setattr("app.scheduler.jobs._resolve_trade_date", lambda mode="T": date(2024, 4, 1))
    monkeypatch.setattr("app.scheduler.jobs.DailyRadarService", _RadarService)
    monkeypatch.setattr(
        "app.scheduler.jobs._sync_index_constituents_url_for_job",
        lambda **kwargs: {
            "index_key": kwargs["index_key"] or kwargs["universe"].split(":", 1)[1],
            "url": kwargs["url"],
            "source": kwargs["source"],
            "count": 2,
        },
    )

    result = run_daily_radar_job(
        repo=repo,  # type: ignore[arg-type]
        sync_service=sync_service,  # type: ignore[arg-type]
        notifiers=[notifier],  # type: ignore[list-item]
        trade_date_mode="T-1",
        target_count=12,
        risk_profile="conservative",
        threshold_offset=2.0,
        universe="index:tip_tw50",
        symbols="2330, 2454",
        include_attention=False,
        include_extended_data=True,
        prefer_official_bulk=False,
        index_constituents_url="https://backend.taiwanindex.com.tw/files/tip.xlsx",
        index_constituents_source="tip_url",
        notify_style="postmarket",
    )

    assert captured["sync_service"] is sync_service
    assert captured["request"].trade_date == date(2024, 4, 1)
    assert captured["request"].target_count == 12
    assert captured["request"].risk_profile == "conservative"
    assert captured["request"].threshold_offset == 2.0
    assert captured["request"].universe == "index:tip_tw50"
    assert captured["request"].symbols == ["2330", "2454"]
    assert captured["request"].include_attention is False
    assert captured["request"].include_extended_data is True
    assert captured["request"].prefer_official_bulk is False
    assert repo.session.committed is True
    assert repo.session.rolled_back is False
    assert result["items"] == 2
    assert [item["symbol"] for item in result["top_items"]] == ["2330", "2454"]
    assert result["top_items"][0]["action_label"] == "ENTRY_READY"
    assert result["threshold_offset"] == 2.0
    assert result["label_counts"]["ENTRY_READY"] == 1
    assert result["prefer_official_bulk"] is False
    assert result["notification"]["sent"] == 1
    assert result["notification"]["notify_style"] == "postmarket"
    assert "[每日雷達] 2024-04-01 盤後回顧" in notifier.messages[0]
    assert "主線: 先檢查可建立 Preview" in notifier.messages[0]
    assert result["index_constituents_sync"] == {
        "index_key": "tip_tw50",
        "url": "https://backend.taiwanindex.com.tw/files/tip.xlsx",
        "source": "tip_url",
        "count": 2,
    }


def test_run_daily_radar_trigger_monitor_job_notifies_when_threshold_matches(monkeypatch, tmp_path):
    captured = {}

    class _TriggerService:
        def __init__(self, *, count, limit, near_entry_gap, near_price_gap_pct, max_price_age_seconds=180, price_provider=None):  # noqa: ANN001
            captured["service_args"] = {
                "count": count,
                "limit": limit,
                "near_entry_gap": near_entry_gap,
                "near_price_gap_pct": near_price_gap_pct,
                "max_price_age_seconds": max_price_age_seconds,
                "has_price_provider": price_provider is not None,
            }

        def evaluate(self, repo):  # noqa: ANN001
            captured["repo"] = repo
            return {"summary": {"ready_count": 1, "near_count": 0}, "items": [{"symbol": "2330"}]}

    monkeypatch.setattr("app.reports.daily_radar_trigger_monitor.DailyRadarTriggerMonitorService", _TriggerService)
    monkeypatch.setattr("app.reports.daily_radar_trigger_monitor.daily_radar_trigger_monitor_should_notify", lambda payload, notify_on: True)
    monkeypatch.setattr("app.reports.daily_radar_trigger_monitor.format_daily_radar_trigger_monitor_message", lambda payload: "[每日雷達] trigger")

    repo = object()
    notifier = DummyNotifier()
    result = run_daily_radar_trigger_monitor_job(
        repo=repo,  # type: ignore[arg-type]
        notifiers=[notifier],  # type: ignore[list-item]
        count=60,
        limit=8,
        near_entry_gap=1.5,
        near_price_gap_pct=0.015,
        notify_on="near",
        price_source="shioaji_streaming",
        shioaji_simulation=True,
        max_price_age_seconds=45,
        streaming_seconds=0.25,
        notification_state_path=str(tmp_path / "trigger-state.json"),
        notification_cooldown_minutes=240,
    )

    assert captured["repo"] is repo
    assert captured["service_args"] == {
        "count": 60,
        "limit": 8,
        "near_entry_gap": 1.5,
        "near_price_gap_pct": 0.015,
        "max_price_age_seconds": 45,
        "has_price_provider": True,
    }
    assert result["notification"]["notify_on"] == "near"
    assert result["notification"]["triggered"] is True
    assert result["notification"]["sent"] == 1
    assert result["notification"]["state_record"]["recorded"] is True
    assert notifier.messages == ["[每日雷達] trigger"]

    duplicate = run_daily_radar_trigger_monitor_job(
        repo=repo,  # type: ignore[arg-type]
        notifiers=[notifier],  # type: ignore[list-item]
        count=60,
        limit=8,
        near_entry_gap=1.5,
        near_price_gap_pct=0.015,
        notify_on="near",
        price_source="shioaji_streaming",
        shioaji_simulation=True,
        max_price_age_seconds=45,
        streaming_seconds=0.25,
        notification_state_path=str(tmp_path / "trigger-state.json"),
        notification_cooldown_minutes=240,
    )

    assert duplicate["notification"]["sent"] == 0
    assert duplicate["notification"]["suppressed"] is True
    assert duplicate["notification"]["skipped_reason"] == "cooldown_active"
    assert notifier.messages == ["[每日雷達] trigger"]


def test_build_daily_radar_trigger_price_provider_supports_streaming(monkeypatch):
    captured = {}

    class _Gateway:
        def __init__(self, config):  # noqa: ANN001
            captured["simulation"] = config.simulation
            self.api = SimpleNamespace(logout=lambda: captured.setdefault("logged_out", True))

        def get_stock_streaming_quotes(self, symbols, *, collect_seconds, fallback_to_snapshot):  # noqa: ANN001
            captured["symbols"] = list(symbols)
            captured["collect_seconds"] = collect_seconds
            captured["fallback_to_snapshot"] = fallback_to_snapshot
            return {
                "2330": {
                    "symbol": "2330",
                    "price": 103.5,
                    "source": "shioaji_streaming_tick",
                    "checked_at": "2026-07-13T09:15:00",
                }
            }

    monkeypatch.setattr("app.broker.shioaji_gateway.ShioajiGateway", _Gateway)

    provider = build_daily_radar_trigger_price_provider(
        price_source="shioaji_streaming",
        shioaji_simulation=False,
        streaming_seconds=0.25,
    )

    assert provider is not None
    assert provider(["2330", "2454"]) == {
        "2330": {
            "symbol": "2330",
            "price": 103.5,
            "source": "shioaji_streaming_tick",
            "checked_at": "2026-07-13T09:15:00",
        }
    }
    assert captured == {
        "simulation": False,
        "symbols": ["2330", "2454"],
        "collect_seconds": 0.25,
        "fallback_to_snapshot": True,
        "logged_out": True,
    }


def test_start_scheduler_can_schedule_signal_and_daily_radar_jobs(monkeypatch):
    captured = {}

    class _Scheduler:
        latest = None

        def __init__(self, timezone):  # noqa: ANN001
            self.timezone = timezone
            self.jobs = []
            self.started = False
            _Scheduler.latest = self

        def add_job(self, func, **kwargs):  # noqa: ANN001
            self.jobs.append({"func": func, **kwargs})

        def start(self):
            self.started = True

    def fake_run_daily_radar_job(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"run_id": "radar"}

    monkeypatch.setattr("app.scheduler.jobs.run_daily_radar_job", fake_run_daily_radar_job)
    repo = DummyRepo()
    start_scheduler(
        repo=repo,  # type: ignore[arg-type]
        notifiers=[],
        enable_daily_radar=True,
        radar_hour=8,
        radar_minute=35,
        radar_universe="index:tip_tw50",
        radar_include_extended_data=True,
        radar_index_constituents_url="https://backend.taiwanindex.com.tw/files/tip.xlsx",
        radar_index_constituents_source="tip_url",
        radar_notify_style="intraday",
        scheduler_cls=_Scheduler,
    )

    scheduler = _Scheduler.latest
    assert scheduler is not None
    assert scheduler.timezone == "Asia/Taipei"
    assert scheduler.started is True
    assert {job["id"] for job in scheduler.jobs} == {"daily_signal_job", "daily_radar_job"}
    radar_job = next(job for job in scheduler.jobs if job["id"] == "daily_radar_job")
    assert radar_job["hour"] == 8
    assert radar_job["minute"] == 35
    radar_job["func"]()
    assert captured["notifiers"] == []
    assert captured["universe"] == "index:tip_tw50"
    assert captured["include_extended_data"] is True
    assert captured["prefer_official_bulk"] is True
    assert captured["index_constituents_url"] == "https://backend.taiwanindex.com.tw/files/tip.xlsx"
    assert captured["index_constituents_source"] == "tip_url"
    assert captured["notify_style"] == "intraday"


def test_start_scheduler_can_schedule_trigger_monitor_job(monkeypatch):
    captured = {}

    class _Scheduler:
        latest = None

        def __init__(self, timezone):  # noqa: ANN001
            self.timezone = timezone
            self.jobs = []
            self.started = False
            _Scheduler.latest = self

        def add_job(self, func, **kwargs):  # noqa: ANN001
            self.jobs.append({"func": func, **kwargs})

        def start(self):
            self.started = True

    def fake_run_daily_radar_trigger_monitor_job(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"summary": {"ready_count": 1}}

    monkeypatch.setattr("app.scheduler.jobs.run_daily_radar_trigger_monitor_job", fake_run_daily_radar_trigger_monitor_job)
    repo = DummyRepo()
    start_scheduler(
        repo=repo,  # type: ignore[arg-type]
        notifiers=[],
        enable_signal_job=False,
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
        scheduler_cls=_Scheduler,
    )

    scheduler = _Scheduler.latest
    assert scheduler is not None
    assert scheduler.timezone == "Asia/Taipei"
    assert scheduler.started is True
    assert {job["id"] for job in scheduler.jobs} == {"daily_radar_trigger_monitor"}
    trigger_job = scheduler.jobs[0]
    assert trigger_job["hour"] == 9
    assert trigger_job["minute"] == 20
    trigger_job["func"]()
    assert captured["notifiers"] == []
    assert captured["count"] == 60
    assert captured["limit"] == 8
    assert captured["near_entry_gap"] == 1.5
    assert captured["near_price_gap_pct"] == 0.015
    assert captured["notify_on"] == "near"
    assert captured["price_source"] == "shioaji_streaming"
    assert captured["shioaji_simulation"] is False
    assert captured["max_price_age_seconds"] == 45
    assert captured["streaming_seconds"] == 0.25
    assert captured["notification_state_path"] == "data/cache/custom-trigger-state.json"
    assert captured["notification_cooldown_minutes"] == 120
    assert captured["force_notify"] is True


def test_daily_radar_cli_parser_exposes_index_constituent_sync_options():
    from app.cli import (
        build_parser,
        cmd_run_daily_radar_job,
        cmd_run_daily_radar_threshold_optimization,
        cmd_run_scheduler,
        cmd_write_launchd_plist,
        cmd_write_systemd_units,
    )

    parser = build_parser()
    one_shot = parser.parse_args(
        [
            "run-daily-radar-job",
            "--universe",
            "index:tip_tw50",
            "--threshold-offset",
            "2",
            "--index-constituents-url",
            "https://backend.taiwanindex.com.tw/files/tip.xlsx",
            "--index-constituents-source",
            "tip_url",
            "--no-include-attention",
            "--include-extended-data",
            "--no-prefer-official-bulk",
            "--notify",
            "telegram",
            "--notify-style",
            "postmarket",
        ]
    )
    scheduler = parser.parse_args(
        [
            "run-scheduler",
            "--disable-signal-job",
            "--enable-daily-radar",
            "--radar-universe",
            "index:tip_tw50",
            "--radar-threshold-offset",
            "2",
            "--radar-index-constituents-url",
            "https://backend.taiwanindex.com.tw/files/tip.xlsx",
            "--radar-index-constituents-source",
            "tip_url",
            "--radar-notify-style",
            "intraday",
            "--radar-include-extended-data",
            "--no-radar-prefer-official-bulk",
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
        ]
    )
    launchd = parser.parse_args(["write-launchd-plist", "--notify", "line", "--notify-style", "intraday"])
    threshold_optimization = parser.parse_args(
        [
            "run-daily-radar-threshold-optimization",
            "--grid-universes",
            "research_ready,dynamic_balanced,tw0050",
            "--grid-profiles",
            "balanced,conservative",
            "--grid-offsets=-2,0,2",
            "--candidate-source",
            "grid",
        ]
    )
    systemd = parser.parse_args(
        [
            "write-systemd-units",
            "--notify",
            "telegram",
            "--notify-style",
            "postmarket",
            "--include-trigger-monitor",
            "--trigger-monitor-notify",
            "line",
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
            "--include-intraday-quote-service",
            "--intraday-quote-symbols",
            "2330,2317",
            "--intraday-quote-shioaji-live",
            "--intraday-quote-no-bidask",
            "--intraday-quote-allow-outside-session",
            "--intraday-quote-health-max-age-seconds",
            "20",
        ]
    )

    assert one_shot.func is cmd_run_daily_radar_job
    assert one_shot.universe == "index:tip_tw50"
    assert one_shot.threshold_offset == 2.0
    assert one_shot.index_constituents_url == "https://backend.taiwanindex.com.tw/files/tip.xlsx"
    assert one_shot.index_constituents_source == "tip_url"
    assert one_shot.include_attention is False
    assert one_shot.include_extended_data is True
    assert one_shot.prefer_official_bulk is False
    assert one_shot.notify == "telegram"
    assert one_shot.notify_style == "postmarket"
    assert scheduler.func is cmd_run_scheduler
    assert scheduler.radar_universe == "index:tip_tw50"
    assert scheduler.radar_threshold_offset == 2.0
    assert scheduler.radar_index_constituents_url == "https://backend.taiwanindex.com.tw/files/tip.xlsx"
    assert scheduler.radar_index_constituents_source == "tip_url"
    assert scheduler.radar_include_extended_data is True
    assert scheduler.radar_prefer_official_bulk is False
    assert scheduler.radar_notify_style == "intraday"
    assert scheduler.enable_trigger_monitor is True
    assert scheduler.trigger_monitor_hour == 9
    assert scheduler.trigger_monitor_minute == 20
    assert scheduler.trigger_monitor_notify_on == "near"
    assert scheduler.trigger_monitor_price_source == "shioaji_streaming"
    assert scheduler.trigger_monitor_shioaji_live is True
    assert scheduler.trigger_monitor_max_price_age_seconds == 45
    assert scheduler.trigger_monitor_streaming_seconds == 0.25
    assert scheduler.trigger_monitor_notification_state_path == "data/cache/custom-trigger-state.json"
    assert scheduler.trigger_monitor_notification_cooldown_minutes == 120
    assert scheduler.trigger_monitor_force_notify is True
    assert launchd.func is cmd_write_launchd_plist
    assert launchd.notify == "line"
    assert launchd.notify_style == "intraday"
    assert threshold_optimization.func is cmd_run_daily_radar_threshold_optimization
    assert threshold_optimization.grid_universes == "research_ready,dynamic_balanced,tw0050"
    assert threshold_optimization.grid_profiles == "balanced,conservative"
    assert threshold_optimization.grid_offsets == "-2,0,2"
    assert threshold_optimization.candidate_source == "grid"
    assert systemd.func is cmd_write_systemd_units
    assert systemd.notify == "telegram"
    assert systemd.notify_style == "postmarket"
    assert systemd.include_trigger_monitor is True
    assert systemd.trigger_monitor_notify == "line"
    assert systemd.trigger_monitor_notify_on == "near"
    assert systemd.trigger_monitor_price_source == "shioaji_streaming"
    assert systemd.trigger_monitor_shioaji_live is True
    assert systemd.trigger_monitor_max_price_age_seconds == 45
    assert systemd.trigger_monitor_streaming_seconds == 0.25
    assert systemd.trigger_monitor_notification_state_path == "data/cache/custom-trigger-state.json"
    assert systemd.trigger_monitor_notification_cooldown_minutes == 120
    assert systemd.trigger_monitor_force_notify is True
    assert systemd.health_use_ops_readiness is True
    assert systemd.health_ops_target_phase == "small_live"
    assert systemd.health_include_intraday_quote_review is True
    assert systemd.health_intraday_quote_review_max_age_seconds == 7200
    assert systemd.health_intraday_quote_review_min_heartbeat_count == 3
    assert systemd.health_intraday_quote_review_min_quote_coverage == 0.6
    assert systemd.include_intraday_quote_service is True
    assert systemd.intraday_quote_symbols == "2330,2317"
    assert systemd.intraday_quote_shioaji_live is True
    assert systemd.intraday_quote_no_bidask is True
    assert systemd.intraday_quote_allow_outside_session is True
    assert systemd.intraday_quote_health_max_age_seconds == 20
