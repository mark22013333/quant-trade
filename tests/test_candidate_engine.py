from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.alerts.candidate_engine import generate_candidate_suggestions


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
