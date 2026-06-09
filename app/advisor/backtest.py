from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import PROJECT_ROOT
from app.db.repository import TradingRepository
from app.db.schema import initialize_schema
from app.security.reporting import csv_join_row, sanitize_report_payload
from app.advisor.interface import TradingAdvisor
from app.advisor.models import AdvisorRequest
from app.advisor.stub import StubTradingAdvisor
from app.backtest.costs import estimate_buy_cost_breakdown, estimate_sell_proceeds_breakdown
from app.execution.models import normalize_symbol


@dataclass
class _Lot:
    qty: int
    unit_cost: float
    total_cost: float


@dataclass
class _PendingSettlement:
    settlement_date: date
    amount: float


def _settlement_date(rows: list[Any], current_idx: int, settlement_days: int) -> date:
    target_idx = current_idx + int(settlement_days)
    if target_idx < len(rows):
        return rows[target_idx].date
    return rows[current_idx].date + timedelta(days=max(1, int(settlement_days)))


class AdvisorBacktestService:
    def __init__(self, *, repo: Any, advisor: TradingAdvisor | None = None):
        self.repo = repo
        self.advisor = advisor or StubTradingAdvisor()

    def run_isolated(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        initial_cash: float = 10_000.0,
        max_days: int = 20,
        settlement_days: int = 2,
    ) -> dict[str, Any]:
        code = normalize_symbol(symbol)
        source_rows = list(self.repo.get_daily_bars(symbol=code, end_date=end_date) or [])
        if len([row for row in source_rows if row.date >= start_date]) < 2:
            return {
                "passed": False,
                "message": "insufficient_data",
                "trades": [],
                "equity_curve": [],
                "summary": {},
                "isolation": {"database": "temporary_sqlite", "source_rows": len(source_rows)},
            }
        with tempfile.TemporaryDirectory(prefix="advisor_backtest_", dir=str(PROJECT_ROOT / "data")) as tmp_dir:
            db_path = Path(tmp_dir) / "advisor_backtest.sqlite"
            engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
            initialize_schema(engine)
            Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
            with Session() as session:
                temp_repo = TradingRepository(session)
                temp_repo.upsert_daily_bars(
                    code,
                    [
                        {
                            "date": row.date,
                            "open": float(row.open),
                            "high": float(row.high),
                            "low": float(row.low),
                            "close": float(row.close),
                            "volume": float(row.volume),
                            "source": "advisor_backtest_snapshot",
                        }
                        for row in source_rows
                    ],
                )
                session.commit()
                result = AdvisorBacktestService(repo=temp_repo, advisor=self.advisor).run(
                    symbol=code,
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=initial_cash,
                    max_days=max_days,
                    settlement_days=settlement_days,
                )
        result["isolation"] = {"database": "temporary_sqlite", "source_rows": len(source_rows)}
        return result

    def run(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        initial_cash: float = 10_000.0,
        max_days: int = 20,
        settlement_days: int = 2,
    ) -> dict[str, Any]:
        code = normalize_symbol(symbol)
        rows = self.repo.get_daily_bars(symbol=code, start_date=start_date, end_date=end_date)
        rows = list(rows or [])[: max(2, min(int(max_days or 20) + 1, 61))]
        if len(rows) < 2:
            return {"passed": False, "message": "insufficient_data", "trades": [], "equity_curve": [], "summary": {}}

        cash = float(initial_cash)
        lots: list[_Lot] = []
        pending: list[_PendingSettlement] = []
        trades: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        realized_pnls: list[float] = []

        for idx in range(len(rows) - 1):
            day = rows[idx].date
            due = [item for item in pending if item.settlement_date <= day]
            if due:
                pending = [item for item in pending if item.settlement_date > day]
                cash += sum(float(item.amount) for item in due)
            next_open = float(rows[idx + 1].open)
            request = self._build_request(code=code, trade_date=day, available_cash=cash, position_qty=sum(lot.qty for lot in lots))
            decision = self.advisor.advise(request)
            proposal = decision.proposal
            if decision.status == "accepted" and proposal is not None and proposal.action == "buy":
                qty = int(proposal.quantity or 0)
                cost = estimate_buy_cost_breakdown(next_open * qty)
                if qty > 0 and cash >= float(cost["total_cost"]):
                    cash -= float(cost["total_cost"])
                    lots.append(_Lot(qty=qty, unit_cost=float(cost["total_cost"]) / qty, total_cost=float(cost["total_cost"])))
                    trades.append(
                        {
                            "decision_date": day.isoformat(),
                            "fill_date": rows[idx + 1].date.isoformat(),
                            "side": "BUY",
                            "qty": qty,
                            "price": next_open,
                            "fee": float(cost["fee"]),
                            "tax": 0.0,
                            "total_cost": float(cost["total_cost"]),
                            "rationale": proposal.rationale,
                        }
                    )
            elif decision.status == "accepted" and proposal is not None and proposal.action == "sell":
                qty_to_sell = min(int(proposal.quantity or 0), sum(lot.qty for lot in lots))
                if qty_to_sell > 0:
                    matched_cost = 0.0
                    remaining = qty_to_sell
                    while remaining > 0 and lots:
                        lot = lots[0]
                        take = min(remaining, lot.qty)
                        matched_cost += lot.unit_cost * take
                        lot.qty -= take
                        remaining -= take
                        if lot.qty == 0:
                            lots.pop(0)
                    proceeds = estimate_sell_proceeds_breakdown(next_open * qty_to_sell)
                    settlement_date = _settlement_date(rows, idx + 1, settlement_days)
                    pending.append(_PendingSettlement(settlement_date=settlement_date, amount=float(proceeds["net_proceeds"])))
                    realized = float(proceeds["net_proceeds"] - matched_cost)
                    realized_pnls.append(realized)
                    trades.append(
                        {
                            "decision_date": day.isoformat(),
                            "fill_date": rows[idx + 1].date.isoformat(),
                            "side": "SELL",
                            "qty": qty_to_sell,
                            "price": next_open,
                            "fee": float(proceeds["fee"]),
                            "tax": float(proceeds["tax"]),
                            "net_proceeds": float(proceeds["net_proceeds"]),
                            "settlement_date": settlement_date.isoformat(),
                            "realized_pnl": realized,
                            "rationale": proposal.rationale,
                        }
                    )
            mark_price = float(rows[idx + 1].close)
            market_value = sum(lot.qty for lot in lots) * mark_price
            unsettled_sell_receivable = sum(float(item.amount) for item in pending)
            equity_curve.append(
                {
                    "date": rows[idx + 1].date.isoformat(),
                    "cash": cash,
                    "position_qty": sum(lot.qty for lot in lots),
                    "market_value": market_value,
                    "unsettled_sell_receivable": unsettled_sell_receivable,
                    "equity": cash + market_value + unsettled_sell_receivable,
                }
            )

        wins = [value for value in realized_pnls if value > 0]
        final_equity = float(equity_curve[-1]["equity"]) if equity_curve else float(initial_cash)
        return {
            "passed": True,
            "message": "advisor_backtest_completed",
            "symbol": code,
            "summary": {
                "initial_cash": float(initial_cash),
                "final_equity": final_equity,
                "total_return": (final_equity - float(initial_cash)) / float(initial_cash) if initial_cash else 0.0,
                "trade_count": len(trades),
                "closed_round_trips": len(realized_pnls),
                "win_rate": (len(wins) / len(realized_pnls)) if realized_pnls else None,
                "expectancy": (sum(realized_pnls) / len(realized_pnls)) if realized_pnls else None,
                "pending_settlements": len(pending),
                "settlement_days": int(settlement_days),
            },
            "trades": trades,
            "equity_curve": equity_curve,
        }

    def _build_request(self, *, code: str, trade_date: date, available_cash: float, position_qty: int) -> AdvisorRequest:
        bars = []
        for row in self.repo.get_daily_bars(symbol=code, end_date=trade_date)[-80:]:
            bars.append(
                {
                    "date": row.date.isoformat(),
                    "open": float(row.open),
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                    "volume": float(row.volume),
                }
            )
        return AdvisorRequest(
            symbol=code,
            trade_date=trade_date,
            available_cash=float(available_cash),
            position_qty=int(position_qty),
            bars=bars,
            radar_item={},
            constraints={"no_future_data": True, "fill": "next_open"},
        )


