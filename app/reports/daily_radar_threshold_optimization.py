from __future__ import annotations

import shlex
from typing import Any, Callable


DailyRadarJobRunner = Callable[..., dict[str, Any]]


def run_daily_radar_threshold_optimization_batch(
    repo,
    *,
    sync_service=None,
    execute: bool = False,
    max_runs: int = 3,
    lookback_runs: int = 8,
    lookahead_bars_list: tuple[int, ...] = (5, 10, 20),
    count: int = 50,
    base_profile: str | None = None,
    trade_date_mode: str = "T",
    grid_universes: tuple[str, ...] = (),
    grid_profiles: tuple[str, ...] = (),
    grid_offsets: tuple[float, ...] = (),
    candidate_source: str = "mixed",
    runner: DailyRadarJobRunner | None = None,
) -> dict[str, Any]:
    """Build or execute the threshold-backtest rerun plan in a bounded batch."""
    safe_max_runs = max(1, min(6, int(max_runs or 3)))
    backtest = repo.get_daily_radar_threshold_backtest(
        lookback_runs=max(2, min(24, int(lookback_runs or 8))),
        lookahead_bars_list=tuple(sorted({max(1, min(60, int(value))) for value in lookahead_bars_list if int(value) > 0})),
        count=max(1, min(100, int(count or 50))),
        base_profile=base_profile,
    )
    plan = backtest.get("optimization_plan") if isinstance(backtest, dict) else {}
    run_items = plan.get("run_items") if isinstance(plan, dict) else []
    plan_items = [item for item in (run_items or []) if isinstance(item, dict)]
    grid_items = _grid_candidate_items(
        backtest if isinstance(backtest, dict) else {},
        plan_items,
        grid_universes=grid_universes,
        grid_profiles=grid_profiles,
        grid_offsets=grid_offsets,
    )
    normalized_candidate_source = _normalized_candidate_source(candidate_source)
    selected = _select_optimization_items(
        plan_items,
        grid_items,
        max_runs=safe_max_runs,
        candidate_source=normalized_candidate_source,
    )
    exploration_excluded = list(selected)
    if normalized_candidate_source == "exploration":
        exploration_excluded.extend(
            _select_optimization_items(
                plan_items,
                grid_items,
                max_runs=safe_max_runs,
                candidate_source="mixed",
            )
        )
    exploration_queue = _exploration_queue(
        plan_items,
        grid_items,
        exploration_excluded,
        candidate_source=normalized_candidate_source,
    )

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        request_payload = _normalized_request_payload(item.get("request_payload") if isinstance(item.get("request_payload"), dict) else {})
        row = {
            "rank": item.get("rank") or index,
            "label": item.get("label") or f"候選 #{index}",
            "pool_key": item.get("pool_key"),
            "readiness": item.get("readiness"),
            "score": item.get("score"),
            "candidate_source": item.get("candidate_source") or "plan",
            "request_payload": request_payload,
            "cli_command": item.get("cli_command") or _daily_radar_cli_command(request_payload),
            "note": item.get("note") or "",
            "status": "planned",
        }
        if execute:
            row.update(
                _execute_run_item(
                    repo,
                    request_payload=request_payload,
                    adoption_metadata=_adoption_metadata_for_item(item, request_payload),
                    sync_service=sync_service,
                    trade_date_mode=trade_date_mode,
                    runner=runner,
                )
            )
        row.update(_decision_metrics(row, execute=execute))
        rows.append(row)

    summary = _summarize(rows, execute=execute)
    recommended_run = _recommended_run(rows, execute=execute)
    optimization_grid = _optimization_grid(rows, execute=execute)
    source_comparison = _source_comparison(
        rows,
        execute=execute,
        plan_available_count=max(len(plan_items), _safe_int(plan.get("candidate_count") if isinstance(plan, dict) else 0)),
        grid_available_count=len(grid_items),
    )
    walk_forward_plan = _walk_forward_plan(
        backtest if isinstance(backtest, dict) else {},
        rows,
        exploration_queue,
        execute=execute,
        lookback_runs=max(2, min(24, int(lookback_runs or 8))),
        lookahead_bars_list=tuple(sorted({max(1, min(60, int(value))) for value in lookahead_bars_list if int(value) > 0})),
        count=max(1, min(100, int(count or 50))),
        base_profile=base_profile,
        candidate_source=normalized_candidate_source,
        grid_universes=grid_universes,
        grid_profiles=grid_profiles,
        grid_offsets=grid_offsets,
    )
    return {
        "status": summary["status"],
        "mode": "execute" if execute else "dry_run",
        "batch_type": "daily_radar_threshold_optimization",
        "max_runs": safe_max_runs,
        "candidate_source": normalized_candidate_source,
        "selected_count": len(selected),
        "selected_grid_count": sum(1 for item in selected if str(item.get("candidate_source") or "") == "grid"),
        "source_plan_status": plan.get("status") if isinstance(plan, dict) else "missing",
        "source_candidate_count": plan.get("candidate_count") if isinstance(plan, dict) else 0,
        "grid_candidate_count": len(grid_items),
        "candidate_sources": _candidate_source_counts(selected),
        "lookback_runs": max(2, min(24, int(lookback_runs or 8))),
        "lookahead_bars_list": list(tuple(sorted({max(1, min(60, int(value))) for value in lookahead_bars_list if int(value) > 0}))),
        "trade_date_mode": _normalized_trade_date_mode(trade_date_mode),
        "summary": summary,
        "recommended_run": recommended_run,
        "source_comparison": source_comparison,
        "optimization_grid": optimization_grid,
        "exploration_queue": exploration_queue,
        "walk_forward_plan": walk_forward_plan,
        "run_items": rows,
        "next_step": _next_step(summary, execute=execute),
        "source_backtest": {
            "status": backtest.get("status") if isinstance(backtest, dict) else "error",
            "verdict": backtest.get("verdict") if isinstance(backtest, dict) else "",
            "robustness_score": backtest.get("robustness_score") if isinstance(backtest, dict) else 0,
            "recommended_config": backtest.get("recommended_config") if isinstance(backtest, dict) else None,
            "threshold_heatmap": backtest.get("threshold_heatmap") if isinstance(backtest, dict) else None,
        },
    }


