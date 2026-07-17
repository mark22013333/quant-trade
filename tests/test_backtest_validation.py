from __future__ import annotations

from app.backtest.validation import build_backtest_validation_report


def _equity_curve(values: list[float]) -> list[dict]:
    return [{"date": f"2026-01-{(idx % 28) + 1:02d}", "equity": value} for idx, value in enumerate(values)]


def _base_summary() -> dict:
    values = [100_000 + (idx * 120) for idx in range(90)]
    return {
        "metrics": {
            "total_return": 0.11,
            "max_drawdown": -0.04,
            "sharpe": 1.1,
            "cagr": 0.18,
            "win_rate": 0.58,
            "trade_count": 8,
        },
        "equity_curve": _equity_curve(values),
        "cost_breakdown": {
            "buy_fee_total": 400,
            "sell_fee_total": 380,
            "sell_tax_total": 600,
            "slippage_buy_total": 120,
            "slippage_sell_total": 110,
            "gross_buy_value": 500_000,
            "gross_sell_value": 520_000,
        },
        "fill_rejects": [],
        "data_quality_summary": {"complete": 90},
        "execution_model": {"name": "research_t1_open", "config": {"slippage": 0.001}},
        "validation_splits": {
            "status": "ok",
            "method": "chronological_70_30_with_oos_warmup",
            "in_sample": {"total_return": 0.08, "max_drawdown": -0.03, "trade_count": 6},
            "out_of_sample": {"total_return": 0.04, "max_drawdown": -0.02, "trade_count": 2, "fill_reject_count": 0},
            "parameter_sensitivity": {
                "status": "ok",
                "method": "threshold_neighborhood_oos",
                "baseline_threshold": 0.6,
                "step": 0.05,
                "baseline_return": 0.04,
                "worst_return": 0.02,
                "best_return": 0.05,
                "return_range": 0.03,
                "worst_degradation": -0.02,
                "negative_variant_count": 0,
                "zero_trade_variant_count": 0,
                "rows": [
                    {"parameter": "threshold", "value": 0.55, "total_return": 0.02, "trade_count": 2},
                    {"parameter": "threshold", "value": 0.6, "total_return": 0.04, "trade_count": 2},
                    {"parameter": "threshold", "value": 0.65, "total_return": 0.05, "trade_count": 1},
                ],
            },
            "walk_forward": {
                "status": "ok",
                "method": "rolling_walk_forward_fixed_parameters",
                "window_count": 3,
                "positive_window_count": 3,
                "positive_window_ratio": 1.0,
                "negative_window_count": 0,
                "zero_trade_window_count": 0,
                "tradable_window_ratio": 1.0,
                "worst_return": 0.01,
                "best_return": 0.04,
                "average_return": 0.025,
                "median_return": 0.025,
                "windows": [
                    {"window": 1, "total_return": 0.02, "trade_count": 1},
                    {"window": 2, "total_return": 0.01, "trade_count": 2},
                    {"window": 3, "total_return": 0.04, "trade_count": 1},
                ],
            },
            "cross_symbol_sensitivity": {
                "status": "ok",
                "method": "representative_symbol_basket",
                "baseline_symbol": "2330.TW",
                "baseline_return": 0.11,
                "symbol_count": 3,
                "group_count": 3,
                "group_summary": [
                    {"group": "水泥/建材", "symbol_count": 1, "average_return": 0.01},
                    {"group": "電子/科技", "symbol_count": 1, "average_return": 0.03},
                    {"group": "金融/保險", "symbol_count": 1, "average_return": 0.08},
                ],
                "group_stress": {
                    "status": "ok",
                    "method": "industry_group_return_stress",
                    "group_count": 3,
                    "context_group_count": 3,
                    "resilient_group_count": 3,
                    "mixed_group_count": 0,
                    "fragile_group_count": 0,
                    "fragile_group_ratio": 0.0,
                    "underperform_group_count": 0,
                    "worst_group": {"group": "水泥/建材", "label": "resilient", "worst_return": 0.01},
                    "groups": [
                        {"group": "水泥/建材", "label": "resilient", "average_return": 0.01, "worst_return": 0.01},
                        {"group": "電子/科技", "label": "resilient", "average_return": 0.03, "worst_return": 0.03},
                        {"group": "金融/保險", "label": "resilient", "average_return": 0.08, "worst_return": 0.08},
                    ],
                },
                "skipped_count": 0,
                "positive_symbol_count": 3,
                "positive_symbol_ratio": 1.0,
                "negative_symbol_count": 0,
                "zero_trade_symbol_count": 0,
                "tradable_symbol_ratio": 1.0,
                "worst_return": 0.01,
                "best_return": 0.08,
                "average_return": 0.04,
                "median_return": 0.03,
                "worst_degradation_vs_baseline": -0.10,
                "rows": [
                    {"symbol": "2317.TW", "status": "ok", "total_return": 0.01, "trade_count": 1},
                    {"symbol": "2454.TW", "status": "ok", "total_return": 0.03, "trade_count": 2},
                    {"symbol": "2881.TW", "status": "ok", "total_return": 0.08, "trade_count": 1},
                ],
            },
        },
    }


