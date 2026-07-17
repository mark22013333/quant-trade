from __future__ import annotations

from statistics import mean
from typing import Any


PROFILE_THRESHOLDS: dict[str, dict[str, Any]] = {
    "aggressive": {
        "key": "aggressive",
        "label": "積極",
        "entry_score_min": 66.0,
        "watch_score_min": 55.0,
        "allow_soft_entry": True,
    },
    "balanced": {
        "key": "balanced",
        "label": "平衡",
        "entry_score_min": 72.0,
        "watch_score_min": 60.0,
        "allow_soft_entry": False,
    },
    "conservative": {
        "key": "conservative",
        "label": "保守",
        "entry_score_min": 76.0,
        "watch_score_min": 64.0,
        "allow_soft_entry": False,
    },
}


def build_daily_radar_threshold_sweep(
    items: list[dict[str, Any]],
    *,
    base_profile: str | None = None,
    threshold_offsets: tuple[float, ...] = (-4.0, -2.0, 0.0, 2.0, 4.0),
) -> dict[str, Any]:
    """Summarize how latest radar candidates survive nearby entry/watch thresholds."""

    rows = [row for row in items if isinstance(row, dict)]
    if not rows:
        return {
            "status": "empty",
            "verdict": "資料不足",
            "risk_level": "watch",
            "robustness_score": 0,
            "reason": "no_daily_radar_items",
            "profiles": [],
            "threshold_variants": [],
            "stable_symbols": [],
            "fragile_symbols": [],
            "next_step": "先產生每日雷達，再做門檻穩健性掃描。",
        }

    profile_key = _normalize_profile(base_profile or _infer_profile(rows))
    baseline_profile = PROFILE_THRESHOLDS[profile_key]
    baseline = _evaluate_variant(rows, baseline_profile, entry_offset=0.0, watch_offset=0.0)
    profiles = [_evaluate_variant(rows, PROFILE_THRESHOLDS[key], entry_offset=0.0, watch_offset=0.0) for key in ("aggressive", "balanced", "conservative")]
    threshold_variants = [
        _evaluate_variant(rows, baseline_profile, entry_offset=offset, watch_offset=offset)
        for offset in threshold_offsets
    ]

    baseline_actionable = set(baseline["actionable_symbols"])
    strict_actionable = set(next(item for item in profiles if item["profile"] == "conservative")["actionable_symbols"])
    loose_actionable = set(next(item for item in profiles if item["profile"] == "aggressive")["actionable_symbols"])
    strict_entry = set(next(item for item in profiles if item["profile"] == "conservative")["entry_symbols"])
    baseline_entry = set(baseline["entry_symbols"])
    strictest_variant = max(threshold_variants, key=lambda item: float(item["entry_score_min"]))
    strictest_actionable = set(strictest_variant["actionable_symbols"])

    stable_symbols = sorted(baseline_actionable & strict_actionable & loose_actionable)
    stable_entry_symbols = sorted(baseline_entry & strict_entry)
    fragile_symbols = sorted(baseline_actionable - strictest_actionable)
    loose_only_symbols = sorted(loose_actionable - baseline_actionable)

    action_retention_values = [
        _ratio(item["actionable_count"], baseline["actionable_count"])
        for item in threshold_variants
        if float(item.get("entry_offset", 0.0)) >= 0.0
    ]
    strict_retention = _ratio(len(strict_actionable & baseline_actionable), len(baseline_actionable))
    stable_ratio = _ratio(len(stable_symbols), len(baseline_actionable))
    entry_stability = _ratio(len(stable_entry_symbols), max(1, len(baseline_entry)))
    retention_score = int(round((mean(action_retention_values) if action_retention_values else 0.0) * 35.0))
    strict_score = int(round(strict_retention * 25.0))
    stable_score = int(round(stable_ratio * 25.0))
    entry_score = int(round(entry_stability * 15.0))
    robustness_score = max(0, min(100, retention_score + strict_score + stable_score + entry_score))
    verdict, risk_level, next_step = _verdict_for_score(
        robustness_score,
        baseline_actionable_count=baseline["actionable_count"],
        stable_count=len(stable_symbols),
        fragile_count=len(fragile_symbols),
    )

    return {
        "status": "ok",
        "verdict": verdict,
        "risk_level": risk_level,
        "robustness_score": robustness_score,
        "base_profile": baseline_profile["key"],
        "base_profile_label": baseline_profile["label"],
        "baseline": _compact_variant(baseline),
        "profiles": [_compact_variant(item) for item in profiles],
        "threshold_variants": [_compact_variant(item) for item in threshold_variants],
        "metrics": {
            "candidate_count": len(rows),
            "baseline_actionable_count": baseline["actionable_count"],
            "baseline_entry_count": baseline["entry_count"],
            "stable_count": len(stable_symbols),
            "stable_entry_count": len(stable_entry_symbols),
            "fragile_count": len(fragile_symbols),
            "loose_only_count": len(loose_only_symbols),
            "strict_retention": strict_retention,
            "stable_ratio": stable_ratio,
            "entry_stability": entry_stability,
        },
        "stable_symbols": stable_symbols[:12],
        "stable_entry_symbols": stable_entry_symbols[:12],
        "fragile_symbols": fragile_symbols[:12],
        "loose_only_symbols": loose_only_symbols[:12],
        "next_step": next_step,
    }


