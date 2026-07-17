from __future__ import annotations

import json
import statistics
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict

import pandas as pd

from app.backtest.research_spec import build_strategy_research_spec
from app.backtest.validation import build_backtest_validation_report
from backtest.backtest_engine import BacktestEngine
from app.security.reporting import sanitize_report_payload
from app.services.strategy_config import StrategyConfigError, StrategyRunConfig
from app.services.market_context import classify_tw_symbol_group
from config.settings import DEFAULT_SETTINGS
from data.data_facade import DataFacade


class StrategyWorkflowService:
    def __init__(
        self,
        data_facade: DataFacade | None = None,
        *,
        instrument_industry_lookup: Callable[[str], str] | None = None,
        industry_symbol_provider: Callable[..., list[dict]] | None = None,
        industry_context_provider: Callable[..., dict] | None = None,
    ):
        self.data_facade = data_facade or DataFacade()
        self.instrument_industry_lookup = instrument_industry_lookup or self._default_instrument_industry_lookup
        self.industry_symbol_provider = industry_symbol_provider or self._default_industry_symbol_provider
        self.industry_context_provider = industry_context_provider or self._default_industry_context_provider
        self._industry_cache: dict[str, str] = {}

    @staticmethod
    def default_payload() -> dict:
        cfg = StrategyRunConfig()
        start_date, end_date = cfg.normalized_dates()
        return {
            "symbol": cfg.symbol,
            "market": cfg.market,
            "start_date": start_date,
            "end_date": end_date,
            "enabled": cfg.enabled,
            "weights": cfg.weights,
            "threshold": cfg.threshold,
            "strategy_params": cfg.strategy_params,
            "risk_config": cfg.risk_config,
            "backtest_config": cfg.backtest_config,
            "validation_config": cfg.validation_config,
        }

    def run_multi_strategy_backtest(self, config: StrategyRunConfig) -> dict:
        executed = self._execute_backtest(config)
        if executed.get("error"):
            return executed
        start_date = str(executed["start_date"])
        end_date = str(executed["end_date"])
        result = executed["result"]
        return self._build_payload(config, start_date, end_date, result, validation_splits=executed.get("validation_splits"))

    def _build_payload(
        self,
        config: StrategyRunConfig,
        start_date: str,
        end_date: str,
        result: dict,
        *,
        validation_splits: dict | None = None,
    ) -> dict:
        trades = result.get("trade_log", pd.DataFrame())
        signals = result.get("signals", pd.DataFrame())

        passed = result.get("trade_count", 0) > 0
        message = "回測完成" if passed else "回測完成，但期間未觸發交易"
        data_rows = int((validation_splits or {}).get("rows") or len(signals) or 0)
        payload = {
            "passed": passed,
            "message": message,
            "symbol": config.symbol,
            "market": config.market,
            "period": {"start_date": start_date, "end_date": end_date},
            "metrics": {
                "total_return": result.get("total_return", 0.0),
                "max_drawdown": result.get("max_drawdown", 0.0),
                "sharpe": result.get("sharpe", 0.0),
                "cagr": result.get("cagr", 0.0),
                "win_rate": result.get("win_rate", 0.0),
                "trade_count": result.get("trade_count", 0),
            },
            "strategy_summary": self._strategy_summary(signals),
            "recent_signals": self._recent_signals(signals, limit=30),
            "trade_log": self._frame_to_rows(trades, limit=120),
            "equity_curve": self._equity_to_rows(result.get("equity_curve"), limit=300),
            "cost_breakdown": result.get("cost_breakdown", {}),
            "fill_rejects": self._frame_to_rows(result.get("fill_rejects", pd.DataFrame()), limit=200),
            "data_quality_summary": result.get("data_quality_summary", {}),
            "execution_model": result.get("execution_model", {}),
            "validation_splits": validation_splits or {},
            "raw": {
                "enabled": config.enabled,
                "weights": config.weights,
                "threshold": config.threshold,
                "risk_config": config.risk_config,
                "backtest_config": config.backtest_config,
                "validation_config": config.validation_config,
            },
        }
        payload["research_spec"] = build_strategy_research_spec(
            config,
            start_date=start_date,
            end_date=end_date,
            data_rows=data_rows,
            validation_splits=validation_splits,
        )
        payload["validation_report"] = build_backtest_validation_report(payload)
        payload["strategy_comparison"] = self._build_strategy_comparison(payload)
        payload["parameter_robustness"] = self._build_parameter_robustness_summary(payload)
        return payload

    def run_multi_strategy_backtest_export(
        self,
        config: StrategyRunConfig,
        *,
        output_dir: str | Path = "reports",
    ) -> dict:
        executed = self._execute_backtest(config)
        if executed.get("error"):
            return executed

        start_date = str(executed["start_date"])
        end_date = str(executed["end_date"])
        result = executed["result"]
        payload = self._build_payload(config, start_date, end_date, result, validation_splits=executed.get("validation_splits"))
        export_payload = self._export_backtest_artifacts(
            config=config,
            start_date=start_date,
            end_date=end_date,
            result=result,
            summary=payload,
            output_dir=output_dir,
        )
        return {
            "passed": payload.get("passed", False),
            "message": "回測與匯出完成",
            "backtest": payload,
            "export": export_payload,
        }

    def _execute_backtest(self, config: StrategyRunConfig) -> dict:
        try:
            config = config.validated()
            start_date, end_date = config.normalized_dates()
        except StrategyConfigError as exc:
            return {
                "passed": False,
                "message": str(exc),
                "error": "invalid_config",
            }
        df = self.data_facade.load_for_strategy(
            symbol=config.symbol,
            start_date=start_date,
            end_date=end_date,
            market=config.market,
            include_chip=True,
        )
        if df is None or df.empty:
            return {
                "passed": False,
                "message": f"讀不到 {config.symbol} 在 {start_date}~{end_date} 的資料",
                "error": "no_data",
            }
        if len(df) < 80:
            return {
                "passed": False,
                "message": f"資料筆數不足（{len(df)}），至少需要 80 筆日線資料",
                "error": "insufficient_data",
            }

        result = self._run_backtest_engine(config, df)
        validation_splits = self._build_validation_splits(
            config,
            df,
            start_date=start_date,
            end_date=end_date,
            baseline_result=result,
        )
        return {
            "passed": True,
            "start_date": start_date,
            "end_date": end_date,
            "result": result,
            "validation_splits": validation_splits,
        }

    def _run_backtest_engine(self, config: StrategyRunConfig, df: pd.DataFrame) -> dict:
        engine = BacktestEngine(df=df, strategy=None, broker=None, settings=DEFAULT_SETTINGS.get("BACKTEST", {}))
        return engine.run_multi(
            df=df,
            symbol=config.symbol,
            market=config.market,
            strategy_config=config.strategy_engine_config(),
            risk_config=config.risk_config,
            backtest_config=config.backtest_config,
        )

    def _build_validation_splits(
        self,
        config: StrategyRunConfig,
        df: pd.DataFrame,
        *,
        start_date: str,
        end_date: str,
        baseline_result: dict | None = None,
        split_ratio: float = 0.70,
        warmup_rows: int = 80,
    ) -> dict:
        if df is None or df.empty:
            return {"status": "insufficient", "reason": "no_data"}
        rows = len(df)
        split_idx = int(rows * split_ratio)
        if split_idx < 80 or (rows - split_idx) < 20:
            return {
                "status": "insufficient",
                "reason": "not_enough_rows_for_oos",
                "rows": rows,
                "required": "in_sample>=80 and oos>=20",
            }

        ordered = df.copy().sort_index()
        split_idx = max(80, min(split_idx, rows - 20))
        split_at = ordered.index[split_idx]
        in_sample_df = ordered.iloc[:split_idx].copy()
        oos_warmup_start = max(0, split_idx - warmup_rows)
        oos_df = ordered.iloc[oos_warmup_start:].copy()

        in_sample_result = self._run_backtest_engine(config, in_sample_df)
        oos_result = self._run_backtest_engine(config, oos_df)
        in_sample_summary = self._result_window_summary(in_sample_result, eval_start=None)
        oos_summary = self._result_window_summary(oos_result, eval_start=split_at)
        threshold_sensitivity = self._build_threshold_sensitivity(
            config=config,
            oos_df=oos_df,
            eval_start=split_at,
            baseline=oos_summary,
        )
        walk_forward = self._build_walk_forward_validation(
            config=config,
            df=ordered,
            warmup_rows=warmup_rows,
        )
        baseline_summary = self._result_window_summary(baseline_result or {}, eval_start=None)
        cross_symbol_sensitivity = self._build_cross_symbol_sensitivity(
            config=config,
            start_date=start_date,
            end_date=end_date,
            baseline=baseline_summary,
        )
        return {
            "status": "ok",
            "method": "chronological_70_30_with_oos_warmup",
            "split_ratio": split_ratio,
            "warmup_rows": min(warmup_rows, split_idx),
            "rows": rows,
            "split_at": self._serialize_date(split_at),
            "in_sample": in_sample_summary,
            "out_of_sample": oos_summary,
            "parameter_sensitivity": threshold_sensitivity,
            "walk_forward": walk_forward,
            "cross_symbol_sensitivity": cross_symbol_sensitivity,
        }

    def _build_walk_forward_validation(
        self,
        *,
        config: StrategyRunConfig,
        df: pd.DataFrame,
        train_rows: int = 80,
        test_rows: int = 20,
        step_rows: int = 20,
        warmup_rows: int = 80,
        max_windows: int = 6,
    ) -> dict:
        if df is None or df.empty:
            return {"status": "insufficient", "reason": "no_data"}
        ordered = df.copy().sort_index()
        rows = len(ordered)
        if rows < train_rows + test_rows:
            return {
                "status": "insufficient",
                "reason": "not_enough_rows_for_walk_forward",
                "rows": rows,
                "required": f"rows>={train_rows + test_rows}",
            }

        start_positions = list(range(0, rows - train_rows - test_rows + 1, max(1, step_rows)))
        if max_windows > 0:
            start_positions = start_positions[-max_windows:]

        windows: list[dict[str, Any]] = []
        for window_no, train_start in enumerate(start_positions, start=1):
            train_end = train_start + train_rows
            test_end = min(train_end + test_rows, rows)
            if test_end <= train_end:
                continue

            warmup_start = max(train_start, train_end - warmup_rows)
            window_df = ordered.iloc[warmup_start:test_end].copy()
            test_start_at = ordered.index[train_end]
            train_start_at = ordered.index[train_start]
            train_end_at = ordered.index[train_end - 1]
            test_end_at = ordered.index[test_end - 1]
            summary = self._result_window_summary(
                self._run_backtest_engine(config, window_df),
                eval_start=test_start_at,
            )
            windows.append(
                {
                    "window": window_no,
                    "train_start_date": self._serialize_date(train_start_at),
                    "train_end_date": self._serialize_date(train_end_at),
                    "test_start_date": self._serialize_date(test_start_at),
                    "test_end_date": self._serialize_date(test_end_at),
                    "train_rows": int(train_end - train_start),
                    "test_rows": int(test_end - train_end),
                    "warmup_rows": int(train_end - warmup_start),
                    **summary,
                }
            )

        if not windows:
            return {
                "status": "insufficient",
                "reason": "no_walk_forward_windows",
                "rows": rows,
                "required": f"rows>={train_rows + test_rows}",
            }

        returns = [float(item.get("total_return") or 0.0) for item in windows]
        trade_counts = [int(item.get("trade_count") or 0) for item in windows]
        return {
            "status": "ok",
            "method": "rolling_walk_forward_fixed_parameters",
            "rows": rows,
            "train_rows": int(train_rows),
            "test_rows": int(test_rows),
            "step_rows": int(step_rows),
            "max_windows": int(max_windows),
            "window_count": int(len(windows)),
            "positive_window_count": int(sum(1 for value in returns if value > 0)),
            "positive_window_ratio": float(sum(1 for value in returns if value > 0) / len(returns)),
            "negative_window_count": int(sum(1 for value in returns if value < 0)),
            "zero_trade_window_count": int(sum(1 for count in trade_counts if count == 0)),
            "tradable_window_ratio": float(sum(1 for count in trade_counts if count > 0) / len(trade_counts)),
            "worst_return": float(min(returns)),
            "best_return": float(max(returns)),
            "average_return": float(sum(returns) / len(returns)),
            "median_return": float(statistics.median(returns)),
            "windows": windows,
        }

    def _build_cross_symbol_sensitivity(
        self,
        *,
        config: StrategyRunConfig,
        start_date: str,
        end_date: str,
        baseline: dict,
        max_symbols: int = 4,
        min_rows: int = 100,
    ) -> dict:
        validation_config = config.validation_config or {}
        max_symbols = int(validation_config.get("max_cross_symbols", max_symbols) or max_symbols)
        min_rows = int(validation_config.get("min_cross_symbol_rows", min_rows) or min_rows)
        symbols = self._default_cross_symbol_symbols(config, limit=max_symbols)
        if not symbols:
            return {"status": "insufficient", "reason": "no_peer_symbols"}

        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            peer_config = replace(config, symbol=symbol)
            try:
                peer_df = self.data_facade.load_for_strategy(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    market=config.market,
                    include_chip=True,
                )
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        "symbol": symbol,
                        "group": self._symbol_group(symbol, config.market),
                        "group_source": self._symbol_group_source(symbol, config.market),
                        "status": "error",
                        "reason": self._safe_reason(exc),
                    }
                )
                continue

            if peer_df is None or peer_df.empty:
                rows.append(
                    {
                        "symbol": symbol,
                        "group": self._symbol_group(symbol, config.market),
                        "group_source": self._symbol_group_source(symbol, config.market),
                        "status": "insufficient",
                        "reason": "no_data",
                        "rows": 0,
                    }
                )
                continue
            peer_rows = int(len(peer_df))
            if peer_rows < min_rows:
                rows.append(
                    {
                        "symbol": symbol,
                        "group": self._symbol_group(symbol, config.market),
                        "group_source": self._symbol_group_source(symbol, config.market),
                        "status": "insufficient",
                        "reason": "not_enough_rows",
                        "rows": peer_rows,
                        "required": min_rows,
                    }
                )
                continue

            summary = self._result_window_summary(self._run_backtest_engine(peer_config, peer_df), eval_start=None)
            rows.append(
                {
                    "symbol": symbol,
                    "group": self._symbol_group(symbol, config.market),
                    "group_source": self._symbol_group_source(symbol, config.market),
                    "status": "ok",
                    "rows": peer_rows,
                    "total_return": float(summary.get("total_return") or 0.0),
                    "max_drawdown": float(summary.get("max_drawdown") or 0.0),
                    "cagr": float(summary.get("cagr") or 0.0),
                    "trade_count": int(summary.get("trade_count") or 0),
                    "win_rate": float(summary.get("win_rate") or 0.0),
                    "fill_reject_count": int(summary.get("fill_reject_count") or 0),
                    "start_date": summary.get("start_date"),
                    "end_date": summary.get("end_date"),
                }
            )

        ok_rows = [row for row in rows if row.get("status") == "ok"]
        group_summary = self._cross_symbol_group_summary(ok_rows)
        group_source_summary = self._cross_symbol_group_source_summary(ok_rows)
        group_stress = self._cross_symbol_group_stress(
            group_summary,
            config=config,
            start_date=start_date,
            end_date=end_date,
        )
        if len(ok_rows) < 2:
            return {
                "status": "insufficient",
                "reason": "not_enough_peer_results",
                "method": "representative_symbol_basket",
                "baseline_symbol": config.symbol,
                "baseline_return": float(baseline.get("total_return") or 0.0),
                "requested_symbols": symbols,
                "symbol_count": int(len(ok_rows)),
                "group_count": int(len(group_summary)),
                "group_summary": group_summary,
                "group_source_summary": group_source_summary,
                "group_stress": group_stress,
                "skipped_count": int(len(rows) - len(ok_rows)),
                "rows": rows,
            }

        returns = [float(row.get("total_return") or 0.0) for row in ok_rows]
        trade_counts = [int(row.get("trade_count") or 0) for row in ok_rows]
        baseline_return = float(baseline.get("total_return") or 0.0)
        worst_return = min(returns)
        best_return = max(returns)
        return {
            "status": "ok",
            "method": "representative_symbol_basket",
            "baseline_symbol": config.symbol,
            "baseline_return": baseline_return,
            "requested_symbols": symbols,
            "symbol_count": int(len(ok_rows)),
            "group_count": int(len(group_summary)),
            "group_summary": group_summary,
            "group_source_summary": group_source_summary,
            "group_stress": group_stress,
            "skipped_count": int(len(rows) - len(ok_rows)),
            "positive_symbol_count": int(sum(1 for value in returns if value > 0)),
            "positive_symbol_ratio": float(sum(1 for value in returns if value > 0) / len(returns)),
            "negative_symbol_count": int(sum(1 for value in returns if value < 0)),
            "zero_trade_symbol_count": int(sum(1 for count in trade_counts if count == 0)),
            "tradable_symbol_ratio": float(sum(1 for count in trade_counts if count > 0) / len(trade_counts)),
            "worst_return": float(worst_return),
            "best_return": float(best_return),
            "average_return": float(sum(returns) / len(returns)),
            "median_return": float(statistics.median(returns)),
            "worst_degradation_vs_baseline": float(worst_return - baseline_return),
            "best_improvement_vs_baseline": float(best_return - baseline_return),
            "rows": rows,
        }

    def _build_threshold_sensitivity(
        self,
        *,
        config: StrategyRunConfig,
        oos_df: pd.DataFrame,
        eval_start,
        baseline: dict,
        step: float = 0.05,
    ) -> dict:
        base_threshold = float(config.threshold)
        candidates = sorted({round(max(0.0, min(1.0, base_threshold + offset)), 4) for offset in (-step, 0.0, step)})
        rows: list[dict[str, Any]] = []
        for threshold in candidates:
            if abs(threshold - base_threshold) < 1e-9:
                summary = dict(baseline)
            else:
                variant = replace(config, threshold=threshold)
                summary = self._result_window_summary(self._run_backtest_engine(variant, oos_df), eval_start=eval_start)
            rows.append(
                {
                    "parameter": "threshold",
                    "value": float(threshold),
                    "is_baseline": abs(threshold - base_threshold) < 1e-9,
                    "total_return": float(summary.get("total_return") or 0.0),
                    "max_drawdown": float(summary.get("max_drawdown") or 0.0),
                    "trade_count": int(summary.get("trade_count") or 0),
                    "fill_reject_count": int(summary.get("fill_reject_count") or 0),
                }
            )

        returns = [float(row["total_return"]) for row in rows]
        baseline_return = float(baseline.get("total_return") or 0.0)
        worst_return = min(returns) if returns else 0.0
        best_return = max(returns) if returns else 0.0
        return_range = best_return - worst_return if returns else 0.0
        return {
            "status": "ok" if len(rows) >= 3 else "insufficient",
            "method": "threshold_neighborhood_oos",
            "baseline_threshold": base_threshold,
            "step": step,
            "rows": rows,
            "baseline_return": baseline_return,
            "worst_return": worst_return,
            "best_return": best_return,
            "return_range": float(return_range),
            "worst_degradation": float(worst_return - baseline_return),
            "negative_variant_count": int(sum(1 for row in rows if float(row["total_return"]) < 0)),
            "zero_trade_variant_count": int(sum(1 for row in rows if int(row["trade_count"]) == 0)),
        }

    @staticmethod
    def _build_strategy_comparison(payload: dict[str, Any]) -> dict[str, Any]:
        splits = payload.get("validation_splits") if isinstance(payload.get("validation_splits"), dict) else {}
        report = payload.get("validation_report") if isinstance(payload.get("validation_report"), dict) else {}
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        sensitivity = splits.get("parameter_sensitivity") if isinstance(splits.get("parameter_sensitivity"), dict) else {}
        cross_symbol = splits.get("cross_symbol_sensitivity") if isinstance(splits.get("cross_symbol_sensitivity"), dict) else {}
        group_stress = cross_symbol.get("group_stress") if isinstance(cross_symbol.get("group_stress"), dict) else {}

        threshold_variants: list[dict[str, Any]] = []
        for row in sensitivity.get("rows") if isinstance(sensitivity.get("rows"), list) else []:
            if not isinstance(row, dict):
                continue
            total_return = float(row.get("total_return") or 0.0)
            trade_count = int(row.get("trade_count") or 0)
            max_drawdown = float(row.get("max_drawdown") or 0.0)
            if trade_count <= 0:
                verdict = "no_trades"
            elif total_return < -0.05:
                verdict = "fragile"
            elif total_return < 0 or max_drawdown < -0.08:
                verdict = "mixed"
            else:
                verdict = "candidate"
            threshold_variants.append(
                {
                    "parameter": str(row.get("parameter") or "threshold"),
                    "value": float(row.get("value") or 0.0),
                    "is_baseline": bool(row.get("is_baseline")),
                    "total_return": total_return,
                    "max_drawdown": max_drawdown,
                    "trade_count": trade_count,
                    "fill_reject_count": int(row.get("fill_reject_count") or 0),
                    "verdict": verdict,
                }
            )
        threshold_variants.sort(key=lambda item: (item["verdict"] == "candidate", item["total_return"], -abs(item["max_drawdown"])), reverse=True)

        group_pressure: list[dict[str, Any]] = []
        for row in group_stress.get("groups") if isinstance(group_stress.get("groups"), list) else []:
            if not isinstance(row, dict):
                continue
            group_pressure.append(
                {
                    "group": str(row.get("group") or "其他"),
                    "label": str(row.get("label") or "unknown"),
                    "symbol_count": int(row.get("symbol_count") or 0),
                    "strategy_return": float(row.get("average_return") or 0.0),
                    "market_return": row.get("market_return"),
                    "relative_return": row.get("relative_return"),
                    "worst_return": float(row.get("worst_return") or 0.0),
                    "positive_ratio": float(row.get("positive_ratio") or 0.0),
                    "tradable_ratio": float(row.get("tradable_ratio") or 0.0),
                }
            )
        label_order = {"fragile": 0, "mixed": 1, "resilient": 2}
        group_pressure.sort(key=lambda item: (label_order.get(item["label"], 1), float(item["relative_return"] if item["relative_return"] is not None else 0.0)))

        best_threshold = threshold_variants[0] if threshold_variants else None
        return {
            "schema_version": "2026-06-25.strategy-comparison.v1",
            "readiness": report.get("readiness"),
            "baseline": {
                "symbol": payload.get("symbol"),
                "total_return": float(metrics.get("total_return") or 0.0),
                "max_drawdown": float(metrics.get("max_drawdown") or 0.0),
                "trade_count": int(metrics.get("trade_count") or 0),
            },
            "threshold_variants": threshold_variants,
            "best_threshold": best_threshold,
            "group_pressure": group_pressure,
            "blockers": list(report.get("blockers") or []),
            "warnings": list(report.get("warnings") or []),
        }

    @staticmethod
    def _build_parameter_robustness_summary(payload: dict[str, Any]) -> dict[str, Any]:
        splits = payload.get("validation_splits") if isinstance(payload.get("validation_splits"), dict) else {}
        sensitivity = splits.get("parameter_sensitivity") if isinstance(splits.get("parameter_sensitivity"), dict) else {}
        if sensitivity.get("status") != "ok":
            return {
                "schema_version": "2026-06-30.parameter-robustness.v1",
                "status": "insufficient",
                "verdict": "insufficient",
                "label": "資料不足",
                "score": 0,
                "reason": sensitivity.get("reason") or "parameter_sensitivity_unavailable",
                "next_actions": ["先產生至少 3 組 threshold 鄰近值掃描，再判斷是否適合進 paper。"],
            }

        rows = [row for row in sensitivity.get("rows") if isinstance(row, dict)] if isinstance(sensitivity.get("rows"), list) else []
        if len(rows) < 3:
            return {
                "schema_version": "2026-06-30.parameter-robustness.v1",
                "status": "insufficient",
                "verdict": "insufficient",
                "label": "樣本不足",
                "score": 0,
                "reason": "parameter_variant_count_below_3",
                "variant_count": len(rows),
                "next_actions": ["增加 threshold 鄰近值組數，避免只用單點回測判斷。"],
            }

        def _num(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except Exception:
                return default

        normalized_rows: list[dict[str, Any]] = []
        baseline_value = _num(sensitivity.get("baseline_threshold"))
        for row in rows:
            total_return = _num(row.get("total_return"))
            max_drawdown = _num(row.get("max_drawdown"))
            trade_count = int(_num(row.get("trade_count"), 0.0))
            value = _num(row.get("value"))
            normalized_rows.append(
                {
                    "parameter": str(row.get("parameter") or "threshold"),
                    "value": value,
                    "is_baseline": bool(row.get("is_baseline")) or abs(value - baseline_value) < 1e-9,
                    "total_return": total_return,
                    "max_drawdown": max_drawdown,
                    "trade_count": trade_count,
                    "fill_reject_count": int(_num(row.get("fill_reject_count"), 0.0)),
                }
            )
        normalized_rows.sort(key=lambda row: row["value"])
        ranked_rows = sorted(normalized_rows, key=lambda row: (row["total_return"], -abs(row["max_drawdown"]), row["trade_count"]), reverse=True)
        best_variant = ranked_rows[0]
        baseline_variant = next((row for row in normalized_rows if row["is_baseline"]), None)
        if baseline_variant is None:
            baseline_return = _num(sensitivity.get("baseline_return"))
            baseline_variant = {
                "parameter": "threshold",
                "value": baseline_value,
                "is_baseline": True,
                "total_return": baseline_return,
                "max_drawdown": 0.0,
                "trade_count": 0,
                "fill_reject_count": 0,
            }
        baseline_rank = next(
            (idx + 1 for idx, row in enumerate(ranked_rows) if abs(row["value"] - baseline_variant["value"]) < 1e-9),
            None,
        )

        variant_count = len(normalized_rows)
        positive_variant_count = sum(1 for row in normalized_rows if row["trade_count"] > 0 and row["total_return"] >= 0)
        negative_variant_count = int(sensitivity.get("negative_variant_count") or sum(1 for row in normalized_rows if row["total_return"] < 0))
        zero_trade_variant_count = int(
            sensitivity.get("zero_trade_variant_count") or sum(1 for row in normalized_rows if row["trade_count"] <= 0)
        )
        return_range = _num(sensitivity.get("return_range"), max(row["total_return"] for row in normalized_rows) - min(row["total_return"] for row in normalized_rows))
        worst_return = _num(sensitivity.get("worst_return"), min(row["total_return"] for row in normalized_rows))
        best_return = _num(sensitivity.get("best_return"), max(row["total_return"] for row in normalized_rows))
        worst_degradation = _num(sensitivity.get("worst_degradation"), worst_return - baseline_variant["total_return"])
        positive_variant_ratio = positive_variant_count / variant_count if variant_count else 0.0
        candidate_variant_count = sum(
            1
            for row in normalized_rows
            if row["trade_count"] > 0 and row["total_return"] >= 0 and row["max_drawdown"] >= -0.08
        )
        candidate_variant_ratio = candidate_variant_count / variant_count if variant_count else 0.0

        score = 100
        score -= min(35, int(max(return_range, 0.0) * 250))
        score -= min(30, negative_variant_count * 12)
        score -= min(25, zero_trade_variant_count * 10)
        if worst_return < -0.05:
            score -= 35
        elif worst_return < 0:
            score -= 15
        if positive_variant_ratio < 0.5:
            score -= 25
        elif positive_variant_ratio < 0.75:
            score -= 10
        baseline_gap = float(best_variant["total_return"]) - float(baseline_variant["total_return"])
        if baseline_gap > 0.06:
            score -= 15
        elif baseline_gap > 0.03:
            score -= 8
        score = max(0, min(100, int(round(score))))

        if zero_trade_variant_count >= 2 or worst_return < -0.05 or positive_variant_ratio < 0.5:
            verdict = "fragile"
            label = "參數脆弱"
        elif negative_variant_count > 0 or return_range > 0.12 or candidate_variant_ratio < 0.67:
            verdict = "sensitive"
            label = "參數敏感"
        elif return_range <= 0.05 and negative_variant_count == 0 and zero_trade_variant_count == 0 and baseline_gap <= 0.03:
            verdict = "robust"
            label = "穩健區間"
        else:
            verdict = "candidate"
            label = "可觀察"

        next_actions_by_verdict = {
            "robust": [
                "保留現有 threshold，下一步優先看跨標的、族群壓力與 paper 成交差異。",
                "不要只因單一最佳值再微調，避免把穩健區間調窄。",
            ],
            "candidate": [
                "現有 threshold 可列入觀察，但 paper 前仍需比對 walk-forward 與跨標的結果。",
                "若最佳值與 baseline 差距持續擴大，先延長 OOS 而不是立刻改參數。",
            ],
            "sensitive": [
                "暫時不要追逐最佳 threshold，先擴大掃描範圍或降低單次參數變動幅度。",
                "檢查負報酬或零交易變體是否集中在較高門檻，必要時縮小策略適用情境。",
            ],
            "fragile": [
                "暫不建議依本組參數進 paper；先增加資料期間、標的籃子或重新檢查訊號門檻。",
                "若多個鄰近值無交易或明顯轉負，應把策略視為假說而非候選策略。",
            ],
        }

        return {
            "schema_version": "2026-06-30.parameter-robustness.v1",
            "status": "ok",
            "verdict": verdict,
            "label": label,
            "score": score,
            "variant_count": variant_count,
            "candidate_variant_count": candidate_variant_count,
            "candidate_variant_ratio": candidate_variant_ratio,
            "positive_variant_count": positive_variant_count,
            "positive_variant_ratio": positive_variant_ratio,
            "negative_variant_count": negative_variant_count,
            "zero_trade_variant_count": zero_trade_variant_count,
            "return_range": return_range,
            "worst_return": worst_return,
            "best_return": best_return,
            "worst_degradation": worst_degradation,
            "baseline_gap_to_best": baseline_gap,
            "baseline_rank": baseline_rank,
            "baseline_variant": baseline_variant,
            "best_variant": best_variant,
            "rows": normalized_rows,
            "next_actions": next_actions_by_verdict[verdict],
        }

    def _result_window_summary(self, result: dict, *, eval_start) -> dict:
        equity = result.get("equity_curve")
        if equity is None or len(equity) == 0:
            equity_window = pd.Series(dtype=float)
        else:
            equity_window = pd.Series(equity).sort_index()
            if eval_start is not None:
                equity_window = equity_window[equity_window.index >= eval_start]

        trade_log = result.get("trade_log", pd.DataFrame())
        trades = trade_log.copy() if isinstance(trade_log, pd.DataFrame) else pd.DataFrame()
        if not trades.empty and eval_start is not None and "date" in trades.columns:
            dates = pd.to_datetime(trades["date"], errors="coerce")
            trades = trades[dates >= pd.Timestamp(eval_start)]
        closed = trades[trades["side"] == "sell"] if not trades.empty and "side" in trades.columns else pd.DataFrame()

        fill_rejects = result.get("fill_rejects", pd.DataFrame())
        rejects = fill_rejects.copy() if isinstance(fill_rejects, pd.DataFrame) else pd.DataFrame()
        if not rejects.empty and eval_start is not None and "date" in rejects.columns:
            reject_dates = pd.to_datetime(rejects["date"], errors="coerce")
            rejects = rejects[reject_dates >= pd.Timestamp(eval_start)]

        total_return = 0.0
        max_drawdown = 0.0
        cagr = 0.0
        if len(equity_window) >= 2 and float(equity_window.iloc[0]) > 0:
            total_return = float(equity_window.iloc[-1] / equity_window.iloc[0] - 1.0)
            max_drawdown = self._max_drawdown(equity_window)
            days = (equity_window.index[-1] - equity_window.index[0]).days if hasattr(equity_window.index[-1], "__sub__") else 0
            years = days / 365.0 if days > 0 else 0.0
            cagr = float((equity_window.iloc[-1] / equity_window.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0

        win_rate = 0.0
        if not closed.empty and "pnl" in closed.columns:
            win_rate = float((pd.to_numeric(closed["pnl"], errors="coerce").fillna(0) > 0).sum() / len(closed))

        return {
            "start_date": self._serialize_date(equity_window.index[0]) if len(equity_window) else None,
            "end_date": self._serialize_date(equity_window.index[-1]) if len(equity_window) else None,
            "rows": int(len(equity_window)),
            "total_return": float(total_return),
            "max_drawdown": float(max_drawdown),
            "cagr": float(cagr),
            "trade_count": int(len(closed)),
            "win_rate": float(win_rate),
            "fill_reject_count": int(len(rejects)),
        }

    def _export_backtest_artifacts(
        self,
        *,
        config: StrategyRunConfig,
        start_date: str,
        end_date: str,
        result: dict,
        summary: dict,
        output_dir: str | Path,
    ) -> dict:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        symbol_safe = self._safe_symbol(config.symbol)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"strategy_backtest_{symbol_safe}_{stamp}"

        trade_log = result.get("trade_log", pd.DataFrame())
        signals = result.get("signals", pd.DataFrame())
        equity = result.get("equity_curve", pd.Series(dtype=float))
        fill_rejects = result.get("fill_rejects", pd.DataFrame())

        summary_json_name = f"{prefix}_summary.json"
        trades_csv_name = f"{prefix}_trades.csv"
        signals_csv_name = f"{prefix}_signals.csv"
        equity_csv_name = f"{prefix}_equity.csv"
        fill_rejects_csv_name = f"{prefix}_fill_rejects.csv"

        summary_path = out_dir / summary_json_name
        trades_path = out_dir / trades_csv_name
        signals_path = out_dir / signals_csv_name
        equity_path = out_dir / equity_csv_name
        fill_rejects_path = out_dir / fill_rejects_csv_name

        summary_with_meta = sanitize_report_payload(
            {
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": config.symbol,
                "market": config.market,
                "period": {"start_date": start_date, "end_date": end_date},
                "config": {
                    "enabled": config.enabled,
                    "weights": config.weights,
                    "threshold": config.threshold,
                    "strategy_params": config.strategy_params,
                    "risk_config": config.risk_config,
                    "backtest_config": config.backtest_config,
                    "validation_config": config.validation_config,
                },
                "result": summary,
            }
        )
        summary_path.write_text(json.dumps(summary_with_meta, ensure_ascii=False, indent=2), encoding="utf-8")

        self._sanitize_frame(trade_log).to_csv(trades_path, index=False, encoding="utf-8-sig")
        self._sanitize_frame(signals).to_csv(signals_path, index=True, encoding="utf-8-sig")
        equity_frame = pd.Series(equity, name="equity").to_frame()
        equity_frame.index.name = "date"
        self._sanitize_frame(equity_frame).to_csv(equity_path, encoding="utf-8-sig")
        self._sanitize_frame(fill_rejects).to_csv(fill_rejects_path, index=False, encoding="utf-8-sig")

        return {
            "prefix": prefix,
            "directory": str(out_dir),
            "files": {
                "summary_json": summary_json_name,
                "trades_csv": trades_csv_name,
                "signals_csv": signals_csv_name,
                "equity_csv": equity_csv_name,
                "fill_rejects_csv": fill_rejects_csv_name,
            },
            "urls": {
                "summary_json": f"/reports/{summary_json_name}",
                "trades_csv": f"/reports/{trades_csv_name}",
                "signals_csv": f"/reports/{signals_csv_name}",
                "equity_csv": f"/reports/{equity_csv_name}",
                "fill_rejects_csv": f"/reports/{fill_rejects_csv_name}",
            },
        }

    def _default_cross_symbol_symbols(self, config: StrategyRunConfig, *, limit: int = 4) -> list[str]:
        configured = (config.validation_config or {}).get("cross_symbol_symbols")
        if configured:
            base = self._symbol_identity(config.symbol)
            selected: list[str] = []
            for symbol in configured:
                if self._symbol_identity(symbol) == base:
                    continue
                selected.append(str(symbol))
                if len(selected) >= max(0, int(limit)):
                    break
            return selected

        market = str(config.market or "").upper()
        if market == "TW":
            stratified = self._industry_stratified_cross_symbols(config, limit=limit)
            if stratified:
                return stratified

        if market == "TW":
            candidates = [
                "2330.TW",
                "2317.TW",
                "2454.TW",
                "2303.TW",
                "2881.TW",
                "1301.TW",
                "2412.TW",
                "2002.TW",
            ]
        else:
            candidates = [
                "SPY",
                "QQQ",
                "AAPL",
                "MSFT",
                "NVDA",
                "JPM",
            ]
        base = self._symbol_identity(config.symbol)
        selected: list[str] = []
        for symbol in candidates:
            if self._symbol_identity(symbol) == base:
                continue
            selected.append(symbol)
            if len(selected) >= max(0, int(limit)):
                break
        return selected

    def _industry_stratified_cross_symbols(self, config: StrategyRunConfig, *, limit: int) -> list[str]:
        validation_config = config.validation_config or {}
        try:
            rows = self.industry_symbol_provider(
                markets=["TWSE", "TPEX"],
                exclude_symbols=[self._symbol_identity(config.symbol)],
                limit=max(0, int(limit)),
                per_industry=int(validation_config.get("cross_industry_per_group", 1) or 1),
                lookback_days=int(validation_config.get("cross_symbol_lookback_days", 90) or 90),
                min_bars=max(1, int(validation_config.get("min_cross_symbol_rows", 100) or 100) // 2),
            )
        except Exception:
            rows = []
        selected: list[str] = []
        seen: set[str] = set()
        for row in rows or []:
            raw_symbol = str(row.get("symbol") if isinstance(row, dict) else row).strip()
            if not raw_symbol:
                continue
            identity = self._symbol_identity(raw_symbol)
            if identity in seen or identity == self._symbol_identity(config.symbol):
                continue
            industry = str(row.get("industry", "") if isinstance(row, dict) else "").strip()
            if industry:
                self._industry_cache[identity] = industry
            seen.add(identity)
            selected.append(self._normalize_peer_symbol(raw_symbol, config.market))
            if len(selected) >= max(0, int(limit)):
                break
        return selected

    def _symbol_group(self, symbol: str, market: str) -> str:
        if str(market or "").upper() == "TW":
            return self._resolve_instrument_industry(symbol) or classify_tw_symbol_group(str(symbol or "").upper().removesuffix(".TW"))
        return str(market or "OTHER").upper()

    def _symbol_group_source(self, symbol: str, market: str) -> str:
        if str(market or "").upper() != "TW":
            return "market"
        return "instrument_industry" if self._resolve_instrument_industry(symbol) else "symbol_prefix"

    def _resolve_instrument_industry(self, symbol: str) -> str:
        identity = self._symbol_identity(symbol)
        if not identity:
            return ""
        if identity in self._industry_cache:
            return self._industry_cache[identity]
        industry = ""
        try:
            industry = str(self.instrument_industry_lookup(identity) or "").strip()
            if not industry:
                industry = str(self.instrument_industry_lookup(str(symbol or "").strip()) or "").strip()
        except Exception:
            industry = ""
        self._industry_cache[identity] = industry
        return industry

    @staticmethod
    def _default_instrument_industry_lookup(symbol: str) -> str:
        try:
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db

            init_db()
            session_factory = get_session_factory()
            with session_factory() as session:
                repo = TradingRepository(session)
                return repo.get_instrument_industry(str(symbol or "").strip())
        except Exception:
            return ""

    @staticmethod
    def _default_industry_symbol_provider(**kwargs) -> list[dict]:
        try:
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db

            init_db()
            session_factory = get_session_factory()
            with session_factory() as session:
                repo = TradingRepository(session)
                return repo.get_recent_liquid_symbols_by_industry(**kwargs)
        except Exception:
            return []

    @staticmethod
    def _default_industry_context_provider(**kwargs) -> dict:
        try:
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db

            init_db()
            session_factory = get_session_factory()
            with session_factory() as session:
                repo = TradingRepository(session)
                return repo.get_industry_return_context(**kwargs)
        except Exception:
            return {"status": "insufficient", "reason": "industry_context_unavailable"}

    @staticmethod
    def _cross_symbol_group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            group = str(row.get("group") or "其他")
            grouped.setdefault(group, []).append(row)
        summary: list[dict[str, Any]] = []
        for group, items in sorted(grouped.items()):
            returns = [float(item.get("total_return") or 0.0) for item in items]
            trades = [int(item.get("trade_count") or 0) for item in items]
            summary.append(
                {
                    "group": group,
                    "symbol_count": int(len(items)),
                    "positive_symbol_count": int(sum(1 for value in returns if value > 0)),
                    "tradable_symbol_count": int(sum(1 for count in trades if count > 0)),
                    "average_return": float(sum(returns) / len(returns)) if returns else 0.0,
                    "worst_return": float(min(returns)) if returns else 0.0,
                    "best_return": float(max(returns)) if returns else 0.0,
                }
            )
        return summary

    @staticmethod
    def _cross_symbol_group_source_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
        summary: dict[str, int] = {}
        for row in rows:
            source = str(row.get("group_source") or "unknown")
            summary[source] = summary.get(source, 0) + 1
        return summary

    def _cross_symbol_group_stress(
        self,
        group_summary: list[dict[str, Any]],
        *,
        config: StrategyRunConfig,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        if not group_summary:
            return {"status": "insufficient", "reason": "no_group_summary", "groups": []}
        groups: list[dict[str, Any]] = []
        for item in group_summary:
            avg_return = float(item.get("average_return") or 0.0)
            worst_return = float(item.get("worst_return") or 0.0)
            symbol_count = int(item.get("symbol_count") or 0)
            positive_count = int(item.get("positive_symbol_count") or 0)
            tradable_count = int(item.get("tradable_symbol_count") or 0)
            positive_ratio = positive_count / symbol_count if symbol_count > 0 else 0.0
            tradable_ratio = tradable_count / symbol_count if symbol_count > 0 else 0.0
            if worst_return < -0.08 or positive_ratio < 0.5:
                label = "fragile"
            elif avg_return < 0 or tradable_ratio < 0.5:
                label = "mixed"
            else:
                label = "resilient"
            market_context = self._industry_market_context(
                group=str(item.get("group") or ""),
                config=config,
                start_date=start_date,
                end_date=end_date,
            )
            market_return = None
            relative_return = None
            if market_context.get("status") == "ok":
                market_return = float(market_context.get("average_return") or 0.0)
                relative_return = float(avg_return - market_return)
            groups.append(
                {
                    "group": str(item.get("group") or "其他"),
                    "label": label,
                    "symbol_count": symbol_count,
                    "positive_ratio": float(positive_ratio),
                    "tradable_ratio": float(tradable_ratio),
                    "average_return": avg_return,
                    "worst_return": worst_return,
                    "best_return": float(item.get("best_return") or 0.0),
                    "market_context": market_context,
                    "market_return": market_return,
                    "relative_return": relative_return,
                }
            )

        fragile = [item for item in groups if item["label"] == "fragile"]
        mixed = [item for item in groups if item["label"] == "mixed"]
        resilient = [item for item in groups if item["label"] == "resilient"]
        worst_group = min(groups, key=lambda item: float(item["worst_return"]))
        return {
            "status": "ok",
            "method": "industry_group_return_stress",
            "group_count": int(len(groups)),
            "resilient_group_count": int(len(resilient)),
            "mixed_group_count": int(len(mixed)),
            "fragile_group_count": int(len(fragile)),
            "fragile_group_ratio": float(len(fragile) / len(groups)),
            "context_group_count": int(sum(1 for item in groups if item.get("market_context", {}).get("status") == "ok")),
            "underperform_group_count": int(sum(1 for item in groups if item.get("relative_return") is not None and float(item["relative_return"]) < -0.03)),
            "worst_group": worst_group,
            "groups": groups,
        }

    def _industry_market_context(self, *, group: str, config: StrategyRunConfig, start_date: str, end_date: str) -> dict[str, Any]:
        text = str(group or "").strip()
        if not text:
            return {"status": "insufficient", "reason": "group_required"}
        try:
            return self.industry_context_provider(
                industry=text,
                start_date=date.fromisoformat(str(start_date)[:10]),
                end_date=date.fromisoformat(str(end_date)[:10]),
                markets=["TWSE", "TPEX"] if str(config.market or "").upper() == "TW" else [str(config.market or "").upper()],
                min_bars=max(2, int((config.validation_config or {}).get("min_cross_symbol_rows", 100) or 100) // 4),
            )
        except Exception:
            return {"status": "insufficient", "reason": "industry_context_unavailable", "industry": text}

    @staticmethod
    def _symbol_identity(symbol: str) -> str:
        text = str(symbol or "").strip().upper()
        return text[:-3] if text.endswith(".TW") else text

    @staticmethod
    def _normalize_peer_symbol(symbol: str, market: str) -> str:
        text = str(symbol or "").strip().upper()
        if str(market or "").upper() == "TW" and text.isdigit():
            return f"{text}.TW"
        return text

    @staticmethod
    def _safe_reason(exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        return text[:160]

    @staticmethod
    def _safe_symbol(symbol: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(symbol or "SYMBOL"))
        return cleaned.strip("_") or "SYMBOL"

    @staticmethod
    def _sanitize_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        return frame.map(sanitize_report_payload)

    @staticmethod
    def _strategy_summary(signals: pd.DataFrame) -> dict:
        if signals is None or signals.empty:
            return {}
        summary = {}
        for name in ("momentum_trend", "mean_reversion", "chip_flow"):
            col = f"{name}_signal"
            if col in signals.columns:
                summary[name] = int(pd.to_numeric(signals[col], errors="coerce").fillna(0).sum())
        if "entry_signal" in signals.columns:
            summary["entry_signal_count"] = int(pd.to_numeric(signals["entry_signal"], errors="coerce").fillna(0).sum())
        return summary

    @staticmethod
    def _recent_signals(signals: pd.DataFrame, limit: int = 30) -> list[dict]:
        if signals is None or signals.empty:
            return []
        cols = [
            "Close",
            "ensemble_score",
            "entry_signal",
            "active_strategies",
            "momentum_trend_signal",
            "mean_reversion_signal",
            "chip_flow_signal",
        ]
        available = [col for col in cols if col in signals.columns]
        view = signals[available].tail(limit).copy()
        return StrategyWorkflowService._frame_to_rows(view, limit=limit)

    @staticmethod
    def _equity_to_rows(equity: pd.Series | None, limit: int = 300) -> list[dict]:
        if equity is None or len(equity) == 0:
            return []
        series = equity.tail(limit)
        rows = []
        for idx, value in series.items():
            rows.append({"date": StrategyWorkflowService._serialize_date(idx), "equity": float(value)})
        return rows

    @staticmethod
    def _max_drawdown(curve: pd.Series) -> float:
        series = pd.Series(curve, dtype=float).replace([float("inf"), float("-inf")], pd.NA).dropna()
        if series.empty:
            return 0.0
        peak = series.cummax()
        drawdown = (series - peak) / peak
        return float(drawdown.min())

    @staticmethod
    def _serialize_date(value) -> str:
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d")
        return str(value)

    @staticmethod
    def _frame_to_rows(df: pd.DataFrame, limit: int = 120) -> list[dict]:
        if df is None or df.empty:
            return []
        view = df.tail(limit).copy()
        if isinstance(view.index, pd.DatetimeIndex):
            view = view.reset_index().rename(columns={"index": "date"})
        elif view.index.name:
            view = view.reset_index()
        rows = []
        for item in view.to_dict(orient="records"):
            row = {}
            for key, value in item.items():
                if pd.isna(value):
                    row[key] = None
                    continue
                if hasattr(value, "strftime"):
                    row[key] = value.strftime("%Y-%m-%d")
                elif isinstance(value, float):
                    row[key] = float(value)
                elif isinstance(value, int):
                    row[key] = int(value)
                else:
                    row[key] = value
            rows.append(row)
        return rows
