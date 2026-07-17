from __future__ import annotations

from datetime import datetime
import json

import pytest

from app.cli import (
    build_parser,
    cmd_intraday_quote_service_review,
    cmd_intraday_quote_service_status,
    cmd_run_intraday_quote_service,
)
from app.services.intraday_quote_service import (
    IntradayQuoteService,
    build_intraday_quote_service_review,
    format_intraday_quote_service_review,
    load_intraday_quote_service_status,
    run_intraday_quote_service,
)


class _FakeManager:
    def __init__(self):
        self.started_symbols: list[str] = []
        self.stop_count = 0
        self.connection_status = "connected"
        self.latest_count: int | None = None
        self.tick_count: int | None = None
        self.bidask_count: int | None = None

    def start(self, symbols):  # noqa: ANN001
        for symbol in symbols:
            if symbol not in self.started_symbols:
                self.started_symbols.append(symbol)

    def stop(self):
        self.stop_count += 1

    def status(self):
        latest_count = len(self.started_symbols) if self.latest_count is None else self.latest_count
        tick_count = len(self.started_symbols) if self.tick_count is None else self.tick_count
        bidask_count = len(self.started_symbols) if self.bidask_count is None else self.bidask_count
        return {
            "state": "active",
            "connection_status": self.connection_status,
            "subscribed_symbols": list(self.started_symbols),
            "subscription_count": len(self.started_symbols) * 2,
            "reconnect_count": 0,
            "resubscribe_count": 0,
            "buffer": {
                "latest_count": latest_count,
                "tick_count": tick_count,
                "bidask_count": bidask_count,
            },
        }

    def snapshot(self, symbols):  # noqa: ANN001
        return {
            symbol: {
                "symbol": symbol,
                "price": 100.0,
                "source": "shioaji_streaming_tick",
                "quote_manager": self.status(),
            }
            for symbol in symbols
        }


class _FakeGateway:
    def __init__(self, simulation: bool):
        self.simulation = simulation
        self.manager = _FakeManager()
        self.api = self
        self.logout_count = 0

    def create_stock_streaming_quote_manager(self, *, include_bidask=True):  # noqa: ANN001
        self.include_bidask = include_bidask
        return self.manager

    def logout(self):
        self.logout_count += 1


def test_intraday_quote_service_blocks_outside_market_session_by_default():
    created: list[_FakeGateway] = []
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 11, 10, 0, 0),
    )

    status = service.start(["2330"], simulation=True)
    stopped = service.stop()

    assert status["state"] == "blocked_outside_session"
    assert status["health"]["status"] == "warn"
    assert status["health"]["issues"][0]["code"] == "outside_market_session"
    assert status["last_error"] == "outside_market_session"
    assert status["market_session"]["status"] == "closed"
    assert stopped["state"] == "stopped"
    assert stopped["health"]["status"] == "idle"
    assert created == []


def test_intraday_quote_service_starts_merges_symbols_and_stops():
    created: list[_FakeGateway] = []
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 13, 10, 0, 0),
    )

    first = service.start(["2330.TW", "2330", "2317"], simulation=True, include_bidask=True)
    second = service.start(["2454"], simulation=True, include_bidask=True)
    snapshot = service.snapshot()
    stopped = service.stop()

    assert first["state"] == "running"
    assert first["health"]["status"] == "ok"
    assert first["symbols"] == ["2330", "2317"]
    assert second["symbols"] == ["2330", "2317", "2454"]
    assert second["manager"]["subscribed_symbols"] == ["2330", "2317", "2454"]
    assert set(snapshot["items"]) == {"2330", "2317", "2454"}
    assert stopped["state"] == "stopped"
    assert stopped["health"]["status"] == "warn"
    assert stopped["health"]["issues"][0]["code"] == "market_open_service_stopped"
    assert stopped["manager"] is None
    assert created[0].manager.stop_count == 1
    assert created[0].logout_count == 1


def test_intraday_quote_service_recreates_manager_when_environment_changes():
    created: list[_FakeGateway] = []
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 13, 10, 0, 0),
    )

    service.start(["2330"], simulation=True, include_bidask=True)
    status = service.start(["2330"], simulation=False, include_bidask=False)

    assert len(created) == 2
    assert created[0].manager.stop_count == 1
    assert created[0].logout_count == 1
    assert created[1].simulation is False
    assert created[1].include_bidask is False
    assert status["simulation"] is False
    assert status["include_bidask"] is False


def test_intraday_quote_service_clears_previous_stop_time_when_restarted():
    created: list[_FakeGateway] = []
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 13, 10, 0, 0),
    )

    service.start(["2330"], simulation=True)
    stopped = service.stop()
    restarted = service.start(["2330"], simulation=True)

    assert stopped["stopped_at"]
    assert restarted["state"] == "running"
    assert restarted["stopped_at"] == ""


def test_intraday_quote_service_reports_disconnected_health_as_danger():
    created: list[_FakeGateway] = []
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 13, 10, 0, 0),
    )

    service.start(["2330"], simulation=True)
    created[0].manager.connection_status = "disconnected"
    status = service.status()

    assert status["health"]["status"] == "danger"
    assert any(issue["code"] == "quote_disconnected" for issue in status["health"]["issues"])
    assert any("重新啟動" in action for action in status["health"]["next_actions"])


