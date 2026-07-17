from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from app.alerts.signal_engine import evaluate_entry_signal
from app.data.symbol_exclusions import load_symbol_exclusions
from app.services.scoring import score_symbol
from app.services.market_context import compute_group_trend_metrics, apply_group_rotation_scores, select_with_group_diversity
from app.services.universe import normalize_risk_profile

if TYPE_CHECKING:
    from app.db.repository import TradingRepository


@dataclass
class CandidateItem:
    symbol: str
    name: str
    price: float
    score: float
    risk_score: float
    candidate_type: str
    reason_codes: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    last_bar_date: str | None = None
    data_quality: str = "unknown"
    fundamental_data_quality: str = "missing"
    industry_group: str = "其他"
    industry_rotation_score: float = 0.0
    industry_trend_label: str = "neutral"
    industry_trend_score: float = 0.0
    industry_short_return: float = 0.0
    industry_medium_return: float = 0.0
    industry_breadth: float = 0.0
    atr_pct: float = 0.0
    atr_heat: str = "normal"


def build_candidates(
    repo: "TradingRepository",
    *,
    symbols: list[str],
    trade_date: date,
    data_freshness_required: bool = True,
    relaxed: bool = False,
    risk_profile: str = "balanced",
) -> list[CandidateItem]:
    profile = normalize_risk_profile(risk_profile)
    rows: list[CandidateItem] = []
    if profile == "aggressive":
        watch_threshold = 0.55 if not relaxed else 0.35
        chip_min = 0.35
        liquidity_entry_min = 0.25
        liquidity_flag_min = 0.25
        aggressive_entry_score = 0.66
    elif profile == "conservative":
        watch_threshold = 0.64 if not relaxed else 0.45
        chip_min = 0.55
        liquidity_entry_min = 0.38
        liquidity_flag_min = 0.38
        aggressive_entry_score = 0.0
    else:
        watch_threshold = 0.60 if not relaxed else 0.40
        chip_min = 0.45
        liquidity_entry_min = 0.30
        liquidity_flag_min = 0.30
        aggressive_entry_score = 0.0
    excluded_symbols = load_symbol_exclusions()
    for symbol in sorted(set(symbols)):
        if str(symbol).strip().upper() in excluded_symbols:
            continue
        scored = score_symbol(
            repo,
            symbol=symbol,
            trade_date=trade_date,
            risk_profile=profile,
            signal_evaluator=evaluate_entry_signal,
        )
        if scored is None:
            continue
        if data_freshness_required and scored.data_quality != "fresh":
            continue
        if scored.disposition_active or scored.attention_active:
            continue
        signal = scored.signal
        chip_ok = scored.chip_score >= chip_min

        reason_codes: list[str] = [
            f"profile={profile}",
            f"industry_group={scored.industry_group}",
            f"atr_pct={scored.atr_pct * 100.0:.2f}",
            f"atr_heat={scored.atr_heat}",
            f"signal_entry={bool(signal.get('entry'))}",
            f"chip_up3={scored.chip_up3}",
            f"major_ratio={scored.major_ratio:.2f}",
            f"retail_ratio={scored.retail_ratio:.2f}",
            f"concentration_proxy={scored.concentration_proxy:.2f}",
            f"fundamental_quality={scored.fundamental_data_quality}",
            f"revenue_score={scored.revenue_score:.2f}",
            f"quality_score={scored.quality_score:.2f}",
            f"news_risk_score={scored.news_risk_score:.2f}",
        ]
        risk_flags: list[str] = []
        if scored.data_quality != "fresh":
            risk_flags.append("stale_data")
        if not chip_ok:
            risk_flags.append("chip_weak")
        if scored.liquidity_score < liquidity_flag_min:
            risk_flags.append("low_liquidity")
        if scored.structure_score < 0.35:
            risk_flags.append("holder_structure_weak")
        if scored.atr_heat == "hot":
            risk_flags.append("atr_overheated")
        elif scored.atr_heat == "elevated":
            risk_flags.append("atr_elevated")

        aggressive_entry = (
            profile == "aggressive"
            and bool(signal.get("trend_ok"))
            and bool(signal.get("vol_ok"))
            and chip_ok
            and scored.liquidity_score >= liquidity_entry_min
            and scored.total_score >= aggressive_entry_score
        )
        if (bool(signal.get("entry")) and chip_ok and scored.liquidity_score >= liquidity_entry_min) or aggressive_entry:
            candidate_type = "entry"
        elif scored.total_score >= watch_threshold:
            candidate_type = "watch"
        else:
            continue

        rows.append(
            CandidateItem(
                symbol=scored.symbol,
                name=scored.name,
                price=scored.price,
                score=float(scored.total_score),
                risk_score=float(scored.risk_score),
                candidate_type=candidate_type,
                reason_codes=reason_codes,
                risk_flags=risk_flags,
                last_bar_date=scored.last_bar_date,
                data_quality=scored.data_quality,
                fundamental_data_quality=scored.fundamental_data_quality,
                industry_group=scored.industry_group,
                industry_rotation_score=0.0,
                atr_pct=float(scored.atr_pct),
                atr_heat=scored.atr_heat,
            )
        )
    trend_metrics = compute_group_trend_metrics(
        rows,
        repo=repo,
        trade_date=trade_date,
        symbol_getter=lambda item: item.symbol,
        group_getter=lambda item: item.industry_group,
    )

    def _set_rotation_score(item: CandidateItem, score: float) -> None:
        trend = trend_metrics.get(item.industry_group, {})
        trend_score = float(trend.get("score", 0.0) or 0.0) if trend else 0.0
        combined_score = (float(score) * 0.70) + (trend_score * 0.30) if trend else float(score)
        item.industry_rotation_score = float(combined_score)
        item.industry_trend_label = str(trend.get("label", "neutral") or "neutral") if trend else "neutral"
        item.industry_trend_score = trend_score
        item.industry_short_return = float(trend.get("short_return", 0.0) or 0.0) if trend else 0.0
        item.industry_medium_return = float(trend.get("medium_return", 0.0) or 0.0) if trend else 0.0
        item.industry_breadth = float(trend.get("breadth", 0.0) or 0.0) if trend else 0.0
        item.reason_codes.insert(3, f"industry_rotation_score={combined_score * 100.0:.1f}")
        item.reason_codes.insert(4, f"industry_trend={item.industry_trend_label}")
        item.reason_codes.insert(5, f"industry_trend_score={trend_score * 100.0:.1f}")
        item.reason_codes.insert(6, f"industry_short_return={item.industry_short_return * 100.0:.2f}")
        item.reason_codes.insert(7, f"industry_medium_return={item.industry_medium_return * 100.0:.2f}")
        item.reason_codes.insert(8, f"industry_breadth={item.industry_breadth * 100.0:.1f}")

    apply_group_rotation_scores(
        rows,
        group_getter=lambda item: item.industry_group,
        score_getter=lambda item: item.score,
        setter=_set_rotation_score,
    )
    rows.sort(
        key=lambda item: (item.score + (item.industry_rotation_score * 0.03), item.risk_score),
        reverse=True,
    )
    return rows


