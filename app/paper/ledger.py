from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from app.alerts.signal_engine import evaluate_entry_signal
from app.backtest.costs import estimate_fee, estimate_tax
from app.backtest.execution_model import ExecutionModelConfig, PaperLedgerExecutionModel
from app.backtest.sizer_absolute_capital import AbsoluteSizerConfig, compute_order_size

if TYPE_CHECKING:
    from app.db.repository import TradingRepository
else:
    TradingRepository = Any


@dataclass
class PaperLedgerConfig:
    initial_cash: float = 10_000.0
    min_trade_value: float = 2_000.0
    max_allocation_per_trade: float = 5_000.0
    fee_rate: float = 0.001425
    min_fee: float = 20.0
    tax_rate: float = 0.003
    settlement_days: int = 2
    hold_days: int = 5
    force_close_end: bool = True


@dataclass
class PendingSettlement:
    settlement_date: date
    side: str
    delta_available: float
    delta_settled: float
    amount: float
    memo: str


def _normalize_symbol(symbol: str) -> str:
    code = (symbol or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def _bars_to_frame(rows: list[Any]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": row.date,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
            for row in rows
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").set_index("date")
    return frame


def _calc_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def _settlement_date(rows: list[Any], current_idx: int, settlement_days: int) -> date:
    target_idx = current_idx + int(settlement_days)
    if target_idx < len(rows):
        return rows[target_idx].date
    # Out of sample tail fallback.
    return rows[current_idx].date + timedelta(days=max(1, int(settlement_days)))


def run_symbol_paper_ledger(
    repo: TradingRepository,
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    config: PaperLedgerConfig | None = None,
) -> dict[str, Any]:
    cfg = config or PaperLedgerConfig()
    code = _normalize_symbol(symbol)
    rows = repo.get_daily_bars(symbol=code, start_date=start_date, end_date=end_date)
    if not rows:
        return {
            "passed": False,
            "symbol": code,
            "message": "no_data",
            "summary": {},
            "trades": [],
            "snapshots": [],
        }

    frame = _bars_to_frame(rows)
    atr14 = _calc_atr(frame, period=14)

    sizer_cfg = AbsoluteSizerConfig(
        min_trade_value=float(cfg.min_trade_value),
        max_allocation_per_trade=float(cfg.max_allocation_per_trade),
        fee_rate=float(cfg.fee_rate),
        min_fee=float(cfg.min_fee),
    )

    cash_available = float(cfg.initial_cash)
    cash_settled = float(cfg.initial_cash)
    pending: list[PendingSettlement] = []
    trades: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []

    position_qty = 0
    entry_price = 0.0
    entry_atr = 0.0
    entry_idx = -1
    entry_total_cost = 0.0

    for idx, row in enumerate(rows):
        day = row.date
        close = float(row.close)
        notes: list[str] = []

        # Apply T+n settlements due on current trading day.
        due = [item for item in pending if item.settlement_date <= day]
        if due:
            pending = [item for item in pending if item.settlement_date > day]
            for item in due:
                cash_available += float(item.delta_available)
                cash_settled += float(item.delta_settled)
                notes.append(f"settle_{item.side}:{item.delta_settled:+.0f}")

        # Exit logic for existing position.
        if position_qty > 0:
            holding_days = idx - entry_idx + 1
            pnl_per_share = close - entry_price
            exit_reason = None
            if pnl_per_share >= (2.0 * entry_atr):
                exit_reason = "take_profit_atr2"
            elif pnl_per_share <= (-1.0 * entry_atr):
                exit_reason = "stop_loss_atr1"
            elif holding_days >= int(cfg.hold_days):
                exit_reason = "time_exit"

            if exit_reason is None and idx == len(rows) - 1 and cfg.force_close_end:
                exit_reason = "end_of_period"

            if exit_reason:
                order_value = close * position_qty
                fee = estimate_fee(order_value, fee_rate=cfg.fee_rate, min_fee=cfg.min_fee)
                tax = estimate_tax(order_value, tax_rate=cfg.tax_rate)
                net_cash = float(order_value - fee - tax)
                settle_date = _settlement_date(rows, idx, cfg.settlement_days)
                pending.append(
                    PendingSettlement(
                        settlement_date=settle_date,
                        side="sell",
                        delta_available=net_cash,
                        delta_settled=net_cash,
                        amount=net_cash,
                        memo=exit_reason,
                    )
                )
                realized_pnl = float(net_cash - entry_total_cost)
                trades.append(
                    {
                        "date": day.isoformat(),
                        "side": "SELL",
                        "symbol": code,
                        "qty": int(position_qty),
                        "price": float(close),
                        "order_value": float(order_value),
                        "fee": float(fee),
                        "tax": float(tax),
                        "net_cash": float(net_cash),
                        "settlement_date": settle_date.isoformat(),
                        "reason": exit_reason,
                        "realized_pnl": realized_pnl,
                    }
                )
                notes.append(f"sell:{position_qty}@{close:.2f}:{exit_reason}")
                position_qty = 0
                entry_price = 0.0
                entry_atr = 0.0
                entry_idx = -1
                entry_total_cost = 0.0

        # Entry logic when no position.
        allow_entry = idx < (len(rows) - 1) or (not cfg.force_close_end)
        if position_qty == 0 and allow_entry:
            hist = frame.iloc[: idx + 1]
            signal = evaluate_entry_signal(hist)
            if signal.get("entry"):
                size = compute_order_size(available_cash=float(cash_available), current_price=float(close), config=sizer_cfg)
                if size.get("accepted"):
                    qty = int(size["qty"])
                    order_value = close * qty
                    fee = estimate_fee(order_value, fee_rate=cfg.fee_rate, min_fee=cfg.min_fee)
                    total_cost = float(order_value + fee)
                    settle_date = _settlement_date(rows, idx, cfg.settlement_days)

                    # Reserve cash immediately to avoid over-buying.
                    cash_available -= total_cost
                    pending.append(
                        PendingSettlement(
                            settlement_date=settle_date,
                            side="buy",
                            delta_available=0.0,
                            delta_settled=-total_cost,
                            amount=total_cost,
                            memo=str(signal.get("trigger_reason", "entry")),
                        )
                    )
                    trades.append(
                        {
                            "date": day.isoformat(),
                            "side": "BUY",
                            "symbol": code,
                            "qty": qty,
                            "price": float(close),
                            "order_value": float(order_value),
                            "fee": float(fee),
                            "tax": 0.0,
                            "estimated_total_cost": total_cost,
                            "settlement_date": settle_date.isoformat(),
                            "reason": f"entry:{signal.get('trigger_reason', 'signal')}",
                        }
                    )
                    notes.append(f"buy:{qty}@{close:.2f}")
                    position_qty = qty
                    entry_price = float(close)
                    atr_now = atr14.iloc[idx]
                    if pd.isna(atr_now) or float(atr_now) <= 0:
                        atr_now = max(close * 0.02, 0.01)
                    entry_atr = float(atr_now)
                    entry_idx = idx
                    entry_total_cost = total_cost

        unsettled_buy_payable = float(sum(-item.delta_settled for item in pending if item.side == "buy" and item.delta_settled < 0))
        unsettled_sell_receivable = float(sum(item.delta_available for item in pending if item.side == "sell" and item.delta_available > 0))
        market_value = float(position_qty * close)
        equity = float(cash_available + market_value + unsettled_sell_receivable)

        snapshots.append(
            {
                "date": day.isoformat(),
                "close": float(close),
                "cash_available": float(cash_available),
                "cash_settled": float(cash_settled),
                "unsettled_buy_payable": unsettled_buy_payable,
                "unsettled_sell_receivable": unsettled_sell_receivable,
                "position_qty": int(position_qty),
                "market_value": market_value,
                "equity": equity,
                "capital_guard_ok": bool(cash_available >= -1e-9),
                "notes": " | ".join(notes),
            }
        )

    sell_trades = [t for t in trades if t.get("side") == "SELL"]
    realized = [float(t.get("realized_pnl", 0.0)) for t in sell_trades]
    wins = [value for value in realized if value > 0]
    final = snapshots[-1] if snapshots else {}
    summary = {
        "symbol": code,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "initial_cash": float(cfg.initial_cash),
        "final_cash_available": float(final.get("cash_available", cfg.initial_cash)),
        "final_cash_settled": float(final.get("cash_settled", cfg.initial_cash)),
        "final_market_value": float(final.get("market_value", 0.0)),
        "final_equity": float(final.get("equity", cfg.initial_cash)),
        "trade_count": len(trades),
        "buy_count": sum(1 for t in trades if t.get("side") == "BUY"),
        "sell_count": len(sell_trades),
        "closed_round_trips": len(realized),
        "win_rate": (len(wins) / len(realized)) if realized else None,
        "total_realized_pnl": float(sum(realized)),
        "pending_settlements": len(pending),
        "capital_guard_ok": bool(min(float(s["cash_available"]) for s in snapshots) >= -1e-9) if snapshots else True,
        "execution_model": PaperLedgerExecutionModel(
            ExecutionModelConfig(
                commission_rate=float(cfg.fee_rate),
                min_commission_fee=float(cfg.min_fee),
                tax_rate=float(cfg.tax_rate),
                settlement_days=int(cfg.settlement_days),
            )
        ).describe(),
    }
    return {
        "passed": True,
        "symbol": code,
        "message": "paper_ledger_simulated",
        "summary": summary,
        "trades": trades,
        "snapshots": snapshots,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _build_html(summary: dict[str, Any], snapshots: list[dict[str, Any]], trades: list[dict[str, Any]]) -> str:
    summary_json = json.dumps(summary, ensure_ascii=False)
    snapshots_json = json.dumps(snapshots, ensure_ascii=False)
    trades_json = json.dumps(trades[-40:], ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Paper Ledger</title>
  <style>
    body {{
      margin: 0;
      font-family: "Noto Sans TC", sans-serif;
      background: linear-gradient(150deg, #f2f6ff, #eef5f4);
      color: #192035;
      padding: 20px;
    }}
    .card {{
      background: #fff;
      border: 1px solid rgba(25, 32, 53, 0.12);
      border-radius: 14px;
      padding: 14px;
      margin-bottom: 14px;
      box-shadow: 0 8px 24px rgba(20, 28, 46, 0.08);
    }}
    .title {{
      margin: 0;
      font-size: 22px;
      font-weight: 700;
    }}
    .sub {{
      color: #52607a;
      font-size: 13px;
      margin-top: 4px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px;
      margin-top: 10px;
    }}
    .item {{
      border: 1px solid rgba(25, 32, 53, 0.1);
      border-radius: 10px;
      padding: 8px;
      background: #f8fbff;
    }}
    .item .k {{
      color: #607294;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .item .v {{
      font-family: "JetBrains Mono", monospace;
      font-size: 15px;
      margin-top: 4px;
    }}
    canvas {{
      width: 100%;
      height: 300px;
      border: 1px solid rgba(25, 32, 53, 0.15);
      border-radius: 10px;
      background: #fff;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}
    th, td {{
      border-bottom: 1px solid rgba(25, 32, 53, 0.1);
      text-align: left;
      padding: 6px 8px;
      white-space: nowrap;
    }}
    th {{
      background: #eef4ff;
      position: sticky;
      top: 0;
    }}
    .table-wrap {{
      max-height: 360px;
      overflow: auto;
      border: 1px solid rgba(25, 32, 53, 0.12);
      border-radius: 10px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1 class="title">模擬本金帳本（T+2）</h1>
    <p class="sub">顯示可用現金、已交割現金、權益與未交割款項。策略訊號：MA60 + 量能 + RSI3/KD。</p>
    <div id="summary" class="grid"></div>
  </div>

  <div class="card">
    <h2 class="title" style="font-size:18px;">資金曲線</h2>
    <canvas id="chart" width="1200" height="320"></canvas>
  </div>

  <div class="card">
    <h2 class="title" style="font-size:18px;">最近交易</h2>
    <div class="table-wrap"><table id="trade-table"></table></div>
  </div>

  <script>
    const summary = {summary_json};
    const snapshots = {snapshots_json};
    const trades = {trades_json};

    function fmt(n) {{
      if (typeof n !== 'number' || Number.isNaN(n)) return '-';
      return n.toLocaleString('zh-TW', {{ maximumFractionDigits: 2 }});
    }}

    const summaryOrder = [
      'symbol', 'start_date', 'end_date', 'initial_cash', 'final_cash_available',
      'final_cash_settled', 'final_equity', 'trade_count', 'buy_count', 'sell_count',
      'win_rate', 'total_realized_pnl', 'capital_guard_ok'
    ];
    const summaryEl = document.getElementById('summary');
    summaryOrder.forEach((key) => {{
      if (!(key in summary)) return;
      const div = document.createElement('div');
      div.className = 'item';
      const label = document.createElement('div');
      label.className = 'k';
      label.textContent = key;
      const value = document.createElement('div');
      value.className = 'v';
      const raw = summary[key];
      if (typeof raw === 'number') {{
        value.textContent = key.includes('rate') ? `${{(raw * 100).toFixed(2)}}%` : fmt(raw);
      }} else {{
        value.textContent = String(raw);
      }}
      div.appendChild(label);
      div.appendChild(value);
      summaryEl.appendChild(div);
    }});

    function drawLineChart() {{
      const canvas = document.getElementById('chart');
      const ctx = canvas.getContext('2d');
      const w = canvas.width;
      const h = canvas.height;
      const pad = 34;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, w, h);

      if (!snapshots || snapshots.length < 2) {{
        ctx.fillStyle = '#52607a';
        ctx.font = '14px sans-serif';
        ctx.fillText('資料不足，無法繪圖', 20, 40);
        return;
      }}

      const series = [
        {{ key: 'cash_available', color: '#1d6ef2' }},
        {{ key: 'cash_settled', color: '#0f8f73' }},
        {{ key: 'equity', color: '#c26e10' }},
      ];
      const values = [];
      snapshots.forEach(s => series.forEach(ss => values.push(Number(s[ss.key]))));
      const minV = Math.min(...values);
      const maxV = Math.max(...values);
      const span = Math.max(maxV - minV, 1);

      ctx.strokeStyle = '#d4dff4';
      ctx.lineWidth = 1;
      for (let i = 0; i < 4; i += 1) {{
        const y = pad + ((h - pad * 2) * i) / 3;
        ctx.beginPath();
        ctx.moveTo(pad, y);
        ctx.lineTo(w - pad, y);
        ctx.stroke();
      }}

      series.forEach((ss, idx) => {{
        ctx.strokeStyle = ss.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        snapshots.forEach((s, i) => {{
          const x = pad + ((w - pad * 2) * i) / (snapshots.length - 1);
          const y = h - pad - ((Number(s[ss.key]) - minV) / span) * (h - pad * 2);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }});
        ctx.stroke();

        ctx.fillStyle = ss.color;
        ctx.font = '12px sans-serif';
        ctx.fillText(ss.key, pad + idx * 160, 18);
      }});
    }}

    function renderTradeTable() {{
      const el = document.getElementById('trade-table');
      if (!trades.length) {{
        el.innerHTML = '<tr><td>無交易資料</td></tr>';
        return;
      }}
      const columns = Object.keys(trades[0]);
      const thead = `<thead><tr>${{columns.map(c => `<th>${{c}}</th>`).join('')}}</tr></thead>`;
      const tbody = `<tbody>${{trades.map(row => `<tr>${{columns.map(c => `<td>${{row[c] ?? ''}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
      el.innerHTML = thead + tbody;
    }}

    drawLineChart();
    renderTradeTable();
  </script>
</body>
</html>
"""


def export_paper_ledger_report(
    result: dict[str, Any],
    *,
    output_dir: Path,
    symbol: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"paper_ledger_{_normalize_symbol(symbol)}_{stamp}"

    summary_path = output_dir / f"{base}_summary.json"
    trades_path = output_dir / f"{base}_trades.csv"
    snapshots_path = output_dir / f"{base}_snapshots.csv"
    html_path = output_dir / f"{base}.html"

    summary = dict(result.get("summary") or {})
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(trades_path, list(result.get("trades") or []))
    _write_csv(snapshots_path, list(result.get("snapshots") or []))
    html_path.write_text(
        _build_html(summary=summary, snapshots=list(result.get("snapshots") or []), trades=list(result.get("trades") or [])),
        encoding="utf-8",
    )

    return {
        "files": {
            "summary_json": summary_path.name,
            "trades_csv": trades_path.name,
            "snapshots_csv": snapshots_path.name,
            "html_report": html_path.name,
        },
        "urls": {
            "summary_json": f"/reports/{summary_path.name}",
            "trades_csv": f"/reports/{trades_path.name}",
            "snapshots_csv": f"/reports/{snapshots_path.name}",
            "html_report": f"/reports/{html_path.name}",
        },
    }
