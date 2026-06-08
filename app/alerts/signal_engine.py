from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd

from app.backtest.sizer_absolute_capital import AbsoluteSizerConfig, compute_order_size

if TYPE_CHECKING:
    from app.db.repository import TradingRepository
else:
    TradingRepository = Any


@dataclass
class SignalSuggestion:
    symbol: str
    price: float
    qty: int
    estimated_total_cost: float
    reason: str
    rsi3: float
    k: float
    d: float
    close: float
    ma60: float
    volume: float
    volume_ma5: float
    reason_codes: list[str] = field(default_factory=list)
    score: float = 0.0
    risk_score: float = 0.0
    data_quality: str = "unknown"
    last_bar_date: str | None = None
    chip_net_buy_5d: float = 0.0
    chip_concentration_up3: bool = False
    disposition_active: bool = False
    chip_ok: bool = False


def _bars_to_frame(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
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


def _calc_rsi(close: pd.Series, period: int = 3) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100.0)


def _calc_kd(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 9,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(window=period, min_periods=period).min()
    highest_high = high.rolling(window=period, min_periods=period).max()
    rsv = ((close - lowest_low) / (highest_high - lowest_low).replace(0.0, float("nan")) * 100.0).fillna(50.0)
    k = rsv.ewm(alpha=1 / smooth_k, adjust=False).mean()
    d = k.ewm(alpha=1 / smooth_d, adjust=False).mean()
    return k, d


def evaluate_entry_signal(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 65:
        return {"entry": False, "reason": "insufficient_data"}

    close = df["close"]
    volume = df["volume"]
    ma60 = close.rolling(window=60, min_periods=60).mean()
    vol_ma5 = volume.rolling(window=5, min_periods=5).mean()
    rsi3 = _calc_rsi(close, period=3)
    k, d = _calc_kd(df["high"], df["low"], close, period=9, smooth_k=3, smooth_d=3)

    latest = df.index[-1]
    prev = df.index[-2]
    latest_close = float(close.loc[latest])
    latest_volume = float(volume.loc[latest])
    latest_ma60 = float(ma60.loc[latest]) if pd.notna(ma60.loc[latest]) else float("nan")
    latest_vol_ma5 = float(vol_ma5.loc[latest]) if pd.notna(vol_ma5.loc[latest]) else float("nan")
    latest_rsi3 = float(rsi3.loc[latest]) if pd.notna(rsi3.loc[latest]) else float("nan")
    latest_k = float(k.loc[latest]) if pd.notna(k.loc[latest]) else float("nan")
    latest_d = float(d.loc[latest]) if pd.notna(d.loc[latest]) else float("nan")
    prev_k = float(k.loc[prev]) if pd.notna(k.loc[prev]) else latest_k
    prev_d = float(d.loc[prev]) if pd.notna(d.loc[prev]) else latest_d

    trend_ok = pd.notna(latest_ma60) and (latest_close > latest_ma60)
    vol_ok = pd.notna(latest_vol_ma5) and (latest_volume > latest_vol_ma5 * 1.5)
    rsi_signal = pd.notna(latest_rsi3) and (latest_rsi3 < 20.0)
    kd_signal = (
        pd.notna(latest_k)
        and pd.notna(latest_d)
        and latest_k < 20.0
        and latest_d < 20.0
        and latest_k > latest_d
        and prev_k <= prev_d
    )
    trigger = rsi_signal or kd_signal
    entry = trend_ok and vol_ok and trigger

    trigger_reason = "rsi3<20" if rsi_signal else ("kd_gold_cross_below20" if kd_signal else "none")
    vol_strength = 0.0
    if pd.notna(latest_vol_ma5) and latest_vol_ma5 > 0:
        vol_strength = min(latest_volume / (latest_vol_ma5 * 1.5), 2.0)
    trigger_strength = 1.0 if trigger else 0.0
    trend_strength = 1.0 if trend_ok else 0.0
    raw_score = (0.45 * trend_strength) + (0.25 * min(vol_strength, 1.0)) + (0.30 * trigger_strength)
    if trigger and rsi_signal:
        raw_score += 0.1
    score = float(max(0.0, min(raw_score, 1.0)))
    ma_gap = 0.0
    if pd.notna(latest_ma60) and latest_ma60 > 0:
        ma_gap = abs(latest_close - latest_ma60) / latest_ma60
    risk_score = float(max(0.0, score * (1.0 - min(ma_gap, 0.5))))
    return {
        "entry": bool(entry),
        "trend_ok": bool(trend_ok),
        "vol_ok": bool(vol_ok),
        "trigger": bool(trigger),
        "trigger_reason": trigger_reason,
        "close": latest_close,
        "ma60": latest_ma60,
        "volume": latest_volume,
        "volume_ma5": latest_vol_ma5,
        "rsi3": latest_rsi3,
        "k": latest_k,
        "d": latest_d,
        "score": score,
        "risk_score": risk_score,
    }


def compute_daily_signal_suggestions(
    repo: TradingRepository,
    trade_date: date,
    available_cash: float = 10_000.0,
    sizer_config: AbsoluteSizerConfig | None = None,
    max_symbols: int = 50,
    require_chip: bool = False,
    block_disposition: bool = True,
    rank_by: str = "score",
    max_open_positions: int | None = None,
    data_freshness_required: bool = False,
    chip_threshold_mode: str = "absolute",
) -> list[SignalSuggestion]:
    symbols = sorted(repo.get_latest_0050_symbols())[:max_symbols]
    candidates: list[dict[str, Any]] = []
    for symbol in symbols:
        bars = repo.get_daily_bars(symbol=symbol, end_date=trade_date)
        if not bars:
            continue
        last_bar_date = bars[-1].date
        data_quality = "fresh" if last_bar_date >= trade_date else "stale"
        if data_freshness_required and last_bar_date < trade_date:
            continue

        frame = _bars_to_frame(bars)
        signal = evaluate_entry_signal(frame)
        if not signal["entry"]:
            continue

        chip_rollup = {
            "institutional_net_buy_sum": 0.0,
        }
        if hasattr(repo, "get_institutional_chip_rollup"):
            try:
                chip_rollup = repo.get_institutional_chip_rollup(symbol=symbol, end_date=trade_date, lookback_days=5)
            except Exception:
                chip_rollup = {"institutional_net_buy_sum": 0.0}
        chip_net_5d = float(chip_rollup.get("institutional_net_buy_sum", 0.0))

        chip_up3 = False
        if hasattr(repo, "is_chip_concentration_up"):
            try:
                chip_up3 = bool(repo.is_chip_concentration_up(symbol=symbol, end_date=trade_date, days=3))
            except Exception:
                chip_up3 = False

        disposition_active = False
        if hasattr(repo, "get_active_disposition"):
            try:
                disposition_info = repo.get_active_disposition(symbol=symbol, on_date=trade_date)
                disposition_active = bool(disposition_info.get("active", False))
            except Exception:
                disposition_active = False

        chip_mode = str(chip_threshold_mode or "absolute").strip().lower()
        chip_ok = chip_net_5d > 1000.0 and chip_up3
        reason_codes = [f"entry:{signal['trigger_reason']}"]
        if chip_mode == "zscore" and hasattr(repo, "get_institutional_chip"):
            try:
                chip_rows = repo.get_institutional_chip(symbol=symbol, end_date=trade_date)
                chip_values = [
                    float(item.foreign_net_buy) + float(item.investment_trust_net_buy)
                    for item in chip_rows[-60:]
                ]
                if len(chip_values) >= 20:
                    series = pd.Series(chip_values, dtype=float)
                    std = float(series.std())
                    latest = float(series.iloc[-1])
                    mean = float(series.mean())
                    zscore = (latest - mean) / std if std > 0 else 0.0
                    chip_ok = bool(zscore > 0.5 and chip_up3)
                    reason_codes.append(f"chip_zscore:{zscore:.2f}")
                else:
                    chip_ok = False
                    reason_codes.append("chip_zscore:insufficient")
            except Exception:
                chip_ok = False
                reason_codes.append("chip_zscore:error")
        elif chip_mode not in {"absolute", ""}:
            reason_codes.append(f"chip_mode_unsupported:{chip_mode}")

        if require_chip and not chip_ok:
            continue
        if block_disposition and disposition_active:
            continue

        price = float(signal["close"])
        if require_chip:
            reason_codes.append("chip_ok")
        if disposition_active:
            reason_codes.append("disposition_active")
        candidates.append(
            {
                "symbol": symbol,
                "price": price,
                "signal": signal,
                "chip_net_5d": chip_net_5d,
                "chip_up3": chip_up3,
                "disposition_active": disposition_active,
                "chip_ok": chip_ok,
                "reason_codes": reason_codes,
                "data_quality": data_quality,
                "last_bar_date": str(last_bar_date),
            }
        )

    rank_mode = str(rank_by or "score").strip().lower()
    rank_field = "risk_score" if rank_mode in {"risk_adjusted_score", "risk_score"} else "score"
    candidates = sorted(candidates, key=lambda item: float(item["signal"].get(rank_field, 0.0)), reverse=True)

    remaining_cash = float(max(available_cash, 0))
    suggestions: list[SignalSuggestion] = []
    for item in candidates:
        if remaining_cash <= 0:
            break
        if max_open_positions is not None and max_open_positions > 0 and len(suggestions) >= int(max_open_positions):
            break
        sizing = compute_order_size(available_cash=remaining_cash, current_price=float(item["price"]), config=sizer_config)
        if not sizing.get("accepted"):
            continue
        remaining_cash -= float(sizing["estimated_total_cost"])
        reason_text = ";".join(item["reason_codes"])
        signal = item["signal"]
        suggestions.append(
            SignalSuggestion(
                symbol=str(item["symbol"]),
                price=float(item["price"]),
                qty=int(sizing["qty"]),
                estimated_total_cost=float(sizing["estimated_total_cost"]),
                reason=reason_text,
                reason_codes=list(item["reason_codes"]),
                rsi3=float(signal["rsi3"]),
                k=float(signal["k"]),
                d=float(signal["d"]),
                close=float(signal["close"]),
                ma60=float(signal["ma60"]),
                volume=float(signal["volume"]),
                volume_ma5=float(signal["volume_ma5"]),
                score=float(signal.get("score", 0.0)),
                risk_score=float(signal.get("risk_score", 0.0)),
                data_quality=str(item["data_quality"]),
                last_bar_date=str(item["last_bar_date"]),
                chip_net_buy_5d=float(item["chip_net_5d"]),
                chip_concentration_up3=bool(item["chip_up3"]),
                disposition_active=bool(item["disposition_active"]),
                chip_ok=bool(item["chip_ok"]),
            )
        )
    return suggestions
