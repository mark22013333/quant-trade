from __future__ import annotations

import shlex
from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Callable

from app.reports.daily_radar_threshold_sweep import PROFILE_THRESHOLDS


DEFAULT_THRESHOLD_OFFSETS: tuple[float, ...] = (-4.0, -2.0, 0.0, 2.0, 4.0)
DEFAULT_LOOKAHEAD_BARS: tuple[int, ...] = (5, 10, 20)


def build_daily_radar_threshold_backtest(
    runs: list[dict[str, Any]],
    *,
    base_profile: str | None = None,
    threshold_offsets: tuple[float, ...] = DEFAULT_THRESHOLD_OFFSETS,
    lookahead_bars_list: tuple[int, ...] = DEFAULT_LOOKAHEAD_BARS,
    min_samples: int = 3,
    adoption_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_runs = sorted(
        [run for run in runs if isinstance(run, dict) and isinstance(run.get("items"), list)],
        key=_run_sort_key,
    )
    horizons = tuple(sorted({max(1, min(60, int(value or 0))) for value in lookahead_bars_list if int(value or 0) > 0}))
    if not clean_runs or not horizons:
        return {
            "status": "insufficient",
            "reason": "no_historical_daily_radar_runs",
            "verdict": "等待歷史樣本",
            "risk_level": "watch",
            "robustness_score": 0,
            "lookahead_bars_list": list(horizons or DEFAULT_LOOKAHEAD_BARS),
            "evaluated_run_count": 0,
            "threshold_variants": [],
            "recommended_action": _threshold_recommended_action(
                None,
                robustness_score=0,
                baseline_variant={},
                best_variant={},
                viable_count=0,
                min_samples=min_samples,
            ),
            "recommended_config": None,
            "optimization_candidates": [],
            "pool_recommendation": _pool_recommendation([]),
            "optimization_plan": _optimization_plan([]),
            "threshold_heatmap": _threshold_heatmap([], {}, {}, []),
            "walk_forward_windows": _walk_forward_windows([], [], tuple(horizons or DEFAULT_LOOKAHEAD_BARS), min_samples=min_samples),
            "walk_forward_heatmap": _walk_forward_heatmap({}, []),
            "adoption_feedback": _compact_adoption_feedback(adoption_feedback),
            "segments": {"market": [], "profile": [], "industry": []},
            "next_step": "先累積多輪每日雷達與後續日線，再做跨期間門檻實跑。",
        }

    profile_key = _normalize_profile(base_profile or _infer_base_profile(clean_runs))
    offsets = tuple(float(offset) for offset in threshold_offsets)
    variant_defs = [(profile, offset) for profile in ("aggressive", "balanced", "conservative") for offset in offsets]
    threshold_variants = [
        _evaluate_variant(clean_runs, profile, offset, horizons, min_samples=min_samples)
        for profile, offset in variant_defs
    ]
    baseline = next(
        (
            variant
            for variant in threshold_variants
            if variant["profile"] == profile_key and abs(float(variant["entry_offset"])) < 0.0001
        ),
        threshold_variants[0],
    )
    best_variant = max(
        threshold_variants,
        key=lambda item: (
            float(item.get("multi_horizon_score") or 0.0),
            int(item.get("evaluated_count") or 0),
            float(item.get("average_return") or 0.0),
        ),
    )
    viable_variants = [
        variant
        for variant in threshold_variants
        if int(variant.get("evaluated_count") or 0) >= max(1, int(min_samples))
        and float(variant.get("multi_horizon_score") or 0.0) >= max(0.0, float(best_variant.get("multi_horizon_score") or 0.0) - 8.0)
    ]
    horizon_summary = [
        _best_horizon_variant(threshold_variants, horizon)
        for horizon in horizons
    ]
    score = _robustness_score(baseline, best_variant, viable_variants, horizons)
    verdict, risk_level, next_step = _verdict_for_score(
        score,
        baseline_score=float(baseline.get("multi_horizon_score") or 0.0),
        best_score=float(best_variant.get("multi_horizon_score") or 0.0),
        viable_count=len(viable_variants),
    )
    candidate_count = sum(len(run.get("items") or []) for run in clean_runs)
    segments = {
        "market": _segment_summaries(clean_runs, variant_defs, horizons, min_samples, _market_segment, limit=6),
        "profile": _segment_summaries(clean_runs, variant_defs, horizons, min_samples, _profile_segment, limit=3),
        "industry": _segment_summaries(clean_runs, variant_defs, horizons, min_samples, _industry_segment, limit=6),
    }
    walk_forward_windows = _walk_forward_windows(clean_runs, variant_defs, horizons, min_samples=min_samples)
    walk_forward_heatmap = _walk_forward_heatmap(walk_forward_windows, variant_defs)
    optimization_candidates = _optimization_candidates(
        clean_runs,
        variant_defs,
        horizons,
        min_samples=min_samples,
        limit=8,
        adoption_feedback=adoption_feedback,
        walk_forward_windows=walk_forward_windows,
    )
    pool_recommendation = _pool_recommendation(optimization_candidates)
    recommended_config = optimization_candidates[0] if optimization_candidates else None
    recommended_action = _threshold_recommended_action(
        recommended_config,
        robustness_score=score,
        baseline_variant=baseline,
        best_variant=best_variant,
        viable_count=len(viable_variants),
        min_samples=min_samples,
    )

    return {
        "status": "ok" if int(best_variant.get("evaluated_count") or 0) >= max(1, int(min_samples)) else "insufficient",
        "reason": "" if int(best_variant.get("evaluated_count") or 0) >= max(1, int(min_samples)) else "not_enough_matured_outcomes",
        "verdict": verdict,
        "risk_level": risk_level,
        "robustness_score": score,
        "base_profile": profile_key,
        "base_profile_label": PROFILE_THRESHOLDS[profile_key]["label"],
        "lookahead_bars_list": list(horizons),
        "lookback_runs": len(clean_runs),
        "evaluated_run_count": max(int(variant.get("evaluated_run_count") or 0) for variant in threshold_variants),
        "candidate_count": candidate_count,
        "min_samples": max(1, int(min_samples)),
        "baseline_variant": _compact_variant(baseline),
        "best_variant": _compact_variant(best_variant),
        "stable_zone": [_compact_variant(variant) for variant in viable_variants[:8]],
        "horizon_summary": horizon_summary,
        "threshold_variants": [_compact_variant(variant) for variant in threshold_variants],
        "threshold_heatmap": _threshold_heatmap(threshold_variants, baseline, best_variant, viable_variants),
        "recommended_action": recommended_action,
        "recommended_config": recommended_config,
        "optimization_candidates": optimization_candidates,
        "pool_recommendation": pool_recommendation,
        "optimization_plan": _optimization_plan(optimization_candidates),
        "walk_forward_windows": walk_forward_windows,
        "walk_forward_heatmap": walk_forward_heatmap,
        "adoption_feedback": _compact_adoption_feedback(adoption_feedback),
        "segments": segments,
        "next_step": next_step,
    }


def _evaluate_variant(
    runs: list[dict[str, Any]],
    profile_key: str,
    offset: float,
    horizons: tuple[int, ...],
    *,
    min_samples: int,
) -> dict[str, Any]:
    profile = PROFILE_THRESHOLDS[_normalize_profile(profile_key)]
    selected_symbol_counts: Counter[str] = Counter()
    horizon_results = [
        _evaluate_horizon(runs, profile, offset, horizon, selected_symbol_counts)
        for horizon in horizons
    ]
    scored_horizons = [item for item in horizon_results if int(item.get("evaluated_count") or 0) >= max(1, int(min_samples))]
    if scored_horizons:
        multi_score = mean(float(item.get("score") or 0.0) for item in scored_horizons)
        positive_values = [float(item.get("positive_rate") or 0.0) for item in scored_horizons if item.get("positive_rate") is not None]
        return_values = [float(item.get("average_return") or 0.0) for item in scored_horizons if item.get("average_return") is not None]
    else:
        multi_score = 0.0
        positive_values = []
        return_values = []
    evaluated_count = sum(int(item.get("evaluated_count") or 0) for item in horizon_results)
    return {
        "profile": profile["key"],
        "profile_label": profile["label"],
        "entry_offset": float(offset),
        "watch_offset": float(offset),
        "entry_score_min": float(profile["entry_score_min"]) + float(offset),
        "watch_score_min": float(profile["watch_score_min"]) + float(offset),
        "multi_horizon_score": round(multi_score, 1),
        "evaluated_count": evaluated_count,
        "evaluated_run_count": max((int(item.get("evaluated_run_count") or 0) for item in horizon_results), default=0),
        "average_positive_rate": mean(positive_values) if positive_values else None,
        "average_return": mean(return_values) if return_values else None,
        "horizons": horizon_results,
        "top_symbols": [symbol for symbol, _count in selected_symbol_counts.most_common(8)],
    }


def _evaluate_horizon(
    runs: list[dict[str, Any]],
    profile: dict[str, Any],
    offset: float,
    horizon: int,
    selected_symbol_counts: Counter[str] | None = None,
) -> dict[str, Any]:
    returns: list[float] = []
    run_returns: list[float] = []
    selected_counts: list[int] = []
    empty_run_count = 0
    entry_min = float(profile["entry_score_min"]) + float(offset)
    watch_min = float(profile["watch_score_min"]) + float(offset)
    for run in runs:
        selected = [
            item
            for item in run.get("items", [])
            if isinstance(item, dict) and _row_is_actionable(item, profile, entry_min=entry_min, watch_min=watch_min)
        ]
        selected_counts.append(len(selected))
        if selected_symbol_counts is not None:
            for item in selected:
                symbol = str(item.get("symbol") or "").strip()
                if symbol:
                    selected_symbol_counts[symbol] += 1
        evaluated_returns = [
            float(value)
            for item in selected
            for value in [_horizon_return(item, horizon)]
            if value is not None and _horizon_matured(item, horizon)
        ]
        if not evaluated_returns:
            empty_run_count += 1
            continue
        returns.extend(evaluated_returns)
        run_returns.append(mean(evaluated_returns))

    evaluated_count = len(returns)
    positive_count = sum(1 for value in returns if value > 0.0)
    positive_rate = (positive_count / evaluated_count) if evaluated_count else None
    average_return = mean(returns) if returns else None
    average_run_return = mean(run_returns) if run_returns else None
    return {
        "lookahead_bars": int(horizon),
        "selected_count": sum(selected_counts),
        "average_selected_per_run": mean(selected_counts) if selected_counts else 0.0,
        "evaluated_count": evaluated_count,
        "evaluated_run_count": len(run_returns),
        "empty_run_count": empty_run_count,
        "positive_count": positive_count,
        "positive_rate": positive_rate,
        "average_return": average_return,
        "average_run_return": average_run_return,
        "best_run_return": max(run_returns) if run_returns else None,
        "worst_run_return": min(run_returns) if run_returns else None,
        "score": _horizon_score(positive_rate, average_return, len(run_returns), len(runs)),
    }


def _horizon_score(
    positive_rate: float | None,
    average_return: float | None,
    evaluated_runs: int,
    total_runs: int,
) -> int:
    if positive_rate is None or average_return is None or evaluated_runs <= 0:
        return 0
    hit_score = max(0.0, min(1.0, float(positive_rate)))
    return_score = max(0.0, min(1.0, (float(average_return) + 0.02) / 0.06))
    coverage_score = max(0.0, min(1.0, float(evaluated_runs) / max(1.0, float(total_runs))))
    return int(round(hit_score * 45.0 + return_score * 35.0 + coverage_score * 20.0))


def _robustness_score(
    baseline: dict[str, Any],
    best_variant: dict[str, Any],
    viable_variants: list[dict[str, Any]],
    horizons: tuple[int, ...],
) -> int:
    baseline_score = float(baseline.get("multi_horizon_score") or 0.0)
    best_score = float(best_variant.get("multi_horizon_score") or 0.0)
    baseline_retention = (baseline_score / best_score) if best_score > 0 else 0.0
    viable_ratio = len(viable_variants) / max(1, 3 * 5)
    horizon_coverage = sum(
        1
        for horizon in horizons
        for result in baseline.get("horizons", [])
        if int(result.get("lookahead_bars") or 0) == int(horizon) and int(result.get("evaluated_count") or 0) > 0
    ) / max(1, len(horizons))
    score = (
        max(0.0, min(100.0, best_score)) * 0.45
        + max(0.0, min(1.0, baseline_retention)) * 30.0
        + max(0.0, min(1.0, viable_ratio)) * 15.0
        + max(0.0, min(1.0, horizon_coverage)) * 10.0
    )
    return int(round(max(0.0, min(100.0, score))))


def _verdict_for_score(score: int, *, baseline_score: float, best_score: float, viable_count: int) -> tuple[str, str, str]:
    if best_score <= 0:
        return "等待成熟樣本", "watch", "先累積後續日線 outcome，再比較門檻區間。"
    if score >= 72 and viable_count >= 3:
        return "門檻有歷史支撐", "ok", "優先使用穩定區間內仍留存、且跨週期表現不差的標的。"
    if score >= 55 and best_score - baseline_score <= 10:
        return "門檻可觀察", "watch", "基準門檻尚可，進場前再看市場池與族群分層弱點。"
    if best_score - baseline_score > 12:
        return "需調整門檻", "warn", "最佳門檻明顯優於基準，先用歷史較穩的門檻做觀察，不急著放大部位。"
    return "門檻勝率不足", "warn", "目前歷史樣本不支持積極挑標的，先縮小市場池或提高門檻。"


def _best_horizon_variant(variants: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    rows = [
        (variant, result)
        for variant in variants
        for result in variant.get("horizons", [])
        if int(result.get("lookahead_bars") or 0) == int(horizon)
    ]
    if not rows:
        return {"lookahead_bars": int(horizon), "status": "insufficient"}
    variant, result = max(
        rows,
        key=lambda item: (
            int(item[1].get("score") or 0),
            int(item[1].get("evaluated_count") or 0),
            float(item[1].get("average_return") or 0.0),
        ),
    )
    return {
        "lookahead_bars": int(horizon),
        "profile": variant.get("profile"),
        "profile_label": variant.get("profile_label"),
        "entry_offset": variant.get("entry_offset"),
        "watch_offset": variant.get("watch_offset"),
        "score": result.get("score"),
        "evaluated_count": result.get("evaluated_count"),
        "positive_rate": result.get("positive_rate"),
        "average_return": result.get("average_return"),
    }


def _walk_forward_windows(
    runs: list[dict[str, Any]],
    variant_defs: list[tuple[str, float]],
    horizons: tuple[int, ...],
    *,
    min_samples: int,
    max_windows: int = 6,
) -> dict[str, Any]:
    clean_runs = sorted([run for run in runs if isinstance(run, dict)], key=_run_sort_key)
    if len(clean_runs) < 3 or not variant_defs or not horizons:
        return {
            "status": "insufficient",
            "method": "rolling_train_validate",
            "window_count": 0,
            "windows": [],
            "summary": {},
            "next_step": "先累積至少 3 輪成熟每日雷達，再做 rolling walk-forward 驗證。",
        }

    train_size = max(2, min(len(clean_runs) - 1, max(2, len(clean_runs) // 2)))
    validation_size = 1
    rows: list[dict[str, Any]] = []
    for start in range(0, len(clean_runs) - train_size - validation_size + 1):
        train_runs = clean_runs[start:start + train_size]
        validation_runs = clean_runs[start + train_size:start + train_size + validation_size]
        if not validation_runs:
            continue
        train_variants = [
            _evaluate_variant(train_runs, profile, offset, horizons, min_samples=max(1, int(min_samples)))
            for profile, offset in variant_defs
        ]
        best_train = max(
            train_variants,
            key=lambda item: (
                float(item.get("multi_horizon_score") or 0.0),
                int(item.get("evaluated_count") or 0),
                float(item.get("average_return") or 0.0),
            ),
        )
        validation_variant = _evaluate_variant(
            validation_runs,
            str(best_train.get("profile") or "balanced"),
            float(best_train.get("entry_offset") or 0.0),
            horizons,
            min_samples=1,
        )
        pool_results = _walk_forward_pool_results(
            train_runs,
            validation_runs,
            variant_defs,
            horizons,
            min_samples=max(1, int(min_samples)),
        )
        validation_return = validation_variant.get("average_return")
        rows.append(
            {
                "index": len(rows) + 1,
                "train_run_count": len(train_runs),
                "validation_run_count": len(validation_runs),
                "train_start": _run_date_text(train_runs[0]),
                "train_end": _run_date_text(train_runs[-1]),
                "validation_start": _run_date_text(validation_runs[0]),
                "validation_end": _run_date_text(validation_runs[-1]),
                "profile": best_train.get("profile"),
                "profile_label": best_train.get("profile_label"),
                "entry_offset": best_train.get("entry_offset"),
                "train_score": best_train.get("multi_horizon_score"),
                "train_evaluated_count": best_train.get("evaluated_count"),
                "validation_score": validation_variant.get("multi_horizon_score"),
                "validation_evaluated_count": validation_variant.get("evaluated_count"),
                "validation_positive_rate": validation_variant.get("average_positive_rate"),
                "validation_average_return": validation_return,
                "validation_status": _walk_forward_window_status(validation_variant),
                "top_symbols": validation_variant.get("top_symbols", [])[:6],
                "pool_results": pool_results,
            }
        )
        if len(rows) >= max(1, int(max_windows)):
            break

    if not rows:
        return {
            "status": "insufficient",
            "method": "rolling_train_validate",
            "window_count": 0,
            "windows": [],
            "summary": {},
            "next_step": "成熟 outcome 不足，先累積更多每日雷達後再驗證。",
        }
    returns = [
        float(row.get("validation_average_return") or 0.0)
        for row in rows
        if row.get("validation_average_return") is not None
    ]
    scores = [float(row.get("validation_score") or 0.0) for row in rows]
    ok_count = sum(1 for row in rows if row.get("validation_status") == "ok")
    positive_count = sum(1 for value in returns if value > 0.0)
    summary = {
        "window_count": len(rows),
        "ok_count": ok_count,
        "positive_window_count": positive_count,
        "positive_window_rate": (positive_count / len(returns)) if returns else None,
        "average_validation_score": round(mean(scores), 1) if scores else 0.0,
        "average_validation_return": mean(returns) if returns else None,
        "worst_validation_return": min(returns) if returns else None,
        "best_validation_return": max(returns) if returns else None,
    }
    if ok_count >= max(1, len(rows) // 2) and (summary["average_validation_return"] is None or float(summary["average_validation_return"]) >= 0.0):
        verdict = "walk-forward 可觀察"
        risk_level = "ok"
        next_step = "可把最高分探索組合拿去做小批次 probe，並等待下一批 outcome 成熟。"
    elif positive_count > 0:
        verdict = "walk-forward 混合"
        risk_level = "watch"
        next_step = "部分驗證視窗有效，先縮小市場池或降低採用權重。"
    else:
        verdict = "walk-forward 偏弱"
        risk_level = "warn"
        next_step = "訓練門檻移到後段驗證表現偏弱，先不要放大部位。"
    return {
        "status": "ok",
        "method": "rolling_train_validate",
        "train_window_size": train_size,
        "validation_window_size": validation_size,
        "window_count": len(rows),
        "verdict": verdict,
        "risk_level": risk_level,
        "summary": summary,
        "windows": rows,
        "next_step": next_step,
    }


def _walk_forward_window_status(variant: dict[str, Any]) -> str:
    score = float(variant.get("multi_horizon_score") or 0.0)
    average_return = variant.get("average_return")
    evaluated_count = int(variant.get("evaluated_count") or 0)
    if evaluated_count <= 0:
        return "empty"
    if score >= 60 and (average_return is None or float(average_return) >= 0.0):
        return "ok"
    if score >= 40 or (average_return is not None and float(average_return) > 0.0):
        return "watch"
    return "weak"


def _walk_forward_pool_results(
    train_runs: list[dict[str, Any]],
    validation_runs: list[dict[str, Any]],
    variant_defs: list[tuple[str, float]],
    horizons: tuple[int, ...],
    *,
    min_samples: int,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pools = _optimization_pools(train_runs, min_samples=max(1, int(min_samples)), industry_limit=4)
    for pool in pools:
        pool_key = str(pool.get("key") or "")
        pool_type = str(pool.get("type") or "all")
        pool_label = str(pool.get("label") or pool_key or "全部候選")
        train_pool_runs = pool.get("runs") if isinstance(pool.get("runs"), list) else []
        validation_pool_runs = _validation_runs_for_pool(
            validation_runs,
            pool_key=pool_key,
            pool_type=pool_type,
            pool_label=pool_label,
        )
        if not train_pool_runs or not validation_pool_runs:
            continue
        train_variants = [
            _evaluate_variant(train_pool_runs, profile, offset, horizons, min_samples=max(1, int(min_samples)))
            for profile, offset in variant_defs
        ]
        best_train = max(
            train_variants,
            key=lambda item: (
                float(item.get("multi_horizon_score") or 0.0),
                int(item.get("evaluated_count") or 0),
                float(item.get("average_return") or 0.0),
            ),
        )
        validation_variant = _evaluate_variant(
            validation_pool_runs,
            str(best_train.get("profile") or "balanced"),
            float(best_train.get("entry_offset") or 0.0),
            horizons,
            min_samples=1,
        )
        rows.append(
            {
                "pool_key": pool_key,
                "pool_label": pool_label,
                "pool_type": pool_type,
                "profile": best_train.get("profile"),
                "profile_label": best_train.get("profile_label"),
                "entry_offset": best_train.get("entry_offset"),
                "train_score": best_train.get("multi_horizon_score"),
                "validation_score": validation_variant.get("multi_horizon_score"),
                "validation_evaluated_count": validation_variant.get("evaluated_count"),
                "validation_positive_rate": validation_variant.get("average_positive_rate"),
                "validation_average_return": validation_variant.get("average_return"),
                "validation_status": _walk_forward_window_status(validation_variant),
                "top_symbols": validation_variant.get("top_symbols", [])[:6],
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            float(item.get("validation_score") or 0.0),
            int(item.get("validation_evaluated_count") or 0),
            float(item.get("validation_average_return") or 0.0),
        ),
        reverse=True,
    )[: max(1, int(limit))]


def _validation_runs_for_pool(
    runs: list[dict[str, Any]],
    *,
    pool_key: str,
    pool_type: str,
    pool_label: str,
) -> list[dict[str, Any]]:
    if pool_type == "all" or pool_key == "all":
        return runs
    if pool_type == "market":
        key = pool_key.split(":", 1)[1] if ":" in pool_key else pool_label
        grouped, _labels, _counts = _group_runs_by_segment(runs, _market_segment)
        return grouped.get(key, [])
    if pool_type == "industry":
        key = pool_key.split(":", 1)[1] if ":" in pool_key else pool_label
        grouped, _labels, _counts = _group_runs_by_segment(runs, _industry_segment)
        return grouped.get(key, [])
    return []


def _walk_forward_adjustment_by_variant(payload: dict[str, Any] | None) -> dict[tuple[str, float], dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    windows = data.get("windows") if isinstance(data.get("windows"), list) else []
    grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for window in windows:
        if not isinstance(window, dict):
            continue
        key = (_normalize_profile(str(window.get("profile") or "balanced")), _rounded_offset(window.get("entry_offset")))
        grouped[key].append(window)
    output: dict[tuple[str, float], dict[str, Any]] = {}
    for key, rows in grouped.items():
        returns = [
            float(row.get("validation_average_return") or 0.0)
            for row in rows
            if row.get("validation_average_return") is not None
        ]
        scores = [float(row.get("validation_score") or 0.0) for row in rows]
        ok_count = sum(1 for row in rows if row.get("validation_status") == "ok")
        positive_count = sum(1 for value in returns if value > 0.0)
        positive_rate = (positive_count / len(returns)) if returns else None
        average_return = mean(returns) if returns else None
        average_score = mean(scores) if scores else 0.0
        adjustment = _walk_forward_adjustment_score(
            ok_count=ok_count,
            window_count=len(rows),
            positive_rate=positive_rate,
            average_return=average_return,
            average_score=average_score,
        )
        output[key] = {
            "window_count": len(rows),
            "ok_count": ok_count,
            "positive_rate": positive_rate,
            "average_return": average_return,
            "average_score": average_score,
            "adjustment": adjustment,
        }
    return output


def _walk_forward_heatmap(payload: dict[str, Any] | None, variant_defs: list[tuple[str, float]]) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    evidence_by_key = _walk_forward_adjustment_by_variant(data)
    profile_order = ["aggressive", "balanced", "conservative"]
    profile_labels = {
        profile: PROFILE_THRESHOLDS[profile]["label"]
        for profile in profile_order
        if profile in PROFILE_THRESHOLDS
    }
    offsets = sorted({_rounded_offset(offset) for _profile, offset in variant_defs})
    if not offsets:
        offsets = sorted({key[1] for key in evidence_by_key})
    matrix: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for profile in profile_order:
        row_cells: list[dict[str, Any]] = []
        for offset in offsets:
            evidence = evidence_by_key.get((profile, offset), {})
            cell = _walk_forward_heatmap_cell(
                evidence,
                profile=profile,
                profile_label=profile_labels.get(profile, profile),
                offset=offset,
            )
            row_cells.append(cell)
            cells.append(cell)
        matrix.append(
            {
                "profile": profile,
                "profile_label": profile_labels.get(profile, profile),
                "cells": row_cells,
            }
        )

    evaluated_cells = [cell for cell in cells if int(cell.get("window_count") or 0) > 0]
    strongest = max(
        evaluated_cells,
        key=lambda cell: (
            float(cell.get("adjustment") or 0.0),
            float(cell.get("average_score") or 0.0),
            int(cell.get("ok_count") or 0),
            float(cell.get("average_return") or 0.0),
        ),
        default=None,
    )
    weakening_count = sum(1 for cell in evaluated_cells if float(cell.get("adjustment") or 0.0) < -0.25)
    return {
        "status": "ok" if evaluated_cells else "insufficient",
        "method": "rolling_train_validate_heatmap",
        "profile_order": profile_order,
        "profile_labels": profile_labels,
        "offsets": offsets,
        "evaluated_cell_count": len(evaluated_cells),
        "weakening_cell_count": weakening_count,
        "matrix": matrix,
        "strongest_cell": strongest,
        "summary": _walk_forward_heatmap_summary(
            strongest,
            evaluated_count=len(evaluated_cells),
            weakening_count=weakening_count,
        ),
    }


def _walk_forward_heatmap_cell(
    evidence: dict[str, Any],
    *,
    profile: str,
    profile_label: str,
    offset: float,
) -> dict[str, Any]:
    window_count = int(evidence.get("window_count") or 0)
    ok_count = int(evidence.get("ok_count") or 0)
    average_score = float(evidence.get("average_score") or 0.0)
    average_return = evidence.get("average_return")
    adjustment = float(evidence.get("adjustment") or 0.0)
    tone = _walk_forward_heatmap_tone(
        window_count=window_count,
        adjustment=adjustment,
        average_score=average_score,
        average_return=float(average_return) if average_return is not None else None,
    )
    badges = []
    if adjustment >= 1.5:
        badges.append("加權")
    elif adjustment <= -1.5:
        badges.append("降權")
    if ok_count >= max(1, window_count // 2) and window_count > 0:
        badges.append("穩定")
    return {
        "key": _variant_key_text((profile, offset)),
        "profile": profile,
        "profile_label": profile_label,
        "entry_offset": offset,
        "watch_offset": offset,
        "window_count": window_count,
        "ok_count": ok_count,
        "positive_rate": evidence.get("positive_rate"),
        "average_return": average_return,
        "average_score": round(average_score, 1),
        "adjustment": round(adjustment, 1),
        "tone": tone,
        "badges": badges,
    }


def _walk_forward_heatmap_tone(
    *,
    window_count: int,
    adjustment: float,
    average_score: float,
    average_return: float | None,
) -> str:
    if window_count <= 0:
        return "empty"
    if adjustment >= 1.5 and average_score >= 60 and (average_return is None or average_return >= 0.0):
        return "strong"
    if adjustment > 0.0 and (average_return is None or average_return >= 0.0):
        return "ok"
    if adjustment >= -0.25 or (average_return is not None and average_return > 0.0):
        return "watch"
    return "weak"


def _walk_forward_heatmap_summary(
    strongest: dict[str, Any] | None,
    *,
    evaluated_count: int,
    weakening_count: int,
) -> str:
    if not strongest:
        return "rolling 熱區還沒有足夠視窗，先累積更多成熟 outcome。"
    profile = str(strongest.get("profile_label") or strongest.get("profile") or "-")
    offset = float(strongest.get("entry_offset") or 0.0)
    adjustment = float(strongest.get("adjustment") or 0.0)
    return (
        f"out-of-sample 較穩的是 {profile} offset {offset:+.1f}，rolling 加權 {adjustment:+.1f}；"
        f"{evaluated_count} 格已有 rolling 證據，{weakening_count} 格需要降權。"
    )


def _walk_forward_adjustment_by_pool_variant(payload: dict[str, Any] | None) -> dict[tuple[str, str, float], dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    windows = data.get("windows") if isinstance(data.get("windows"), list) else []
    grouped: dict[tuple[str, str, float], list[dict[str, Any]]] = defaultdict(list)
    for window in windows:
        pool_results = window.get("pool_results") if isinstance(window.get("pool_results"), list) else []
        for row in pool_results:
            if not isinstance(row, dict):
                continue
            key = (
                str(row.get("pool_key") or ""),
                _normalize_profile(str(row.get("profile") or "balanced")),
                _rounded_offset(row.get("entry_offset")),
            )
            if not key[0]:
                continue
            grouped[key].append(row)
    output: dict[tuple[str, str, float], dict[str, Any]] = {}
    for key, rows in grouped.items():
        output[key] = _walk_forward_adjustment_evidence(rows)
    return output


def _walk_forward_adjustment_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [
        float(row.get("validation_average_return") or 0.0)
        for row in rows
        if row.get("validation_average_return") is not None
    ]
    scores = [float(row.get("validation_score") or 0.0) for row in rows]
    ok_count = sum(1 for row in rows if row.get("validation_status") == "ok")
    positive_count = sum(1 for value in returns if value > 0.0)
    positive_rate = (positive_count / len(returns)) if returns else None
    average_return = mean(returns) if returns else None
    average_score = mean(scores) if scores else 0.0
    adjustment = _walk_forward_adjustment_score(
        ok_count=ok_count,
        window_count=len(rows),
        positive_rate=positive_rate,
        average_return=average_return,
        average_score=average_score,
    )
    return {
        "window_count": len(rows),
        "ok_count": ok_count,
        "positive_rate": positive_rate,
        "average_return": average_return,
        "average_score": average_score,
        "adjustment": adjustment,
    }


def _walk_forward_adjustment_score(
    *,
    ok_count: int,
    window_count: int,
    positive_rate: float | None,
    average_return: float | None,
    average_score: float,
) -> float:
    if window_count <= 0:
        return 0.0
    ok_rate = ok_count / max(1, window_count)
    hit_component = ((positive_rate if positive_rate is not None else 0.5) - 0.5) * 10.0
    return_component = max(-0.05, min(0.05, float(average_return or 0.0))) * 90.0
    score_component = (max(0.0, min(100.0, float(average_score or 0.0))) - 55.0) / 10.0
    consistency_component = (ok_rate - 0.5) * 8.0
    raw = hit_component + return_component + score_component + consistency_component
    return round(max(-4.0, min(4.0, raw)), 1)


def _candidate_walk_forward_adjustment(
    walk_forward_by_variant: dict[tuple[str, float], dict[str, Any]],
    item: dict[str, Any],
) -> dict[str, Any]:
    key = (_normalize_profile(str(item.get("profile") or "balanced")), _rounded_offset(item.get("entry_offset")))
    evidence = walk_forward_by_variant.get(key)
    current_score = float(item.get("score") or item.get("historical_score") or 0.0)
    if not evidence:
        return {
            "score": round(max(0.0, min(100.0, current_score)), 1),
            "walk_forward_adjustment": 0.0,
            "walk_forward_label": "尚無 rolling 證據",
            "walk_forward_reason": "這組門檻尚未在 rolling 驗證視窗中出現，暫不調整分數。",
            "walk_forward_window_count": 0,
        }
    if int(evidence.get("window_count") or 0) < 2:
        return {
            "score": round(max(0.0, min(100.0, current_score)), 1),
            "walk_forward_adjustment": 0.0,
            "walk_forward_label": "rolling 觀察中",
            "walk_forward_reason": "rolling 視窗少於 2 個，先顯示證據但不調整分數。",
            "walk_forward_window_count": int(evidence.get("window_count") or 0),
            "walk_forward_ok_count": int(evidence.get("ok_count") or 0),
            "walk_forward_positive_rate": evidence.get("positive_rate"),
            "walk_forward_average_return": evidence.get("average_return"),
            "walk_forward_average_score": round(float(evidence.get("average_score") or 0.0), 1),
        }
    adjustment = float(evidence.get("adjustment") or 0.0)
    adjusted_score = round(max(0.0, min(100.0, current_score + adjustment)), 1)
    if adjustment >= 2.0:
        label = "rolling 加分"
        reason = "這組門檻在 rolling 驗證視窗表現較穩，推薦分數上修。"
    elif adjustment <= -2.0:
        label = "rolling 降權"
        reason = "這組門檻移到驗證視窗後表現偏弱，推薦分數下修。"
    else:
        label = "rolling 持平"
        reason = "rolling 驗證沒有顯著改變推薦分數。"
    return {
        "score": adjusted_score,
        "walk_forward_adjustment": adjustment,
        "walk_forward_label": label,
        "walk_forward_reason": reason,
        "walk_forward_window_count": int(evidence.get("window_count") or 0),
        "walk_forward_ok_count": int(evidence.get("ok_count") or 0),
        "walk_forward_positive_rate": evidence.get("positive_rate"),
        "walk_forward_average_return": evidence.get("average_return"),
        "walk_forward_average_score": round(float(evidence.get("average_score") or 0.0), 1),
    }


def _candidate_pool_walk_forward_adjustment(
    walk_forward_by_pool_variant: dict[tuple[str, str, float], dict[str, Any]],
    item: dict[str, Any],
) -> dict[str, Any]:
    key = (
        str(item.get("pool_key") or ""),
        _normalize_profile(str(item.get("profile") or "balanced")),
        _rounded_offset(item.get("entry_offset")),
    )
    evidence = walk_forward_by_pool_variant.get(key)
    current_score = float(item.get("score") or item.get("historical_score") or 0.0)
    if not evidence:
        return {
            "score": round(max(0.0, min(100.0, current_score)), 1),
            "pool_walk_forward_adjustment": 0.0,
            "pool_walk_forward_label": "尚無池別 rolling 證據",
            "pool_walk_forward_reason": "這個市場池或族群尚未在 rolling 驗證視窗中出現，暫不調整分數。",
            "pool_walk_forward_window_count": 0,
        }
    if int(evidence.get("window_count") or 0) < 2:
        return {
            "score": round(max(0.0, min(100.0, current_score)), 1),
            "pool_walk_forward_adjustment": 0.0,
            "pool_walk_forward_label": "池別 rolling 觀察中",
            "pool_walk_forward_reason": "池別 rolling 視窗少於 2 個，先顯示證據但不調整分數。",
            "pool_walk_forward_window_count": int(evidence.get("window_count") or 0),
            "pool_walk_forward_ok_count": int(evidence.get("ok_count") or 0),
            "pool_walk_forward_positive_rate": evidence.get("positive_rate"),
            "pool_walk_forward_average_return": evidence.get("average_return"),
            "pool_walk_forward_average_score": round(float(evidence.get("average_score") or 0.0), 1),
        }
    raw_adjustment = float(evidence.get("adjustment") or 0.0)
    adjustment = round(max(-3.0, min(3.0, raw_adjustment)), 1)
    adjusted_score = round(max(0.0, min(100.0, current_score + adjustment)), 1)
    if adjustment >= 1.5:
        label = "池別 rolling 加分"
        reason = "這個市場池或族群在 rolling 驗證視窗表現較穩，候選分數上修。"
    elif adjustment <= -1.5:
        label = "池別 rolling 降權"
        reason = "這個市場池或族群移到驗證視窗後表現偏弱，候選分數下修。"
    else:
        label = "池別 rolling 持平"
        reason = "池別 rolling 驗證沒有顯著改變候選分數。"
    return {
        "score": adjusted_score,
        "pool_walk_forward_adjustment": adjustment,
        "pool_walk_forward_label": label,
        "pool_walk_forward_reason": reason,
        "pool_walk_forward_window_count": int(evidence.get("window_count") or 0),
        "pool_walk_forward_ok_count": int(evidence.get("ok_count") or 0),
        "pool_walk_forward_positive_rate": evidence.get("positive_rate"),
        "pool_walk_forward_average_return": evidence.get("average_return"),
        "pool_walk_forward_average_score": round(float(evidence.get("average_score") or 0.0), 1),
    }


def _threshold_heatmap(
    variants: list[dict[str, Any]],
    baseline: dict[str, Any],
    best_variant: dict[str, Any],
    stable_variants: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [variant for variant in variants if isinstance(variant, dict)]
    profile_order = ["aggressive", "balanced", "conservative"]
    profile_labels = {
        profile: PROFILE_THRESHOLDS[profile]["label"]
        for profile in profile_order
        if profile in PROFILE_THRESHOLDS
    }
    offsets = sorted({_rounded_offset(variant.get("entry_offset")) for variant in rows})
    stable_keys = {_variant_key(variant) for variant in stable_variants if isinstance(variant, dict)}
    baseline_key = _variant_key(baseline)
    best_key = _variant_key(best_variant)
    by_key = {_variant_key(variant): variant for variant in rows}

    matrix: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for profile in profile_order:
        row_cells: list[dict[str, Any]] = []
        for offset in offsets:
            variant = by_key.get((profile, offset), {})
            cell = _threshold_heatmap_cell(
                variant,
                profile=profile,
                profile_label=profile_labels.get(profile, profile),
                offset=offset,
                is_baseline=(profile, offset) == baseline_key,
                is_best=(profile, offset) == best_key,
                in_stable_zone=(profile, offset) in stable_keys,
            )
            row_cells.append(cell)
            cells.append(cell)
        matrix.append(
            {
                "profile": profile,
                "profile_label": profile_labels.get(profile, profile),
                "cells": row_cells,
            }
        )

    strongest = max(
        (cell for cell in cells if int(cell.get("evaluated_count") or 0) > 0),
        key=lambda cell: (
            float(cell.get("score") or 0.0),
            int(cell.get("evaluated_count") or 0),
            float(cell.get("average_return") or 0.0),
        ),
        default=None,
    )
    stable_count = sum(1 for cell in cells if cell.get("in_stable_zone"))
    evaluated_count = sum(1 for cell in cells if int(cell.get("evaluated_count") or 0) > 0)
    return {
        "status": "ok" if rows and offsets else "empty",
        "profile_order": profile_order,
        "profile_labels": profile_labels,
        "offsets": offsets,
        "baseline_key": _variant_key_text(baseline_key),
        "best_key": _variant_key_text(best_key),
        "stable_keys": [_variant_key_text(key) for key in sorted(stable_keys)],
        "stable_count": stable_count,
        "evaluated_cell_count": evaluated_count,
        "matrix": matrix,
        "strongest_cell": strongest,
        "summary": _threshold_heatmap_summary(strongest, stable_count=stable_count, evaluated_count=evaluated_count),
    }


def _threshold_heatmap_cell(
    variant: dict[str, Any],
    *,
    profile: str,
    profile_label: str,
    offset: float,
    is_baseline: bool,
    is_best: bool,
    in_stable_zone: bool,
) -> dict[str, Any]:
    score = float(variant.get("multi_horizon_score") or 0.0) if variant else 0.0
    evaluated_count = int(variant.get("evaluated_count") or 0) if variant else 0
    average_return = variant.get("average_return") if variant else None
    positive_rate = variant.get("average_positive_rate") if variant else None
    tone = _threshold_heatmap_tone(
        score=score,
        evaluated_count=evaluated_count,
        average_return=float(average_return) if average_return is not None else None,
    )
    badges: list[str] = []
    if is_baseline:
        badges.append("基準")
    if is_best:
        badges.append("最佳")
    if in_stable_zone:
        badges.append("穩定區")
    return {
        "key": _variant_key_text((profile, offset)),
        "profile": profile,
        "profile_label": profile_label,
        "entry_offset": offset,
        "watch_offset": offset,
        "score": round(score, 1),
        "evaluated_count": evaluated_count,
        "evaluated_run_count": int(variant.get("evaluated_run_count") or 0) if variant else 0,
        "average_positive_rate": positive_rate,
        "average_return": average_return,
        "tone": tone,
        "is_baseline": bool(is_baseline),
        "is_best": bool(is_best),
        "in_stable_zone": bool(in_stable_zone),
        "badges": badges,
    }


def _threshold_heatmap_tone(*, score: float, evaluated_count: int, average_return: float | None) -> str:
    if evaluated_count <= 0:
        return "empty"
    if score >= 74 and (average_return is None or average_return >= 0.0):
        return "strong"
    if score >= 60 and (average_return is None or average_return >= 0.0):
        return "ok"
    if score >= 45:
        return "watch"
    return "weak"


def _threshold_heatmap_summary(
    strongest: dict[str, Any] | None,
    *,
    stable_count: int,
    evaluated_count: int,
) -> str:
    if not strongest:
        return "門檻熱區還沒有成熟 outcome，先累積更多每日雷達與後續日線。"
    profile = str(strongest.get("profile_label") or strongest.get("profile") or "-")
    offset = float(strongest.get("entry_offset") or 0.0)
    score = float(strongest.get("score") or 0.0)
    return (
        f"目前最佳熱區是 {profile} offset {offset:+.1f}，分數 {score:.0f}；"
        f"已有 {stable_count} 格落在穩定區，{evaluated_count} 格具備成熟 outcome。"
    )


def _variant_key(variant: dict[str, Any]) -> tuple[str, float]:
    return (
        _normalize_profile(str(variant.get("profile") or "balanced")),
        _rounded_offset(variant.get("entry_offset")),
    )


def _variant_key_text(key: tuple[str, float]) -> str:
    return f"{key[0]}:{key[1]:+.1f}"


def _run_sort_key(run: dict[str, Any]) -> str:
    return str(run.get("run_date") or run.get("trade_date") or run.get("date") or run.get("run_id") or "")


def _run_date_text(run: dict[str, Any]) -> str:
    return str(run.get("run_date") or run.get("trade_date") or run.get("date") or run.get("run_id") or "-")


def _segment_summaries(
    runs: list[dict[str, Any]],
    variant_defs: list[tuple[str, float]],
    horizons: tuple[int, ...],
    min_samples: int,
    key_fn: Callable[[dict[str, Any]], tuple[str, str]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    grouped_runs, labels, candidate_counts = _group_runs_by_segment(runs, key_fn)

    output: list[dict[str, Any]] = []
    primary_horizon = horizons[0]
    for key, segment_runs in grouped_runs.items():
        variants = [
            _evaluate_variant(segment_runs, profile, offset, (primary_horizon,), min_samples=min_samples)
            for profile, offset in variant_defs
        ]
        best = max(
            variants,
            key=lambda item: (
                float(item.get("multi_horizon_score") or 0.0),
                int(item.get("evaluated_count") or 0),
                float(item.get("average_return") or 0.0),
            ),
        )
        best_horizon = (best.get("horizons") or [{}])[0]
        output.append(
            {
                "key": key,
                "label": labels.get(key, key),
                "candidate_count": int(candidate_counts[key]),
                "run_count": len(segment_runs),
                "best_profile": best.get("profile"),
                "best_profile_label": best.get("profile_label"),
                "best_entry_offset": best.get("entry_offset"),
                "score": best.get("multi_horizon_score"),
                "evaluated_count": best_horizon.get("evaluated_count"),
                "positive_rate": best_horizon.get("positive_rate"),
                "average_return": best_horizon.get("average_return"),
            }
        )
    return sorted(
        output,
        key=lambda item: (
            float(item.get("score") or 0.0),
            int(item.get("evaluated_count") or 0),
            int(item.get("candidate_count") or 0),
        ),
        reverse=True,
    )[: max(1, int(limit))]


def _optimization_candidates(
    runs: list[dict[str, Any]],
    variant_defs: list[tuple[str, float]],
    horizons: tuple[int, ...],
    *,
    min_samples: int,
    limit: int,
    adoption_feedback: dict[str, Any] | None = None,
    walk_forward_windows: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    pools = _optimization_pools(runs, min_samples=min_samples, industry_limit=5)
    feedback_by_key = _adoption_feedback_by_key(adoption_feedback)
    walk_forward_by_variant = _walk_forward_adjustment_by_variant(walk_forward_windows)
    walk_forward_by_pool_variant = _walk_forward_adjustment_by_pool_variant(walk_forward_windows)
    output: list[dict[str, Any]] = []
    minimum = max(1, int(min_samples))
    total_run_count = len(runs)
    for pool in pools:
        pool_runs = pool.get("runs") if isinstance(pool.get("runs"), list) else []
        if not pool_runs:
            continue
        for profile, offset in variant_defs:
            variant = _evaluate_variant(pool_runs, profile, offset, horizons, min_samples=minimum)
            evaluated_count = int(variant.get("evaluated_count") or 0)
            if evaluated_count < minimum or float(variant.get("multi_horizon_score") or 0.0) <= 0.0:
                continue
            readiness, readiness_label, next_step = _optimization_readiness(
                variant,
                total_run_count=total_run_count,
                min_samples=minimum,
            )
            rank_score = _optimization_rank_score(
                variant,
                total_run_count=total_run_count,
                min_samples=minimum,
            )
            pool_key = str(pool.get("key") or "")
            pool_label = str(pool.get("label") or pool_key or "全部候選")
            pool_type = str(pool.get("type") or "all")
            top_symbols = variant.get("top_symbols", [])[:6]
            item = {
                "pool_key": pool_key,
                "pool_label": pool_label,
                "pool_type": pool_type,
                "pool_run_count": len(pool_runs),
                "candidate_count": int(pool.get("candidate_count") or 0),
                "profile": variant.get("profile"),
                "profile_label": variant.get("profile_label"),
                "entry_offset": variant.get("entry_offset"),
                "watch_offset": variant.get("watch_offset"),
                "entry_score_min": variant.get("entry_score_min"),
                "watch_score_min": variant.get("watch_score_min"),
                "historical_score": rank_score,
                "multi_horizon_score": variant.get("multi_horizon_score"),
                "evaluated_count": evaluated_count,
                "evaluated_run_count": variant.get("evaluated_run_count"),
                "average_positive_rate": variant.get("average_positive_rate"),
                "average_return": variant.get("average_return"),
                "readiness": readiness,
                "readiness_label": readiness_label,
                "next_step": next_step,
                "top_symbols": top_symbols,
                "run_params": _optimization_run_params(
                    pool_key=pool_key,
                    pool_label=pool_label,
                    pool_type=pool_type,
                    profile=str(variant.get("profile") or "balanced"),
                    threshold_offset=float(variant.get("entry_offset") or 0.0),
                    top_symbols=top_symbols,
                ),
                "horizons": variant.get("horizons", []),
            }
            item.update(_candidate_feedback_adjustment(feedback_by_key, item, historical_score=rank_score))
            item.update(_candidate_walk_forward_adjustment(walk_forward_by_variant, item))
            item.update(_candidate_pool_walk_forward_adjustment(walk_forward_by_pool_variant, item))
            output.append(item)

    ranked = sorted(
        output,
        key=lambda item: (
            float(item.get("score") or 0.0),
            int(item.get("evaluated_run_count") or 0),
            int(item.get("evaluated_count") or 0),
            float(item.get("average_return") or 0.0),
            -abs(float(item.get("entry_offset") or 0.0)),
            _profile_tie_rank(str(item.get("profile") or "")),
        ),
        reverse=True,
    )
    output = []
    pool_counts: Counter[str] = Counter()
    used_keys: set[tuple[str, str, float]] = set()
    for item in ranked:
        pool_key = str(item.get("pool_key") or "")
        unique_key = (pool_key, str(item.get("profile") or ""), float(item.get("entry_offset") or 0.0))
        if pool_counts[pool_key] >= 2:
            continue
        output.append(item)
        used_keys.add(unique_key)
        pool_counts[pool_key] += 1
        if len(output) >= max(1, int(limit)):
            break
    if len(output) < max(1, int(limit)):
        for item in ranked:
            unique_key = (str(item.get("pool_key") or ""), str(item.get("profile") or ""), float(item.get("entry_offset") or 0.0))
            if unique_key in used_keys:
                continue
            output.append(item)
            if len(output) >= max(1, int(limit)):
                break
    for idx, item in enumerate(output, start=1):
        item["rank"] = idx
    return output


def _pool_recommendation(candidates: list[dict[str, Any]], *, limit: int = 4) -> dict[str, Any]:
    rows = [item for item in candidates if isinstance(item, dict) and str(item.get("pool_key") or "").strip()]
    if not rows:
        return {
            "status": "empty",
            "label": "等待池別證據",
            "recommended_pool": None,
            "alternatives": [],
            "next_step": "先累積更多成熟 outcome，再判斷下一輪優先觀察的市場池或族群。",
        }

    best_by_pool: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(rows):
        pool_key = str(item.get("pool_key") or "")
        annotated = dict(item)
        annotated["candidate_index"] = idx
        current = best_by_pool.get(pool_key)
        if current is None or _pool_recommendation_rank_key(annotated) > _pool_recommendation_rank_key(current):
            best_by_pool[pool_key] = annotated

    specific_rows = [item for item in best_by_pool.values() if str(item.get("pool_type") or "") != "all"]
    ranked = sorted(specific_rows or list(best_by_pool.values()), key=_pool_recommendation_rank_key, reverse=True)
    if not ranked:
        return {
            "status": "empty",
            "label": "等待池別證據",
            "recommended_pool": None,
            "alternatives": [],
            "next_step": "候選設定尚未形成可執行的池別建議。",
        }

    top = ranked[0]
    compact_top = _compact_pool_recommendation_candidate(top)
    alternatives = [_compact_pool_recommendation_candidate(item) for item in ranked[: max(1, int(limit))]]
    pool_type = str(top.get("pool_type") or "all")
    window_count = int(top.get("pool_walk_forward_window_count") or 0)
    adjustment = float(top.get("pool_walk_forward_adjustment") or 0.0)
    readiness = str(top.get("readiness") or "")
    if pool_type == "industry":
        label = "優先族群池"
    elif pool_type == "market":
        label = "優先市場池"
    else:
        label = "全市場預設"
    if window_count < 2:
        status = "watch"
        next_step = "池別 rolling 視窗仍少，下一輪先小批次重跑，確認最新資料下是否仍有 ENTRY_READY。"
    elif adjustment < -1.5:
        status = "warn"
        next_step = "池別 out-of-sample 表現偏弱，這組先當對照組，不建議提高權重。"
    elif readiness == "preferred" or adjustment > 0:
        status = "ok"
        next_step = "下一輪優先重跑這個池別，並用小權重觀察代表代號是否延續。"
    else:
        status = "watch"
        next_step = "這個池別可列入觀察，但仍要搭配最新市場資料與流動性檢查。"

    return {
        "status": status,
        "label": label,
        "summary": (
            f"{label}：{top.get('pool_label') or '-'}，"
            f"{top.get('profile_label') or top.get('profile') or '-'} offset {float(top.get('entry_offset') or 0.0):+.1f}，"
            f"排名分 {float(top.get('score') or 0.0):.0f}。"
        ),
        "recommended_pool": compact_top,
        "alternatives": alternatives,
        "next_step": next_step,
    }


def _pool_recommendation_rank_key(item: dict[str, Any]) -> tuple[float, float, int, int, float, int]:
    return (
        float(item.get("score") or 0.0),
        float(item.get("pool_walk_forward_adjustment") or 0.0),
        int(item.get("pool_walk_forward_ok_count") or 0),
        int(item.get("evaluated_count") or 0),
        float(item.get("average_return") or 0.0),
        -int(item.get("candidate_index") or 0),
    )


def _compact_pool_recommendation_candidate(item: dict[str, Any]) -> dict[str, Any]:
    params = item.get("run_params") if isinstance(item.get("run_params"), dict) else {}
    symbols = _clean_symbol_list(item.get("top_symbols"))
    if not symbols:
        symbols = _clean_symbol_list(str(params.get("symbols_csv") or "").split(","))
    return {
        "candidate_rank": item.get("rank"),
        "candidate_index": item.get("candidate_index"),
        "pool_key": item.get("pool_key"),
        "pool_label": item.get("pool_label"),
        "pool_type": item.get("pool_type"),
        "profile": item.get("profile"),
        "profile_label": item.get("profile_label"),
        "entry_offset": item.get("entry_offset"),
        "score": item.get("score"),
        "readiness": item.get("readiness"),
        "readiness_label": item.get("readiness_label"),
        "evaluated_count": item.get("evaluated_count"),
        "evaluated_run_count": item.get("evaluated_run_count"),
        "average_positive_rate": item.get("average_positive_rate"),
        "average_return": item.get("average_return"),
        "pool_walk_forward_adjustment": item.get("pool_walk_forward_adjustment", 0.0),
        "pool_walk_forward_label": item.get("pool_walk_forward_label"),
        "pool_walk_forward_reason": item.get("pool_walk_forward_reason"),
        "pool_walk_forward_window_count": item.get("pool_walk_forward_window_count", 0),
        "pool_walk_forward_ok_count": item.get("pool_walk_forward_ok_count", 0),
        "pool_walk_forward_positive_rate": item.get("pool_walk_forward_positive_rate"),
        "pool_walk_forward_average_return": item.get("pool_walk_forward_average_return"),
        "top_symbols": symbols[:8],
        "run_params": params,
        "next_step": item.get("next_step"),
    }


def _threshold_recommended_action(
    config: dict[str, Any] | None,
    *,
    robustness_score: int,
    baseline_variant: dict[str, Any],
    best_variant: dict[str, Any],
    viable_count: int,
    min_samples: int,
) -> dict[str, Any]:
    if not config:
        return {
            "action": "wait",
            "label": "等待樣本",
            "decision_score": 0.0,
            "reason": "成熟 outcome 還不足，先不要把單一門檻當成日常預設。",
            "candidate_rank": None,
            "pool_label": "",
            "profile": "",
            "profile_label": "",
            "entry_offset": 0.0,
            "top_symbols": [],
            "run_params": {},
            "guardrails": [
                "至少累積多輪每日雷達與 5 / 10 / 20 日 outcome 後再採用。",
                "樣本不足時只適合拿來觀察，不適合放大部位。",
            ],
        }

    candidate_score = float(config.get("score") or config.get("multi_horizon_score") or 0.0)
    evidence_count = int(config.get("evaluated_count") or 0)
    evaluated_run_count = int(config.get("evaluated_run_count") or 0)
    readiness = str(config.get("readiness") or "")
    baseline_score = float(baseline_variant.get("multi_horizon_score") or 0.0)
    best_score = float(best_variant.get("multi_horizon_score") or 0.0)
    baseline_gap = max(0.0, best_score - baseline_score)
    evidence_score = min(1.0, evidence_count / max(1.0, float(min_samples) * 3.0)) * 10.0
    viable_score = min(1.0, viable_count / 4.0) * 8.0
    readiness_bonus = {"preferred": 8.0, "watch": 3.0, "thin": -4.0, "insufficient": -12.0}.get(readiness, 0.0)
    gap_penalty = 6.0 if baseline_gap > 18 else 3.0 if baseline_gap > 12 else 0.0
    decision_score = round(
        max(
            0.0,
            min(
                100.0,
                candidate_score * 0.42
                + max(0.0, min(100.0, float(robustness_score))) * 0.32
                + evidence_score
                + viable_score
                + readiness_bonus
                - gap_penalty,
            ),
        ),
        1,
    )

    if readiness == "preferred" and robustness_score >= 72 and evidence_count >= max(1, int(min_samples)) * 2 and decision_score >= 72:
        action = "apply_small"
        label = "可小量採用"
        reason = "歷史穩健分、候選設定分與樣本數都過門檻，可先套用成下一輪雷達設定，再用小權重觀察。"
    elif readiness in {"preferred", "watch"} and decision_score >= 58:
        action = "observe"
        label = "先觀察"
        reason = "推薦設定有可用訊號，但歷史穩健性或樣本厚度還不到直接採用，建議先重跑驗證。"
    else:
        action = "wait"
        label = "暫不採用"
        reason = "目前證據仍偏薄，先累積更多 outcome 或縮小市場池後再採用。"

    params = config.get("run_params") if isinstance(config.get("run_params"), dict) else {}
    top_symbols = _clean_symbol_list(config.get("top_symbols"))
    if not top_symbols:
        top_symbols = _clean_symbol_list(str(params.get("symbols_csv") or "").split(","))
    return {
        "action": action,
        "label": label,
        "decision_score": decision_score,
        "reason": reason,
        "candidate_rank": config.get("rank"),
        "pool_key": config.get("pool_key"),
        "pool_label": config.get("pool_label"),
        "pool_type": config.get("pool_type"),
        "profile": config.get("profile"),
        "profile_label": config.get("profile_label"),
        "entry_offset": config.get("entry_offset"),
        "readiness": readiness,
        "readiness_label": config.get("readiness_label"),
        "candidate_score": candidate_score,
        "robustness_score": int(robustness_score),
        "evaluated_count": evidence_count,
        "evaluated_run_count": evaluated_run_count,
        "top_symbols": top_symbols[:8],
        "run_params": params,
        "guardrails": [
            "先套用到下一輪每日雷達，不直接放大部位。",
            "若最新重跑沒有 ENTRY_READY 或風險降級，維持觀察不採用。",
            "族群型設定需確認流動性與個股事件，避免只押單一族群。",
        ],
    }


def _compact_adoption_feedback(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    return {
        "status": data.get("status") or "empty",
        "tracked_count": int(data.get("tracked_count") or 0),
        "matured_run_count": int(data.get("matured_run_count") or 0),
        "next_step": str(data.get("next_step") or ""),
    }


def _adoption_feedback_by_key(payload: dict[str, Any] | None) -> dict[tuple[str, str, float], dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    runs = data.get("runs") if isinstance(data.get("runs"), list) else []
    grouped: dict[tuple[str, str, float], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        if not isinstance(run, dict) or not bool(run.get("matured")):
            continue
        adoption = run.get("adoption") if isinstance(run.get("adoption"), dict) else {}
        pool_key = str(adoption.get("pool_key") or _pool_key_from_adoption(adoption)).strip()
        profile = _normalize_profile(str(adoption.get("profile") or run.get("risk_profile") or "balanced"))
        offset = _rounded_offset(adoption.get("threshold_offset", adoption.get("entry_offset", run.get("threshold_offset", 0.0))))
        if not pool_key:
            continue
        horizon_rows = [
            item
            for item in (run.get("horizon_summary") if isinstance(run.get("horizon_summary"), list) else [])
            if isinstance(item, dict) and int(item.get("matured_count") or 0) > 0
        ]
        if not horizon_rows:
            continue
        positive_values = [float(item.get("positive_rate") or 0.0) for item in horizon_rows if item.get("positive_rate") is not None]
        return_values = [float(item.get("average_return") or 0.0) for item in horizon_rows if item.get("average_return") is not None]
        grouped[(pool_key, profile, offset)].append(
            {
                "run_id": run.get("run_id"),
                "positive_rate": mean(positive_values) if positive_values else None,
                "average_return": mean(return_values) if return_values else None,
                "matured_count": sum(int(item.get("matured_count") or 0) for item in horizon_rows),
            }
        )

    output: dict[tuple[str, str, float], dict[str, Any]] = {}
    for key, rows in grouped.items():
        positive_values = [float(item.get("positive_rate") or 0.0) for item in rows if item.get("positive_rate") is not None]
        return_values = [float(item.get("average_return") or 0.0) for item in rows if item.get("average_return") is not None]
        matured_count = sum(int(item.get("matured_count") or 0) for item in rows)
        positive_rate = mean(positive_values) if positive_values else None
        average_return = mean(return_values) if return_values else None
        output[key] = {
            "run_count": len(rows),
            "matured_count": matured_count,
            "positive_rate": positive_rate,
            "average_return": average_return,
            "adjustment": _feedback_adjustment_score(
                positive_rate=positive_rate,
                average_return=average_return,
                matured_count=matured_count,
                run_count=len(rows),
            ),
        }
    return output


def _pool_key_from_adoption(adoption: dict[str, Any]) -> str:
    pool_key = str(adoption.get("pool_key") or "").strip()
    if pool_key:
        return pool_key
    pool_type = str(adoption.get("pool_type") or "").strip()
    pool_label = str(adoption.get("pool_label") or "").strip()
    if pool_type == "industry" and pool_label:
        return f"industry:{pool_label}"
    if pool_type == "market" and pool_label:
        return f"market:{pool_label}"
    if pool_label in {"全部候選", "all"}:
        return "all"
    return ""


def _rounded_offset(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except Exception:
        return 0.0


def _feedback_adjustment_score(
    *,
    positive_rate: float | None,
    average_return: float | None,
    matured_count: int,
    run_count: int,
) -> float:
    if positive_rate is None or average_return is None or matured_count <= 0:
        return 0.0
    evidence_weight = min(1.0, (float(matured_count) / 6.0) * 0.7 + (float(run_count) / 3.0) * 0.3)
    hit_component = (max(0.0, min(1.0, float(positive_rate))) - 0.5) * 18.0
    return_component = max(-0.06, min(0.06, float(average_return))) * 120.0
    raw = (hit_component + return_component) * evidence_weight
    return round(max(-10.0, min(10.0, raw)), 1)


def _candidate_feedback_adjustment(
    feedback_by_key: dict[tuple[str, str, float], dict[str, Any]],
    item: dict[str, Any],
    *,
    historical_score: float,
) -> dict[str, Any]:
    key = (
        str(item.get("pool_key") or ""),
        _normalize_profile(str(item.get("profile") or "balanced")),
        _rounded_offset(item.get("entry_offset")),
    )
    feedback = feedback_by_key.get(key)
    if not feedback:
        return {
            "score": round(max(0.0, min(100.0, float(historical_score or 0.0))), 1),
            "feedback_adjustment": 0.0,
            "feedback_label": "尚無採用回饋",
            "feedback_reason": "這組設定尚未有成熟採用 outcome，暫時只看歷史回測分。",
            "feedback_matured_count": 0,
            "feedback_run_count": 0,
        }
    adjustment = float(feedback.get("adjustment") or 0.0)
    adjusted_score = round(max(0.0, min(100.0, float(historical_score or 0.0) + adjustment)), 1)
    if adjustment >= 3.0:
        label = "採用後加分"
        reason = "採用追蹤 outcome 表現較佳，推薦分數上修。"
    elif adjustment <= -3.0:
        label = "採用後降權"
        reason = "採用追蹤 outcome 偏弱，推薦分數下修。"
    else:
        label = "採用後持平"
        reason = "採用追蹤 outcome 未顯著改變推薦分數。"
    return {
        "score": adjusted_score,
        "feedback_adjustment": adjustment,
        "feedback_label": label,
        "feedback_reason": reason,
        "feedback_matured_count": int(feedback.get("matured_count") or 0),
        "feedback_run_count": int(feedback.get("run_count") or 0),
        "feedback_positive_rate": feedback.get("positive_rate"),
        "feedback_average_return": feedback.get("average_return"),
    }


def _optimization_pools(
    runs: list[dict[str, Any]],
    *,
    min_samples: int,
    industry_limit: int,
) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = [
        {
            "key": "all",
            "label": "全部候選",
            "type": "all",
            "runs": runs,
            "candidate_count": sum(len(run.get("items") or []) for run in runs),
        }
    ]
    for pool_type, key_fn, prefix, limit in (
        ("market", _market_segment, "market", 6),
        ("industry", _industry_segment, "industry", max(1, int(industry_limit))),
    ):
        grouped_runs, labels, candidate_counts = _group_runs_by_segment(runs, key_fn)
        sorted_keys = sorted(
            grouped_runs,
            key=lambda key: (int(candidate_counts[key]), len(grouped_runs[key]), str(labels.get(key, key))),
            reverse=True,
        )
        for key in sorted_keys[:limit]:
            candidate_count = int(candidate_counts[key])
            if candidate_count < max(1, int(min_samples)):
                continue
            pools.append(
                {
                    "key": f"{prefix}:{key}",
                    "label": labels.get(key, key),
                    "type": pool_type,
                    "runs": grouped_runs[key],
                    "candidate_count": candidate_count,
                }
            )
    return pools


def _group_runs_by_segment(
    runs: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], tuple[str, str]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], Counter[str]]:
    grouped_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    labels: dict[str, str] = {}
    candidate_counts: Counter[str] = Counter()
    for run in runs:
        bucket_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in run.get("items", []):
            if not isinstance(item, dict):
                continue
            key, label = key_fn(item)
            labels[key] = label
            bucket_items[key].append(item)
            candidate_counts[key] += 1
        for key, items in bucket_items.items():
            grouped_runs[key].append({**run, "items": items})
    return grouped_runs, labels, candidate_counts


def _optimization_rank_score(variant: dict[str, Any], *, total_run_count: int, min_samples: int) -> float:
    base_score = float(variant.get("multi_horizon_score") or 0.0)
    evidence_count = int(variant.get("evaluated_count") or 0)
    run_count = int(variant.get("evaluated_run_count") or 0)
    evidence_bonus = min(12.0, (float(evidence_count) / max(1.0, float(min_samples) * 2.0)) * 12.0)
    run_coverage_bonus = min(8.0, (float(run_count) / max(1.0, float(total_run_count))) * 8.0)
    return round(max(0.0, min(100.0, base_score * 0.8 + evidence_bonus + run_coverage_bonus)), 1)


def _optimization_readiness(
    variant: dict[str, Any],
    *,
    total_run_count: int,
    min_samples: int,
) -> tuple[str, str, str]:
    score = float(variant.get("multi_horizon_score") or 0.0)
    positive_rate = variant.get("average_positive_rate")
    average_return = variant.get("average_return")
    evaluated_count = int(variant.get("evaluated_count") or 0)
    evaluated_run_count = int(variant.get("evaluated_run_count") or 0)
    if evaluated_count < max(1, int(min_samples)):
        return "insufficient", "樣本不足", "先累積更多成熟 outcome，再把這組設定納入日常雷達。"
    if score >= 74 and (positive_rate is None or float(positive_rate) >= 0.55) and (average_return is None or float(average_return) >= 0.0):
        return "preferred", "優先候選", "下次雷達先用這組市場池與門檻做觀察，並避免一次放大部位。"
    if score >= 60 and evaluated_run_count >= max(1, min(3, int(total_run_count))):
        return "watch", "可觀察", "可列入候選設定，但仍要看最新市場池品質與族群集中。"
    return "thin", "證據偏薄", "只留作對照組，等更多交易日與不同市場情境驗證。"


def _optimization_run_params(
    *,
    pool_key: str,
    pool_label: str,
    pool_type: str,
    profile: str,
    threshold_offset: float,
    top_symbols: list[Any],
) -> dict[str, Any]:
    clean_symbols = [str(symbol or "").strip() for symbol in top_symbols if str(symbol or "").strip()]
    offset = max(-10.0, min(10.0, float(threshold_offset or 0.0)))
    base: dict[str, Any] = {
        "risk_profile": _normalize_profile(profile),
        "threshold_offset": offset,
        "target_count": 15,
        "universe": "research_ready",
        "symbols": [],
        "symbols_csv": "",
        "source": "threshold_backtest",
        "pool_key": pool_key,
        "pool_type": pool_type,
        "pool_label": pool_label,
        "note": "全部候選設定會套用研究就緒池，作為下一輪每日雷達的穩健預設。",
    }
    if pool_type == "industry" and clean_symbols:
        base.update(
            {
                "universe": "custom",
                "symbols": clean_symbols,
                "symbols_csv": ",".join(clean_symbols),
                "target_count": max(10, min(20, len(clean_symbols))),
                "industry_filter": pool_label,
                "note": "族群型設定會用歷史常見代號建立自訂觀察池；正式跨市場池重跑仍需後續最佳化服務。",
            }
        )
    elif pool_type == "market":
        market = pool_key.split(":", 1)[1] if ":" in pool_key else pool_label
        base.update(
            {
                "universe": "twse_tpex",
                "market_filter": market,
                "note": "上市 / 上櫃分層目前用全市場池重跑，並在結果中保留市場分層判讀。",
            }
        )
    return base


def _clean_symbol_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = list(values) if isinstance(values, tuple) else []
    output: list[str] = []
    for value in values:
        symbol = str(value or "").strip()
        if symbol and symbol not in output:
            output.append(symbol)
    return output


def _optimization_plan(candidates: list[dict[str, Any]], *, limit: int = 6) -> dict[str, Any]:
    rows = [item for item in candidates if isinstance(item, dict)]
    run_items: list[dict[str, Any]] = []
    for item in rows[: max(1, int(limit))]:
        params = item.get("run_params") if isinstance(item.get("run_params"), dict) else {}
        request_payload = _optimization_request_payload(params)
        cli_args = _optimization_cli_args(request_payload)
        run_items.append(
            {
                "rank": item.get("rank"),
                "label": f"{item.get('pool_label') or '-'} / {item.get('profile_label') or item.get('profile') or '-'} / offset {item.get('entry_offset') or 0}",
                "pool_key": item.get("pool_key"),
                "readiness": item.get("readiness"),
                "score": item.get("score"),
                "request_payload": request_payload,
                "cli_args": cli_args,
                "cli_command": " ".join(shlex.quote(str(arg)) for arg in cli_args),
                "note": params.get("note") or item.get("next_step") or "",
            }
        )
    return {
        "status": "ok" if run_items else "empty",
        "plan_type": "daily_radar_threshold_candidate_rerun",
        "mode": "dry_run_plan",
        "candidate_count": len(rows),
        "run_count": len(run_items),
        "run_items": run_items,
        "limitations": [
            "此計畫只整理可重跑參數，不會在讀取 threshold-backtest 時自動同步或重跑每日雷達。",
            "上市 / 上櫃市場分層目前以 twse_tpex 全市場池重跑，再由結果分層判讀。",
            "族群型設定會用歷史常見代號建立 custom 觀察池，尚未做完整族群 universe 重新抽樣。",
        ],
        "next_step": "從排名前幾組挑 1 到 3 組重跑每日雷達，確認最新市場資料下候選是否仍穩定。",
    }


def _optimization_request_payload(params: dict[str, Any]) -> dict[str, Any]:
    target_count = int(float(params.get("target_count") or 15))
    offset = max(-10.0, min(10.0, float(params.get("threshold_offset") or 0.0)))
    return {
        "trade_date": None,
        "target_count": max(10, min(20, target_count)),
        "risk_profile": _normalize_profile(str(params.get("risk_profile") or "balanced")),
        "threshold_offset": offset,
        "universe": str(params.get("universe") or "research_ready"),
        "symbols_csv": str(params.get("symbols_csv") or ""),
        "data_freshness_required": True,
        "include_attention": False,
        "include_extended_data": False,
    }


def _optimization_cli_args(request_payload: dict[str, Any]) -> list[str]:
    args = [
        ".venv/bin/python",
        "-m",
        "app.cli",
        "run-daily-radar-job",
        "--target-count",
        str(int(request_payload.get("target_count") or 15)),
        "--risk-profile",
        str(request_payload.get("risk_profile") or "balanced"),
        "--threshold-offset",
        str(float(request_payload.get("threshold_offset") or 0.0)),
        "--universe",
        str(request_payload.get("universe") or "research_ready"),
    ]
    symbols_csv = str(request_payload.get("symbols_csv") or "").strip()
    if symbols_csv:
        args.extend(["--symbols", symbols_csv])
    return args


def _profile_tie_rank(profile: str) -> int:
    return {"balanced": 3, "conservative": 2, "aggressive": 1}.get(_normalize_profile(profile), 0)


def _compact_variant(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": variant.get("profile"),
        "profile_label": variant.get("profile_label"),
        "entry_offset": variant.get("entry_offset"),
        "watch_offset": variant.get("watch_offset"),
        "entry_score_min": variant.get("entry_score_min"),
        "watch_score_min": variant.get("watch_score_min"),
        "multi_horizon_score": variant.get("multi_horizon_score"),
        "evaluated_count": variant.get("evaluated_count"),
        "evaluated_run_count": variant.get("evaluated_run_count"),
        "average_positive_rate": variant.get("average_positive_rate"),
        "average_return": variant.get("average_return"),
        "top_symbols": variant.get("top_symbols", [])[:8],
        "horizons": variant.get("horizons", []),
    }


def _row_is_actionable(item: dict[str, Any], profile: dict[str, Any], *, entry_min: float, watch_min: float) -> bool:
    blockers = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
    if blockers:
        return False
    score = _number(item.get("entry_score"))
    if score >= entry_min and _entry_signal_available(item, allow_soft_entry=bool(profile.get("allow_soft_entry"))):
        return True
    return score >= watch_min


def _entry_signal_available(row: dict[str, Any], *, allow_soft_entry: bool) -> bool:
    action = str(row.get("action_label") or "")
    if action == "ENTRY_READY":
        return True
    if not allow_soft_entry:
        return False
    tags = _tag_map(row.get("reason_tags") if isinstance(row.get("reason_tags"), list) else [])
    return tags.get("TREND_OK") == "True" and tags.get("VOL_OK") == "True"


def _horizon_return(item: dict[str, Any], horizon: int) -> float | None:
    returns = item.get("horizon_returns") if isinstance(item.get("horizon_returns"), dict) else {}
    for key in (horizon, str(horizon), f"{horizon}d"):
        if key in returns:
            return _optional_number(returns[key])
    return None


def _horizon_matured(item: dict[str, Any], horizon: int) -> bool:
    matured = item.get("horizon_matured") if isinstance(item.get("horizon_matured"), dict) else {}
    for key in (horizon, str(horizon), f"{horizon}d"):
        if key in matured:
            return bool(matured[key])
    return _horizon_return(item, horizon) is not None


def _market_segment(item: dict[str, Any]) -> tuple[str, str]:
    key = str(item.get("market") or "unknown").strip().upper() or "UNKNOWN"
    labels = {"TWSE": "上市", "TPEX": "上櫃", "TW": "台股", "UNKNOWN": "市場未標示"}
    return key, labels.get(key, key)


def _profile_segment(item: dict[str, Any]) -> tuple[str, str]:
    key = _normalize_profile(_tag_map(item.get("reason_tags") if isinstance(item.get("reason_tags"), list) else []).get("PROFILE"))
    return key, str(PROFILE_THRESHOLDS[key]["label"])


def _industry_segment(item: dict[str, Any]) -> tuple[str, str]:
    label = str(item.get("industry_group") or "其他").strip() or "其他"
    return label, label


def _infer_base_profile(runs: list[dict[str, Any]]) -> str:
    for run in runs:
        for item in run.get("items", []):
            if not isinstance(item, dict):
                continue
            profile = _tag_map(item.get("reason_tags") if isinstance(item.get("reason_tags"), list) else []).get("PROFILE")
            if profile:
                return profile
    return "balanced"


def _normalize_profile(profile: str | None) -> str:
    value = str(profile or "balanced").strip().lower()
    return value if value in PROFILE_THRESHOLDS else "balanced"


def _tag_map(tags: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for tag in tags:
        text = str(tag or "")
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except Exception:
        return None
    return number if number == number else None