def select_candidates(candidates: list[CandidateItem], target_count: int = 8, risk_profile: str = "balanced") -> list[CandidateItem]:
    cap = max(5, min(10, int(target_count or 8)))
    entries = [item for item in candidates if item.candidate_type == "entry"]
    watches = [item for item in candidates if item.candidate_type == "watch"]
    selected = select_with_group_diversity(
        [*entries, *watches],
        target_count=cap,
        group_getter=lambda item: item.industry_group,
        risk_profile=risk_profile,
    )
    for idx, item in enumerate(selected, start=1):
        item.reason_codes = [f"rank={idx}", *item.reason_codes]
    return selected


def build_candidate_payload(items: list[CandidateItem]) -> list[dict]:
    payload: list[dict] = []
    for idx, item in enumerate(items, start=1):
        payload.append(
            {
                "rank": idx,
                "symbol": item.symbol,
                "name": item.name,
                "price": float(item.price),
                "score": float(item.score),
                "risk_score": float(item.risk_score),
                "candidate_type": item.candidate_type,
                "reason_codes": list(item.reason_codes),
                "risk_flags": list(item.risk_flags),
                "last_bar_date": item.last_bar_date,
                "data_quality": item.data_quality,
                "fundamental_data_quality": item.fundamental_data_quality,
                "industry_group": item.industry_group,
                "industry_rotation_score": float(item.industry_rotation_score),
                "industry_trend_label": item.industry_trend_label,
                "industry_trend_score": float(item.industry_trend_score),
                "industry_short_return": float(item.industry_short_return),
                "industry_medium_return": float(item.industry_medium_return),
                "industry_breadth": float(item.industry_breadth),
                "atr_pct": float(item.atr_pct),
                "atr_heat": item.atr_heat,
            }
        )
    return payload


def generate_candidate_suggestions(
    repo: "TradingRepository",
    *,
    symbols: list[str],
    trade_date: date,
    target_count: int = 8,
    data_freshness_required: bool = True,
    risk_profile: str = "balanced",
) -> tuple[list[dict], bool]:
    profile = normalize_risk_profile(risk_profile)
    first = build_candidates(
        repo=repo,
        symbols=symbols,
        trade_date=trade_date,
        data_freshness_required=data_freshness_required,
        relaxed=False,
        risk_profile=profile,
    )
    selected = select_candidates(first, target_count=target_count, risk_profile=profile)
    relaxed_applied = False
    if len(selected) < 5:
        relaxed_applied = True
        second = build_candidates(
            repo=repo,
            symbols=symbols,
            trade_date=trade_date,
            data_freshness_required=data_freshness_required,
            relaxed=True,
            risk_profile=profile,
        )
        selected = select_candidates(second, target_count=target_count, risk_profile=profile)
    return build_candidate_payload(selected), relaxed_applied