def _grid_candidate_items(
    backtest: dict[str, Any],
    plan_items: list[dict[str, Any]],
    *,
    grid_universes: tuple[str, ...],
    grid_profiles: tuple[str, ...],
    grid_offsets: tuple[float, ...],
) -> list[dict[str, Any]]:
    universes = _normalized_grid_universes(grid_universes)
    profiles = _normalized_grid_profiles(grid_profiles)
    offsets = _normalized_grid_offsets(grid_offsets)
    if not universes or not profiles or not offsets:
        return []

    seen = {_candidate_key(item) for item in plan_items if isinstance(item, dict)}
    generated: list[dict[str, Any]] = []
    for universe in universes:
        for profile in profiles:
            for offset in offsets:
                request_payload = _normalized_request_payload(
                    {
                        "target_count": 15,
                        "risk_profile": profile,
                        "threshold_offset": offset,
                        "universe": universe,
                        "symbols_csv": "",
                        "data_freshness_required": True,
                        "include_attention": False,
                        "include_extended_data": False,
                        "prefer_official_bulk": True,
                    }
                )
                key = _candidate_key({"request_payload": request_payload})
                if key in seen:
                    continue
                seen.add(key)
                generated.append(
                    {
                        "rank": len(plan_items) + len(generated) + 1,
                        "label": f"{_universe_label(universe)} / {_profile_label(profile)} / offset {float(offset):+.1f}",
                        "pool_key": f"grid:{universe}",
                        "readiness": "grid",
                        "score": _grid_candidate_score(backtest, profile=profile, offset=offset, universe=universe),
                        "candidate_source": "grid",
                        "request_payload": request_payload,
                        "cli_args": _daily_radar_cli_args(request_payload),
                        "cli_command": _daily_radar_cli_command(request_payload),
                        "note": "跨市場池 grid 候選；用來比較不同市場池、風險檔位與門檻偏移是否比歷史推薦更穩。",
                    }
                )
    generated.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            -abs(float(((item.get("request_payload") or {}).get("threshold_offset") if isinstance(item.get("request_payload"), dict) else 0.0) or 0.0)),
            str((item.get("request_payload") or {}).get("universe") if isinstance(item.get("request_payload"), dict) else ""),
        ),
        reverse=True,
    )
    for index, item in enumerate(generated, start=1):
        item["rank"] = len(plan_items) + index
    return generated


