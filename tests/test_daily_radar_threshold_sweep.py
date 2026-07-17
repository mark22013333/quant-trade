from __future__ import annotations

from app.reports.daily_radar_threshold_backtest import build_daily_radar_threshold_backtest
from app.reports.daily_radar_threshold_optimization import run_daily_radar_threshold_optimization_batch
from app.reports.daily_radar_threshold_sweep import build_daily_radar_threshold_sweep


def test_daily_radar_threshold_sweep_marks_stable_and_fragile_symbols():
    payload = build_daily_radar_threshold_sweep(
        [
            {
                "symbol": "2330",
                "action_label": "ENTRY_READY",
                "entry_score": 88.0,
                "blocker_tags": [],
                "reason_tags": ["PROFILE=balanced", "TREND_OK=True", "VOL_OK=True"],
            },
            {
                "symbol": "2454",
                "action_label": "WATCH_WAIT_TRIGGER",
                "entry_score": 76.0,
                "blocker_tags": [],
                "reason_tags": ["PROFILE=balanced", "TREND_OK=True", "VOL_OK=True"],
            },
            {
                "symbol": "2881",
                "action_label": "WATCH_WAIT_TRIGGER",
                "entry_score": 61.0,
                "blocker_tags": [],
                "reason_tags": ["PROFILE=balanced", "TREND_OK=False", "VOL_OK=True"],
            },
            {
                "symbol": "2603",
                "action_label": "NOT_RECOMMENDED",
                "entry_score": 48.0,
                "blocker_tags": [],
                "reason_tags": ["PROFILE=balanced", "TREND_OK=False", "VOL_OK=False"],
            },
            {
                "symbol": "2303",
                "action_label": "ENTRY_READY",
                "entry_score": 84.0,
                "blocker_tags": ["B_DATA_STALE"],
                "reason_tags": ["PROFILE=balanced", "TREND_OK=True", "VOL_OK=True"],
            },
        ]
    )

    assert payload["status"] == "ok"
    assert payload["base_profile"] == "balanced"
    assert payload["baseline"]["entry_count"] == 1
    assert payload["baseline"]["watch_count"] == 2
    assert payload["metrics"]["baseline_actionable_count"] == 3
    assert payload["stable_symbols"] == ["2330", "2454"]
    assert payload["stable_entry_symbols"] == ["2330"]
    assert payload["fragile_symbols"] == ["2881"]
    assert payload["profiles"][0]["profile"] == "aggressive"
    assert payload["profiles"][2]["profile"] == "conservative"
    assert [item["entry_offset"] for item in payload["threshold_variants"]] == [-4.0, -2.0, 0.0, 2.0, 4.0]
    assert payload["robustness_score"] >= 55


def test_daily_radar_threshold_sweep_reports_empty_payload():
    payload = build_daily_radar_threshold_sweep([])

    assert payload["status"] == "empty"
    assert payload["verdict"] == "資料不足"
    assert payload["threshold_variants"] == []