def test_backtest_validation_report_marks_clean_backtest_paper_ready() -> None:
    report = build_backtest_validation_report(_base_summary())

    assert report["readiness"] == "paper_ready"
    assert report["paper_candidate"] is True
    assert report["blockers"] == []
    assert report["warnings"] == []
    assert {check["code"] for check in report["checks"]} >= {
        "trade_sample",
        "return_profile",
        "drawdown_guard",
        "oos_split",
        "threshold_sensitivity",
        "walk_forward",
        "cross_symbol_sensitivity",
        "cross_group_stress",
        "oos_proxy",
        "cost_drag",
        "fill_reject_ratio",
        "data_quality",
        "execution_model",
    }


def test_backtest_validation_report_blocks_unstable_backtest() -> None:
    summary = _base_summary()
    summary["metrics"].update({"total_return": -0.08, "max_drawdown": -0.32, "sharpe": -0.4, "trade_count": 0})
    summary["equity_curve"] = _equity_curve([100_000 - (idx * 220) for idx in range(90)])
    summary["fill_rejects"] = [{"reason": "entry_gap_exceed"} for _ in range(7)]
    summary["data_quality_summary"] = {"complete": 60, "missing": 30}
    summary["validation_splits"]["out_of_sample"] = {
        "total_return": -0.12,
        "max_drawdown": -0.18,
        "trade_count": 1,
        "fill_reject_count": 4,
    }
    summary["validation_splits"]["parameter_sensitivity"].update(
        {
            "worst_return": -0.14,
            "best_return": 0.01,
            "return_range": 0.15,
            "worst_degradation": -0.18,
            "negative_variant_count": 2,
            "zero_trade_variant_count": 0,
        }
    )
    summary["validation_splits"]["walk_forward"].update(
        {
            "positive_window_count": 0,
            "positive_window_ratio": 0.0,
            "negative_window_count": 3,
            "zero_trade_window_count": 1,
            "tradable_window_ratio": 0.67,
            "worst_return": -0.13,
            "best_return": -0.01,
            "average_return": -0.06,
            "median_return": -0.04,
        }
    )
    summary["validation_splits"]["cross_symbol_sensitivity"].update(
        {
            "positive_symbol_count": 0,
            "group_count": 3,
            "positive_symbol_ratio": 0.0,
            "negative_symbol_count": 3,
            "zero_trade_symbol_count": 1,
            "tradable_symbol_ratio": 0.67,
            "worst_return": -0.11,
            "best_return": -0.02,
            "average_return": -0.07,
            "median_return": -0.08,
            "worst_degradation_vs_baseline": -0.03,
            "group_stress": {
                "status": "ok",
                "method": "industry_group_return_stress",
                "group_count": 3,
                "context_group_count": 3,
                "resilient_group_count": 0,
                "mixed_group_count": 0,
                "fragile_group_count": 3,
                "fragile_group_ratio": 1.0,
                "underperform_group_count": 2,
                "worst_group": {"group": "電子/科技", "label": "fragile", "worst_return": -0.11},
                "groups": [
                    {"group": "水泥/建材", "label": "fragile", "average_return": -0.05, "worst_return": -0.05},
                    {"group": "電子/科技", "label": "fragile", "average_return": -0.11, "worst_return": -0.11},
                    {"group": "金融/保險", "label": "fragile", "average_return": -0.04, "worst_return": -0.04},
                ],
            },
        }
    )

    report = build_backtest_validation_report(summary)

    assert report["readiness"] == "blocked"
    assert report["paper_candidate"] is False
    assert {
        "trade_sample",
        "return_profile",
        "drawdown_guard",
        "oos_split",
        "threshold_sensitivity",
        "walk_forward",
        "cross_symbol_sensitivity",
        "cross_group_stress",
        "oos_proxy",
        "fill_reject_ratio",
        "data_quality",
    } <= set(report["blockers"])
