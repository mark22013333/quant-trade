from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from app.alerts.signal_engine import evaluate_entry_signal
from app.data.symbol_exclusions import load_symbol_exclusions
from app.services.scoring import limit_lock_risk, score_symbol
from app.services.market_context import compute_group_trend_metrics, apply_group_rotation_scores, select_with_group_diversity
from app.signals.registry import radar_outcome_bucket_for_tag
from app.services.universe import normalize_risk_profile

if TYPE_CHECKING:
    from app.db.repository import TradingRepository


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), float(value)))


def _load_signal_reliability(repo: "TradingRepository", trade_date: date) -> dict:
    getter = getattr(repo, "get_radar_signal_reliability_horizons", None)
    if not callable(getter):
        getter = getattr(repo, "get_radar_signal_reliability", None)
    if not callable(getter):
        return {}
    try:
        return dict(
            getter(
                lookahead_bars_list=(5, 10, 20),
                max_snapshots=300,
                min_evaluated=4,
                on_or_before=trade_date,
            )
            or {}
        )
    except TypeError:
        try:
            return dict(getter() or {})
        except Exception:
            return {}
    except Exception:
        return {}


def _load_symbol_adoption_feedback(repo: "TradingRepository", symbols: list[str], trade_date: date) -> dict:
    getter = getattr(repo, "get_daily_radar_symbol_adoption_feedback", None)
    if not callable(getter):
        return {}
    try:
        return dict(
            getter(
                symbols=symbols,
                lookahead_bars_list=(5, 10, 20),
                max_runs=50,
                on_or_before=trade_date,
            )
            or {}
        )
    except TypeError:
        try:
            return dict(getter(symbols=symbols) or {})
        except Exception:
            return {}
    except Exception:
        return {}


def _item_outcome_buckets(item: dict) -> list[str]:
    buckets: list[str] = []
    seen: set[str] = set()
    for tag in item.get("reason_tags", []):
        bucket = radar_outcome_bucket_for_tag(str(tag or ""))
        if not bucket or bucket in seen:
            continue
        buckets.append(bucket)
        seen.add(bucket)
    return buckets