def test_daily_radar_threshold_backtest_prefers_historically_stable_thresholds():
    runs = []
    for idx, run_date in enumerate(["2026-06-01", "2026-06-02", "2026-06-03"], start=1):
        runs.append(
            {
                "run_id": f"radar_{idx}",
                "run_date": run_date,
                "items": [
                    {
                        "symbol": "2330",
                        "market": "TWSE",
                        "industry_group": "半導體業",
                        "action_label": "ENTRY_READY",
                        "entry_score": 86.0,
                        "blocker_tags": [],
                        "reason_tags": ["PROFILE=balanced", "TREND_OK=True", "VOL_OK=True"],
                        "horizon_returns": {5: 0.02 + idx * 0.004, 10: 0.03 + idx * 0.003},
                        "horizon_matured": {5: True, 10: True},
                    },
                    {
                        "symbol": "2454",
                        "market": "TWSE",
                        "industry_group": "半導體業",
                        "action_label": "WATCH_WAIT_TRIGGER",
                        "entry_score": 73.5,
                        "blocker_tags": [],
                        "reason_tags": ["PROFILE=balanced", "TREND_OK=True", "VOL_OK=True"],
                        "horizon_returns": {5: 0.006 + idx * 0.002, 10: 0.012 + idx * 0.001},
                        "horizon_matured": {5: True, 10: True},
                    },
                    {
                        "symbol": "2881",
                        "market": "TPEX",
                        "industry_group": "金融保險業",
                        "action_label": "WATCH_WAIT_TRIGGER",
                        "entry_score": 59.0,
                        "blocker_tags": [],
                        "reason_tags": ["PROFILE=balanced", "TREND_OK=False", "VOL_OK=True"],
                        "horizon_returns": {5: -0.035, 10: -0.042},
                        "horizon_matured": {5: True, 10: True},
                    },
                ],
            }
        )

    payload = build_daily_radar_threshold_backtest(
        runs,
        base_profile="balanced",
        lookahead_bars_list=(5, 10),
        min_samples=3,
    )

    assert payload["status"] == "ok"
    assert payload["base_profile"] == "balanced"
    assert payload["best_variant"]["profile"] in {"balanced", "conservative"}
    assert payload["best_variant"]["entry_offset"] >= 0
    assert payload["best_variant"]["multi_horizon_score"] >= payload["baseline_variant"]["multi_horizon_score"]
    assert payload["threshold_heatmap"]["status"] == "ok"
    assert payload["threshold_heatmap"]["offsets"] == [-4.0, -2.0, 0.0, 2.0, 4.0]
    assert payload["threshold_heatmap"]["best_key"].startswith(payload["best_variant"]["profile"])
    assert payload["threshold_heatmap"]["baseline_key"] == "balanced:+0.0"
    assert payload["threshold_heatmap"]["stable_count"] >= 1
    assert payload["threshold_heatmap"]["matrix"][1]["profile"] == "balanced"
    balanced_cells = payload["threshold_heatmap"]["matrix"][1]["cells"]
    baseline_cell = next(item for item in balanced_cells if item["entry_offset"] == 0.0)
    assert baseline_cell["is_baseline"] is True
    assert "基準" in baseline_cell["badges"]
    assert payload["threshold_heatmap"]["strongest_cell"]["is_best"] is True
    assert payload["horizon_summary"][0]["lookahead_bars"] == 5
    assert payload["walk_forward_windows"]["status"] == "ok"
    assert payload["walk_forward_windows"]["method"] == "rolling_train_validate"
    assert payload["walk_forward_windows"]["window_count"] >= 1
    assert payload["walk_forward_windows"]["summary"]["average_validation_score"] >= 0
    assert payload["walk_forward_windows"]["windows"][0]["validation_run_count"] == 1
    assert payload["walk_forward_windows"]["windows"][0]["validation_status"] in {"ok", "watch", "weak", "empty"}
    assert payload["walk_forward_windows"]["windows"][0]["pool_results"]
    assert payload["walk_forward_heatmap"]["status"] == "ok"
    assert payload["walk_forward_heatmap"]["method"] == "rolling_train_validate_heatmap"
    assert payload["walk_forward_heatmap"]["offsets"] == [-4.0, -2.0, 0.0, 2.0, 4.0]
    assert payload["walk_forward_heatmap"]["evaluated_cell_count"] >= 1
    assert payload["walk_forward_heatmap"]["strongest_cell"]["window_count"] >= 1
    assert payload["walk_forward_heatmap"]["matrix"][1]["profile"] == "balanced"
    assert payload["segments"]["market"][0]["label"] == "上市"
    assert payload["segments"]["industry"][0]["label"] == "半導體業"
    assert payload["stable_zone"]
    assert payload["recommended_action"]["action"] == "apply_small"
    assert payload["recommended_action"]["label"] == "可小量採用"
    assert payload["recommended_action"]["decision_score"] >= 72
    assert payload["recommended_action"]["run_params"]["risk_profile"] == "balanced"
    assert payload["recommended_action"]["run_params"]["threshold_offset"] == 0.0
    assert payload["recommended_action"]["top_symbols"][:2] == ["2330", "2454"]
    assert payload["recommended_config"] == payload["optimization_candidates"][0]
    assert payload["recommended_config"]["rank"] == 1
    assert payload["recommended_config"]["pool_key"] == "all"
    assert payload["recommended_config"]["profile"] == "balanced"
    assert payload["recommended_config"]["readiness"] == "preferred"
    assert "walk_forward_adjustment" in payload["recommended_config"]
    assert payload["recommended_config"]["walk_forward_label"]
    assert payload["recommended_config"]["walk_forward_window_count"] >= 0
    assert "pool_walk_forward_adjustment" in payload["recommended_config"]
    assert payload["recommended_config"]["pool_walk_forward_label"]
    assert payload["recommended_config"]["pool_walk_forward_window_count"] >= 0
    assert payload["recommended_config"]["run_params"]["risk_profile"] == "balanced"
    assert payload["recommended_config"]["run_params"]["threshold_offset"] == 0.0
    assert payload["recommended_config"]["run_params"]["universe"] == "research_ready"
    assert payload["pool_recommendation"]["status"] in {"ok", "watch"}
    assert payload["pool_recommendation"]["label"] == "優先市場池"
    assert payload["pool_recommendation"]["recommended_pool"]["pool_key"] == "market:TWSE"
    assert payload["pool_recommendation"]["recommended_pool"]["run_params"]["market_filter"] == "TWSE"
    assert payload["pool_recommendation"]["recommended_pool"]["candidate_rank"] == 2
    assert payload["pool_recommendation"]["alternatives"]
    assert payload["optimization_candidates"][1]["pool_key"] == "market:TWSE"
    assert payload["optimization_candidates"][1]["run_params"]["universe"] == "twse_tpex"
    assert payload["optimization_candidates"][1]["run_params"]["market_filter"] == "TWSE"
    assert payload["optimization_candidates"][2]["pool_key"] == "industry:半導體業"
    assert payload["optimization_candidates"][2]["run_params"]["universe"] == "custom"
    assert payload["optimization_candidates"][2]["run_params"]["symbols_csv"] == "2330,2454"
    assert payload["optimization_plan"]["status"] == "ok"
    assert payload["optimization_plan"]["run_count"] >= 3
    first_plan = payload["optimization_plan"]["run_items"][0]
    assert first_plan["request_payload"]["risk_profile"] == "balanced"
    assert first_plan["request_payload"]["threshold_offset"] == 0.0
    assert first_plan["request_payload"]["universe"] == "research_ready"
    assert "--threshold-offset" in first_plan["cli_args"]


