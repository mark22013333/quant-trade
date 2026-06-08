from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.alerts import radar_engine


@dataclass
class _Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class _Shareholding:
    major_holder_ratio: float
    retail_holder_ratio: float


@dataclass
class _Holding:
    concentration_proxy: float
    dispersion_proxy: float


@dataclass
class _Chip:
    foreign_net_buy: float
    investment_trust_net_buy: float


class _Repo:
    def __init__(self, bars: dict[str, list[_Bar]], disposition_symbols: set[str]):
        self._bars = bars
        self._disposition_symbols = disposition_symbols

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return self._bars.get(symbol, [])

    def get_active_disposition(self, symbol: str, on_date):  # noqa: ANN001, ARG002
        return {"active": symbol in self._disposition_symbols}

    def get_institutional_chip_rollup(self, symbol: str, end_date, lookback_days=5):  # noqa: ANN001, ARG002
        if symbol == "AAA":
            return {"institutional_net_buy_sum": 120_000_000.0}
        if symbol == "BBB":
            return {"institutional_net_buy_sum": 55_000_000.0}
        return {"institutional_net_buy_sum": 20_000_000.0}

    def get_institutional_chip(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return [_Chip(1.0, 1.0) for _ in range(30)]

    def is_chip_concentration_up(self, symbol: str, end_date, days=3):  # noqa: ANN001, ARG002
        return symbol in {"AAA", "BBB"}

    def get_latest_shareholding(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _Shareholding(major_holder_ratio=35.0, retail_holder_ratio=12.0)

    def get_latest_holding_shares_per(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _Holding(concentration_proxy=40.0, dispersion_proxy=10.0)

    def get_instrument_name(self, symbol: str) -> str:
        return symbol


def _make_bars(base_close: float) -> list[_Bar]:
    rows: list[_Bar] = []
    start = date(2025, 12, 1)
    for idx in range(65):
        close = base_close + idx * 0.1
        rows.append(
            _Bar(
                date=date.fromordinal(start.toordinal() + idx),
                open=close * 0.99,
                high=close * 1.01,
                low=close * 0.98,
                close=close,
                volume=2000.0,
            )
        )
    return rows


def test_generate_daily_radar_emits_action_labels(monkeypatch):
    bars = {
        "AAA": _make_bars(100.0),
        "BBB": _make_bars(80.0),
        "CCC": _make_bars(60.0),
    }
    repo = _Repo(bars=bars, disposition_symbols={"CCC"})

    def fake_signal(df):  # noqa: ANN001
        close = float(df["close"].iloc[-1])
        if close >= 106:
            return {
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": close,
                "ma60": close * 0.9,
                "volume": 3000.0,
                "volume_ma5": 1200.0,
                "score": 0.9,
                "risk_score": 0.8,
            }
        if close >= 86:
            return {
                "entry": False,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": False,
                "trigger_reason": "none",
                "close": close,
                "ma60": close * 0.92,
                "volume": 2600.0,
                "volume_ma5": 1300.0,
                "score": 0.75,
                "risk_score": 0.6,
            }
        return {
            "entry": False,
            "trend_ok": True,
            "vol_ok": False,
            "trigger": False,
            "trigger_reason": "none",
            "close": close,
            "ma60": close * 0.95,
            "volume": 800.0,
            "volume_ma5": 1500.0,
            "score": 0.45,
            "risk_score": 0.3,
        }

    monkeypatch.setattr(radar_engine, "evaluate_entry_signal", fake_signal)

    rows = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB", "CCC"],
        trade_date=bars["AAA"][-1].date,
        target_count=15,
        data_freshness_required=True,
    )

    assert len(rows) == 3
    label_map = {item["symbol"]: item["action_label"] for item in rows}
    assert label_map["AAA"] == "ENTRY_READY"
    assert label_map["BBB"] == "WATCH_WAIT_TRIGGER"
    assert label_map["CCC"] == "NOT_RECOMMENDED"
    ccc = next(item for item in rows if item["symbol"] == "CCC")
    assert "B_DISPOSITION_ACTIVE" in ccc["blocker_tags"]


def test_generate_daily_radar_aggressive_allows_soft_entry(monkeypatch):
    bars = {
        "AAA": _make_bars(100.0),
        "BBB": _make_bars(80.0),
    }
    repo = _Repo(bars=bars, disposition_symbols=set())

    def fake_signal(df):  # noqa: ANN001
        close = float(df["close"].iloc[-1])
        if close >= 106:
            return {
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": close,
                "ma60": close * 0.9,
                "volume": 3000.0,
                "volume_ma5": 1200.0,
                "score": 0.9,
                "risk_score": 0.8,
            }
        return {
            "entry": False,
            "trend_ok": True,
            "vol_ok": True,
            "trigger": False,
            "trigger_reason": "none",
            "close": close,
            "ma60": close * 0.92,
            "volume": 2600.0,
            "volume_ma5": 1300.0,
            "score": 0.75,
            "risk_score": 0.6,
        }

    monkeypatch.setattr(radar_engine, "evaluate_entry_signal", fake_signal)

    balanced = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB"],
        trade_date=bars["AAA"][-1].date,
        target_count=15,
        data_freshness_required=True,
        risk_profile="balanced",
    )
    aggressive = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB"],
        trade_date=bars["AAA"][-1].date,
        target_count=15,
        data_freshness_required=True,
        risk_profile="aggressive",
    )

    b_balanced = next(item for item in balanced if item["symbol"] == "BBB")
    b_aggressive = next(item for item in aggressive if item["symbol"] == "BBB")
    assert b_balanced["action_label"] == "WATCH_WAIT_TRIGGER"
    assert b_aggressive["action_label"] == "ENTRY_READY"
