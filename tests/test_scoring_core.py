from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.services.scoring import score_symbol


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
    def __init__(self, bars: list[_Bar]):
        self._bars = bars

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return self._bars

    def get_active_disposition(self, symbol: str, on_date):  # noqa: ANN001, ARG002
        return {"active": False}

    def get_institutional_chip_rollup(self, symbol: str, end_date, lookback_days=5):  # noqa: ANN001, ARG002
        return {"institutional_net_buy_sum": 80_000_000.0}

    def get_institutional_chip(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return [_Chip(1.0, 1.0) for _ in range(30)]

    def is_chip_concentration_up(self, symbol: str, end_date, days=3):  # noqa: ANN001, ARG002
        return True

    def get_latest_shareholding(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _Shareholding(major_holder_ratio=35.0, retail_holder_ratio=12.0)

    def get_latest_holding_shares_per(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _Holding(concentration_proxy=40.0, dispersion_proxy=10.0)

    def get_instrument_name(self, symbol: str) -> str:
        return "測試股"


def _make_bars() -> list[_Bar]:
    start = date(2026, 1, 1)
    rows: list[_Bar] = []
    for idx in range(70):
        close = 100 + idx * 0.2
        rows.append(_Bar(date.fromordinal(start.toordinal() + idx), close * 0.99, close * 1.01, close * 0.98, close, 2000.0))
    return rows


def test_score_symbol_centralizes_quality_chip_structure_and_liquidity():
    bars = _make_bars()

    def fake_signal(_df):  # noqa: ANN001
        return {
            "entry": True,
            "trend_ok": True,
            "vol_ok": True,
            "trigger": True,
            "trigger_reason": "rsi3<20",
            "close": 120.0,
            "ma60": 100.0,
            "volume": 3000.0,
            "volume_ma5": 1500.0,
            "score": 0.9,
            "risk_score": 0.8,
        }

    scored = score_symbol(
        _Repo(bars),
        symbol="2330",
        trade_date=bars[-1].date,
        signal_evaluator=fake_signal,
    )

    assert scored is not None
    assert scored.symbol == "2330"
    assert scored.name == "測試股"
    assert scored.data_quality == "fresh"
    assert scored.chip_available is True
    assert scored.chip_up3 is True
    assert 0.0 <= scored.total_score <= 1.0
    assert scored.total_score > 0.7