def test_intraday_quote_service_warns_when_no_quotes_received_yet():
    created: list[_FakeGateway] = []
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 13, 10, 0, 0),
    )

    service.start(["2330"], simulation=True)
    created[0].manager.latest_count = 0
    created[0].manager.tick_count = 0
    created[0].manager.bidask_count = 0
    status = service.status()

    assert status["health"]["status"] == "warn"
    assert any(issue["code"] == "no_quotes_received" for issue in status["health"]["issues"])


def test_intraday_quote_service_persists_health_snapshot(tmp_path):
    created: list[_FakeGateway] = []
    status_path = tmp_path / "intraday_quote_status.json"
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 13, 10, 0, 0),
        status_path=status_path,
    )

    started = service.start(["2330", "2317"], simulation=True)
    loaded = load_intraday_quote_service_status(
        status_path,
        now_fn=lambda: datetime(2026, 7, 13, 10, 1, 0),
        max_age_seconds=300,
    )

    assert status_path.exists()
    assert started["health"]["status"] == "ok"
    assert loaded["state"] == "running"
    assert loaded["health"]["status"] == "ok"
    assert loaded["snapshot"]["freshness_status"] == "fresh"
    assert loaded["snapshot"]["age_seconds"] == 60
    assert loaded["symbols"] == ["2330", "2317"]


