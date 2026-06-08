from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from app.alerts.signal_engine import _bars_to_frame, evaluate_entry_signal
from app.services.universe import normalize_risk_profile


def clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def norm_percent(value: float | None) -> float:
    if value is None:
        return 0.0
    number = float(value)
    if number <= 0:
        return 0.0
    if number <= 1:
        return clip01(number)
    return clip01(number / 100.0)


def norm_concentration(value: float | None) -> float:
    if value is None:
        return 0.0
    number = float(value)
    if number <= 0:
        return 0.0
    if number <= 1:
        return clip01(number)
    if number <= 100:
        return clip01(number / 100.0)
    return clip01(number / 5000.0)


def resolve_data_quality(bar_date: date | None, trade_date: date, window_days: int = 3) -> tuple[str | None, str]:
    if bar_date is None:
        return (None, "missing")
    window_start = trade_date - timedelta(days=max(0, int(window_days)))
    status = "fresh" if window_start <= bar_date <= trade_date else "stale"
    return (bar_date.isoformat(), status)


def limit_lock_risk(signal: dict) -> bool:
    close = float(signal.get("close", 0.0) or 0.0)
    ma60 = float(signal.get("ma60", 0.0) or 0.0)
    volume = float(signal.get("volume", 0.0) or 0.0)
    volume_ma5 = float(signal.get("volume_ma5", 0.0) or 0.0)
    if close <= 0 or ma60 <= 0:
        return False
    extension = (close - ma60) / ma60
    vol_ratio = (volume / volume_ma5) if volume_ma5 > 0 else 0.0
    return bool(extension >= 0.25 and vol_ratio <= 0.85)


def chip_flow_score(chip_net_5d: float, chip_up3: bool) -> float:
    scaled = math.tanh(float(chip_net_5d) / 50_000_000.0)
    base = 0.5 + (scaled * 0.4) + (0.1 if chip_up3 else -0.05)
    return clip01(base)


@dataclass
class ScoredSymbol:
    symbol: str
    name: str
    price: float
    signal: dict
    last_bar_date: str | None
    data_quality: str
    disposition_active: bool
    chip_net_5d: float
    chip_up3: bool
    chip_available: bool
    major_ratio: float
    retail_ratio: float
    concentration_proxy: float
    dispersion_proxy: float
    trend_momentum: float
    trigger_quality: float
    chip_score: float
    structure_score: float
    liquidity_score: float
    total_score: float
    risk_score: float
    meta: dict = field(default_factory=dict)


def score_symbol(
    repo,
    *,
    symbol: str,
    trade_date: date,
    risk_profile: str = "balanced",
    signal_evaluator: Callable[[object], dict] = evaluate_entry_signal,
) -> ScoredSymbol | None:
    bars = repo.get_daily_bars(symbol=symbol, end_date=trade_date)
    if len(bars) < 65:
        return None
    frame = _bars_to_frame(bars)
    if frame.empty:
        return None

    signal = signal_evaluator(frame)
    latest_bar_date = bars[-1].date if bars else None
    last_bar_date, data_quality = resolve_data_quality(latest_bar_date, trade_date, window_days=3)

    disposition = repo.get_active_disposition(symbol=symbol, on_date=trade_date)
    disposition_active = bool(disposition.get("active", False))

    chip_rollup = repo.get_institutional_chip_rollup(symbol=symbol, end_date=trade_date, lookback_days=5)
    chip_net_5d = float(chip_rollup.get("institutional_net_buy_sum", 0.0) or 0.0)
    chip_rows = repo.get_institutional_chip(symbol=symbol, end_date=trade_date) if hasattr(repo, "get_institutional_chip") else []
    chip_available = len(chip_rows) >= 20
    chip_up3 = bool(repo.is_chip_concentration_up(symbol=symbol, end_date=trade_date, days=3))

    shareholding = repo.get_latest_shareholding(symbol=symbol, on_or_before=trade_date)
    major_ratio = float(shareholding.major_holder_ratio) if shareholding else 0.0
    retail_ratio = float(shareholding.retail_holder_ratio) if shareholding else 0.0
    major_norm = norm_percent(major_ratio)
    retail_norm = norm_percent(retail_ratio)

    holding = repo.get_latest_holding_shares_per(symbol=symbol, on_or_before=trade_date)
    concentration_proxy = float(holding.concentration_proxy) if holding else 0.0
    dispersion_proxy = float(holding.dispersion_proxy) if holding else 0.0
    concentration_norm = norm_concentration(concentration_proxy)
    dispersion_norm = norm_percent(dispersion_proxy)

    trend_momentum = clip01(float(signal.get("score", 0.0) or 0.0))
    trigger_quality = 1.0 if bool(signal.get("trigger")) else (0.45 if bool(signal.get("trend_ok")) and bool(signal.get("vol_ok")) else 0.1)
    chip_score = chip_flow_score(chip_net_5d=chip_net_5d, chip_up3=chip_up3)
    structure_score = clip01((major_norm * 0.6) + (concentration_norm * 0.25) + ((1.0 - max(retail_norm, dispersion_norm)) * 0.15))
    volume = float(signal.get("volume", 0.0) or 0.0)
    volume_ma5 = float(signal.get("volume_ma5", 0.0) or 0.0)
    liquidity_score = clip01(((volume / volume_ma5) if volume_ma5 > 0 else 0.0) / 2.0)

    total_score = clip01(
        (trend_momentum * 0.30)
        + (trigger_quality * 0.20)
        + (chip_score * 0.25)
        + (structure_score * 0.15)
        + (liquidity_score * 0.10)
    )
    risk_score = clip01((float(signal.get("risk_score", 0.0) or 0.0) * 0.65) + (total_score * 0.35))
    profile = normalize_risk_profile(risk_profile)

    return ScoredSymbol(
        symbol=str(symbol),
        name=repo.get_instrument_name(symbol) or str(symbol),
        price=float(signal.get("close", 0.0) or 0.0),
        signal=signal,
        last_bar_date=last_bar_date,
        data_quality=data_quality,
        disposition_active=disposition_active,
        chip_net_5d=chip_net_5d,
        chip_up3=chip_up3,
        chip_available=chip_available,
        major_ratio=major_ratio,
        retail_ratio=retail_ratio,
        concentration_proxy=concentration_proxy,
        dispersion_proxy=dispersion_proxy,
        trend_momentum=trend_momentum,
        trigger_quality=trigger_quality,
        chip_score=chip_score,
        structure_score=structure_score,
        liquidity_score=liquidity_score,
        total_score=total_score,
        risk_score=risk_score,
        meta={"risk_profile": profile, "limit_lock_risk": limit_lock_risk(signal)},
    )