def test_daily_radar_threshold_backtest_uses_adoption_feedback_to_adjust_candidate_score():
    runs = []
    for idx, run_date in enumerate(["2026-06-01", "2026-06-02", "2026-06-03"], start=1):
        runs.append(
            {
                "run_id": f"radar_{idx}",
                "run_date": run_date,
                "items": [
                    {
                        "symbol": "2330",
                        "market": "TWSE",
                        "industry_group": "半導體業",
                        "action_label": "ENTRY_READY",
                        "entry_score": 84.0,
                        "blocker_tags": [],
                        "reason_tags": ["PROFILE=balanced", "TREND_OK=True", "VOL_OK=True"],
                        "horizon_returns": {5: 0.02, 10: 0.025},
                        "horizon_matured": {5: True, 10: True},
                    }
                ],
            }
        )

    payload = build_daily_radar_threshold_backtest(
        runs,
        base_profile="balanced",
        lookahead_bars_list=(5, 10),
        min_samples=3,
        adoption_feedback={
            "status": "ok",
            "tracked_count": 1,
            "matured_run_count": 1,
            "runs": [
                {
                    "run_id": "radar_adopted",
                    "risk_profile": "balanced",
                    "threshold_offset": 0.0,
                    "matured": True,
                    "adoption": {
                        "pool_key": "all",
                        "profile": "balanced",
                        "threshold_offset": 0.0,
                    },
                    "horizon_summary": [
                        {"lookahead_bars": 5, "matured_count": 1, "positive_rate": 1.0, "average_return": 0.04},
                        {"lookahead_bars": 10, "matured_count": 1, "positive_rate": 1.0, "average_return": 0.05},
                    ],
                }
            ],
        },
    )

    candidate = payload["recommended_config"]
    assert candidate["pool_key"] == "all"
    assert candidate["profile"] == "balanced"
    assert candidate["feedback_label"] == "採用後加分"
    assert candidate["feedback_adjustment"] > 0
    assert candidate["score"] > candidate["historical_score"]
    assert payload["adoption_feedback"]["matured_run_count"] == 1


