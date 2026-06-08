from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.features.snapshot_builder import rebuild_feature_snapshots


class DummyRepo:
    def __init__(self):
        self.feature_records: dict[str, list[dict]] = {}

    def get_latest_0050_symbols(self) -> list[str]:
        return ["2330"]

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001
        rows = []
        start = date(2024, 1, 2)
        for i in range(90):
            close = 100 + i * 0.3
            if i > 84:
                close = 120 - (i - 84) * 1.2
            volume = 1000 if i < 89 else 4000
            rows.append(
                SimpleNamespace(
                    date=date.fromordinal(start.toordinal() + i),
                    open=close * 0.99,
                    high=close * 1.02,
                    low=close * 0.98,
                    close=close,
                    volume=volume,
                )
            )
        return rows

    def get_institutional_chip(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001
        rows = []
        start = date(2024, 1, 2)
        for i in range(90):
            rows.append(
                SimpleNamespace(
                    date=date.fromordinal(start.toordinal() + i),
                    foreign_net_buy=300.0,
                    investment_trust_net_buy=120.0,
                    dealer_net_buy=40.0,
                )
            )
        return rows

    def get_broker_agg(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001
        rows = []
        start = date(2024, 1, 2)
        for i in range(90):
            rows.append(
                SimpleNamespace(
                    date=date.fromordinal(start.toordinal() + i),
                    concentration_proxy=float(50 + i * 3),
                )
            )
        return rows

    def get_disposition_periods(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return []

    def upsert_feature_snapshots(self, symbol: str, records: list[dict]) -> int:
        self.feature_records[symbol] = records
        return len(records)


def test_rebuild_feature_snapshots_generates_records():
    repo = DummyRepo()
    result = rebuild_feature_snapshots(
        repo=repo,
        start_date=date(2024, 1, 2),
        end_date=date(2024, 5, 31),
        symbols=["2330"],
    )

    assert result["failed_count"] == 0
    assert result["rows_upserted"] > 0
    records = repo.feature_records["2330"]
    assert len(records) > 0
    assert {"date", "close", "rsi3", "k9", "d9", "atr14", "entry_ready"}.issubset(records[0].keys())
