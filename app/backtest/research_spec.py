from __future__ import annotations

import hashlib
import json
from typing import Any


def build_strategy_research_spec(
    config: Any,
    *,
    start_date: str,
    end_date: str,
    data_rows: int,
    validation_splits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a portable research spec for a multi-strategy backtest run."""

    enabled = dict(getattr(config, "enabled", {}) or {})
    weights = dict(getattr(config, "weights", {}) or {})
    threshold = float(getattr(config, "threshold", 0.0) or 0.0)
    strategy_params = dict(getattr(config, "strategy_params", {}) or {})
    risk_config = dict(getattr(config, "risk_config", {}) or {})
    backtest_config = dict(getattr(config, "backtest_config", {}) or {})
    validation_config = dict(getattr(config, "validation_config", {}) or {})
    symbol = str(getattr(config, "symbol", "") or "").strip()
    market = str(getattr(config, "market", "") or "").strip().upper()
    splits = validation_splits if isinstance(validation_splits, dict) else {}
    active_strategies = [name for name, active in enabled.items() if active]

    spec_core = {
        "symbol": symbol,
        "market": market,
        "start_date": start_date,
        "end_date": end_date,
        "enabled": enabled,
        "weights": weights,
        "threshold": threshold,
        "strategy_params": strategy_params,
        "risk_config": risk_config,
        "backtest_config": backtest_config,
        "validation_config": validation_config,
    }
    digest = hashlib.sha1(json.dumps(spec_core, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:12]

    return {
        "schema_version": "2026-06-25.strategy-research-spec.v1",
        "spec_id": f"{market or 'MKT'}:{symbol or 'SYMBOL'}:{start_date}:{end_date}:{digest}",
        "status": "research_only",
        "hypothesis": _hypothesis(active_strategies),
        "universe": {
            "market": market,
            "symbol": symbol,
            "scope": "single_symbol_backtest",
            "future_extension": "cross_symbol_and_cross_industry_sensitivity",
        },
        "data_requirements": {
            "bar_frequency": "daily",
            "required_rows": 80,
            "actual_rows": int(data_rows or 0),
            "required_fields": ["Open", "High", "Low", "Close", "Volume"],
            "optional_fields": ["Foreign_Net_Buy", "InvestmentTrust_Net_Buy", "Dealer_Net_Buy", "Chip_Concentration_Proxy"],
        },
        "strategy": {
            "name": "multi_strategy_tw_swing",
            "active_strategies": active_strategies,
            "enabled": enabled,
            "weights": weights,
            "threshold": threshold,
            "params": strategy_params,
        },
        "risk": {
            "config": risk_config,
            "promotion_stop_conditions": [
                "paper 觀察期間最大回撤超過研究門檻",
                "paper 成交滑價或成交拒絕率明顯高於回測假設",
                "策略訊號連續失效且 OOS / walk-forward 未能支持續用",
            ],
        },
        "execution_assumptions": {
            "fill_model": "next_valid_open_after_signal",
            "costs": "commission_tax_and_slippage_from_backtest_config",
            "position_sizing": "risk_fraction_or_engine_default",
            "known_bias_controls": [
                "日線策略僅使用訊號日前可得資料",
                "進場以後續有效開盤價模擬，避免同日收盤訊號直接成交",
                "OOS 與 walk-forward 視窗以時間順序切分",
            ],
            "backtest_config": backtest_config,
            "validation_config": validation_config,
        },
        "validation_plan": {
            "oos_split": {
                "required": True,
                "status": _status_from_split(splits),
                "method": splits.get("method") or "chronological_70_30_with_oos_warmup",
            },
            "walk_forward": {
                "required": True,
                "status": _status_from_nested(splits, "walk_forward"),
                "method": (splits.get("walk_forward") or {}).get("method") if isinstance(splits.get("walk_forward"), dict) else "rolling_fixed_parameter_windows",
            },
            "parameter_sensitivity": {
                "required": True,
                "status": _status_from_nested(splits, "parameter_sensitivity"),
                "method": (splits.get("parameter_sensitivity") or {}).get("method") if isinstance(splits.get("parameter_sensitivity"), dict) else "threshold_neighborhood_oos",
            },
            "cross_symbol_sensitivity": {
                "required_before_live": True,
                "status": _status_from_nested(splits, "cross_symbol_sensitivity"),
                "method": (splits.get("cross_symbol_sensitivity") or {}).get("method") if isinstance(splits.get("cross_symbol_sensitivity"), dict) else "representative_symbol_basket",
                "symbols": validation_config.get("cross_symbol_symbols", []),
            },
            "paper_observation": {
                "required_before_live": True,
                "status": "planned",
            },
        },
        "promotion_criteria": {
            "paper": [
                "無 fail 等級研究 gate",
                "OOS 分段不可明顯轉負",
                "walk-forward 多數視窗不可轉負",
                "threshold 鄰近值不可出現大幅崩落",
                "成交成本、拒絕率與資料品質需在可接受範圍",
            ],
            "small_live": [
                "paper 觀察期通過",
                "跨標的或跨族群敏感度通過",
                "live order 全數帶 strategy version 與 signal id",
            ],
        },
        "artifacts": {
            "required": ["summary_json", "trades_csv", "signals_csv", "equity_csv", "fill_rejects_csv"],
            "included_in_payload": ["validation_report", "validation_splits", "research_spec"],
        },
    }


def _hypothesis(active_strategies: list[str]) -> str:
    if not active_strategies:
        return "以多策略分數尋找台股波段候選，但目前未啟用任何策略。"
    names = {
        "momentum_trend": "動能趨勢",
        "mean_reversion": "均值回歸",
        "chip_flow": "籌碼資金流",
    }
    readable = "、".join(names.get(name, name) for name in active_strategies)
    return f"結合{readable}訊號，篩出台股波段進場候選，並以時間切分驗證降低樣本內過度擬合風險。"


def _status_from_split(splits: dict[str, Any]) -> str:
    if not splits:
        return "missing"
    return str(splits.get("status") or "missing")


def _status_from_nested(splits: dict[str, Any], key: str) -> str:
    nested = splits.get(key)
    if not isinstance(nested, dict):
        return "missing"
    return str(nested.get("status") or "missing")
