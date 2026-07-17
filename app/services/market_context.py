from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import TypeVar

from app.services.universe import normalize_risk_profile


T = TypeVar("T")


ELECTRONICS_PREFIXES = {
    "23",
    "24",
    "30",
    "31",
    "32",
    "33",
    "34",
    "35",
    "36",
    "37",
    "49",
    "62",
    "64",
    "66",
    "80",
    "81",
}

SYMBOL_GROUPS = {
    "11": "水泥/建材",
    "12": "食品",
    "13": "塑化",
    "14": "紡織",
    "15": "電機機械",
    "16": "電器電纜",
    "17": "化學/生技",
    "18": "玻璃陶瓷",
    "20": "鋼鐵",
    "21": "橡膠",
    "22": "汽車",
    "25": "營建",
    "26": "航運/運輸",
    "27": "觀光",
    "28": "金融/保險",
    "29": "貿易百貨",
    "58": "金融/保險",
}


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


def classify_tw_symbol_group(symbol: str) -> str:
    code = str(symbol or "").strip()
    if len(code) < 2 or not code[:2].isdigit():
        return "其他"
    prefix = code[:2]
    if prefix in ELECTRONICS_PREFIXES:
        return "電子/科技"
    return SYMBOL_GROUPS.get(prefix, f"代號{prefix}群")


def market_regime_advice(
    repo,
    *,
    symbols: Sequence[str],
    trade_date: date,
    requested_risk_profile: str = "balanced",
    requested_threshold_offset: float = 0.0,
    lookback_days: int = 20,
    max_groups: int = 6,
) -> dict[str, object]:
    profile = normalize_risk_profile(requested_risk_profile)
    base_offset = max(-10.0, min(10.0, _safe_float(requested_threshold_offset)))
    if not hasattr(repo, "get_instrument_industry") or not hasattr(repo, "get_industry_return_context"):
        return _market_regime_neutral(
            profile=profile,
            offset=base_offset,
            reason="repo 缺少產業或市場脈絡資料，維持原始門檻。",
        )

    start_date = trade_date - timedelta(days=max(5, int(lookback_days or 20) * 2))
    group_counts: dict[str, int] = {}
    for symbol in symbols:
        code = str(symbol or "").strip().upper()
        if not code:
            continue
        try:
            group = str(repo.get_instrument_industry(code) or classify_tw_symbol_group(code) or "其他").strip() or "其他"
        except Exception:
            group = classify_tw_symbol_group(code)
        group_counts[group] = group_counts.get(group, 0) + 1
    if not group_counts:
        return _market_regime_neutral(profile=profile, offset=base_offset, reason="候選池沒有可辨識族群，維持原始門檻。")

    contexts: list[dict[str, object]] = []
    for group, count in sorted(group_counts.items(), key=lambda item: (-item[1], item[0]))[: max(1, int(max_groups or 6))]:
        try:
            context = repo.get_industry_return_context(
                industry=group,
                start_date=start_date,
                end_date=trade_date,
                min_bars=2,
            )
        except Exception:
            continue
        if not isinstance(context, dict) or context.get("status") != "ok":
            continue
        contexts.append({"group": group, "candidate_count": count, **context})

    if not contexts:
        return _market_regime_neutral(
            profile=profile,
            offset=base_offset,
            reason="市場脈絡樣本不足，維持原始門檻。",
        )

    total_weight = sum(int(item.get("candidate_count") or 1) for item in contexts)
    weighted_return = (
        sum(_safe_float(item.get("average_return")) * int(item.get("candidate_count") or 1) for item in contexts)
        / max(1, total_weight)
    )
    weighted_breadth = (
        sum(_safe_float(item.get("breadth"), 0.5) * int(item.get("candidate_count") or 1) for item in contexts)
        / max(1, total_weight)
    )
    headwind_count = sum(1 for item in contexts if str(item.get("label") or "") == "headwind")
    tailwind_count = sum(1 for item in contexts if str(item.get("label") or "") == "tailwind")
    regime = "neutral"
    label = "中性盤"
    offset_adjustment = 0.0
    suggested_profile = profile
    reason_parts = [
        f"族群加權 {weighted_return:.1%}",
        f"上漲廣度 {weighted_breadth:.0%}",
        f"順風 {tailwind_count} / 逆風 {headwind_count}",
    ]
    if weighted_return <= -0.03 and weighted_breadth <= 0.45:
        regime = "risk_off"
        label = "風險收縮"
        offset_adjustment = 2.0
        suggested_profile = "conservative"
        reason_parts.append("提高門檻並切保守風格")
    elif weighted_return >= 0.04 and weighted_breadth >= 0.58 and tailwind_count >= headwind_count:
        regime = "risk_on"
        label = "風險擴張"
        offset_adjustment = -1.0
        suggested_profile = "aggressive" if profile == "aggressive" else "balanced"
        reason_parts.append("小幅放寬門檻")
    elif weighted_breadth < 0.50 or headwind_count > tailwind_count:
        regime = "choppy"
        label = "震盪偏弱"
        offset_adjustment = 1.0
        suggested_profile = "conservative" if profile != "aggressive" else "balanced"
        reason_parts.append("盤勢分歧，門檻略收緊")
    else:
        reason_parts.append("維持原始門檻")

    effective_offset = max(-10.0, min(10.0, base_offset + offset_adjustment))
    return {
        "status": "ok",
        "method": "industry_context_weighted_regime",
        "regime": regime,
        "label": label,
        "requested_risk_profile": profile,
        "effective_risk_profile": suggested_profile,
        "requested_threshold_offset": base_offset,
        "threshold_offset_adjustment": offset_adjustment,
        "effective_threshold_offset": effective_offset,
        "weighted_return": weighted_return,
        "weighted_breadth": weighted_breadth,
        "tailwind_count": tailwind_count,
        "headwind_count": headwind_count,
        "context_count": len(contexts),
        "contexts": contexts[: max(1, int(max_groups or 6))],
        "reason": "；".join(reason_parts),
    }


