from __future__ import annotations

from typing import Any


def build_backtest_validation_report(
    summary: dict[str, Any],
    *,
    min_trade_count: int = 5,
    warn_drawdown: float = -0.15,
    fail_drawdown: float = -0.25,
    min_sharpe: float = 0.50,
    warn_cost_ratio: float = 0.015,
    fail_cost_ratio: float = 0.030,
    warn_fill_reject_ratio: float = 0.20,
    fail_fill_reject_ratio: float = 0.50,
) -> dict[str, Any]:
    """Build a practical research gate for deciding whether a backtest can enter paper."""

    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    cost_breakdown = summary.get("cost_breakdown") if isinstance(summary.get("cost_breakdown"), dict) else {}
    data_quality = summary.get("data_quality_summary") if isinstance(summary.get("data_quality_summary"), dict) else {}
    fill_rejects = summary.get("fill_rejects") if isinstance(summary.get("fill_rejects"), list) else []
    equity_curve = summary.get("equity_curve") if isinstance(summary.get("equity_curve"), list) else []
    execution_model = summary.get("execution_model") if isinstance(summary.get("execution_model"), dict) else {}
    validation_splits = summary.get("validation_splits") if isinstance(summary.get("validation_splits"), dict) else {}

    trade_count = int(_number(metrics.get("trade_count"), 0))
    total_return = _number(metrics.get("total_return"), 0.0)
    max_drawdown = _number(metrics.get("max_drawdown"), 0.0)
    sharpe = _number(metrics.get("sharpe"), 0.0)
    cost_ratio = _cost_ratio(cost_breakdown)
    fill_reject_ratio = _fill_reject_ratio(fill_rejects, trade_count)
    data_quality_bad_ratio = _data_quality_bad_ratio(data_quality)
    oos_proxy = _equity_last_third_proxy(equity_curve)
    oos_split = _oos_split_metrics(validation_splits)
    sensitivity = _parameter_sensitivity_metrics(validation_splits)
    walk_forward = _walk_forward_metrics(validation_splits)
    cross_symbol = _cross_symbol_sensitivity_metrics(validation_splits)
    group_stress = _cross_group_stress_metrics(validation_splits)

    checks: list[dict[str, Any]] = []

    if trade_count == 0:
        _add_check(checks, "trade_sample", "fail", "交易樣本", "期間沒有完成交易，不能判斷策略穩定度。", trade_count, f">= {min_trade_count}")
    elif trade_count < min_trade_count:
        _add_check(checks, "trade_sample", "warn", "交易樣本", "交易筆數偏少，建議延長期間或降低門檻再驗證。", trade_count, f">= {min_trade_count}")
    else:
        _add_check(checks, "trade_sample", "pass", "交易樣本", "交易筆數足以做第一層研究判讀。", trade_count, f">= {min_trade_count}")

    if total_return < -0.02:
        _add_check(checks, "return_profile", "fail", "報酬輪廓", "總報酬明顯為負，不建議進 paper。", total_return, "> -2%")
    elif total_return <= 0:
        _add_check(checks, "return_profile", "warn", "報酬輪廓", "總報酬未轉正，需先檢查進出場與成本設定。", total_return, "> 0%")
    else:
        _add_check(checks, "return_profile", "pass", "報酬輪廓", "總報酬為正。", total_return, "> 0%")

    if max_drawdown <= fail_drawdown:
        _add_check(checks, "drawdown_guard", "fail", "最大回撤", "回撤過深，策略目前不適合升級。", max_drawdown, f"> {fail_drawdown:.0%}")
    elif max_drawdown <= warn_drawdown:
        _add_check(checks, "drawdown_guard", "warn", "最大回撤", "回撤偏大，paper 前應先降風險或調整停損。", max_drawdown, f"> {warn_drawdown:.0%}")
    else:
        _add_check(checks, "drawdown_guard", "pass", "最大回撤", "回撤落在第一層容許範圍內。", max_drawdown, f"> {warn_drawdown:.0%}")

    if trade_count < 2:
        _add_check(checks, "sharpe_floor", "warn", "Sharpe", "交易樣本不足，Sharpe 只能參考。", sharpe, f">= {min_sharpe}")
    elif sharpe < 0:
        _add_check(checks, "sharpe_floor", "fail", "Sharpe", "風險調整後報酬為負。", sharpe, f">= {min_sharpe}")
    elif sharpe < min_sharpe:
        _add_check(checks, "sharpe_floor", "warn", "Sharpe", "風險調整後報酬偏弱。", sharpe, f">= {min_sharpe}")
    else:
        _add_check(checks, "sharpe_floor", "pass", "Sharpe", "風險調整後報酬達標。", sharpe, f">= {min_sharpe}")

    if oos_split["status"] != "ok":
        _add_check(
            checks,
            "oos_split",
            "warn",
            "OOS 分段驗證",
            "資料不足或尚未產生正式 OOS 分段結果，暫以後段穩定度 proxy 輔助判讀。",
            oos_split.get("reason"),
            "in-sample>=80 and OOS>=20",
        )
    elif int(oos_split["trade_count"]) == 0:
        _add_check(checks, "oos_split", "warn", "OOS 分段驗證", "OOS 期間沒有完成交易，尚不能證明策略可升級。", 0, "> 0 trades")
    elif float(oos_split["total_return"]) < -0.05:
        _add_check(checks, "oos_split", "fail", "OOS 分段驗證", "OOS 分段報酬明顯為負，策略可能只適合樣本內。", oos_split["total_return"], ">= -5%")
    elif float(oos_split["total_return"]) < 0:
        _add_check(checks, "oos_split", "warn", "OOS 分段驗證", "OOS 分段小幅為負，paper 前需做 walk-forward。", oos_split["total_return"], ">= 0%")
    else:
        _add_check(checks, "oos_split", "pass", "OOS 分段驗證", "OOS 分段報酬為正。", oos_split["total_return"], ">= 0%")

    if sensitivity["status"] != "ok":
        _add_check(
            checks,
            "threshold_sensitivity",
            "warn",
            "門檻敏感度",
            "尚未產生 threshold 鄰近值敏感度掃描。",
            sensitivity.get("reason"),
            "至少 3 組 threshold",
        )
    elif int(sensitivity["zero_trade_variant_count"]) >= 2:
        _add_check(checks, "threshold_sensitivity", "warn", "門檻敏感度", "多數鄰近門檻在 OOS 無交易，策略訊號可能太稀疏。", sensitivity["zero_trade_variant_count"], "< 2")
    elif float(sensitivity["worst_return"]) < -0.05:
        _add_check(checks, "threshold_sensitivity", "fail", "門檻敏感度", "鄰近門檻 OOS 報酬明顯轉負，策略對參數過度敏感。", sensitivity["worst_return"], ">= -5%")
    elif int(sensitivity["negative_variant_count"]) > 0 or float(sensitivity["return_range"]) > 0.12:
        _add_check(checks, "threshold_sensitivity", "warn", "門檻敏感度", "鄰近門檻表現差異偏大，paper 前需做 walk-forward。", sensitivity["return_range"], "<= 12%")
    else:
        _add_check(checks, "threshold_sensitivity", "pass", "門檻敏感度", "鄰近門檻 OOS 表現未出現明顯崩落。", sensitivity["return_range"], "<= 12%")

    if walk_forward["status"] != "ok":
        _add_check(
            checks,
            "walk_forward",
            "warn",
            "Walk-forward",
            "尚未產生滾動視窗驗證，paper 前仍需確認跨時段穩定度。",
            walk_forward.get("reason"),
            ">= 2 windows",
        )
    elif int(walk_forward["window_count"]) < 2:
        _add_check(checks, "walk_forward", "warn", "Walk-forward", "滾動視窗數量不足，尚不能判斷跨時段穩定度。", walk_forward["window_count"], ">= 2 windows")
    elif float(walk_forward["worst_return"]) < -0.08:
        _add_check(checks, "walk_forward", "fail", "Walk-forward", "至少一個滾動視窗報酬明顯轉負，策略可能對時段過度敏感。", walk_forward["worst_return"], ">= -8%")
    elif float(walk_forward["positive_window_ratio"]) < 0.50:
        _add_check(checks, "walk_forward", "fail", "Walk-forward", "多數滾動視窗未能維持正報酬。", walk_forward["positive_window_ratio"], ">= 50%")
    elif float(walk_forward["tradable_window_ratio"]) < 0.50:
        _add_check(checks, "walk_forward", "warn", "Walk-forward", "多數滾動視窗交易數過少，訊號可能不夠穩定。", walk_forward["tradable_window_ratio"], ">= 50%")
    elif float(walk_forward["worst_return"]) < 0:
        _add_check(checks, "walk_forward", "warn", "Walk-forward", "滾動視窗中仍有小幅負報酬，paper 前需觀察情境差異。", walk_forward["worst_return"], ">= 0%")
    else:
        _add_check(checks, "walk_forward", "pass", "Walk-forward", "滾動視窗多數維持正報酬，跨時段穩定度通過第一層檢查。", walk_forward["positive_window_ratio"], ">= 50%")

    if cross_symbol["status"] != "ok":
        _add_check(
            checks,
            "cross_symbol_sensitivity",
            "warn",
            "跨標的敏感度",
            "尚未產生足夠的代表標的敏感度結果，paper 前仍需確認策略不是只適用單一股票。",
            cross_symbol.get("reason"),
            ">= 2 peer symbols",
        )
    elif int(cross_symbol["symbol_count"]) < 2:
        _add_check(checks, "cross_symbol_sensitivity", "warn", "跨標的敏感度", "可用代表標的太少，尚不能判斷跨標的穩定度。", cross_symbol["symbol_count"], ">= 2 peer symbols")
    elif float(cross_symbol["worst_return"]) < -0.08:
        _add_check(checks, "cross_symbol_sensitivity", "fail", "跨標的敏感度", "代表標的中出現明顯負報酬，策略可能只適用少數標的。", cross_symbol["worst_return"], ">= -8%")
    elif float(cross_symbol["positive_symbol_ratio"]) < 0.50:
        _add_check(checks, "cross_symbol_sensitivity", "fail", "跨標的敏感度", "多數代表標的未能維持正報酬。", cross_symbol["positive_symbol_ratio"], ">= 50%")
    elif float(cross_symbol["tradable_symbol_ratio"]) < 0.50:
        _add_check(checks, "cross_symbol_sensitivity", "warn", "跨標的敏感度", "多數代表標的交易數過少，策略可複用性仍需觀察。", cross_symbol["tradable_symbol_ratio"], ">= 50%")
    elif int(cross_symbol["group_count"]) < 2:
        _add_check(checks, "cross_symbol_sensitivity", "warn", "跨標的敏感度", "代表標的通過基本檢查，但族群覆蓋不足，仍需補跨族群驗證。", cross_symbol["group_count"], ">= 2 groups")
    elif float(cross_symbol["worst_return"]) < 0:
        _add_check(checks, "cross_symbol_sensitivity", "warn", "跨標的敏感度", "代表標的中仍有小幅負報酬，paper 前建議擴大觀察池檢查。", cross_symbol["worst_return"], ">= 0%")
    else:
        _add_check(checks, "cross_symbol_sensitivity", "pass", "跨標的敏感度", "代表標的籃子多數維持正報酬，通過第一層跨標的檢查。", cross_symbol["positive_symbol_ratio"], ">= 50%")

    if group_stress["status"] != "ok":
        _add_check(
            checks,
            "cross_group_stress",
            "warn",
            "跨族群壓力",
            "尚未產生跨族群壓力摘要，無法判斷策略在不同產業情境下的穩定度。",
            group_stress.get("reason"),
            ">= 2 groups",
        )
    elif int(group_stress["group_count"]) < 2:
        _add_check(checks, "cross_group_stress", "warn", "跨族群壓力", "族群數不足，尚不能做跨族群壓力判讀。", group_stress["group_count"], ">= 2 groups")
    elif float(group_stress["fragile_group_ratio"]) >= 0.50:
        _add_check(checks, "cross_group_stress", "fail", "跨族群壓力", "半數以上族群屬於脆弱情境，策略跨族群穩定度不足。", group_stress["fragile_group_ratio"], "< 50% fragile")
    elif int(group_stress["fragile_group_count"]) > 0:
        _add_check(checks, "cross_group_stress", "warn", "跨族群壓力", "存在脆弱族群，paper 前需確認是否要限制適用產業。", group_stress["fragile_group_count"], "0 fragile groups")
    elif int(group_stress["context_group_count"]) < max(1, int(group_stress["group_count"]) // 2):
        _add_check(checks, "cross_group_stress", "warn", "跨族群壓力", "可用的本機族群背景不足，尚無法判斷策略是否只是受惠於族群順風。", group_stress["context_group_count"], ">= half groups")
    elif int(group_stress["underperform_group_count"]) > 0:
        _add_check(checks, "cross_group_stress", "warn", "跨族群壓力", "部分族群策略報酬明顯落後本機族群等權背景，需檢查訊號是否失效。", group_stress["underperform_group_count"], "0 underperform groups")
    elif int(group_stress["mixed_group_count"]) > 0:
        _add_check(checks, "cross_group_stress", "warn", "跨族群壓力", "部分族群表現混合，建議觀察族群輪動情境。", group_stress["mixed_group_count"], "0 mixed groups")
    else:
        _add_check(checks, "cross_group_stress", "pass", "跨族群壓力", "跨族群壓力摘要未發現脆弱族群。", group_stress["resilient_group_count"], "all resilient")

    if oos_proxy["status"] == "insufficient":
        _add_check(checks, "oos_proxy", "warn", "後段穩定度", "權益曲線資料不足，尚不能做後段穩定度檢查。", oos_proxy["return"], ">= 0%")
    elif oos_proxy["return"] < -0.05:
        _add_check(checks, "oos_proxy", "fail", "後段穩定度", "權益曲線後段明顯轉弱，需做正式 OOS / walk-forward。", oos_proxy["return"], ">= -5%")
    elif oos_proxy["return"] < 0:
        _add_check(checks, "oos_proxy", "warn", "後段穩定度", "權益曲線後段小幅轉弱，paper 前需再驗證。", oos_proxy["return"], ">= 0%")
    else:
        _add_check(checks, "oos_proxy", "pass", "後段穩定度", "權益曲線後段仍為正報酬。", oos_proxy["return"], ">= 0%")

    if cost_ratio >= fail_cost_ratio:
        _add_check(checks, "cost_drag", "fail", "交易成本占比", "成本占成交額偏高，績效可能被交易摩擦吃掉。", cost_ratio, f"< {fail_cost_ratio:.1%}")
    elif cost_ratio >= warn_cost_ratio:
        _add_check(checks, "cost_drag", "warn", "交易成本占比", "成本占比偏高，需確認交易頻率與滑價假設。", cost_ratio, f"< {warn_cost_ratio:.1%}")
    else:
        _add_check(checks, "cost_drag", "pass", "交易成本占比", "交易成本落在可接受範圍。", cost_ratio, f"< {warn_cost_ratio:.1%}")

    if fill_reject_ratio >= fail_fill_reject_ratio:
        _add_check(checks, "fill_reject_ratio", "fail", "成交拒絕率", "成交拒絕率過高，回測成交假設不可靠。", fill_reject_ratio, f"< {fail_fill_reject_ratio:.0%}")
    elif fill_reject_ratio >= warn_fill_reject_ratio:
        _add_check(checks, "fill_reject_ratio", "warn", "成交拒絕率", "成交拒絕偏多，需提高流動性門檻或放寬進場時機。", fill_reject_ratio, f"< {warn_fill_reject_ratio:.0%}")
    else:
        _add_check(checks, "fill_reject_ratio", "pass", "成交拒絕率", "成交拒絕率落在可接受範圍。", fill_reject_ratio, f"< {warn_fill_reject_ratio:.0%}")

    if not data_quality:
        _add_check(checks, "data_quality", "warn", "資料品質", "回測尚未回報資料品質摘要。", None, "需有資料品質摘要")
    elif data_quality_bad_ratio > 0.20:
        _add_check(checks, "data_quality", "fail", "資料品質", "缺漏或降級資料占比偏高。", data_quality_bad_ratio, "<= 20%")
    elif data_quality_bad_ratio > 0:
        _add_check(checks, "data_quality", "warn", "資料品質", "存在少量缺漏或降級資料。", data_quality_bad_ratio, "0%")
    else:
        _add_check(checks, "data_quality", "pass", "資料品質", "未發現明顯缺漏或降級標籤。", data_quality_bad_ratio, "0%")

    if execution_model.get("name") and execution_model.get("config"):
        _add_check(checks, "execution_model", "pass", "成交模型", "已記錄成交、成本、滑價與漲跌停假設。", execution_model.get("name"), "required")
    else:
        _add_check(checks, "execution_model", "warn", "成交模型", "缺少成交模型設定，難以比較 backtest / paper / live。", None, "required")

    failures = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    if failures:
        readiness = "blocked"
        recommendation = "暫不建議進 paper，請先處理阻擋項目。"
    elif warnings:
        readiness = "needs_review"
        recommendation = "可列入研究觀察，但進 paper 前應補齊警示項目。"
    else:
        readiness = "paper_ready"
        recommendation = "可進入 paper 觀察期，但仍需維持小額與固定停用條件。"

    return {
        "schema_version": "2026-06-25.backtest-validation.v1",
        "readiness": readiness,
        "paper_candidate": readiness == "paper_ready",
        "recommendation": recommendation,
        "checks": checks,
        "blockers": [item["code"] for item in failures],
        "warnings": [item["code"] for item in warnings],
        "metrics": {
            "trade_count": trade_count,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "sharpe": sharpe,
            "cost_ratio": cost_ratio,
            "fill_reject_ratio": fill_reject_ratio,
            "data_quality_bad_ratio": data_quality_bad_ratio,
            "oos_split_return": oos_split.get("total_return"),
            "oos_split_trade_count": oos_split.get("trade_count"),
            "oos_split_max_drawdown": oos_split.get("max_drawdown"),
            "threshold_sensitivity_return_range": sensitivity.get("return_range"),
            "threshold_sensitivity_worst_return": sensitivity.get("worst_return"),
            "threshold_sensitivity_negative_variant_count": sensitivity.get("negative_variant_count"),
            "walk_forward_window_count": walk_forward.get("window_count"),
            "walk_forward_positive_window_ratio": walk_forward.get("positive_window_ratio"),
            "walk_forward_tradable_window_ratio": walk_forward.get("tradable_window_ratio"),
            "walk_forward_worst_return": walk_forward.get("worst_return"),
            "walk_forward_average_return": walk_forward.get("average_return"),
            "cross_symbol_symbol_count": cross_symbol.get("symbol_count"),
            "cross_symbol_group_count": cross_symbol.get("group_count"),
            "cross_symbol_positive_symbol_ratio": cross_symbol.get("positive_symbol_ratio"),
            "cross_symbol_tradable_symbol_ratio": cross_symbol.get("tradable_symbol_ratio"),
            "cross_symbol_worst_return": cross_symbol.get("worst_return"),
            "cross_symbol_average_return": cross_symbol.get("average_return"),
            "cross_group_stress_group_count": group_stress.get("group_count"),
            "cross_group_stress_context_group_count": group_stress.get("context_group_count"),
            "cross_group_stress_fragile_group_count": group_stress.get("fragile_group_count"),
            "cross_group_stress_fragile_group_ratio": group_stress.get("fragile_group_ratio"),
            "cross_group_stress_mixed_group_count": group_stress.get("mixed_group_count"),
            "cross_group_stress_underperform_group_count": group_stress.get("underperform_group_count"),
            "oos_proxy_return": oos_proxy["return"],
            "oos_proxy_max_drawdown": oos_proxy["max_drawdown"],
        },
        "next_validation": _next_validation_steps(oos_split, sensitivity, walk_forward, cross_symbol, group_stress),
    }


def _add_check(
    checks: list[dict[str, Any]],
    code: str,
    status: str,
    label: str,
    detail: str,
    value: Any,
    threshold: str,
) -> None:
    checks.append(
        {
            "code": code,
            "status": status,
            "label": label,
            "detail": detail,
            "value": value,
            "threshold": threshold,
        }
    )


def _number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out or out in {float("inf"), float("-inf")}:
        return default
    return out


def _cost_ratio(cost_breakdown: dict[str, Any]) -> float:
    total_cost = sum(
        _number(cost_breakdown.get(key), 0.0)
        for key in ("buy_fee_total", "sell_fee_total", "sell_tax_total", "slippage_buy_total", "slippage_sell_total")
    )
    turnover = sum(_number(cost_breakdown.get(key), 0.0) for key in ("gross_buy_value", "gross_sell_value"))
    return total_cost / turnover if turnover > 0 else 0.0


def _fill_reject_ratio(fill_rejects: list[dict[str, Any]], trade_count: int) -> float:
    rejects = len(fill_rejects)
    opportunities = rejects + max(int(trade_count), 0)
    return rejects / opportunities if opportunities > 0 else 0.0


def _data_quality_bad_ratio(data_quality: dict[str, Any]) -> float:
    if not data_quality:
        return 0.0
    bad_terms = ("missing", "stale", "degraded", "fallback", "unknown", "nan", "none")
    total = sum(max(_number(value), 0.0) for value in data_quality.values())
    if total <= 0:
        return 0.0
    bad = 0.0
    for key, value in data_quality.items():
        normalized = str(key).strip().lower()
        if any(term in normalized for term in bad_terms):
            bad += max(_number(value), 0.0)
    return bad / total


def _equity_last_third_proxy(equity_curve: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_number(item.get("equity"), None) for item in equity_curve if isinstance(item, dict)]
    values = [value for value in values if value is not None and value > 0]
    if len(values) < 60:
        return {"status": "insufficient", "return": None, "max_drawdown": None}
    start_idx = max(int(len(values) * (2 / 3)) - 1, 0)
    segment = values[start_idx:]
    segment_return = (segment[-1] / segment[0]) - 1 if segment[0] > 0 else 0.0
    peak = segment[0]
    max_drawdown = 0.0
    for value in segment:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, (value - peak) / peak)
    return {"status": "ok", "return": float(segment_return), "max_drawdown": float(max_drawdown)}


