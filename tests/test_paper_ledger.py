from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.paper.ledger import PaperLedgerConfig, export_paper_ledger_report, run_symbol_paper_ledger


class DummyRepo:
    def __init__(self, rows):
        self._rows = rows

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001
        return self._rows


def _make_rows(n: int = 8):
    rows = []
    start = date(2024, 1, 2)
    for i in range(n):
        close = 100 + i
        rows.append(
            SimpleNamespace(
                date=date.fromordinal(start.toordinal() + i),
                open=close * 0.995,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=2000 + i * 10,
            )
        )
    return rows


def test_paper_ledger_t2_settlement_flow(monkeypatch):
    rows = _make_rows(8)
    repo = DummyRepo(rows)

    def fake_signal(df):  # noqa: ANN001
        # Buy only on first bar; then rely on time exit.
        return {"entry": len(df) == 1, "trigger_reason": "unit_test"}

    monkeypatch.setattr("app.paper.ledger.evaluate_entry_signal", fake_signal)
    result = run_symbol_paper_ledger(
        repo=repo,
        symbol="2330",
        start_date=rows[0].date,
        end_date=rows[-1].date,
        config=PaperLedgerConfig(initial_cash=10_000, hold_days=2, settlement_days=2, force_close_end=False),
    )

    assert result["passed"] is True
    assert result["summary"]["trade_count"] == 2
    snapshots = result["snapshots"]
    # Day 1 after buy: available cash reduced, settled cash unchanged.
    assert snapshots[0]["cash_available"] < 10_000
    assert snapshots[0]["cash_settled"] == 10_000
    # Buy settles on day 3 (idx2), so settled cash should decrease there.
    assert snapshots[2]["cash_settled"] < 10_000
    # Sell settles on day 4 (idx3), available cash should increase.
    assert snapshots[3]["cash_available"] > snapshots[2]["cash_available"]


def test_paper_ledger_rejects_trade_when_cash_below_min_trade(monkeypatch):
    rows = _make_rows(8)
    repo = DummyRepo(rows)
    monkeypatch.setattr("app.paper.ledger.evaluate_entry_signal", lambda df: {"entry": True, "trigger_reason": "always"})  # noqa: ANN001

    result = run_symbol_paper_ledger(
        repo=repo,
        symbol="2330",
        start_date=rows[0].date,
        end_date=rows[-1].date,
        config=PaperLedgerConfig(initial_cash=1_500),
    )

    assert result["passed"] is True
    assert result["summary"]["trade_count"] == 0
    assert result["summary"]["capital_guard_ok"] is True


def test_export_paper_ledger_report_writes_files(tmp_path):
    sample = {
        "summary": {"symbol": "2330", "initial_cash": 10000, "final_equity": 10100},
        "trades": [{"date": "2024-01-01", "side": "BUY", "qty": 1, "price": 100}],
        "snapshots": [{"date": "2024-01-01", "cash_available": 9900, "equity": 10000}],
    }
    export = export_paper_ledger_report(sample, output_dir=tmp_path, symbol="2330")
    files = export["files"]

    assert (tmp_path / files["summary_json"]).exists()
    assert (tmp_path / files["trades_csv"]).exists()
    assert (tmp_path / files["snapshots_csv"]).exists()
    assert (tmp_path / files["html_report"]).exists()
