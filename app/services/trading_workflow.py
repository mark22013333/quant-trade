from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.alerts.signal_engine import SignalSuggestion, compute_daily_signal_suggestions


@dataclass(frozen=True)
class SignalPreviewWorkflow:
    trade_date: date
    available_cash: float = 10_000.0
    max_symbols: int = 50
    require_chip: bool = True
    block_disposition: bool = True
    rank_by: str = "score"
    max_open_positions: int | None = None
    data_freshness_required: bool = False
    chip_threshold_mode: str = "absolute"


def _suggestion_to_dict(item: SignalSuggestion) -> dict[str, Any]:
    return {
        "symbol": item.symbol,
        "price": item.price,
        "qty": item.qty,
        "estimated_total_cost": item.estimated_total_cost,
        "reason": item.reason,
        "reason_codes": item.reason_codes,
        "score": item.score,
        "risk_score": item.risk_score,
        "data_quality": item.data_quality,
        "last_bar_date": item.last_bar_date,
        "chip_net_buy_5d": item.chip_net_buy_5d,
        "chip_concentration_up3": item.chip_concentration_up3,
        "disposition_active": item.disposition_active,
        "chip_ok": item.chip_ok,
        "rsi3": item.rsi3,
        "k": item.k,
        "d": item.d,
        "close": item.close,
        "ma60": item.ma60,
        "volume": item.volume,
        "volume_ma5": item.volume_ma5,
    }


def build_signal_preview_payload(repo, request: SignalPreviewWorkflow) -> dict[str, Any]:
    suggestions = compute_daily_signal_suggestions(
        repo=repo,
        trade_date=request.trade_date,
        available_cash=float(request.available_cash),
        max_symbols=int(request.max_symbols),
        require_chip=bool(request.require_chip),
        block_disposition=bool(request.block_disposition),
        rank_by=str(request.rank_by),
        max_open_positions=request.max_open_positions,
        data_freshness_required=bool(request.data_freshness_required),
        chip_threshold_mode=str(request.chip_threshold_mode),
    )
    rows = [_suggestion_to_dict(item) for item in suggestions]
    return {
        "passed": True,
        "trade_date": request.trade_date.isoformat(),
        "suggestions_count": len(rows),
        "suggestions": rows,
        "rank_by": str(request.rank_by),
        "max_open_positions": request.max_open_positions,
        "data_freshness_required": bool(request.data_freshness_required),
        "chip_threshold_mode": str(request.chip_threshold_mode),
        "message": f"訊號預覽完成：{len(rows)} 檔符合條件",
    }
