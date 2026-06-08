from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from app.alerts.signal_engine import evaluate_entry_signal
from app.services.scoring import limit_lock_risk, score_symbol
from app.services.universe import normalize_risk_profile

if TYPE_CHECKING:
    from app.db.repository import TradingRepository


def generate_daily_radar(
    repo: "TradingRepository",
    *,
    symbols: list[str],
    trade_date: date,
    target_count: int = 15,
    data_freshness_required: bool = True,
    risk_profile: str = "balanced",
) -> list[dict]:
    profile = normalize_risk_profile(risk_profile)
    cap = max(10, min(20, int(target_count or 15)))
    if profile == "aggressive":
        entry_score_min = 66.0
        watch_score_min = 55.0
        blocker_liquidity_min = 0.18
        allow_soft_entry = True
    else:
        entry_score_min = 72.0
        watch_score_min = 60.0
        blocker_liquidity_min = 0.25
        allow_soft_entry = False
    rows: list[dict] = []

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
        signal = scored.signal
        entry_score = float(scored.total_score * 100.0)

        blocker_tags: list[str] = []
        if data_freshness_required and scored.data_quality != "fresh":
            blocker_tags.append("B_DATA_STALE")
        if scored.disposition_active:
            blocker_tags.append("B_DISPOSITION_ACTIVE")
        if scored.liquidity_score < blocker_liquidity_min:
            blocker_tags.append("B_LOW_LIQUIDITY")
        if limit_lock_risk(signal):
            blocker_tags.append("B_LIMIT_LOCK_RISK")
        if not scored.chip_available:
            blocker_tags.append("B_MISSING_CRITICAL_CHIP")

        soft_entry = bool(signal.get("trend_ok")) and bool(signal.get("vol_ok")) if allow_soft_entry else False
        if blocker_tags:
            action_label = "NOT_RECOMMENDED"
        elif entry_score >= entry_score_min and (bool(signal.get("entry")) or soft_entry):
            action_label = "ENTRY_READY"
        elif entry_score >= watch_score_min:
            action_label = "WATCH_WAIT_TRIGGER"
        else:
            action_label = "NOT_RECOMMENDED"

        candidate_type = "entry" if action_label == "ENTRY_READY" else "watch"
        reason_tags = [
            f"PROFILE={profile}",
            f"TREND_OK={bool(signal.get('trend_ok'))}",
            f"VOL_OK={bool(signal.get('vol_ok'))}",
            f"TRIGGER={bool(signal.get('trigger'))}",
            f"TRIGGER_REASON={signal.get('trigger_reason', 'none')}",
            f"CHIP_UP3={scored.chip_up3}",
            f"ENTRY_SCORE={entry_score:.2f}",
        ]

        rows.append(
            {
                "symbol": symbol,
                "name": scored.name,
                "price": scored.price,
                "entry_score": entry_score,
                "risk_score": float(scored.risk_score),
                "action_label": action_label,
                "candidate_type": candidate_type,
                "reason_tags": reason_tags,
                "blocker_tags": blocker_tags,
                "last_bar_date": scored.last_bar_date,
                "data_quality": scored.data_quality,
            }
        )

    priority = {"ENTRY_READY": 2, "WATCH_WAIT_TRIGGER": 1, "NOT_RECOMMENDED": 0}
    rows.sort(key=lambda item: (priority.get(str(item.get("action_label")), 0), float(item.get("entry_score", 0.0)), float(item.get("risk_score", 0.0))), reverse=True)
    selected = rows[:cap]
    for idx, item in enumerate(selected, start=1):
        item["rank"] = idx
    return selected