def _market_regime_neutral(*, profile: str, offset: float, reason: str) -> dict[str, object]:
    return {
        "status": "insufficient",
        "method": "industry_context_weighted_regime",
        "regime": "unknown",
        "label": "市場狀態不足",
        "requested_risk_profile": profile,
        "effective_risk_profile": profile,
        "requested_threshold_offset": offset,
        "threshold_offset_adjustment": 0.0,
        "effective_threshold_offset": offset,
        "weighted_return": None,
        "weighted_breadth": None,
        "tailwind_count": 0,
        "headwind_count": 0,
        "context_count": 0,
        "contexts": [],
        "reason": reason,
    }


def compute_group_rotation_scores(
    items: Sequence[T],
    *,
    group_getter: Callable[[T], str],
    score_getter: Callable[[T], float],
    top_n: int = 3,
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for item in items:
        group = str(group_getter(item) or "其他")
        grouped.setdefault(group, []).append(_clip01(float(score_getter(item) or 0.0)))
    scores: dict[str, float] = {}
    for group, values in grouped.items():
        ranked = sorted(values, reverse=True)
        leaders = ranked[: max(1, int(top_n or 1))]
        leadership = sum(leaders) / len(leaders)
        breadth_bonus = min(len(values), 5) / 5 * 0.08
        scores[group] = _clip01((leadership * 0.92) + breadth_bonus)
    return scores


def apply_group_rotation_scores(
    items: Sequence[T],
    *,
    group_getter: Callable[[T], str],
    score_getter: Callable[[T], float],
    setter: Callable[[T, float], None],
    top_n: int = 3,
) -> dict[str, float]:
    scores = compute_group_rotation_scores(
        items,
        group_getter=group_getter,
        score_getter=score_getter,
        top_n=top_n,
    )
    for item in items:
        setter(item, scores.get(str(group_getter(item) or "其他"), 0.0))
    return scores


def _bar_close(bar) -> float | None:
    try:
        value = getattr(bar, "close", None)
        if value is None and isinstance(bar, dict):
            value = bar.get("close")
        return float(value)
    except Exception:
        return None


def _bar_date(bar) -> date | None:
    value = getattr(bar, "date", None)
    if value is None and isinstance(bar, dict):
        value = bar.get("date")
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _trend_label(score: float, short_return: float, medium_return: float, breadth: float) -> str:
    if score >= 0.68 and short_return > 0.0 and breadth >= 0.55:
        return "warming"
    if score >= 0.55 and medium_return >= -0.01:
        return "firm"
    if score <= 0.35 or (short_return <= -0.03 and breadth < 0.45):
        return "weak"
    if score <= 0.48 or short_return < 0.0:
        return "cooling"
    return "neutral"


def compute_group_trend_metrics(
    items: Sequence[T],
    *,
    repo,
    trade_date: date,
    symbol_getter: Callable[[T], str],
    group_getter: Callable[[T], str],
    short_days: int = 5,
    medium_days: int = 20,
) -> dict[str, dict[str, float | str | int]]:
    if not hasattr(repo, "get_daily_bars"):
        return {}

    grouped: dict[str, list[dict[str, float]]] = {}
    seen: set[str] = set()
    for item in items:
        symbol = str(symbol_getter(item) or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        group = str(group_getter(item) or "其他")
        try:
            bars = list(repo.get_daily_bars(symbol, end_date=trade_date))
        except Exception:
            continue
        cleaned = [
            (bar_date, close)
            for bar in bars
            if (bar_date := _bar_date(bar)) is not None
            and bar_date <= trade_date
            and (close := _bar_close(bar)) is not None
            and close > 0
        ]
        cleaned.sort(key=lambda row: row[0])
        if len(cleaned) < 2:
            continue
        closes = [row[1] for row in cleaned]
        last_close = closes[-1]
        short_index = max(0, len(closes) - max(2, int(short_days) + 1))
        medium_index = max(0, len(closes) - max(2, int(medium_days) + 1))
        short_base = closes[short_index]
        medium_base = closes[medium_index]
        if short_base <= 0 or medium_base <= 0:
            continue
        short_return = (last_close / short_base) - 1.0
        medium_return = (last_close / medium_base) - 1.0
        grouped.setdefault(group, []).append(
            {
                "short_return": short_return,
                "medium_return": medium_return,
                "up": 1.0 if short_return > 0 else 0.0,
            }
        )

    metrics: dict[str, dict[str, float | str | int]] = {}
    for group, rows in grouped.items():
        if not rows:
            continue
        avg_short = sum(row["short_return"] for row in rows) / len(rows)
        avg_medium = sum(row["medium_return"] for row in rows) / len(rows)
        breadth = sum(row["up"] for row in rows) / len(rows)
        short_component = _clip01(0.5 + (avg_short * 4.0))
        medium_component = _clip01(0.5 + (avg_medium * 2.0))
        trend_score = _clip01((short_component * 0.45) + (medium_component * 0.35) + (breadth * 0.20))
        metrics[group] = {
            "label": _trend_label(trend_score, avg_short, avg_medium, breadth),
            "score": trend_score,
            "short_return": avg_short,
            "medium_return": avg_medium,
            "breadth": breadth,
            "sample_size": len(rows),
        }
    return metrics


def atr_heat_level(atr_pct: float, risk_profile: str = "balanced") -> str:
    profile = normalize_risk_profile(risk_profile)
    value = max(0.0, float(atr_pct or 0.0))
    thresholds = {
        "conservative": (0.05, 0.035),
        "balanced": (0.06, 0.04),
        "aggressive": (0.08, 0.055),
    }
    hot_threshold, elevated_threshold = thresholds.get(profile, thresholds["balanced"])
    if value >= hot_threshold:
        return "hot"
    if value >= elevated_threshold:
        return "elevated"
    return "normal"


def group_limit_for_target(target_count: int, risk_profile: str = "balanced") -> int:
    cap = max(1, int(target_count or 1))
    profile = normalize_risk_profile(risk_profile)
    share = {
        "conservative": 0.30,
        "balanced": 0.35,
        "aggressive": 0.45,
    }.get(profile, 0.35)
    return max(2, min(5, math.ceil(cap * share)))


def select_with_group_diversity(
    items: Sequence[T],
    *,
    target_count: int,
    group_getter: Callable[[T], str],
    risk_profile: str = "balanced",
) -> list[T]:
    cap = max(1, int(target_count or 1))
    group_limit = group_limit_for_target(cap, risk_profile=risk_profile)
    selected: list[T] = []
    deferred: list[T] = []
    counts: dict[str, int] = {}

    for item in items:
        group = str(group_getter(item) or "其他")
        if counts.get(group, 0) < group_limit:
            selected.append(item)
            counts[group] = counts.get(group, 0) + 1
        else:
            deferred.append(item)
        if len(selected) >= cap:
            break

    if len(selected) < cap:
        selected_ids = {id(item) for item in selected}
        for item in [*deferred, *items]:
            if id(item) in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(id(item))
            if len(selected) >= cap:
                break
    return selected[:cap]