def test_daily_radar_threshold_optimization_batch_dry_run_uses_plan_without_executing():
    class _Repo:
        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001
            assert kwargs["lookback_runs"] == 6
            assert kwargs["lookahead_bars_list"] == (5, 10)
            return _threshold_optimization_backtest_payload()

    calls = []

    payload = run_daily_radar_threshold_optimization_batch(
        _Repo(),
        execute=False,
        max_runs=2,
        lookback_runs=6,
        lookahead_bars_list=(5, 10),
        runner=lambda **kwargs: calls.append(kwargs) or {},
    )

    assert payload["status"] == "planned"
    assert payload["mode"] == "dry_run"
    assert payload["summary"]["planned_count"] == 2
    assert payload["summary"]["best_decision_score"] == 82.0
    assert payload["recommended_run"]["decision_label"] == "待執行"
    assert payload["recommended_run"]["label"] == "全部候選 / 平衡 / offset 2.0"
    assert payload["optimization_grid"]["status"] == "ok"
    assert payload["optimization_grid"]["mode"] == "dry_run"
    assert payload["optimization_grid"]["summary"]["pool_count"] == 2
    assert payload["optimization_grid"]["summary"]["profile_count"] == 2
    assert payload["optimization_grid"]["summary"]["offset_count"] == 2
    assert payload["optimization_grid"]["summary"]["best_pool"]["key"] == "all"
    assert payload["optimization_grid"]["summary"]["best_profile"]["key"] == "balanced"
    assert payload["optimization_grid"]["pool_rows"][0]["label"] == "全部候選"
    assert payload["optimization_grid"]["pool_rows"][0]["best_decision_score"] == 82.0
    assert payload["optimization_grid"]["matrix"][0]["pool_key"] == "all"
    assert payload["run_items"][0]["status"] == "planned"
    assert payload["run_items"][0]["request_payload"]["threshold_offset"] == 2.0
    assert payload["run_items"][0]["decision_score"] == 82.0
    assert calls == []


def test_daily_radar_threshold_optimization_batch_adds_cross_universe_grid_candidates():
    class _Repo:
        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001, ARG002
            return _threshold_optimization_backtest_payload()

    payload = run_daily_radar_threshold_optimization_batch(
        _Repo(),
        execute=False,
        max_runs=4,
        grid_universes=("dynamic_balanced", "tw0050"),
        grid_profiles=("balanced", "conservative"),
        grid_offsets=(-2.0, 0.0),
    )

    assert payload["status"] == "planned"
    assert payload["grid_candidate_count"] > 0
    assert payload["selected_grid_count"] == 2
    assert payload["candidate_sources"] == {"plan": 2, "grid": 2}
    assert payload["source_comparison"]["status"] == "ok"
    assert payload["source_comparison"]["leader_source"] == "plan"
    assert payload["source_comparison"]["best_plan_score"] > payload["source_comparison"]["best_grid_score"]
    assert payload["source_comparison"]["best_plan"]["label"] == "全部候選 / 平衡 / offset 2.0"
    assert payload["source_comparison"]["best_grid"]["candidate_source"] == "grid"
    assert payload["source_comparison"]["primary_action"]["candidate_source"] == "plan"
    assert payload["source_comparison"]["primary_action"]["cli_command"]
    assert payload["source_comparison"]["confidence_label"]
    assert "乾跑分數仍需小批次重跑驗證。" in payload["source_comparison"]["guardrails"]
    assert "本次只比較已選入批次的前幾組候選。" in payload["source_comparison"]["guardrails"]
    assert payload["source_comparison"]["coverage"]["grid_selected"] == 2
    assert payload["source_comparison"]["coverage"]["grid_available"] > payload["source_comparison"]["coverage"]["grid_selected"]
    assert {item["candidate_source"] for item in payload["source_comparison"]["actions"]} == {"plan", "grid"}
    assert all(item["cli_command"] for item in payload["source_comparison"]["actions"])
    assert payload["exploration_queue"]["status"] == "ok"
    assert payload["exploration_queue"]["remaining_count"] > 0
    assert payload["exploration_queue"]["grid_remaining_count"] > 0
    assert payload["exploration_queue"]["top_candidates"][0]["cli_command"]
    assert payload["exploration_queue"]["top_candidates"][0]["candidate_source"] in {"plan", "grid"}
    assert payload["walk_forward_plan"]["status"] == "ok"
    assert payload["walk_forward_plan"]["method"] == "rolling_threshold_probe"
    assert payload["walk_forward_plan"]["next_candidate_source"] == "exploration"
    assert payload["walk_forward_plan"]["steps"][1]["phase"] == "validate"
    assert "--candidate-source exploration" in payload["walk_forward_plan"]["primary_command"]
    assert "--execute" in payload["walk_forward_plan"]["execute_probe_command"]
    grid_rows = [item for item in payload["run_items"] if item["candidate_source"] == "grid"]
    assert grid_rows[0]["pool_key"] == "grid:dynamic_balanced"
    assert grid_rows[0]["request_payload"]["universe"] == "dynamic_balanced"
    assert grid_rows[0]["request_payload"]["risk_profile"] == "balanced"
    assert grid_rows[0]["request_payload"]["threshold_offset"] == 0.0
    assert "run-daily-radar-job" in grid_rows[0]["cli_command"]
    assert "--universe dynamic_balanced" in grid_rows[0]["cli_command"]
    assert payload["optimization_grid"]["summary"]["pool_count"] >= 3
    assert any(row["key"] == "grid:dynamic_balanced" for row in payload["optimization_grid"]["pool_rows"])


