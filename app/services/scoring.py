from __future__ import annotations

import math
import json
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
    revenue_score: float
    quality_score: float
    valuation_or_growth_score: float
    news_risk_score: float
    fundamental_data_quality: str
    total_score: float
    risk_score: float
    fundamental_summary: dict = field(default_factory=dict)
    news_summary: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


def _norm_growth(value: float | None, center: float = 0.0, scale: float = 40.0) -> float:
    if value is None:
        return 0.5
    return clip01(0.5 + ((float(value) - center) / scale))


def _norm_positive(value: float | None, scale: float) -> float:
    if value is None:
        return 0.5
    return clip01(float(value) / float(scale))


def _norm_inverse(value: float | None, good_at: float = 30.0, bad_at: float = 80.0) -> float:
    if value is None:
        return 0.5
    number = float(value)
    if number <= good_at:
        return 1.0
    if number >= bad_at:
        return 0.0
    return clip01(1.0 - ((number - good_at) / (bad_at - good_at)))


def _safe_json_list(value: str) -> list[str]:
    try:
        payload = json.loads(str(value or "[]"))
        if isinstance(payload, list):
            return [str(item) for item in payload]
    except Exception:
        pass
    return []


def _fundamental_scores(repo, *, symbol: str, trade_date: date) -> dict:
    revenue = repo.get_latest_monthly_revenue(symbol, on_or_before=trade_date) if hasattr(repo, "get_latest_monthly_revenue") else None
    financial = (
        repo.get_latest_financial_summary(symbol, on_or_before=trade_date)
        if hasattr(repo, "get_latest_financial_summary")
        else None
    )
    recent_news = repo.list_recent_news_events(symbol, on_or_before=trade_date, limit=5) if hasattr(repo, "list_recent_news_events") else []

    if revenue is None:
        revenue_score = 0.5
        revenue_summary = {"available": False}
    else:
        revenue_score = clip01(
            (_norm_growth(getattr(revenue, "revenue_yoy_pct", None)) * 0.55)
            + (_norm_growth(getattr(revenue, "revenue_mom_pct", None), scale=25.0) * 0.25)
            + (_norm_positive(getattr(revenue, "revenue", None), scale=max(float(getattr(revenue, "revenue", 0.0) or 0.0), 1.0)) * 0.20)
        )
        revenue_summary = {
            "available": True,
            "period": getattr(revenue, "period", ""),
            "announce_date": revenue.announce_date.isoformat() if getattr(revenue, "announce_date", None) else None,
            "revenue_yoy_pct": getattr(revenue, "revenue_yoy_pct", None),
            "revenue_mom_pct": getattr(revenue, "revenue_mom_pct", None),
        }

    if financial is None:
        quality_score = 0.5
        valuation_or_growth_score = revenue_score
        financial_summary = {"available": False}
    else:
        eps_score = 0.7 if (financial.eps is not None and float(financial.eps) > 0) else 0.35
        quality_score = clip01(
            (_norm_positive(financial.roe_pct, scale=20.0) * 0.30)
            + (_norm_positive(financial.gross_margin_pct, scale=50.0) * 0.25)
            + (_norm_positive(financial.operating_margin_pct, scale=30.0) * 0.20)
            + (_norm_inverse(financial.debt_ratio_pct) * 0.15)
            + ((_norm_positive(financial.operating_cash_flow, scale=1.0) if financial.operating_cash_flow else 0.45) * 0.10)
        )
        valuation_or_growth_score = clip01((eps_score * 0.35) + (revenue_score * 0.40) + (quality_score * 0.25))
        financial_summary = {
            "available": True,
            "period": financial.period,
            "announce_date": financial.announce_date.isoformat() if financial.announce_date else None,
            "eps": financial.eps,
            "roe_pct": financial.roe_pct,
            "gross_margin_pct": financial.gross_margin_pct,
            "operating_margin_pct": financial.operating_margin_pct,
            "debt_ratio_pct": financial.debt_ratio_pct,
        }

    negative_count = 0
    positive_count = 0
    news_items: list[dict] = []
    for item in recent_news:
        tags = _safe_json_list(getattr(item, "risk_tags_json", "[]"))
        negative_count += 1 if "negative_event" in tags else 0
        positive_count += 1 if "positive_event" in tags else 0
        news_items.append(
            {
                "date": item.news_date.isoformat() if item.news_date else None,
                "title": item.title,
                "source_name": item.source_name,
                "risk_tags": tags,
                "summary": item.llm_summary,
            }
        )
    news_risk_score = clip01(0.5 - (negative_count * 0.12) + (positive_count * 0.06))
    if revenue is not None and financial is not None:
        quality = "fresh"
    elif revenue is not None or financial is not None or recent_news:
        quality = "partial"
    else:
        quality = "missing"

    return {
        "revenue_score": revenue_score,
        "quality_score": quality_score,
        "valuation_or_growth_score": valuation_or_growth_score,
        "news_risk_score": news_risk_score,
        "fundamental_data_quality": quality,
        "fundamental_summary": {"revenue": revenue_summary, "financial": financial_summary},
        "news_summary": {"count": len(news_items), "negative_count": negative_count, "positive_count": positive_count, "items": news_items},
    }


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
    fundamentals = _fundamental_scores(repo, symbol=symbol, trade_date=trade_date)
    fundamental_score = clip01(
        (fundamentals["revenue_score"] * 0.30)
        + (fundamentals["quality_score"] * 0.30)
        + (fundamentals["valuation_or_growth_score"] * 0.25)
        + (fundamentals["news_risk_score"] * 0.15)
    )

    technical_chip_score = clip01(
        (trend_momentum * 0.30)
        + (trigger_quality * 0.20)
        + (chip_score * 0.25)
        + (structure_score * 0.15)
        + (liquidity_score * 0.10)
    )
    total_score = clip01((technical_chip_score * 0.78) + (fundamental_score * 0.22))
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
        revenue_score=float(fundamentals["revenue_score"]),
        quality_score=float(fundamentals["quality_score"]),
        valuation_or_growth_score=float(fundamentals["valuation_or_growth_score"]),
        news_risk_score=float(fundamentals["news_risk_score"]),
        fundamental_data_quality=str(fundamentals["fundamental_data_quality"]),
        fundamental_summary=dict(fundamentals["fundamental_summary"]),
        news_summary=dict(fundamentals["news_summary"]),
        total_score=total_score,
        risk_score=risk_score,
        meta={"risk_profile": profile, "limit_lock_risk": limit_lock_risk(signal)},
    )