def test_load_intraday_quote_service_status_marks_stale_snapshot(tmp_path):
    status_path = tmp_path / "intraday_quote_status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "running",
                "running": True,
                "symbols": ["2330"],
                "symbol_count": 1,
                "simulation": True,
                "include_bidask": True,
                "snapshot_recorded_at": "2026-07-13T09:30:00",
                "health": {"status": "ok", "label": "正常", "issues": [], "next_actions": []},
                "manager": {"connection_status": "connected", "subscription_count": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_intraday_quote_service_status(
        status_path,
        now_fn=lambda: datetime(2026, 7, 13, 9, 40, 1),
        max_age_seconds=300,
    )

    assert loaded["health"]["status"] == "warn"
    assert loaded["health"]["label"] == "狀態過期"
    assert loaded["snapshot"]["freshness_status"] == "stale"
    assert loaded["snapshot"]["age_seconds"] == 601
    assert any(issue["code"] == "quote_status_snapshot_stale" for issue in loaded["health"]["issues"])


def test_intraday_quote_service_status_cli_reads_snapshot(tmp_path, capsys):
    status_path = tmp_path / "intraday_quote_status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "running",
                "running": True,
                "symbols": ["2330"],
                "symbol_count": 1,
                "snapshot_recorded_at": datetime.now().isoformat(timespec="seconds"),
                "health": {"status": "danger", "label": "異常", "issues": [], "next_actions": []},
                "manager": {"connection_status": "disconnected", "subscription_count": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            "intraday-quote-service-status",
            "--state-path",
            str(status_path),
            "--max-age-seconds",
            "300",
            "--fail-on-danger",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_intraday_quote_service_status(args)

    output = json.loads(capsys.readouterr().out)
    assert exc.value.code == 2
    assert output["health"]["status"] == "danger"
    assert output["snapshot"]["path"] == str(status_path)


def test_run_intraday_quote_service_updates_status_until_iteration_limit(tmp_path):
    created: list[_FakeGateway] = []
    sleeps: list[float] = []
    status_path = tmp_path / "intraday_quote_status.json"
    service = IntradayQuoteService(
        gateway_factory=lambda simulation: created.append(_FakeGateway(simulation)) or created[-1],
        now_fn=lambda: datetime(2026, 7, 13, 10, 0, 0),
        status_path=status_path,
    )

    result = run_intraday_quote_service(
        ["2330", "2317"],
        simulation=True,
        include_bidask=True,
        allow_outside_session=False,
        status_path=status_path,
        heartbeat_interval_seconds=1.0,
        max_iterations=2,
        service_factory=lambda: service,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )
    persisted = json.loads(status_path.read_text(encoding="utf-8"))

    assert result["exit_reason"] == "max_iterations"
    assert result["iterations"] == 2
    assert result["status"]["state"] == "running"
    assert result["stopped_status"]["state"] == "stopped"
    assert sleeps == [1.0, 1.0]
    assert created[0].manager.stop_count == 1
    assert created[0].logout_count == 1
    assert persisted["state"] == "stopped"
    assert persisted["snapshot_source"] == "intraday_quote_service"
    assert persisted["session_summary"]["heartbeat_count"] >= 2
    assert persisted["session_summary"]["max_quote_coverage"] == 1.0


def test_intraday_quote_service_post_session_review_uses_session_summary(tmp_path):
    status_path = tmp_path / "intraday_quote_status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "stopped",
                "running": False,
                "symbols": ["2330", "2317"],
                "symbol_count": 2,
                "snapshot_recorded_at": datetime(2026, 7, 13, 14, 0, 0).isoformat(timespec="seconds"),
                "health": {"status": "idle", "label": "未啟動", "issues": [], "next_actions": []},
                "manager": None,
                "session_summary": {
                    "heartbeat_count": 42,
                    "max_health_status": "ok",
                    "max_latest_count": 2,
                    "max_tick_count": 2,
                    "max_bidask_count": 2,
                    "max_quote_coverage": 1.0,
                    "last_connection_status": "connected",
                    "issue_counts": {},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    review = build_intraday_quote_service_review(
        status_path,
        max_age_seconds=3600,
        now_fn=lambda: datetime(2026, 7, 13, 14, 10, 0),
    )
    text = format_intraday_quote_service_review(review)

    assert review["status"] == "ok"
    assert review["evidence"]["heartbeat_count"] == 42
    assert review["evidence"]["max_quote_coverage"] == 1.0
    assert "盤後正常" in text
    assert "報價覆蓋 100%" in text


def test_intraday_quote_service_post_session_review_flags_missing_quotes(tmp_path):
    status_path = tmp_path / "intraday_quote_status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "running",
                "running": True,
                "symbols": ["2330", "2317"],
                "symbol_count": 2,
                "snapshot_recorded_at": datetime(2026, 7, 13, 14, 0, 0).isoformat(timespec="seconds"),
                "health": {"status": "warn", "label": "提醒", "summary": "尚未收到任何串流報價。", "issues": [], "next_actions": []},
                "manager": {"connection_status": "connected", "subscription_count": 2},
                "session_summary": {
                    "heartbeat_count": 3,
                    "max_health_status": "warn",
                    "max_latest_count": 0,
                    "max_tick_count": 0,
                    "max_bidask_count": 0,
                    "max_quote_coverage": 0.0,
                    "last_connection_status": "connected",
                    "issue_counts": {"no_quotes_received": 3},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    review = build_intraday_quote_service_review(
        status_path,
        max_age_seconds=3600,
        now_fn=lambda: datetime(2026, 7, 13, 14, 10, 0),
    )

    assert review["status"] == "danger"
    assert any(issue["code"] == "no_quotes_during_session" for issue in review["issues"])
    assert "Shioaji snapshot" in " ".join(review["next_actions"])


def test_intraday_quote_service_review_cli_outputs_text_and_can_fail(tmp_path, capsys):
    status_path = tmp_path / "intraday_quote_status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "running",
                "running": True,
                "symbols": ["2330"],
                "symbol_count": 1,
                "snapshot_recorded_at": datetime.now().isoformat(timespec="seconds"),
                "health": {"status": "danger", "summary": "盤中行情服務需要處理。", "issues": [], "next_actions": []},
                "manager": {"connection_status": "disconnected", "subscription_count": 1},
                "session_summary": {
                    "heartbeat_count": 1,
                    "max_health_status": "danger",
                    "max_latest_count": 0,
                    "max_tick_count": 0,
                    "max_bidask_count": 0,
                    "max_quote_coverage": 0.0,
                    "last_connection_status": "disconnected",
                    "issue_counts": {"quote_disconnected": 1},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            "intraday-quote-service-review",
            "--state-path",
            str(status_path),
            "--fail-on-danger",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_intraday_quote_service_review(args)

    output = capsys.readouterr().out
    assert exc.value.code == 2
    assert "盤中行情服務盤後回顧" in output
    assert "盤後需處理" in output


def test_run_intraday_quote_service_cli_invokes_runner(monkeypatch, tmp_path, capsys):
    captured: dict[str, object] = {}

    def _fake_runner(symbols, **kwargs):  # noqa: ANN001
        captured["symbols"] = list(symbols)
        captured["kwargs"] = kwargs
        return {
            "status": {"state": "running", "health": {"status": "ok"}},
            "stopped_status": {"state": "stopped", "health": {"status": "idle"}},
            "iterations": 1,
            "exit_reason": "max_runtime",
            "status_path": str(kwargs.get("status_path")),
        }

    symbols_file = tmp_path / "symbols.txt"
    symbols_file.write_text("2317\n2454", encoding="utf-8")
    monkeypatch.setattr("app.services.intraday_quote_service.run_intraday_quote_service", _fake_runner)
    args = build_parser().parse_args(
        [
            "run-intraday-quote-service",
            "--symbols",
            "2330, 2317",
            "--symbols-file",
            str(symbols_file),
            "--state-path",
            str(tmp_path / "status.json"),
            "--heartbeat-interval-seconds",
            "2",
            "--max-runtime-seconds",
            "5",
            "--shioaji-live",
            "--no-bidask",
            "--allow-outside-session",
        ]
    )

    cmd_run_intraday_quote_service(args)

    output = json.loads(capsys.readouterr().out)
    kwargs = captured["kwargs"]
    assert captured["symbols"] == ["2330", "2317", "2454"]
    assert kwargs["simulation"] is False
    assert kwargs["include_bidask"] is False
    assert kwargs["allow_outside_session"] is True
    assert kwargs["heartbeat_interval_seconds"] == 2.0
    assert kwargs["max_runtime_seconds"] == 5.0
    assert output["exit_reason"] == "max_runtime"