def _apply_outcome_reliability(rows: list[dict], reliability: dict) -> None:
    buckets = reliability.get("buckets") if isinstance(reliability.get("buckets"), dict) else {}
    baseline_rate = reliability.get("baseline_positive_rate")
    baseline_return = reliability.get("baseline_average_return")
    try:
        baseline_positive_rate = float(baseline_rate)
    except (TypeError, ValueError):
        baseline_positive_rate = 0.5
    try:
        baseline_average_return = float(baseline_return)
    except (TypeError, ValueError):
        baseline_average_return = 0.0

    for item in rows:
        matches: list[dict] = []
        for bucket in _item_outcome_buckets(item):
            stats = buckets.get(bucket) if isinstance(buckets, dict) else None
            if not isinstance(stats, dict):
                continue
            positive_rate = stats.get("positive_rate")
            if positive_rate is None:
                continue
            evaluated_count = int(stats.get("evaluated_count", 0) or 0)
            if evaluated_count <= 0:
                continue
            average_return = float(stats.get("average_return", 0.0) or 0.0)
            rate_edge = float(positive_rate) - baseline_positive_rate
            return_edge = average_return - baseline_average_return
            sample_weight = min(1.0, evaluated_count / 20.0)
            signal_score = _clip(((rate_edge * 4.0) + (return_edge * 20.0)) * sample_weight, -1.0, 1.0)
            matches.append(
                {
                    "bucket": bucket,
                    "evaluated_count": evaluated_count,
                    "positive_rate": float(positive_rate),
                    "average_return": average_return,
                    "score": signal_score,
                    "horizons": list(stats.get("horizons", [])) if isinstance(stats.get("horizons"), list) else [],
                }
            )

        if matches:
            total_weight = sum(max(1, int(match["evaluated_count"])) for match in matches)
            score = sum(float(match["score"]) * max(1, int(match["evaluated_count"])) for match in matches) / total_weight
            samples = max(int(match["evaluated_count"]) for match in matches)
            strongest = sorted(matches, key=lambda match: (abs(float(match["score"])), int(match["evaluated_count"])), reverse=True)[:3]
            horizon_groups: dict[int, dict[str, float | int]] = {}
            for match in matches:
                for horizon_stats in match.get("horizons", []) or []:
                    horizon = int(horizon_stats.get("lookahead_bars", 0) or 0)
                    evaluated = int(horizon_stats.get("evaluated_count", 0) or 0)
                    if horizon <= 0 or evaluated <= 0:
                        continue
                    target = horizon_groups.setdefault(
                        horizon,
                        {"evaluated_count": 0, "positive_count": 0, "return_sum": 0.0},
                    )
                    target["evaluated_count"] = int(target["evaluated_count"]) + evaluated
                    target["positive_count"] = int(target["positive_count"]) + int(horizon_stats.get("positive_count", 0) or 0)
                    target["return_sum"] = float(target["return_sum"]) + (float(horizon_stats.get("average_return", 0.0) or 0.0) * evaluated)
            horizon_summary = [
                {
                    "lookahead_bars": horizon,
                    "evaluated_count": int(values["evaluated_count"]),
                    "positive_count": int(values["positive_count"]),
                    "positive_rate": (int(values["positive_count"]) / int(values["evaluated_count"])) if int(values["evaluated_count"]) else None,
                    "average_return": (float(values["return_sum"]) / int(values["evaluated_count"])) if int(values["evaluated_count"]) else None,
                }
                for horizon, values in sorted(horizon_groups.items())
            ]
        else:
            score = 0.0
            samples = 0
            strongest = []
            horizon_summary = []

        boost = score * 3.0
        horizon_tag = "|".join(
            f"{int(item.get('lookahead_bars', 0) or 0)}d:{float(item.get('positive_rate', 0.0) or 0.0):.2f}"
            for item in horizon_summary
            if int(item.get("lookahead_bars", 0) or 0) > 0 and item.get("positive_rate") is not None
        )
        reason_tags = list(item.get("reason_tags", []))
        reason_tags.extend(
            [
                f"OUTCOME_RELIABILITY_SCORE={score:.2f}",
                f"OUTCOME_RELIABILITY_BOOST={boost:.2f}",
                f"OUTCOME_RELIABILITY_SAMPLES={samples}",
            ]
        )
        if horizon_tag:
            reason_tags.append(f"OUTCOME_RELIABILITY_HORIZONS={horizon_tag}")
        item.update(
            {
                "outcome_reliability_score": float(score),
                "outcome_reliability_boost": float(boost),
                "outcome_reliability_samples": int(samples),
                "outcome_reliability_tags": strongest,
                "outcome_reliability_horizons": horizon_summary,
                "reason_tags": reason_tags,
            }
        )