def test_daily_radar_threshold_optimization_batch_can_select_exploration_queue():
    class _Repo:
        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001, ARG002
            return _threshold_optimization_backtest_payload()

    payload = run_daily_radar_threshold_optimization_batch(
        _Repo(),
        execute=False,
        max_runs=2,
        grid_universes=("dynamic_balanced", "tw0050"),
        grid_profiles=("balanced", "conservative"),
        grid_offsets=(-2.0, 0.0),
        candidate_source="exploration",
    )

    labels = [item["label"] for item in payload["run_items"]]

    assert payload["candidate_source"] == "exploration"
    assert payload["selected_count"] == 2
    assert payload["selected_grid_count"] == 2
    assert payload["candidate_sources"] == {"grid": 2}
    assert "全部候選 / 平衡 / offset 2.0" not in labels
    assert payload["run_items"][0]["request_payload"]["universe"] == "tw0050"
    assert payload["run_items"][0]["request_payload"]["risk_profile"] == "balanced"
    assert payload["run_items"][0]["request_payload"]["threshold_offset"] == 0.0
    assert payload["exploration_queue"]["candidate_source"] == "exploration"
    assert payload["exploration_queue"]["remaining_count"] > 0
    assert all(item["label"] not in labels for item in payload["exploration_queue"]["top_candidates"])


def test_daily_radar_threshold_optimization_batch_execute_calls_daily_radar_runner():
    class _Repo:
        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001, ARG002
            return _threshold_optimization_backtest_payload()

    calls = []

    def _runner(**kwargs):  # noqa: ANN001
        calls.append(kwargs)
        return {
            "run_id": f"radar_{len(calls)}",
            "trade_date": "2026-07-13",
            "items": 12,
            "top_items": [
                {
                    "rank": 1,
                    "symbol": "2330",
                    "name": "台積電",
                    "industry_group": "半導體業",
                    "action_label": "ENTRY_READY",
                    "entry_score": 89.0,
                    "risk_adjusted_score": 87.0,
                    "reason_tags": ["TREND_OK=True"],
                },
                {
                    "rank": 2,
                    "symbol": "2454",
                    "name": "聯發科",
                    "industry_group": "半導體業",
                    "action_label": "WATCH_WAIT_TRIGGER",
                    "entry_score": 76.0,
                    "risk_adjusted_score": 73.0,
                },
            ],
            "label_counts": {"ENTRY_READY": 3, "WATCH_WAIT_TRIGGER": 4},
            "degraded": False,
            "degrade_reasons": [],
            "export": {},
        }

    payload = run_daily_radar_threshold_optimization_batch(
        _Repo(),
        execute=True,
        max_runs=1,
        runner=_runner,
        trade_date_mode="T-1",
    )

    assert payload["status"] == "ok"
    assert payload["mode"] == "execute"
    assert payload["summary"]["ok_count"] == 1
    assert payload["summary"]["entry_ready_count_total"] == 3
    assert payload["summary"]["top_symbols"] == ["2330", "2454"]
    assert payload["optimization_grid"]["mode"] == "execute"
    assert payload["optimization_grid"]["summary"]["best_pool"]["entry_ready_count"] == 3
    assert payload["optimization_grid"]["summary"]["best_pool"]["actionable_count"] == 7
    assert payload["optimization_grid"]["pool_rows"][0]["top_symbols"] == ["2330", "2454"]
    assert payload["recommended_run"]["decision_label"] == "優先採用"
    assert payload["recommended_run"]["top_symbols"] == ["2330", "2454"]
    assert payload["run_items"][0]["run_id"] == "radar_1"
    assert payload["run_items"][0]["decision_label"] == "優先採用"
    assert payload["run_items"][0]["actionable_count"] == 7
    assert payload["run_items"][0]["top_items"][0]["symbol"] == "2330"
    assert calls[0]["trade_date_mode"] == "T-1"
    assert calls[0]["risk_profile"] == "balanced"
    assert calls[0]["threshold_offset"] == 2.0
    assert calls[0]["universe"] == "research_ready"
    assert calls[0]["symbols"] == ""
    assert calls[0]["threshold_adoption"]["source"] == "threshold_optimization_batch"
    assert calls[0]["threshold_adoption"]["candidate_rank"] == 1
    assert calls[0]["threshold_adoption"]["threshold_offset"] == 2.0