def _oos_split_metrics(validation_splits: dict[str, Any]) -> dict[str, Any]:
    if not validation_splits:
        return {"status": "missing", "reason": "validation_splits_missing"}
    if validation_splits.get("status") != "ok":
        return {"status": "insufficient", "reason": validation_splits.get("reason") or "oos_split_unavailable"}
    oos = validation_splits.get("out_of_sample") if isinstance(validation_splits.get("out_of_sample"), dict) else {}
    return {
        "status": "ok",
        "total_return": _number(oos.get("total_return"), 0.0),
        "max_drawdown": _number(oos.get("max_drawdown"), 0.0),
        "cagr": _number(oos.get("cagr"), 0.0),
        "trade_count": int(_number(oos.get("trade_count"), 0)),
        "fill_reject_count": int(_number(oos.get("fill_reject_count"), 0)),
    }


def _parameter_sensitivity_metrics(validation_splits: dict[str, Any]) -> dict[str, Any]:
    if not validation_splits:
        return {"status": "missing", "reason": "validation_splits_missing"}
    sensitivity = validation_splits.get("parameter_sensitivity")
    if not isinstance(sensitivity, dict):
        return {"status": "missing", "reason": "parameter_sensitivity_missing"}
    if sensitivity.get("status") != "ok":
        return {"status": "insufficient", "reason": sensitivity.get("reason") or "parameter_sensitivity_unavailable"}
    return {
        "status": "ok",
        "baseline_return": _number(sensitivity.get("baseline_return"), 0.0),
        "worst_return": _number(sensitivity.get("worst_return"), 0.0),
        "best_return": _number(sensitivity.get("best_return"), 0.0),
        "return_range": _number(sensitivity.get("return_range"), 0.0),
        "worst_degradation": _number(sensitivity.get("worst_degradation"), 0.0),
        "negative_variant_count": int(_number(sensitivity.get("negative_variant_count"), 0)),
        "zero_trade_variant_count": int(_number(sensitivity.get("zero_trade_variant_count"), 0)),
    }