def _apply_symbol_adoption_feedback(rows: list[dict], feedback: dict) -> None:
    symbol_payloads = feedback.get("symbols") if isinstance(feedback.get("symbols"), dict) else {}
    for item in rows:
        symbol = str(item.get("symbol") or "").strip().upper()
        stats = symbol_payloads.get(symbol) if isinstance(symbol_payloads, dict) else None
        if not isinstance(stats, dict):
            score = 0.0
            raw_boost = 0.0
            boost = 0.0
            samples = 0
            confidence_weight = 0.0
            confidence_level = "none"
            confidence_label = "尚無樣本"
            horizons: list[dict] = []
        else:
            score = _clip(float(stats.get("score") or 0.0), -1.0, 1.0)
            raw_boost = _clip(float(stats.get("boost") or (score * 4.0)), -4.0, 4.0)
            samples = int(stats.get("evaluated_count") or 0)
            run_count = int(stats.get("run_count") or 0)
            confidence_weight = _clip(
                float(stats.get("confidence_weight") if stats.get("confidence_weight") is not None else min(1.0, (samples / 6.0) * 0.65 + (run_count / 3.0) * 0.35)),
                0.0,
                1.0,
            )
            confidence_level = str(stats.get("confidence_level") or "").strip().lower()
            if confidence_level not in {"low", "medium", "high"}:
                confidence_level = "high" if confidence_weight >= 0.75 else "medium" if confidence_weight >= 0.35 else "low" if confidence_weight > 0 else "none"
            confidence_label = str(stats.get("confidence_label") or "").strip() or {
                "high": "高信心",
                "medium": "中信心",
                "low": "低信心",
                "none": "尚無樣本",
            }.get(confidence_level, "尚無樣本")
            boost = raw_boost if stats.get("confidence_weight") is not None else _clip(raw_boost * max(0.25, confidence_weight), -4.0, 4.0)
            horizons = list(stats.get("horizons", [])) if isinstance(stats.get("horizons"), list) else []

        horizon_tag = "|".join(
            f"{int(row.get('lookahead_bars', 0) or 0)}d:{float(row.get('positive_rate', 0.0) or 0.0):.2f}"
            for row in horizons
            if int(row.get("lookahead_bars", 0) or 0) > 0 and row.get("positive_rate") is not None
        )
        reason_tags = list(item.get("reason_tags", []))
        reason_tags.extend(
            [
                f"SYMBOL_ADOPTION_FEEDBACK_SCORE={score:.2f}",
                f"SYMBOL_ADOPTION_FEEDBACK_RAW_BOOST={raw_boost:.2f}",
                f"SYMBOL_ADOPTION_FEEDBACK_BOOST={boost:.2f}",
                f"SYMBOL_ADOPTION_FEEDBACK_SAMPLES={samples}",
                f"SYMBOL_ADOPTION_FEEDBACK_CONFIDENCE_WEIGHT={confidence_weight:.2f}",
                f"SYMBOL_ADOPTION_FEEDBACK_CONFIDENCE={confidence_level}",
            ]
        )
        if horizon_tag:
            reason_tags.append(f"SYMBOL_ADOPTION_FEEDBACK_HORIZONS={horizon_tag}")
        item.update(
            {
                "symbol_adoption_feedback_score": float(score),
                "symbol_adoption_feedback_raw_boost": float(raw_boost),
                "symbol_adoption_feedback_boost": float(boost),
                "symbol_adoption_feedback_samples": int(samples),
                "symbol_adoption_feedback_confidence_weight": float(confidence_weight),
                "symbol_adoption_feedback_confidence_level": confidence_level,
                "symbol_adoption_feedback_confidence_label": confidence_label,
                "symbol_adoption_feedback_horizons": horizons,
                "reason_tags": reason_tags,
            }
        )


def _risk_adjusted_score(item: dict) -> float:
    entry_score = float(item.get("entry_score") or 0.0)
    risk_score = float(item.get("risk_score") or 0.0)
    blocker_count = len(item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else [])
    heat = str(item.get("atr_heat") or "normal").lower()
    heat_penalty = 8.0 if heat == "hot" else 3.0 if heat == "elevated" else 0.0
    action = str(item.get("action_label") or "")
    action_bonus = 6.0 if action == "ENTRY_READY" else 1.0 if action == "WATCH_WAIT_TRIGGER" else -8.0
    score = (
        entry_score
        + action_bonus
        + (float(item.get("industry_trend_score") or 0.0) * 4.0)
        + float(item.get("outcome_reliability_boost") or 0.0)
        + float(item.get("symbol_adoption_feedback_boost") or 0.0)
        - (risk_score * 20.0)
        - min(30.0, blocker_count * 10.0)
        - heat_penalty
    )
    return round(max(0.0, min(100.0, score)), 4)


