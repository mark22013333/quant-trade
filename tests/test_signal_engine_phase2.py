from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.alerts.signal_engine import compute_daily_signal_suggestions


def _make_rows(closes: list[float], volumes: list[float]) -> list[SimpleNamespace]:
    assert len(closes) == len(volumes)
    rows: list[SimpleNamespace] = []
    start = date(2024, 1, 1)
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
    def __init__(self, bars_by_symbol: dict[str, list[SimpleNamespace]]):
        self.bars_by_symbol = bars_by_symbol

    def get_latest_0050_symbols(self) -> list[str]:
        return list(self.bars_by_symbol.keys())

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001
        return self.bars_by_symbol.get(symbol, [])


def test_signal_engine_generates_entry_suggestion_when_filters_match():
    closes = [100 + i * 0.4 for i in range(80)]
    closes[-4:] = [130, 126, 123, 122]  # keep above MA60 while forcing short-term oversold
    volumes = [1200] * 79 + [4000]  # last bar volume spike
    repo = DummyRepo({"2330": _make_rows(closes, volumes)})

    result = compute_daily_signal_suggestions(repo=repo, trade_date=date(2024, 4, 1), available_cash=10_000)

    assert len(result) >= 1
    first = result[0]
    assert first.symbol == "2330"
    assert first.qty > 0
    assert first.estimated_total_cost <= 10_000
    assert first.reason.startswith("entry:")


def test_signal_engine_respects_remaining_cash_across_multiple_symbols():
    closes = [100 + i * 0.4 for i in range(80)]
    closes[-4:] = [130, 126, 123, 122]
    volumes = [1200] * 79 + [4000]
    bars = _make_rows(closes, volumes)
    repo = DummyRepo({"2330": bars, "2317": bars})

    result = compute_daily_signal_suggestions(repo=repo, trade_date=date(2024, 4, 1), available_cash=3_000)

    assert len(result) == 1
    assert result[0].estimated_total_cost <= 3_000
