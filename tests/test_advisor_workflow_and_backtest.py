from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.advisor.backtest import AdvisorBacktestService, export_advisor_backtest_report
from app.advisor.models import AdvisorDecision, AdvisorProposal, AdvisorRequest
from app.advisor.workflow import AdvisorWorkflowService


@dataclass
class _Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class _Repo:
    def __init__(self):
        self.decisions = []
        self.previews = []
        self.updated = []
        start = date(2026, 1, 1)
        self.bars = [
            _Bar(start + timedelta(days=idx), 100 + idx, 101 + idx, 99 + idx, 100 + idx, 1000)
            for idx in range(6)
        ]

    def get_daily_bars(self, symbol, start_date=None, end_date=None):  # noqa: ANN001
        rows = self.bars
        if start_date is not None:
            rows = [row for row in rows if row.date >= start_date]
        if end_date is not None:
            rows = [row for row in rows if row.date <= end_date]
        return rows

    def get_latest_daily_radar(self, count=50):  # noqa: ANN001
        return [{"symbol": "2330", "action_label": "ENTRY_READY", "price": 101, "entry_score": 80, "data_quality": "fresh"}]

    def add_order_preview_record(self, preview, intent):  # noqa: ANN001
        self.previews.append({"preview": preview, "intent": intent})

    def add_advisor_decision_record(self, decision, preview_id=""):  # noqa: ANN001
        self.decisions.append({"decision": decision, "preview_id": preview_id})

    def update_advisor_decision_status(self, decision_id, status, rejected_reason=""):  # noqa: ANN001
        self.updated.append((decision_id, status, rejected_reason))
        return True

    def upsert_daily_bars(self, symbol, records):  # noqa: ANN001
        raise AssertionError("source repo must not be mutated by isolated advisor backtest")


class _BuyAdvisor:
    name = "test"
    version = "v1"

    def advise(self, request: AdvisorRequest) -> AdvisorDecision:
        return AdvisorDecision(
            advisor_name=self.name,
            advisor_version=self.version,
            request=request,
            proposal=AdvisorProposal(
                symbol=request.symbol,
                action="buy",
                price=101,
                quantity=1,
                confidence=0.8,
                rationale="測試買進",
                risks=[{"code": "risk", "message": "測試風險"}],
            ),
        )


class _BuyThenSellAdvisor(_BuyAdvisor):
    def __init__(self):
        self.calls = 0

    def advise(self, request: AdvisorRequest) -> AdvisorDecision:
        self.calls += 1
        action = "buy" if self.calls == 1 else "sell" if self.calls == 2 else "hold"
        return AdvisorDecision(
            advisor_name=self.name,
            advisor_version=self.version,
            request=request,
            proposal=AdvisorProposal(
                symbol=request.symbol,
                action=action,
                price=101,
                quantity=1 if action in {"buy", "sell"} else None,
                confidence=0.8,
                rationale=f"測試{action}",
                risks=[{"code": "risk", "message": "測試風險"}],
            ),
        )


def test_advisor_workflow_creates_preview_for_actionable_proposal() -> None:
    repo = _Repo()
    service = AdvisorWorkflowService(repo=repo)

    result = service.create_proposal(symbol="2330", trade_date=date(2026, 1, 6), available_cash=10_000)

    assert result["decision"]["proposal"]["action"] == "buy"
    assert result["preview"]["preview_id"]
    assert len(repo.previews) == 1
    assert len(repo.decisions) == 1


def test_advisor_reject_updates_record_without_execution() -> None:
    repo = _Repo()
    result = AdvisorWorkflowService(repo=repo).reject_decision(decision_id="D1", reason="no thanks")

    assert result["status"] == "manual_rejected"
    assert repo.updated == [("D1", "manual_rejected", "no thanks")]


def test_advisor_backtest_uses_next_open_and_no_future_bars() -> None:
    repo = _Repo()
    seen_lengths: list[int] = []

    class Advisor(_BuyAdvisor):
        def advise(self, request: AdvisorRequest) -> AdvisorDecision:
            seen_lengths.append(len(request.bars))
            return super().advise(request)

    result = AdvisorBacktestService(repo=repo, advisor=Advisor()).run(
        symbol="2330",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 6),
        initial_cash=10_000,
        max_days=3,
    )

    assert result["passed"] is True
    assert result["trades"][0]["fill_date"] == "2026-01-02"
    assert result["trades"][0]["price"] == 101
    assert seen_lengths == [1, 2, 3]


def test_advisor_backtest_isolated_uses_temporary_sqlite_without_source_writes() -> None:
    repo = _Repo()

    result = AdvisorBacktestService(repo=repo, advisor=_BuyAdvisor()).run_isolated(
        symbol="2330",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 6),
        initial_cash=10_000,
        max_days=3,
    )

    assert result["passed"] is True
    assert result["isolation"]["database"] == "temporary_sqlite"
    assert result["isolation"]["source_rows"] == 6
    assert repo.decisions == []
    assert repo.previews == []


def test_advisor_backtest_settles_sell_cash_on_t_plus_2() -> None:
    repo = _Repo()

    result = AdvisorBacktestService(repo=repo, advisor=_BuyThenSellAdvisor()).run(
        symbol="2330",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 6),
        initial_cash=10_000,
        max_days=5,
        settlement_days=2,
    )

    assert result["passed"] is True
    sell = [trade for trade in result["trades"] if trade["side"] == "SELL"][0]
    assert sell["settlement_date"] == "2026-01-05"
    cash_by_date = {row["date"]: row["cash"] for row in result["equity_curve"]}
    receivable_by_date = {row["date"]: row["unsettled_sell_receivable"] for row in result["equity_curve"]}
    assert cash_by_date["2026-01-03"] == 9879.0
    assert receivable_by_date["2026-01-03"] == sell["net_proceeds"]
    assert cash_by_date["2026-01-06"] == 9960.0
    assert receivable_by_date["2026-01-06"] == 0.0
    assert result["summary"]["settlement_days"] == 2
    assert result["summary"]["pending_settlements"] == 0


def test_export_advisor_backtest_report_writes_artifacts(tmp_path) -> None:
    result = {
        "summary": {"final_equity": 10100.0, "trade_count": 1},
        "trades": [{"side": "BUY", "qty": 1, "price": 100.0}],
        "equity_curve": [{"date": "2026-01-02", "equity": 10100.0}],
    }

    export = export_advisor_backtest_report(result, output_dir=tmp_path, symbol="2330")

    assert (tmp_path / export["files"]["summary_json"]).exists()
    assert (tmp_path / export["files"]["trades_csv"]).exists()
    assert (tmp_path / export["files"]["equity_csv"]).exists()
    assert (tmp_path / export["files"]["html_report"]).exists()