def _walk_forward_metrics(validation_splits: dict[str, Any]) -> dict[str, Any]:
    if not validation_splits:
        return {"status": "missing", "reason": "validation_splits_missing"}
    walk_forward = validation_splits.get("walk_forward")
    if not isinstance(walk_forward, dict):
        return {"status": "missing", "reason": "walk_forward_missing"}
    if walk_forward.get("status") != "ok":
        return {"status": "insufficient", "reason": walk_forward.get("reason") or "walk_forward_unavailable"}
    return {
        "status": "ok",
        "window_count": int(_number(walk_forward.get("window_count"), 0)),
        "positive_window_count": int(_number(walk_forward.get("positive_window_count"), 0)),
        "positive_window_ratio": _number(walk_forward.get("positive_window_ratio"), 0.0),
        "negative_window_count": int(_number(walk_forward.get("negative_window_count"), 0)),
        "zero_trade_window_count": int(_number(walk_forward.get("zero_trade_window_count"), 0)),
        "tradable_window_ratio": _number(walk_forward.get("tradable_window_ratio"), 0.0),
        "worst_return": _number(walk_forward.get("worst_return"), 0.0),
        "best_return": _number(walk_forward.get("best_return"), 0.0),
        "average_return": _number(walk_forward.get("average_return"), 0.0),
        "median_return": _number(walk_forward.get("median_return"), 0.0),
    }


