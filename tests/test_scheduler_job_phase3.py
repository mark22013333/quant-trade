from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.scheduler.jobs import run_daily_signal_job


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