def _safe_symbol(symbol: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(symbol or "SYMBOL"))
    return cleaned.strip("_") or "SYMBOL"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)
    lines = [csv_join_row(keys)]
    for row in rows:
        lines.append(csv_join_row([row.get(key, "") for key in keys]))
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_html(summary: dict[str, Any], trades: list[dict[str, Any]], equity_curve: list[dict[str, Any]]) -> str:
    summary_json = json.dumps(summary, ensure_ascii=False)
    trades_json = json.dumps(trades[-80:], ensure_ascii=False)
    equity_json = json.dumps(equity_curve, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Advisor Backtest</title>
  <style>
    body {{ margin:0; padding:20px; font-family:"Noto Sans TC", system-ui, sans-serif; background:#f5f7fb; color:#172033; }}
    .panel {{ background:#fff; border:1px solid #d8deea; border-radius:8px; padding:14px; margin-bottom:14px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }}
    .metric {{ border:1px solid #e4e8f0; border-radius:6px; padding:10px; }}
    .metric span {{ display:block; color:#647084; font-size:12px; }}
    .metric strong {{ font-size:20px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid #e4e8f0; padding:8px; text-align:left; vertical-align:top; }}
    canvas {{ width:100%; height:260px; }}
  </style>
</head>
<body>
  <div class="panel">
    <h1>Advisor Backtest</h1>
    <div class="grid" id="metrics"></div>
  </div>
  <div class="panel"><canvas id="equity" width="1200" height="260"></canvas></div>
  <div class="panel"><h2>交易明細</h2><div id="trades"></div></div>
  <script>
    const summary = {summary_json};
    const trades = {trades_json};
    const equity = {equity_json};
    function fmt(v) {{ return typeof v === 'number' ? v.toLocaleString('zh-TW', {{ maximumFractionDigits: 4 }}) : (v ?? ''); }}
    document.getElementById('metrics').innerHTML = Object.entries(summary).map(([k,v]) => `<div class="metric"><span>${{k}}</span><strong>${{fmt(v)}}</strong></div>`).join('');
    const canvas = document.getElementById('equity');
    const ctx = canvas.getContext('2d');
    const values = equity.map(x => Number(x.equity || 0));
    if (values.length) {{
      const min = Math.min(...values), max = Math.max(...values), pad = 28;
      ctx.clearRect(0,0,canvas.width,canvas.height);
      ctx.strokeStyle = '#2563eb'; ctx.lineWidth = 2; ctx.beginPath();
      values.forEach((v,i) => {{
        const x = pad + (i / Math.max(values.length - 1, 1)) * (canvas.width - pad * 2);
        const y = canvas.height - pad - ((v - min) / Math.max(max - min, 1)) * (canvas.height - pad * 2);
        if (i === 0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      }});
      ctx.stroke();
    }}
    if (trades.length) {{
      const cols = Object.keys(trades[0]);
      document.getElementById('trades').innerHTML = `<table><thead><tr>${{cols.map(c=>`<th>${{c}}</th>`).join('')}}</tr></thead><tbody>${{trades.map(r=>`<tr>${{cols.map(c=>`<td>${{r[c] ?? ''}}</td>`).join('')}}</tr>`).join('')}}</tbody></table>`;
    }} else {{
      document.getElementById('trades').textContent = '沒有交易';
    }}
  </script>
</body>
</html>
"""


def export_advisor_backtest_report(
    result: dict[str, Any],
    *,
    output_dir: Path,
    symbol: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"advisor_backtest_{_safe_symbol(symbol)}_{stamp}"
    summary_path = output_dir / f"{base}_summary.json"
    trades_path = output_dir / f"{base}_trades.csv"
    equity_path = output_dir / f"{base}_equity.csv"
    html_path = output_dir / f"{base}.html"

    safe_result = sanitize_report_payload(result)
    summary = dict((safe_result or {}).get("summary") or {})
    trades = list((safe_result or {}).get("trades") or [])
    equity_curve = list((safe_result or {}).get("equity_curve") or [])
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(trades_path, trades)
    _write_csv(equity_path, equity_curve)
    html_path.write_text(_build_html(summary=summary, trades=trades, equity_curve=equity_curve), encoding="utf-8")
    return {
        "files": {
            "summary_json": summary_path.name,
            "trades_csv": trades_path.name,
            "equity_csv": equity_path.name,
            "html_report": html_path.name,
        },
        "urls": {
            "summary_json": f"/reports/{summary_path.name}",
            "trades_csv": f"/reports/{trades_path.name}",
            "equity_csv": f"/reports/{equity_path.name}",
            "html_report": f"/reports/{html_path.name}",
        },
    }