def _cross_symbol_sensitivity_metrics(validation_splits: dict[str, Any]) -> dict[str, Any]:
    if not validation_splits:
        return {"status": "missing", "reason": "validation_splits_missing"}
    sensitivity = validation_splits.get("cross_symbol_sensitivity")
    if not isinstance(sensitivity, dict):
        return {"status": "missing", "reason": "cross_symbol_sensitivity_missing"}
    if sensitivity.get("status") != "ok":
        return {"status": "insufficient", "reason": sensitivity.get("reason") or "cross_symbol_sensitivity_unavailable"}
    return {
        "status": "ok",
        "symbol_count": int(_number(sensitivity.get("symbol_count"), 0)),
        "group_count": int(_number(sensitivity.get("group_count"), 0)),
        "positive_symbol_count": int(_number(sensitivity.get("positive_symbol_count"), 0)),
        "positive_symbol_ratio": _number(sensitivity.get("positive_symbol_ratio"), 0.0),
        "negative_symbol_count": int(_number(sensitivity.get("negative_symbol_count"), 0)),
        "zero_trade_symbol_count": int(_number(sensitivity.get("zero_trade_symbol_count"), 0)),
        "tradable_symbol_ratio": _number(sensitivity.get("tradable_symbol_ratio"), 0.0),
        "worst_return": _number(sensitivity.get("worst_return"), 0.0),
        "best_return": _number(sensitivity.get("best_return"), 0.0),
        "average_return": _number(sensitivity.get("average_return"), 0.0),
        "median_return": _number(sensitivity.get("median_return"), 0.0),
        "worst_degradation_vs_baseline": _number(sensitivity.get("worst_degradation_vs_baseline"), 0.0),
    }


