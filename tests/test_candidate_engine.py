from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from app.alerts import candidate_engine
from app.alerts.candidate_engine import build_candidates, generate_candidate_suggestions


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
class _HoldingSharesPer:
    concentration_proxy: float
    dispersion_proxy: float


def _make_bars(start: date, strong: bool = True) -> list[_Bar]:
    closes = [100 + i * 0.4 for i in range(80)]
    if strong:
        closes[-4:] = [130, 126, 123, 122]
        last_volume = 7000
    else:
        closes[-4:] = [130, 128, 126, 125]
        last_volume = 3000
    volumes = [1200] * 79 + [last_volume]
    rows: list[_Bar] = []
    for idx, close in enumerate(closes):
        rows.append(
            _Bar(
                date=date.fromordinal(start.toordinal() + idx),
                open=close * 0.995,
                high=close * 1.01,
                low=close * 0.99,
                close=float(close),
                volume=float(volumes[idx]),
            )
        )
    return rows


class _Repo:
    def __init__(self, bars_by_symbol: dict[str, list[_Bar]]):
        self._bars = bars_by_symbol

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return self._bars.get(symbol, [])

    def get_active_disposition(self, symbol: str, on_date):  # noqa: ANN001, ARG002
        return {"active": symbol == "7777"}

    def get_institutional_chip_rollup(self, symbol: str, end_date, lookback_days=5):  # noqa: ANN001, ARG002
        if symbol in {"1111", "2222", "3333", "4444"}:
            return {"institutional_net_buy_sum": 5000.0}
        return {"institutional_net_buy_sum": 1500.0}

    def is_chip_concentration_up(self, symbol: str, end_date, days=3):  # noqa: ANN001, ARG002
        return symbol != "6666"

    def get_latest_shareholding(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _Shareholding(major_holder_ratio=35.0, retail_holder_ratio=11.0)

    def get_latest_holding_shares_per(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _HoldingSharesPer(concentration_proxy=38.0, dispersion_proxy=9.0)

    def get_instrument_name(self, symbol: str) -> str:
        return f"Name{symbol}"


@pytest.fixture(autouse=True)
def _empty_symbol_exclusions(monkeypatch):
    monkeypatch.setattr("app.alerts.candidate_engine.load_symbol_exclusions", lambda: {})


def test_generate_candidate_suggestions_returns_5_to_10_and_excludes_disposition():
    start = date(2024, 1, 1)
    symbols = ["1111", "2222", "3333", "4444", "5555", "6666", "7777"]
    bars = {symbol: _make_bars(start, strong=(symbol != "6666")) for symbol in symbols}
    repo = _Repo(bars_by_symbol=bars)

    result, relaxed = generate_candidate_suggestions(
        repo=repo,
        symbols=symbols,
        trade_date=date.fromordinal(start.toordinal() + 79),
        target_count=8,
        data_freshness_required=True,
    )

    assert 5 <= len(result) <= 8
    assert all(item["symbol"] != "7777" for item in result)
    assert all(item["candidate_type"] in {"entry", "watch"} for item in result)
    assert all("industry_rotation_score" in item for item in result)
    assert all("industry_trend_label" in item for item in result)
    assert all("industry_trend_score" in item for item in result)
    assert all(any(code.startswith("industry_rotation_score=") for code in item["reason_codes"]) for item in result)
    assert all(any(code.startswith("industry_trend=") for code in item["reason_codes"]) for item in result)
    assert isinstance(relaxed, bool)


def test_generate_candidate_suggestions_aggressive_yields_more_entry():
    start = date(2024, 1, 1)
    symbols = ["1111", "2222", "3333", "4444", "5555", "6666", "7777"]
    bars = {symbol: _make_bars(start, strong=(symbol != "6666")) for symbol in symbols}
    repo = _Repo(bars_by_symbol=bars)
    trade_date = date.fromordinal(start.toordinal() + 79)

    balanced, _ = generate_candidate_suggestions(
        repo=repo,
        symbols=symbols,
        trade_date=trade_date,
        target_count=8,
        data_freshness_required=True,
        risk_profile="balanced",
    )
    aggressive, _ = generate_candidate_suggestions(
        repo=repo,
        symbols=symbols,
        trade_date=trade_date,
        target_count=8,
        data_freshness_required=True,
        risk_profile="aggressive",
    )

    balanced_entries = sum(1 for item in balanced if item["candidate_type"] == "entry")
    aggressive_entries = sum(1 for item in aggressive if item["candidate_type"] == "entry")
    assert aggressive_entries >= balanced_entries


def test_build_candidates_conservative_requires_cleaner_watch_candidate(monkeypatch):
    def fake_score_symbol(repo, symbol, trade_date, risk_profile, signal_evaluator):  # noqa: ANN001, ARG001
        return SimpleNamespace(
            symbol=symbol,
            name=f"Name{symbol}",
            price=100.0,
            signal={
                "entry": False,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": False,
                "close": 100.0,
                "ma60": 95.0,
                "volume": 2000.0,
                "volume_ma5": 1200.0,
            },
            last_bar_date="2024-04-01",
            data_quality="fresh",
            disposition_active=False,
            attention_active=False,
            chip_score=0.50,
            chip_up3=True,
            major_ratio=35.0,
            retail_ratio=12.0,
            concentration_proxy=35.0,
            fundamental_data_quality="fresh",
            revenue_score=0.6,
            quality_score=0.6,
            news_risk_score=0.4,
            liquidity_score=0.34,
            structure_score=0.60,
            total_score=0.62,
            risk_score=0.50,
            industry_group="電子/科技",
            industry_rotation_score=0.0,
            industry_trend_label="neutral",
            industry_trend_score=0.0,
            industry_short_return=0.0,
            industry_medium_return=0.0,
            industry_breadth=0.0,
            atr_pct=0.04,
            atr_heat="elevated",
        )

    monkeypatch.setattr(candidate_engine, "score_symbol", fake_score_symbol)
    monkeypatch.setattr(candidate_engine, "compute_group_trend_metrics", lambda *args, **kwargs: {})

    balanced = build_candidates(
        repo=object(),
        symbols=["2330"],
        trade_date=date(2024, 4, 1),
        data_freshness_required=True,
        risk_profile="balanced",
    )
    conservative = build_candidates(
        repo=object(),
        symbols=["2330"],
        trade_date=date(2024, 4, 1),
        data_freshness_required=True,
        risk_profile="conservative",
    )

    assert len(balanced) == 1
    assert balanced[0].candidate_type == "watch"
    assert conservative == []


def test_generate_candidate_suggestions_skips_excluded_symbols(monkeypatch):
    monkeypatch.setattr("app.alerts.candidate_engine.load_symbol_exclusions", lambda: {"1111": "manual"})
    start = date(2024, 1, 1)
    symbols = ["1111", "2222", "3333", "4444", "5555", "6666"]
    bars = {symbol: _make_bars(start, strong=(symbol != "6666")) for symbol in symbols}
    repo = _Repo(bars_by_symbol=bars)

    result, _ = generate_candidate_suggestions(
        repo=repo,
        symbols=symbols,
        trade_date=date.fromordinal(start.toordinal() + 79),
        target_count=8,
        data_freshness_required=True,
    )

    assert result
    assert all(item["symbol"] != "1111" for item in result)