def test_daily_radar_threshold_optimization_batch_can_execute_grid_candidate_source():
    class _Repo:
        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001, ARG002
            return _threshold_optimization_backtest_payload()

    calls = []

    def _runner(**kwargs):  # noqa: ANN001
        calls.append(kwargs)
        return {
            "run_id": "grid_radar_1",
            "trade_date": "2026-07-13",
            "items": 5,
            "top_items": [
                {
                    "rank": 1,
                    "symbol": "2330",
                    "action_label": "ENTRY_READY",
                    "entry_score": 88.0,
                    "risk_adjusted_score": 86.0,
                }
            ],
            "label_counts": {"ENTRY_READY": 1, "WATCH_WAIT_TRIGGER": 1},
            "degraded": False,
        }

    payload = run_daily_radar_threshold_optimization_batch(
        _Repo(),
        execute=True,
        max_runs=1,
        grid_universes=("dynamic_balanced", "tw0050"),
        grid_profiles=("balanced", "conservative"),
        grid_offsets=(-2.0, 0.0),
        candidate_source="grid",
        runner=_runner,
    )

    assert payload["candidate_source"] == "grid"
    assert payload["selected_grid_count"] == 1
    assert payload["candidate_sources"] == {"grid": 1}
    assert payload["source_comparison"]["verdict"] == "grid_only"
    assert payload["source_comparison"]["best_grid"]["candidate_source"] == "grid"
    assert payload["source_comparison"]["primary_action"]["candidate_source"] == "grid"
    assert payload["source_comparison"]["primary_action"]["cli_command"]
    assert "目前只有單一來源候選，不能視為完整來源比較。" in payload["source_comparison"]["guardrails"]
    assert payload["recommended_run"]["candidate_source"] == "grid"
    assert payload["run_items"][0]["candidate_source"] == "grid"
    assert payload["run_items"][0]["status"] == "ok"
    assert calls[0]["universe"] in {"dynamic_balanced", "tw0050"}
    assert calls[0]["threshold_adoption"]["pool_key"].startswith("grid:")
    assert calls[0]["threshold_adoption"]["source"] == "threshold_optimization_batch"


def _threshold_optimization_backtest_payload():
    return {
        "status": "ok",
        "verdict": "門檻有歷史支撐",
        "robustness_score": 76,
        "recommended_config": {"pool_key": "all"},
        "optimization_plan": {
            "status": "ok",
            "candidate_count": 2,
            "run_items": [
                {
                    "rank": 1,
                    "label": "全部候選 / 平衡 / offset 2.0",
                    "pool_key": "all",
                    "readiness": "preferred",
                    "score": 82,
                    "request_payload": {
                        "target_count": 15,
                        "risk_profile": "balanced",
                        "threshold_offset": 2.0,
                        "universe": "research_ready",
                        "symbols_csv": "",
                        "data_freshness_required": True,
                        "include_attention": False,
                        "include_extended_data": False,
                    },
                },
                {
                    "rank": 2,
                    "label": "半導體業 / 保守 / offset 0",
                    "pool_key": "industry:半導體業",
                    "readiness": "watch",
                    "score": 70,
                    "request_payload": {
                        "target_count": 10,
                        "risk_profile": "conservative",
                        "threshold_offset": 0.0,
                        "universe": "custom",
                        "symbols_csv": "2330,2454",
                    },
                },
            ],
        },
    }