def _evaluate_variant(
    rows: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    entry_offset: float,
    watch_offset: float,
) -> dict[str, Any]:
    entry_min = float(profile["entry_score_min"]) + float(entry_offset)
    watch_min = float(profile["watch_score_min"]) + float(watch_offset)
    entry_symbols: list[str] = []
    watch_symbols: list[str] = []
    blocked_symbols: list[str] = []
    score_values: list[float] = []

    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        score = _number(row.get("entry_score"))
        score_values.append(score)
        blockers = row.get("blocker_tags") if isinstance(row.get("blocker_tags"), list) else []
        if blockers:
            blocked_symbols.append(symbol)
            continue
        if score >= entry_min and _entry_signal_available(row, allow_soft_entry=bool(profile.get("allow_soft_entry"))):
            entry_symbols.append(symbol)
        elif score >= watch_min:
            watch_symbols.append(symbol)

    actionable_symbols = [*entry_symbols, *watch_symbols]
    return {
        "profile": str(profile["key"]),
        "profile_label": str(profile["label"]),
        "entry_offset": float(entry_offset),
        "watch_offset": float(watch_offset),
        "entry_score_min": entry_min,
        "watch_score_min": watch_min,
        "entry_count": len(entry_symbols),
        "watch_count": len(watch_symbols),
        "actionable_count": len(actionable_symbols),
        "blocked_count": len(blocked_symbols),
        "entry_symbols": entry_symbols,
        "watch_symbols": watch_symbols,
        "actionable_symbols": actionable_symbols,
        "blocked_symbols": blocked_symbols,
        "average_entry_score": mean(score_values) if score_values else 0.0,
    }


def _entry_signal_available(row: dict[str, Any], *, allow_soft_entry: bool) -> bool:
    action = str(row.get("action_label") or "")
    if action == "ENTRY_READY":
        return True
    if not allow_soft_entry:
        return False
    tags = _tag_map(row.get("reason_tags") if isinstance(row.get("reason_tags"), list) else [])
    return tags.get("TREND_OK") == "True" and tags.get("VOL_OK") == "True"


def _tag_map(tags: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for tag in tags:
        text = str(tag or "")
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _infer_profile(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        tags = row.get("reason_tags") if isinstance(row.get("reason_tags"), list) else []
        profile = _tag_map(tags).get("PROFILE")
        if profile:
            return profile
    return "balanced"


def _normalize_profile(profile: str | None) -> str:
    value = str(profile or "balanced").strip().lower()
    return value if value in PROFILE_THRESHOLDS else "balanced"


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _ratio(numerator: int | float, denominator: int | float) -> float:
    try:
        den = float(denominator)
        if den <= 0:
            return 0.0
        return max(0.0, min(1.0, float(numerator) / den))
    except Exception:
        return 0.0


def _compact_variant(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": item["profile"],
        "profile_label": item["profile_label"],
        "entry_offset": item["entry_offset"],
        "watch_offset": item["watch_offset"],
        "entry_score_min": item["entry_score_min"],
        "watch_score_min": item["watch_score_min"],
        "entry_count": item["entry_count"],
        "watch_count": item["watch_count"],
        "actionable_count": item["actionable_count"],
        "blocked_count": item["blocked_count"],
        "entry_symbols": item["entry_symbols"][:8],
        "watch_symbols": item["watch_symbols"][:8],
        "actionable_symbols": item["actionable_symbols"][:12],
    }


def _verdict_for_score(
    score: int,
    *,
    baseline_actionable_count: int,
    stable_count: int,
    fragile_count: int,
) -> tuple[str, str, str]:
    if baseline_actionable_count <= 0:
        return "資料不足", "watch", "目前沒有可行動標的，先重跑每日雷達或放寬觀察池。"
    if score >= 75 and stable_count > 0:
        return "穩健區間", "ok", "優先研究跨門檻仍留存的穩定標的。"
    if score >= 55:
        return "可觀察", "watch", "先研究穩定標的，門檻邊緣標的只放觀察。"
    if fragile_count >= baseline_actionable_count:
        return "門檻脆弱", "warn", "候選主要靠單一門檻撐住，先調高門檻或擴大樣本驗證。"
    return "門檻敏感", "warn", "先比較保守與平衡檔位差異，再決定是否建立 Preview。"
