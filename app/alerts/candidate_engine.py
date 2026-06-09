from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from app.alerts.signal_engine import evaluate_entry_signal
from app.services.scoring import score_symbol
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
    else:
        watch_threshold = 0.60 if not relaxed else 0.40
        chip_min = 0.45
        liquidity_entry_min = 0.30
        liquidity_flag_min = 0.30
        aggressive_entry_score = 0.0
    for symbol in sorted(set(symbols)):
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
        if scored.disposition_active:
            continue
        signal = scored.signal
        chip_ok = scored.chip_score >= chip_min

        reason_codes: list[str] = [
            f"profile={profile}",
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
            )
        )
    rows.sort(key=lambda item: (item.score, item.risk_score), reverse=True)
    return rows


def select_candidates(candidates: list[CandidateItem], target_count: int = 8) -> list[CandidateItem]:
    cap = max(5, min(10, int(target_count or 8)))
    entries = [item for item in candidates if item.candidate_type == "entry"]
    watches = [item for item in candidates if item.candidate_type == "watch"]
    selected: list[CandidateItem] = []
    selected.extend(entries[:cap])
    remaining = cap - len(selected)
    if remaining > 0:
        selected.extend(watches[:remaining])
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
    selected = select_candidates(first, target_count=target_count)
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
        selected = select_candidates(second, target_count=target_count)
    return build_candidate_payload(selected), relaxed_applied