def _cross_group_stress_metrics(validation_splits: dict[str, Any]) -> dict[str, Any]:
    if not validation_splits:
        return {"status": "missing", "reason": "validation_splits_missing"}
    sensitivity = validation_splits.get("cross_symbol_sensitivity")
    if not isinstance(sensitivity, dict):
        return {"status": "missing", "reason": "cross_symbol_sensitivity_missing"}
    stress = sensitivity.get("group_stress")
    if not isinstance(stress, dict):
        return {"status": "missing", "reason": "group_stress_missing"}
    if stress.get("status") != "ok":
        return {"status": "insufficient", "reason": stress.get("reason") or "group_stress_unavailable"}
    return {
        "status": "ok",
        "group_count": int(_number(stress.get("group_count"), 0)),
        "context_group_count": int(_number(stress.get("context_group_count"), 0)),
        "resilient_group_count": int(_number(stress.get("resilient_group_count"), 0)),
        "mixed_group_count": int(_number(stress.get("mixed_group_count"), 0)),
        "fragile_group_count": int(_number(stress.get("fragile_group_count"), 0)),
        "fragile_group_ratio": _number(stress.get("fragile_group_ratio"), 0.0),
        "underperform_group_count": int(_number(stress.get("underperform_group_count"), 0)),
    }


def _next_validation_steps(
    oos_split: dict[str, Any],
    sensitivity: dict[str, Any],
    walk_forward: dict[str, Any],
    cross_symbol: dict[str, Any],
    group_stress: dict[str, Any],
) -> list[str]:
    steps = [
        "paper 觀察期成交差異追蹤",
    ]
    if oos_split.get("status") != "ok":
        return ["正式 OOS 切分", "walk-forward 滾動視窗驗證", "跨標的/跨族群敏感度", *steps]
    if sensitivity.get("status") != "ok":
        return ["threshold 參數敏感度掃描", "walk-forward 滾動視窗驗證", "跨標的/跨族群敏感度", *steps]
    if walk_forward.get("status") != "ok":
        return ["walk-forward 滾動視窗驗證", "跨標的/跨族群敏感度", *steps]
    if cross_symbol.get("status") != "ok":
        return ["跨標的/跨族群敏感度", *steps]
    if group_stress.get("status") != "ok":
        return ["跨族群壓力摘要", *steps]
    return steps