def generate_daily_radar(
    repo: "TradingRepository",
    *,
    symbols: list[str],
    trade_date: date,
    target_count: int = 15,
    data_freshness_required: bool = True,
    risk_profile: str = "balanced",
    threshold_offset: float = 0.0,
    experiment_id: int | None = None,
    config_hash: str | None = None,
) -> list[dict]:
    if experiment_id is not None:
        from app.alerts.signal_engine import confirmed_signal_intents
        result=confirmed_signal_intents(repo,experiment_id=experiment_id,config_hash=config_hash,cutoff_at=trade_date.isoformat()+'T15:00:00+08:00')
        return [dict(row,experiment_id=experiment_id,config_hash=result['config_hash'],research_mode=result['mode'],coverage=result['coverage'],source='confirmed_swing_research') for row in result['candidates']]
    profile = normalize_risk_profile(risk_profile)
    cap = max(10, min(30, int(target_count or 15)))
    offset = _clip(float(threshold_offset or 0.0), -10.0, 10.0)
    if profile == "aggressive":
        entry_score_min = 66.0
        watch_score_min = 55.0
        blocker_liquidity_min = 0.18
        allow_soft_entry = True
    elif profile == "conservative":
        entry_score_min = 76.0
        watch_score_min = 64.0
        blocker_liquidity_min = 0.32
        allow_soft_entry = False
    else:
        entry_score_min = 72.0
        watch_score_min = 60.0
        blocker_liquidity_min = 0.25
        allow_soft_entry = False
    entry_score_min += offset
    watch_score_min += offset
    rows: list[dict] = []
    excluded_symbols = load_symbol_exclusions()

    for symbol in sorted(set(symbols)):
        exclusion_reason = excluded_symbols.get(str(symbol).strip().upper(), "")
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
        if scored.attention_active:
            blocker_tags.append("B_ATTENTION_ACTIVE")
        if scored.liquidity_score < blocker_liquidity_min:
            blocker_tags.append("B_LOW_LIQUIDITY")
        if limit_lock_risk(signal):
            blocker_tags.append("B_LIMIT_LOCK_RISK")
        if not scored.chip_available:
            blocker_tags.append("B_MISSING_CRITICAL_CHIP")
        if exclusion_reason:
            blocker_tags.append("B_EXCLUDED_SYMBOL")

        if scored.atr_heat == "hot":
            heat_tag = "ATR_HEAT=hot"
        elif scored.atr_heat == "elevated":
            heat_tag = "ATR_HEAT=elevated"
        else:
            heat_tag = "ATR_HEAT=normal"
        exclusion_reason_tags = [f"EXCLUDED_REASON={exclusion_reason}"] if exclusion_reason else []

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
            f"THRESHOLD_OFFSET={offset:.1f}",
            f"ENTRY_SCORE_MIN={entry_score_min:.1f}",
            f"WATCH_SCORE_MIN={watch_score_min:.1f}",
            f"INDUSTRY_GROUP={scored.industry_group}",
            *exclusion_reason_tags,
            f"ATR_PCT={scored.atr_pct * 100.0:.2f}",
            heat_tag,
            f"TREND_OK={bool(signal.get('trend_ok'))}",
            f"VOL_OK={bool(signal.get('vol_ok'))}",
            f"TRIGGER={bool(signal.get('trigger'))}",
            f"TRIGGER_REASON={signal.get('trigger_reason', 'none')}",
            f"CHIP_AVAILABLE={scored.chip_available}",
            f"CHIP_UP3={scored.chip_up3}",
            f"FUNDAMENTAL_QUALITY={scored.fundamental_data_quality}",
            f"REVENUE_SCORE={scored.revenue_score:.2f}",
            f"QUALITY_SCORE={scored.quality_score:.2f}",
            f"NEWS_RISK_SCORE={scored.news_risk_score:.2f}",
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
                "industry_group": scored.industry_group,
                "industry_rotation_score": 0.0,
                "industry_trend_label": "neutral",
                "industry_trend_score": 0.0,
                "industry_short_return": 0.0,
                "industry_medium_return": 0.0,
                "industry_breadth": 0.0,
                "atr_pct": float(scored.atr_pct),
                "atr_heat": scored.atr_heat,
                "fundamental_data_quality": scored.fundamental_data_quality,
                "revenue_score": float(scored.revenue_score),
                "quality_score": float(scored.quality_score),
                "valuation_or_growth_score": float(scored.valuation_or_growth_score),
                "news_risk_score": float(scored.news_risk_score),
                "fundamental_summary": scored.fundamental_summary,
                "news_summary": scored.news_summary,
            }
        )

    priority = {"ENTRY_READY": 2, "WATCH_WAIT_TRIGGER": 1, "NOT_RECOMMENDED": 0}
    trend_metrics = compute_group_trend_metrics(
        rows,
        repo=repo,
        trade_date=trade_date,
        symbol_getter=lambda item: str(item.get("symbol", "")),
        group_getter=lambda item: str(item.get("industry_group", "其他")),
    )

    def _set_rotation_and_trend(item: dict, score: float) -> None:
        group = str(item.get("industry_group") or "其他")
        trend = trend_metrics.get(group, {})
        trend_score = float(trend.get("score", 0.0) or 0.0) if trend else 0.0
        combined_score = (float(score) * 0.70) + (trend_score * 0.30) if trend else float(score)
        trend_label = str(trend.get("label", "neutral") or "neutral") if trend else "neutral"
        short_return = float(trend.get("short_return", 0.0) or 0.0) if trend else 0.0
        medium_return = float(trend.get("medium_return", 0.0) or 0.0) if trend else 0.0
        breadth = float(trend.get("breadth", 0.0) or 0.0) if trend else 0.0
        item.update(
            {
                "industry_rotation_score": float(combined_score),
                "industry_trend_label": trend_label,
                "industry_trend_score": trend_score,
                "industry_short_return": short_return,
                "industry_medium_return": medium_return,
                "industry_breadth": breadth,
                "reason_tags": [
                    *list(item.get("reason_tags", []))[:2],
                    f"INDUSTRY_ROTATION_SCORE={combined_score * 100.0:.1f}",
                    f"INDUSTRY_TREND={trend_label}",
                    f"INDUSTRY_TREND_SCORE={trend_score * 100.0:.1f}",
                    f"INDUSTRY_SHORT_RETURN={short_return * 100.0:.2f}",
                    f"INDUSTRY_MEDIUM_RETURN={medium_return * 100.0:.2f}",
                    f"INDUSTRY_BREADTH={breadth * 100.0:.1f}",
                    *list(item.get("reason_tags", []))[2:],
                ],
            }
        )

    apply_group_rotation_scores(
        rows,
        group_getter=lambda item: str(item.get("industry_group", "其他")),
        score_getter=lambda item: float(item.get("entry_score", 0.0)) / 100.0,
        setter=_set_rotation_and_trend,
    )
    reliability = _load_signal_reliability(repo, trade_date=trade_date)
    _apply_outcome_reliability(rows, reliability)
    symbol_feedback = _load_symbol_adoption_feedback(repo, [str(item.get("symbol") or "") for item in rows], trade_date=trade_date)
    _apply_symbol_adoption_feedback(rows, symbol_feedback)
    for item in rows:
        item["risk_adjusted_score"] = _risk_adjusted_score(item)
        item["reason_tags"].append(f"RISK_ADJUSTED_SCORE={float(item['risk_adjusted_score']):.2f}")
    rows.sort(
        key=lambda item: (
            priority.get(str(item.get("action_label")), 0),
            float(item.get("risk_adjusted_score", 0.0)),
            float(item.get("entry_score", 0.0)),
            -float(item.get("risk_score", 0.0)),
        ),
        reverse=True,
    )
    selected = select_with_group_diversity(
        rows,
        target_count=cap,
        group_getter=lambda item: str(item.get("industry_group", "其他")),
        risk_profile=profile,
    )
    for idx, item in enumerate(selected, start=1):
        item["rank"] = idx
    return selected
