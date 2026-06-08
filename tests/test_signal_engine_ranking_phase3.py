from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.alerts.signal_engine import compute_daily_signal_suggestions


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
    def __init__(self, bars_by_symbol: dict[str, list[SimpleNamespace]]):
        self._bars = bars_by_symbol

    def get_latest_0050_symbols(self) -> list[str]:
        return sorted(self._bars.keys())

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001
        return self._bars[symbol]


def test_signal_engine_ranks_and_limits_open_positions():
    start = date(2024, 1, 1)
    closes_good = [100 + i * 0.4 for i in range(80)]
    closes_good[-4:] = [130, 126, 123, 122]
    # Make the weak candidate farther away from MA60 to reduce risk_score.
    closes_weak = [100 + i * 0.4 for i in range(80)]
    closes_weak[-4:] = [130, 126, 123, 128]
    strong_vol = [1200] * 79 + [7000]
    weak_vol = [1200] * 79 + [1900]
    repo = DummyRepo(
        {
            "2330": _make_rows(closes_good, strong_vol, start=start),
            "2317": _make_rows(closes_weak, weak_vol, start=start),
        }
    )

    suggestions = compute_daily_signal_suggestions(
        repo=repo,
        trade_date=date.fromordinal(start.toordinal() + 79),
        available_cash=10_000,
        rank_by="risk_adjusted_score",
        max_open_positions=1,
        data_freshness_required=True,
    )

    assert len(suggestions) == 1
    assert suggestions[0].symbol == "2330"
    assert suggestions[0].score >= suggestions[0].risk_score
    assert suggestions[0].data_quality == "fresh"