def _select_optimization_items(
    plan_items: list[dict[str, Any]],
    grid_items: list[dict[str, Any]],
    *,
    max_runs: int,
    candidate_source: str,
) -> list[dict[str, Any]]:
    source = _normalized_candidate_source(candidate_source)
    if source == "plan":
        return plan_items[:max_runs]
    if source == "grid":
        return grid_items[:max_runs]
    if source == "exploration":
        first_batch = _select_optimization_items(
            plan_items,
            grid_items,
            max_runs=max_runs,
            candidate_source="mixed",
        )
        first_batch_keys = {_candidate_key(item) for item in first_batch if isinstance(item, dict)}
        return _ordered_exploration_items(plan_items, grid_items, first_batch_keys)[:max_runs]
    if not grid_items:
        return plan_items[:max_runs]
    plan_quota = min(len(plan_items), max(1, max_runs // 2))
    selected = plan_items[:plan_quota]
    selected.extend(grid_items[: max(0, max_runs - len(selected))])
    if len(selected) < max_runs:
        selected.extend(plan_items[plan_quota:max_runs])
    return selected[:max_runs]


def _ordered_exploration_items(
    plan_items: list[dict[str, Any]],
    grid_items: list[dict[str, Any]],
    selected_keys: set[tuple[str, str, float, str]],
) -> list[dict[str, Any]]:
    remaining = [
        item
        for item in [*plan_items, *grid_items]
        if isinstance(item, dict) and _candidate_key(item) not in selected_keys
    ]
    return sorted(
        remaining,
        key=lambda item: (
            float(item.get("score") or 0.0),
            1 if str(item.get("candidate_source") or "plan") == "grid" else 0,
            -abs(float(((item.get("request_payload") or {}).get("threshold_offset") if isinstance(item.get("request_payload"), dict) else 0.0) or 0.0)),
            -int(float(item.get("rank") or 999)),
        ),
        reverse=True,
    )


def _exploration_queue(
    plan_items: list[dict[str, Any]],
    grid_items: list[dict[str, Any]],
    selected_items: list[dict[str, Any]],
    *,
    candidate_source: str,
    limit: int = 6,
) -> dict[str, Any]:
    selected_keys = {_candidate_key(item) for item in selected_items if isinstance(item, dict)}
    remaining_plan = [
        _queue_candidate_summary(item, candidate_source="plan")
        for item in plan_items
        if isinstance(item, dict) and _candidate_key(item) not in selected_keys
    ]
    remaining_grid = [
        _queue_candidate_summary(item, candidate_source="grid")
        for item in grid_items
        if isinstance(item, dict) and _candidate_key(item) not in selected_keys
    ]
    source = _normalized_candidate_source(candidate_source)
    if source == "plan":
        ordered = remaining_plan
    elif source == "grid":
        ordered = remaining_grid
    elif source == "exploration":
        ordered = sorted(
            remaining_plan + remaining_grid,
            key=lambda item: (
                float(item.get("score") or 0.0),
                1 if item.get("candidate_source") == "grid" else 0,
                -abs(float(item.get("threshold_offset") or 0.0)),
            ),
            reverse=True,
        )
    else:
        ordered = sorted(
            remaining_plan + remaining_grid,
            key=lambda item: (
                float(item.get("score") or 0.0),
                1 if item.get("candidate_source") == "grid" else 0,
                -abs(float(item.get("threshold_offset") or 0.0)),
            ),
            reverse=True,
        )
    top_candidates = ordered[: max(1, int(limit or 6))]
    total_remaining = len(remaining_plan) + len(remaining_grid)
    if total_remaining == 0:
        next_step = "目前選入批次已覆蓋可用候選；下一步是小批次執行並累積 outcome。"
    elif source == "grid" and remaining_grid:
        next_step = "Grid 還有未試高分組合；下一輪可加大 max_runs 或分批執行這些市場池設定。"
    elif source == "plan" and remaining_plan:
        next_step = "歷史推薦還有未試組合；下一輪可提高 max_runs 或挑分數接近者做對照。"
    elif source == "exploration" and top_candidates:
        next_step = "已切到下一輪探索來源；可繼續分批試算，或小批次執行最高分探索組合。"
    else:
        next_step = "下一輪可從未選入的高分候選開始，補足市場池、風格與 offset 的探索覆蓋。"
    return {
        "status": "ok" if total_remaining > 0 else "empty",
        "candidate_source": source,
        "remaining_count": total_remaining,
        "plan_remaining_count": len(remaining_plan),
        "grid_remaining_count": len(remaining_grid),
        "top_candidates": top_candidates,
        "next_step": next_step,
    }


def _queue_candidate_summary(item: dict[str, Any], *, candidate_source: str) -> dict[str, Any]:
    request_payload = _normalized_request_payload(item.get("request_payload") if isinstance(item.get("request_payload"), dict) else {})
    source = "grid" if candidate_source == "grid" or str(item.get("candidate_source") or "") == "grid" else "plan"
    return {
        "rank": item.get("rank"),
        "label": item.get("label") or f"{_universe_label(str(request_payload.get('universe') or ''))} / {_profile_label(str(request_payload.get('risk_profile') or ''))} / offset {float(request_payload.get('threshold_offset') or 0.0):+.1f}",
        "candidate_source": source,
        "pool_key": item.get("pool_key"),
        "readiness": item.get("readiness"),
        "score": round(float(item.get("score") or 0.0), 1),
        "risk_profile": request_payload.get("risk_profile"),
        "threshold_offset": request_payload.get("threshold_offset"),
        "universe": request_payload.get("universe"),
        "cli_command": item.get("cli_command") or _daily_radar_cli_command(request_payload),
        "request_payload": request_payload,
    }


def _walk_forward_plan(
    backtest: dict[str, Any],
    rows: list[dict[str, Any]],
    exploration_queue: dict[str, Any],
    *,
    execute: bool,
    lookback_runs: int,
    lookahead_bars_list: tuple[int, ...],
    count: int,
    base_profile: str | None,
    candidate_source: str,
    grid_universes: tuple[str, ...],
    grid_profiles: tuple[str, ...],
    grid_offsets: tuple[float, ...],
) -> dict[str, Any]:
    available_runs = _safe_int(backtest.get("lookback_runs") or lookback_runs)
    evaluated_runs = _safe_int(backtest.get("evaluated_run_count") or backtest.get("lookback_runs") or lookback_runs)
    min_samples = max(1, _safe_int(backtest.get("min_samples") or 3))
    horizons = tuple(lookahead_bars_list or (5, 10, 20))
    exploration_count = int((exploration_queue or {}).get("remaining_count") or 0)
    selected_count = len([row for row in rows if isinstance(row, dict)])
    validation_runs = max(1, min(3, max(available_runs, 1) // 3))
    training_runs = max(0, available_runs - validation_runs)
    status = "ok" if selected_count > 0 and evaluated_runs >= min_samples else "insufficient"
    source = _normalized_candidate_source(candidate_source)
    next_source = "exploration" if exploration_count > 0 else source
    dry_run_command = _threshold_optimization_cli_command(
        execute=False,
        max_runs=6 if next_source == "exploration" else max(1, min(6, selected_count or 3)),
        lookback_runs=lookback_runs,
        lookahead_bars_list=horizons,
        count=count,
        base_profile=base_profile,
        candidate_source=next_source,
        grid_universes=grid_universes,
        grid_profiles=grid_profiles,
        grid_offsets=grid_offsets,
    )
    execute_command = _threshold_optimization_cli_command(
        execute=True,
        max_runs=1,
        lookback_runs=lookback_runs,
        lookahead_bars_list=horizons,
        count=count,
        base_profile=base_profile,
        candidate_source=next_source,
        grid_universes=grid_universes,
        grid_profiles=grid_profiles,
        grid_offsets=grid_offsets,
    )
    steps = [
        {
            "phase": "train",
            "label": "訓練視窗",
            "detail": f"用最近 {training_runs or available_runs} 輪成熟雷達與 {','.join(str(item) for item in horizons)} 日 outcome 產生候選排名。",
            "command": _threshold_optimization_cli_command(
                execute=False,
                max_runs=max(1, min(6, selected_count or 3)),
                lookback_runs=lookback_runs,
                lookahead_bars_list=horizons,
                count=count,
                base_profile=base_profile,
                candidate_source=source,
                grid_universes=grid_universes,
                grid_profiles=grid_profiles,
                grid_offsets=grid_offsets,
            ),
        },
        {
            "phase": "validate",
            "label": "驗證下一批",
            "detail": f"改用 {radar_threshold_source_label(next_source)} 乾跑未試候選，檢查市場池、風格與 offset 是否仍穩。",
            "command": dry_run_command,
        },
        {
            "phase": "probe",
            "label": "小批次 Probe",
            "detail": "只執行第 1 組，寫入雷達 run 後等待 5 / 10 / 20 日 outcome 回看。",
            "command": execute_command,
        },
    ]
    guardrails: list[str] = []
    if not execute:
        guardrails.append("目前仍是乾跑計畫，真正驗證需執行第 1 組並等待 outcome 成熟。")
    if evaluated_runs < min_samples:
        guardrails.append("成熟樣本不足，先降低採用權重。")
    if exploration_count == 0:
        guardrails.append("下一輪探索佇列目前沒有剩餘候選，需擴大 grid 或累積更多歷史 run。")
    return {
        "status": status,
        "method": "rolling_threshold_probe",
        "mode": "execute" if execute else "dry_run",
        "candidate_source": source,
        "next_candidate_source": next_source,
        "available_run_count": available_runs,
        "evaluated_run_count": evaluated_runs,
        "training_run_count": training_runs or available_runs,
        "validation_run_count": validation_runs,
        "lookahead_bars_list": list(horizons),
        "selected_count": selected_count,
        "exploration_remaining_count": exploration_count,
        "recommended_max_runs": 6 if exploration_count > 0 else max(1, min(6, selected_count or 3)),
        "steps": steps,
        "primary_command": dry_run_command,
        "execute_probe_command": execute_command,
        "guardrails": guardrails[:4],
        "next_step": (
            "先乾跑下一批探索，再只執行第 1 組做 out-of-sample probe。"
            if exploration_count > 0
            else "先擴大 grid 或累積更多成熟 outcome，再啟動下一輪 walk-forward 探索。"
        ),
    }


def radar_threshold_source_label(source: str) -> str:
    key = _normalized_candidate_source(source)
    if key == "grid":
        return "跨市場池 Grid"
    if key == "exploration":
        return "下一輪探索"
    if key == "mixed":
        return "推薦 + Grid"
    return "歷史推薦"


def _threshold_optimization_cli_command(
    *,
    execute: bool,
    max_runs: int,
    lookback_runs: int,
    lookahead_bars_list: tuple[int, ...],
    count: int,
    base_profile: str | None,
    candidate_source: str,
    grid_universes: tuple[str, ...],
    grid_profiles: tuple[str, ...],
    grid_offsets: tuple[float, ...],
) -> str:
    args = [
        ".venv/bin/python",
        "-m",
        "app.cli",
        "run-daily-radar-threshold-optimization",
        "--max-runs",
        str(max(1, min(6, int(max_runs or 1)))),
        "--lookback-runs",
        str(max(2, min(24, int(lookback_runs or 8)))),
        "--horizons",
        ",".join(str(int(value)) for value in (lookahead_bars_list or (5, 10, 20))),
        "--count",
        str(max(1, min(100, int(count or 50)))),
        "--candidate-source",
        _normalized_candidate_source(candidate_source),
    ]
    if execute:
        args.append("--execute")
    if base_profile:
        args.extend(["--base-profile", str(base_profile)])
    universes = _normalized_grid_universes(grid_universes)
    profiles = _normalized_grid_profiles(grid_profiles)
    offsets = _normalized_grid_offsets(grid_offsets)
    if universes:
        args.extend(["--grid-universes", ",".join(universes)])
    if profiles:
        args.extend(["--grid-profiles", ",".join(profiles)])
    if offsets:
        args.extend(["--grid-offsets", ",".join(f"{float(value):g}" for value in offsets)])
    return " ".join(shlex.quote(part) for part in args)


def _normalized_candidate_source(value: Any) -> str:
    text = str(value or "mixed").strip().lower()
    return text if text in {"mixed", "plan", "grid", "exploration"} else "mixed"


def _candidate_key(item: dict[str, Any]) -> tuple[str, str, float, str]:
    payload = _normalized_request_payload(item.get("request_payload") if isinstance(item.get("request_payload"), dict) else {})
    return (
        str(payload.get("universe") or ""),
        _normalized_profile(payload.get("risk_profile")),
        round(float(payload.get("threshold_offset") or 0.0), 3),
        str(payload.get("symbols_csv") or ""),
    )


def _candidate_source_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        source = str(item.get("candidate_source") or "plan")
        counts[source] = counts.get(source, 0) + 1
    return counts


def _normalized_grid_universes(values: tuple[str, ...]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return tuple(output[:8])


def _normalized_grid_profiles(values: tuple[str, ...]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        profile = _normalized_profile(raw)
        if profile in seen:
            continue
        seen.add(profile)
        output.append(profile)
    return tuple(output[:3])


def _normalized_grid_offsets(values: tuple[float, ...]) -> tuple[float, ...]:
    output: list[float] = []
    seen: set[float] = set()
    for raw in values or ():
        try:
            offset = max(-10.0, min(10.0, float(raw or 0.0)))
        except Exception:
            continue
        key = round(offset, 3)
        if key in seen:
            continue
        seen.add(key)
        output.append(offset)
    return tuple(output[:7])


def _grid_candidate_score(backtest: dict[str, Any], *, profile: str, offset: float, universe: str) -> float:
    try:
        base_score = float(backtest.get("robustness_score") or 55.0)
    except Exception:
        base_score = 55.0
    profile_bias = {"balanced": 3.0, "conservative": 1.5, "aggressive": -1.0}.get(_normalized_profile(profile), 0.0)
    universe_bias = {
        "research_ready": 3.0,
        "dynamic_balanced": 2.0,
        "dynamic_liquid": 1.5,
        "tw0050": 1.0,
        "tw_mid100": 0.8,
        "curated_watchlist": 0.5,
    }.get(str(universe or ""), 0.0)
    offset_penalty = abs(float(offset or 0.0)) * 1.4
    return round(max(0.0, min(100.0, base_score + profile_bias + universe_bias - offset_penalty)), 1)


def _daily_radar_cli_args(request_payload: dict[str, Any]) -> list[str]:
    return [
        ".venv/bin/python",
        "-m",
        "app.cli",
        "run-daily-radar-job",
        "--universe",
        str(request_payload.get("universe") or "research_ready"),
        "--risk-profile",
        str(request_payload.get("risk_profile") or "balanced"),
        "--threshold-offset",
        str(float(request_payload.get("threshold_offset") or 0.0)),
        "--target-count",
        str(int(request_payload.get("target_count") or 15)),
    ]


def _daily_radar_cli_command(request_payload: dict[str, Any]) -> str:
    return " ".join(shlex.quote(part) for part in _daily_radar_cli_args(request_payload))


def _profile_label(profile: str) -> str:
    return {"aggressive": "積極", "balanced": "平衡", "conservative": "保守"}.get(_normalized_profile(profile), str(profile or ""))


def _universe_label(universe: str) -> str:
    labels = {
        "research_ready": "研究就緒池",
        "curated_watchlist": "精選觀察池",
        "dynamic_balanced": "動態平衡池",
        "dynamic_liquid": "動態流動性池",
        "tw0050": "台灣 50",
        "tw_mid100": "中型 100",
        "twse_tpex": "上市上櫃",
    }
    return labels.get(str(universe or ""), str(universe or "市場池"))


def _execute_run_item(
    repo,
    *,
    request_payload: dict[str, Any],
    adoption_metadata: dict[str, Any],
    sync_service,
    trade_date_mode: str,
    runner: DailyRadarJobRunner | None,
) -> dict[str, Any]:
    if runner is None:
        from app.scheduler.jobs import run_daily_radar_job as runner

    try:
        result = runner(
            repo=repo,
            sync_service=sync_service,
            notifiers=[],
            trade_date_mode=_normalized_trade_date_mode(trade_date_mode),
            target_count=int(request_payload["target_count"]),
            risk_profile=str(request_payload["risk_profile"]),
            threshold_offset=float(request_payload["threshold_offset"]),
            universe=str(request_payload["universe"]),
            data_freshness_required=bool(request_payload["data_freshness_required"]),
            include_attention=bool(request_payload["include_attention"]),
            include_extended_data=bool(request_payload["include_extended_data"]),
            prefer_official_bulk=bool(request_payload["prefer_official_bulk"]),
            symbols=str(request_payload["symbols_csv"]),
            threshold_adoption=adoption_metadata,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": str(exc),
        }
    degraded = bool(result.get("degraded"))
    top_items = _compact_result_items(result)
    return {
        "status": "partial" if degraded else "ok",
        "run_id": result.get("run_id"),
        "trade_date": result.get("trade_date"),
        "items": result.get("items"),
        "top_items": top_items,
        "top_symbols": [item["symbol"] for item in top_items if item.get("symbol")],
        "label_counts": result.get("label_counts") or {},
        "degraded": degraded,
        "degrade_reasons": result.get("degrade_reasons") or [],
        "export": result.get("export") or {},
        "threshold_adoption": result.get("threshold_adoption") or adoption_metadata,
    }


def _adoption_metadata_for_item(item: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
    symbols = str(request_payload.get("symbols_csv") or "").replace("\n", ",").split(",")
    return {
        "source": "threshold_optimization_batch",
        "action": "execute_recommended_run",
        "label": item.get("label") or "",
        "reason": item.get("note") or "由 threshold-backtest 批次最佳化執行。",
        "candidate_rank": item.get("rank"),
        "pool_key": item.get("pool_key"),
        "readiness": item.get("readiness"),
        "candidate_score": item.get("score"),
        "profile": request_payload.get("risk_profile"),
        "profile_label": str(request_payload.get("risk_profile") or ""),
        "entry_offset": request_payload.get("threshold_offset"),
        "threshold_offset": request_payload.get("threshold_offset"),
        "top_symbols": [str(symbol or "").strip() for symbol in symbols if str(symbol or "").strip()],
    }


def _normalized_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": payload.get("trade_date"),
        "target_count": max(10, min(20, int(float(payload.get("target_count") or 15)))),
        "risk_profile": _normalized_profile(payload.get("risk_profile")),
        "threshold_offset": max(-10.0, min(10.0, float(payload.get("threshold_offset") or 0.0))),
        "universe": str(payload.get("universe") or "research_ready"),
        "symbols_csv": str(payload.get("symbols_csv") or ""),
        "data_freshness_required": bool(payload.get("data_freshness_required", True)),
        "include_attention": bool(payload.get("include_attention", False)),
        "include_extended_data": bool(payload.get("include_extended_data", False)),
        "prefer_official_bulk": bool(payload.get("prefer_official_bulk", True)),
    }


def _normalized_profile(value: Any) -> str:
    profile = str(value or "balanced").strip().lower()
    return profile if profile in {"conservative", "balanced", "aggressive"} else "balanced"


def _normalized_trade_date_mode(value: Any) -> str:
    mode = str(value or "T").strip().upper()
    return mode if mode in {"T", "T-1"} else "T"


def _summarize(rows: list[dict[str, Any]], *, execute: bool) -> dict[str, Any]:
    counts = {
        "planned": sum(1 for row in rows if row.get("status") == "planned"),
        "ok": sum(1 for row in rows if row.get("status") == "ok"),
        "partial": sum(1 for row in rows if row.get("status") == "partial"),
        "error": sum(1 for row in rows if row.get("status") == "error"),
    }
    if not rows:
        status = "empty"
    elif not execute:
        status = "planned"
    elif counts["error"] > 0:
        status = "error" if counts["ok"] == 0 and counts["partial"] == 0 else "partial"
    elif counts["partial"] > 0:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "total": len(rows),
        "planned_count": counts["planned"],
        "executed_count": counts["ok"] + counts["partial"] + counts["error"],
        "ok_count": counts["ok"],
        "partial_count": counts["partial"],
        "error_count": counts["error"],
        "best_decision_score": max((float(row.get("decision_score") or 0.0) for row in rows), default=0.0),
        "actionable_count_total": sum(int(row.get("actionable_count") or 0) for row in rows),
        "entry_ready_count_total": sum(int(row.get("entry_ready_count") or 0) for row in rows),
        "top_symbols": _unique_top_symbols(rows),
    }


def _unique_top_symbols(rows: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for symbol in row.get("top_symbols") or []:
            text = str(symbol or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            symbols.append(text)
            if len(symbols) >= max(1, int(limit or 12)):
                return symbols
    return symbols


def _decision_metrics(row: dict[str, Any], *, execute: bool) -> dict[str, Any]:
    plan_score = max(0.0, min(100.0, float(row.get("score") or 0.0)))
    if not execute:
        return {
            "decision_score": round(plan_score, 1),
            "decision_label": "待執行",
            "decision_reason": "此分數仍來自歷史門檻回看；先乾跑確認參數，再小批次重跑。",
            "entry_ready_count": 0,
            "watch_count": 0,
            "actionable_count": 0,
        }
    status = str(row.get("status") or "")
    if status == "error":
        return {
            "decision_score": 0.0,
            "decision_label": "執行失敗",
            "decision_reason": str(row.get("error") or "此組重跑失敗，先修資料同步或市場池問題。"),
            "entry_ready_count": 0,
            "watch_count": 0,
            "actionable_count": 0,
        }

    label_counts = row.get("label_counts") if isinstance(row.get("label_counts"), dict) else {}
    entry_ready = int(label_counts.get("ENTRY_READY") or 0)
    watch = int(label_counts.get("WATCH_WAIT_TRIGGER") or 0)
    actionable = entry_ready + watch
    degraded = bool(row.get("degraded"))
    penalty = 18.0 if degraded else 0.0
    if actionable == 0:
        penalty += 16.0
    score = max(
        0.0,
        min(
            100.0,
            plan_score * 0.55
            + min(28.0, entry_ready * 7.0)
            + min(16.0, watch * 3.0)
            + (6.0 if status == "ok" else 0.0)
            - penalty,
        ),
    )
    if degraded:
        label = "先複核"
        reason = "重跑有降級資料，先看 degrade_reasons 與資料 freshness，再決定是否採用。"
    elif entry_ready > 0:
        label = "優先採用"
        reason = "重跑後仍有可進場標的，可列為下一輪人工檢查的優先設定。"
    elif watch > 0:
        label = "保留觀察"
        reason = "重跑後沒有可進場標的，但有等待觸發標的；適合放進觀察線。"
    else:
        label = "暫不採用"
        reason = "重跑後沒有可進場或等待觸發標的，先保留為對照組。"
    return {
        "decision_score": round(score, 1),
        "decision_label": label,
        "decision_reason": reason,
        "entry_ready_count": entry_ready,
        "watch_count": watch,
        "actionable_count": actionable,
    }


def _compact_result_items(result: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    raw_items = result.get("top_items")
    if not isinstance(raw_items, list):
        raw_items = result.get("items") if isinstance(result.get("items"), list) else []
    rows = [item for item in raw_items if isinstance(item, dict)]
    priority = {"ENTRY_READY": 3, "WATCH_WAIT_TRIGGER": 2, "NOT_RECOMMENDED": 1}
    rows.sort(
        key=lambda item: (
            priority.get(str(item.get("action_label") or ""), 0),
            float(item.get("risk_adjusted_score") or item.get("entry_score") or 0.0),
            float(item.get("entry_score") or 0.0),
        ),
        reverse=True,
    )
    compact: list[dict[str, Any]] = []
    for item in rows[: max(1, int(limit or 5))]:
        compact.append(
            {
                "rank": item.get("rank"),
                "symbol": str(item.get("symbol") or ""),
                "name": str(item.get("name") or ""),
                "industry_group": str(item.get("industry_group") or ""),
                "action_label": str(item.get("action_label") or ""),
                "entry_score": item.get("entry_score"),
                "risk_adjusted_score": item.get("risk_adjusted_score"),
                "risk_score": item.get("risk_score"),
                "reason_tags": list(item.get("reason_tags") or [])[:6] if isinstance(item.get("reason_tags"), list) else [],
                "blocker_tags": list(item.get("blocker_tags") or [])[:6] if isinstance(item.get("blocker_tags"), list) else [],
            }
        )
    return compact


def _recommended_run(rows: list[dict[str, Any]], *, execute: bool) -> dict[str, Any] | None:
    if not rows:
        return None
    best = max(
        rows,
        key=lambda row: (
            float(row.get("decision_score") or 0.0),
            int(row.get("entry_ready_count") or 0),
            int(row.get("actionable_count") or 0),
            -int(row.get("rank") or 999),
        ),
    )
    return {
        "rank": best.get("rank"),
        "label": best.get("label"),
        "pool_key": best.get("pool_key"),
        "readiness": best.get("readiness"),
        "candidate_source": best.get("candidate_source") or "plan",
        "status": best.get("status"),
        "run_id": best.get("run_id"),
        "decision_score": best.get("decision_score"),
        "decision_label": best.get("decision_label"),
        "decision_reason": best.get("decision_reason")
        or ("乾跑結果僅代表歷史排名，尚未重跑。" if not execute else ""),
        "request_payload": best.get("request_payload"),
        "cli_command": best.get("cli_command") or "",
        "top_symbols": best.get("top_symbols") or [],
        "top_items": best.get("top_items") or [],
    }


def _source_comparison(
    rows: list[dict[str, Any]],
    *,
    execute: bool,
    plan_available_count: int = 0,
    grid_available_count: int = 0,
) -> dict[str, Any]:
    clean_rows = [row for row in rows if isinstance(row, dict)]
    plan_rows = [row for row in clean_rows if str(row.get("candidate_source") or "plan") != "grid"]
    grid_rows = [row for row in clean_rows if str(row.get("candidate_source") or "") == "grid"]
    best_plan = _recommended_run(plan_rows, execute=execute)
    best_grid = _recommended_run(grid_rows, execute=execute)
    plan_score = float((best_plan or {}).get("decision_score") or 0.0)
    grid_score = float((best_grid or {}).get("decision_score") or 0.0)
    if best_plan and best_grid:
        if grid_score > plan_score + 0.5:
            leader_source = "grid"
            verdict = "grid_leads"
            label = "Grid 領先"
            next_step = "可優先執行 Grid 第 1 組，再用採用追蹤確認後續 outcome。"
        elif plan_score > grid_score + 0.5:
            leader_source = "plan"
            verdict = "plan_leads"
            label = "歷史推薦領先"
            next_step = "先執行歷史推薦第 1 組；Grid 保留為下一輪探索對照。"
        else:
            leader_source = "tie"
            verdict = "close"
            label = "兩者接近"
            next_step = "推薦與 Grid 分數接近，可各小批次跑 1 組比較穩定度。"
    elif best_grid:
        leader_source = "grid"
        verdict = "grid_only"
        label = "僅 Grid 候選"
        next_step = "目前只選到 Grid 候選；先小批次執行最高分組合。"
    elif best_plan:
        leader_source = "plan"
        verdict = "plan_only"
        label = "僅歷史推薦"
        next_step = "目前只選到歷史推薦；如要探索市場池，請加入 grid 參數乾跑。"
    else:
        leader_source = "none"
        verdict = "empty"
        label = "沒有可比較候選"
        next_step = "先累積更多 outcome 或加入 grid 參數。"
    primary_action = _source_primary_action(leader_source, best_plan=best_plan, best_grid=best_grid)
    actions = [
        action
        for action in (
            _source_candidate_action("plan", best_plan),
            _source_candidate_action("grid", best_grid),
        )
        if action
    ]
    confidence = _source_comparison_confidence(
        leader_source=leader_source,
        plan_count=len(plan_rows),
        grid_count=len(grid_rows),
        plan_available_count=plan_available_count,
        grid_available_count=grid_available_count,
        score_gap=grid_score - plan_score,
        execute=execute,
    )
    coverage = {
        "plan_selected": len(plan_rows),
        "plan_available": max(len(plan_rows), int(plan_available_count or 0)),
        "grid_selected": len(grid_rows),
        "grid_available": max(len(grid_rows), int(grid_available_count or 0)),
    }
    return {
        "status": "ok" if clean_rows else "empty",
        "mode": "execute" if execute else "dry_run",
        "verdict": verdict,
        "leader_source": leader_source,
        "label": label,
        "plan_count": len(plan_rows),
        "grid_count": len(grid_rows),
        "coverage": coverage,
        "best_plan_score": round(plan_score, 1),
        "best_grid_score": round(grid_score, 1),
        "score_gap": round(grid_score - plan_score, 1),
        "best_plan": best_plan,
        "best_grid": best_grid,
        "primary_action": primary_action,
        "actions": actions,
        "confidence": confidence,
        "confidence_label": confidence["label"],
        "guardrails": confidence["guardrails"],
        "next_step": next_step,
    }


def _source_comparison_confidence(
    *,
    leader_source: str,
    plan_count: int,
    grid_count: int,
    plan_available_count: int,
    grid_available_count: int,
    score_gap: float,
    execute: bool,
) -> dict[str, Any]:
    absolute_gap = abs(float(score_gap or 0.0))
    guardrails: list[str] = []
    if not execute:
        guardrails.append("乾跑分數仍需小批次重跑驗證。")
    if plan_count == 0 or grid_count == 0:
        guardrails.append("目前只有單一來源候選，不能視為完整來源比較。")
    if plan_available_count > plan_count or grid_available_count > grid_count:
        guardrails.append("本次只比較已選入批次的前幾組候選。")
    if leader_source == "tie" or absolute_gap <= 0.5:
        level = "close"
        label = "分數接近"
        guardrails.append("兩邊分數差距很小，建議各跑 1 組看 outcome。")
    elif absolute_gap >= 5.0:
        level = "high"
        label = "高信心"
    elif absolute_gap >= 2.0:
        level = "medium"
        label = "中等信心"
    else:
        level = "low"
        label = "低信心"
        guardrails.append("領先幅度有限，先當成探索線索。")
    return {
        "level": level,
        "label": label,
        "score_gap_abs": round(absolute_gap, 1),
        "guardrails": guardrails[:4],
    }


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except Exception:
        return 0


def _source_primary_action(
    leader_source: str,
    *,
    best_plan: dict[str, Any] | None,
    best_grid: dict[str, Any] | None,
) -> dict[str, Any]:
    if leader_source == "grid" and best_grid:
        return _source_candidate_action("grid", best_grid) or {"enabled": False}
    if leader_source == "plan" and best_plan:
        return _source_candidate_action("plan", best_plan) or {"enabled": False}
    if leader_source == "tie":
        if best_plan and best_grid:
            return {
                "enabled": True,
                "candidate_source": "mixed",
                "label": "兩邊各小批次跑 1 組",
                "reason": "推薦與 Grid 分數接近，適合用小批次重跑比較 outcome。",
            }
        return _source_candidate_action("plan", best_plan) or _source_candidate_action("grid", best_grid) or {"enabled": False}
    return _source_candidate_action("plan", best_plan) or _source_candidate_action("grid", best_grid) or {"enabled": False}


def _source_candidate_action(source: str, candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    source_key = "grid" if source == "grid" else "plan"
    return {
        "enabled": True,
        "candidate_source": source_key,
        "label": "執行 Grid 第 1 組" if source_key == "grid" else "執行推薦第 1 組",
        "candidate_label": candidate.get("label") or "",
        "decision_score": candidate.get("decision_score"),
        "cli_command": candidate.get("cli_command") or "",
        "request_payload": candidate.get("request_payload") or {},
    }


def _optimization_grid(rows: list[dict[str, Any]], *, execute: bool) -> dict[str, Any]:
    clean_rows = [row for row in rows if isinstance(row, dict)]
    pool_rows = _group_grid_rows(clean_rows, key_fn=_pool_grid_key, label_fn=_pool_grid_label)
    profile_rows = _group_grid_rows(clean_rows, key_fn=_profile_grid_key, label_fn=_profile_grid_label)
    offset_rows = _group_grid_rows(clean_rows, key_fn=_offset_grid_key, label_fn=_offset_grid_label)
    matrix = _pool_profile_matrix(clean_rows)
    best_pool = pool_rows[0] if pool_rows else None
    best_profile = profile_rows[0] if profile_rows else None
    best_offset = offset_rows[0] if offset_rows else None
    return {
        "status": "ok" if clean_rows else "empty",
        "mode": "execute" if execute else "dry_run",
        "summary": {
            "pool_count": len(pool_rows),
            "profile_count": len(profile_rows),
            "offset_count": len(offset_rows),
            "evaluated_count": len(clean_rows),
            "best_pool": best_pool,
            "best_profile": best_profile,
            "best_offset": best_offset,
            "next_step": _optimization_grid_next_step(best_pool, execute=execute),
        },
        "pool_rows": pool_rows,
        "profile_rows": profile_rows,
        "offset_rows": offset_rows,
        "matrix": matrix,
    }


def _group_grid_rows(
    rows: list[dict[str, Any]],
    *,
    key_fn: Callable[[dict[str, Any]], str],
    label_fn: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        key = key_fn(row)
        grouped.setdefault(key, []).append(row)
        labels.setdefault(key, label_fn(row))
    output = [_grid_group_summary(key, labels.get(key, key), items) for key, items in grouped.items()]
    return sorted(
        output,
        key=lambda item: (
            float(item.get("best_decision_score") or 0.0),
            int(item.get("entry_ready_count") or 0),
            int(item.get("actionable_count") or 0),
            int(item.get("row_count") or 0),
        ),
        reverse=True,
    )


def _grid_group_summary(key: str, label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    best = _recommended_run(rows, execute=any(str(row.get("status") or "") != "planned" for row in rows)) or {}
    readiness_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for row in rows:
        readiness = str(row.get("readiness") or "unknown")
        readiness_counts[readiness] = readiness_counts.get(readiness, 0) + 1
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    scores = [float(row.get("decision_score") or 0.0) for row in rows]
    return {
        "key": key,
        "label": label,
        "row_count": len(rows),
        "planned_count": status_counts.get("planned", 0),
        "ok_count": status_counts.get("ok", 0),
        "partial_count": status_counts.get("partial", 0),
        "error_count": status_counts.get("error", 0),
        "readiness_counts": readiness_counts,
        "best_decision_score": round(max(scores), 1) if scores else 0.0,
        "average_decision_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "entry_ready_count": sum(int(row.get("entry_ready_count") or 0) for row in rows),
        "watch_count": sum(int(row.get("watch_count") or 0) for row in rows),
        "actionable_count": sum(int(row.get("actionable_count") or 0) for row in rows),
        "top_symbols": _unique_top_symbols(rows, limit=8),
        "best_run": best,
    }


def _pool_profile_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    pool_labels: dict[str, str] = {}
    for row in rows:
        pool_key = _pool_grid_key(row)
        profile = _profile_grid_key(row)
        grouped.setdefault(pool_key, {}).setdefault(profile, []).append(row)
        pool_labels.setdefault(pool_key, _pool_grid_label(row))
    matrix: list[dict[str, Any]] = []
    for pool_key, profile_map in grouped.items():
        cells = [
            _grid_group_summary(profile, _profile_grid_label(items[0]), items)
            for profile, items in profile_map.items()
            if items
        ]
        cells.sort(key=lambda item: float(item.get("best_decision_score") or 0.0), reverse=True)
        matrix.append(
            {
                "pool_key": pool_key,
                "pool_label": pool_labels.get(pool_key, pool_key),
                "cells": cells,
            }
        )
    return sorted(
        matrix,
        key=lambda item: max((float(cell.get("best_decision_score") or 0.0) for cell in item["cells"]), default=0.0),
        reverse=True,
    )


def _pool_grid_key(row: dict[str, Any]) -> str:
    payload = row.get("request_payload") if isinstance(row.get("request_payload"), dict) else {}
    return str(row.get("pool_key") or payload.get("universe") or "unknown")


def _pool_grid_label(row: dict[str, Any]) -> str:
    label = str(row.get("label") or "").split(" / ", 1)[0].strip()
    return label or _pool_grid_key(row)


def _profile_grid_key(row: dict[str, Any]) -> str:
    payload = row.get("request_payload") if isinstance(row.get("request_payload"), dict) else {}
    return _normalized_profile(payload.get("risk_profile"))


def _profile_grid_label(row: dict[str, Any]) -> str:
    return {"aggressive": "積極", "balanced": "平衡", "conservative": "保守"}.get(_profile_grid_key(row), _profile_grid_key(row))


def _offset_grid_key(row: dict[str, Any]) -> str:
    payload = row.get("request_payload") if isinstance(row.get("request_payload"), dict) else {}
    try:
        offset = float(payload.get("threshold_offset") or 0.0)
    except Exception:
        offset = 0.0
    return f"{offset:+.1f}"


def _offset_grid_label(row: dict[str, Any]) -> str:
    return f"offset {_offset_grid_key(row)}"


def _optimization_grid_next_step(best_pool: dict[str, Any] | None, *, execute: bool) -> str:
    if not best_pool:
        return "先累積更多 threshold-backtest 推薦設定，再建立跨市場池比較。"
    if not execute:
        return f"優先乾跑排名最高的 {best_pool.get('label') or '市場池'}，再小批次執行前 1 到 3 組。"
    if int(best_pool.get("entry_ready_count") or 0) > 0:
        return f"{best_pool.get('label') or '最佳市場池'} 重跑後仍有可進場標的，可進入人工檢查。"
    return f"{best_pool.get('label') or '最佳市場池'} 目前偏觀察，先保留觸發線並等待更多 outcome。"


def _next_step(summary: dict[str, Any], *, execute: bool) -> str:
    if summary["status"] == "empty":
        return "先累積更多成熟 outcome，再產生可重跑的門檻最佳化計畫。"
    if not execute:
        return "先確認前幾組參數與市場池，再用 execute=true 小批次實跑。"
    if int(summary.get("error_count") or 0) > 0:
        return "先檢查失敗組的資料同步或市場池，再重跑失敗批次。"
    if int(summary.get("partial_count") or 0) > 0:
        return "批次已完成但有降級資料；先看 degrade_reasons 再決定是否採用。"
    return "批次重跑完成；重新整理門檻歷史實跑，確認最新 run 是否改善穩健區間。"
