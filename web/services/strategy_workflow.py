from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from backtest.backtest_engine import BacktestEngine
from config.settings import DEFAULT_SETTINGS
from data.data_facade import DataFacade


@dataclass
class StrategyRunConfig:
    symbol: str = "2330.TW"
    market: str = "TW"
    start_date: str | None = None
    end_date: str | None = None
    enabled: Dict[str, bool] = field(
        default_factory=lambda: {
            "momentum_trend": True,
            "mean_reversion": True,
            "chip_flow": True,
        }
    )
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "momentum_trend": 0.4,
            "mean_reversion": 0.3,
            "chip_flow": 0.3,
        }
    )
    threshold: float = 0.6
    strategy_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    risk_config: Dict[str, Any] = field(default_factory=dict)
    backtest_config: Dict[str, Any] = field(default_factory=dict)

    def normalized_dates(self) -> tuple[str, str]:
        end_date = self.end_date or datetime.now().strftime("%Y-%m-%d")
        start_date = self.start_date or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        return start_date, end_date


class StrategyWorkflowService:
    def __init__(self, data_facade: DataFacade | None = None):
        self.data_facade = data_facade or DataFacade()

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
        }

    def run_multi_strategy_backtest(self, config: StrategyRunConfig) -> dict:
        executed = self._execute_backtest(config)
        if executed.get("error"):
            return executed
        start_date = str(executed["start_date"])
        end_date = str(executed["end_date"])
        result = executed["result"]
        return self._build_payload(config, start_date, end_date, result)

    def _build_payload(self, config: StrategyRunConfig, start_date: str, end_date: str, result: dict) -> dict:
        trades = result.get("trade_log", pd.DataFrame())
        signals = result.get("signals", pd.DataFrame())

        passed = result.get("trade_count", 0) > 0
        message = "回測完成" if passed else "回測完成，但期間未觸發交易"
        return {
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
            "raw": {
                "enabled": config.enabled,
                "weights": config.weights,
                "threshold": config.threshold,
                "risk_config": config.risk_config,
                "backtest_config": config.backtest_config,
            },
        }

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
        payload = self._build_payload(config, start_date, end_date, result)
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
        start_date, end_date = config.normalized_dates()
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

        engine = BacktestEngine(df=df, strategy=None, broker=None, settings=DEFAULT_SETTINGS.get("BACKTEST", {}))
        result = engine.run_multi(
            df=df,
            symbol=config.symbol,
            market=config.market,
            strategy_config={
                "enabled": config.enabled,
                "weights": config.weights,
                "threshold": config.threshold,
                "params": config.strategy_params,
            },
            risk_config=config.risk_config,
            backtest_config=config.backtest_config,
        )
        return {
            "passed": True,
            "start_date": start_date,
            "end_date": end_date,
            "result": result,
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

        summary_json_name = f"{prefix}_summary.json"
        trades_csv_name = f"{prefix}_trades.csv"
        signals_csv_name = f"{prefix}_signals.csv"
        equity_csv_name = f"{prefix}_equity.csv"

        summary_path = out_dir / summary_json_name
        trades_path = out_dir / trades_csv_name
        signals_path = out_dir / signals_csv_name
        equity_path = out_dir / equity_csv_name

        summary_with_meta = {
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
            },
            "result": summary,
        }
        summary_path.write_text(json.dumps(summary_with_meta, ensure_ascii=False, indent=2), encoding="utf-8")

        trade_log.to_csv(trades_path, index=False, encoding="utf-8-sig")
        signals.to_csv(signals_path, index=True, encoding="utf-8-sig")
        equity_frame = pd.Series(equity, name="equity").to_frame()
        equity_frame.index.name = "date"
        equity_frame.to_csv(equity_path, encoding="utf-8-sig")

        return {
            "prefix": prefix,
            "directory": str(out_dir),
            "files": {
                "summary_json": summary_json_name,
                "trades_csv": trades_csv_name,
                "signals_csv": signals_csv_name,
                "equity_csv": equity_csv_name,
            },
            "urls": {
                "summary_json": f"/reports/{summary_json_name}",
                "trades_csv": f"/reports/{trades_csv_name}",
                "signals_csv": f"/reports/{signals_csv_name}",
                "equity_csv": f"/reports/{equity_csv_name}",
            },
        }

    @staticmethod
    def _safe_symbol(symbol: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(symbol or "SYMBOL"))
        return cleaned.strip("_") or "SYMBOL"

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
