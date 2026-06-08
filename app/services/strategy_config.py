from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict


STRATEGY_NAMES = ("momentum_trend", "mean_reversion", "chip_flow")

DEFAULT_ENABLED = {
    "momentum_trend": True,
    "mean_reversion": True,
    "chip_flow": True,
}

DEFAULT_WEIGHTS = {
    "momentum_trend": 0.4,
    "mean_reversion": 0.3,
    "chip_flow": 0.3,
}

STRATEGY_PARAM_KEYS = {
    "momentum_trend": {"volume_multiplier"},
    "mean_reversion": {"rsi_threshold"},
    "chip_flow": {
        "net_buy_threshold",
        "net_buy_threshold_mode",
        "net_buy_zscore_window",
        "net_buy_zscore_threshold",
        "net_buy_volume_ratio_threshold",
    },
}

RISK_CONFIG_KEYS = {
    "stop_loss_pct",
    "atr_multiplier",
    "trail_activate_return",
    "trail_drawdown_pct",
    "max_holding_days",
    "min_return_for_hold",
}

BACKTEST_CONFIG_KEYS = {
    "INITIAL_CAPITAL",
    "COMMISSION_RATE",
    "MIN_COMMISSION_FEE",
    "TAX_RATE",
    "SLIPPAGE",
    "MAX_GAP_FOR_ENTRY",
    "MIN_DAILY_VOLUME_FOR_FILL",
    "LIMIT_DOWN_FACTOR",
    "LIMIT_UP_FACTOR",
    "MAX_ENTRY_DELAY_DAYS",
    "RISK_FRACTION",
}


class StrategyConfigError(ValueError):
    """Raised when strategy/backtest configuration is invalid."""


def _unknown_keys(mapping: dict, allowed: set[str]) -> set[str]:
    return {str(key) for key in mapping.keys()} - set(allowed)


def _finite_float(value: Any, *, name: str, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        number = float(value)
    except Exception as exc:  # noqa: BLE001
        raise StrategyConfigError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise StrategyConfigError(f"{name} must be finite")
    if min_value is not None and number < min_value:
        raise StrategyConfigError(f"{name} must be >= {min_value}")
    if max_value is not None and number > max_value:
        raise StrategyConfigError(f"{name} must be <= {max_value}")
    return number


def _finite_int(value: Any, *, name: str, min_value: int | None = None, max_value: int | None = None) -> int:
    number = int(_finite_float(value, name=name, min_value=min_value, max_value=max_value))
    return number


@dataclass
class StrategyRunConfig:
    symbol: str = "2330.TW"
    market: str = "TW"
    start_date: str | None = None
    end_date: str | None = None
    enabled: Dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_ENABLED))
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    threshold: float = 0.6
    strategy_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    risk_config: Dict[str, Any] = field(default_factory=dict)
    backtest_config: Dict[str, Any] = field(default_factory=dict)

    def normalized_dates(self) -> tuple[str, str]:
        end_date = self.end_date or datetime.now().strftime("%Y-%m-%d")
        start_date = self.start_date or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        except Exception as exc:  # noqa: BLE001
            raise StrategyConfigError("start_date/end_date must use YYYY-MM-DD") from exc
        if start_dt > end_dt:
            raise StrategyConfigError("start_date must be <= end_date")
        return start_dt.isoformat(), end_dt.isoformat()

    def validated(self) -> "StrategyRunConfig":
        symbol = str(self.symbol or "").strip()
        market = str(self.market or "").strip().upper()
        if not symbol:
            raise StrategyConfigError("symbol is required")
        if not market:
            raise StrategyConfigError("market is required")
        self.normalized_dates()

        enabled_unknown = _unknown_keys(self.enabled or {}, set(STRATEGY_NAMES))
        weight_unknown = _unknown_keys(self.weights or {}, set(STRATEGY_NAMES))
        param_unknown = _unknown_keys(self.strategy_params or {}, set(STRATEGY_NAMES))
        if enabled_unknown:
            raise StrategyConfigError(f"unknown enabled strategy: {', '.join(sorted(enabled_unknown))}")
        if weight_unknown:
            raise StrategyConfigError(f"unknown strategy weight: {', '.join(sorted(weight_unknown))}")
        if param_unknown:
            raise StrategyConfigError(f"unknown strategy params: {', '.join(sorted(param_unknown))}")

        threshold = _finite_float(self.threshold, name="threshold", min_value=0.0, max_value=1.0)
        normalized_enabled = {name: bool((self.enabled or {}).get(name, DEFAULT_ENABLED[name])) for name in STRATEGY_NAMES}
        if not any(normalized_enabled.values()):
            raise StrategyConfigError("at least one strategy must be enabled")

        normalized_weights = {}
        for name in STRATEGY_NAMES:
            normalized_weights[name] = _finite_float((self.weights or {}).get(name, DEFAULT_WEIGHTS[name]), name=f"weights.{name}", min_value=0.0)
        if sum(normalized_weights.values()) <= 0:
            raise StrategyConfigError("at least one strategy weight must be > 0")

        normalized_params: dict[str, dict[str, Any]] = {}
        for strategy_name, params in (self.strategy_params or {}).items():
            allowed = STRATEGY_PARAM_KEYS[strategy_name]
            unknown = _unknown_keys(params or {}, allowed)
            if unknown:
                raise StrategyConfigError(f"unknown {strategy_name} param: {', '.join(sorted(unknown))}")
            normalized_params[strategy_name] = self._normalize_strategy_params(strategy_name, params or {})

        risk_unknown = _unknown_keys(self.risk_config or {}, RISK_CONFIG_KEYS)
        backtest_unknown = _unknown_keys(self.backtest_config or {}, BACKTEST_CONFIG_KEYS)
        if risk_unknown:
            raise StrategyConfigError(f"unknown risk_config key: {', '.join(sorted(risk_unknown))}")
        if backtest_unknown:
            raise StrategyConfigError(f"unknown backtest_config key: {', '.join(sorted(backtest_unknown))}")

        return StrategyRunConfig(
            symbol=symbol,
            market=market,
            start_date=self.start_date,
            end_date=self.end_date,
            enabled=normalized_enabled,
            weights=normalized_weights,
            threshold=threshold,
            strategy_params=normalized_params,
            risk_config=self._normalize_risk_config(self.risk_config or {}),
            backtest_config=self._normalize_backtest_config(self.backtest_config or {}),
        )

    @staticmethod
    def _normalize_strategy_params(strategy_name: str, params: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if strategy_name == "momentum_trend" and "volume_multiplier" in params:
            out["volume_multiplier"] = _finite_float(params["volume_multiplier"], name="momentum_trend.volume_multiplier", min_value=0.0, max_value=20.0)
        if strategy_name == "mean_reversion" and "rsi_threshold" in params:
            out["rsi_threshold"] = _finite_float(params["rsi_threshold"], name="mean_reversion.rsi_threshold", min_value=0.0, max_value=100.0)
        if strategy_name == "chip_flow":
            if "net_buy_threshold" in params:
                out["net_buy_threshold"] = _finite_float(params["net_buy_threshold"], name="chip_flow.net_buy_threshold")
            if "net_buy_threshold_mode" in params:
                mode = str(params["net_buy_threshold_mode"]).strip().lower()
                if mode not in {"absolute", "zscore", "relative_volume"}:
                    raise StrategyConfigError("chip_flow.net_buy_threshold_mode must be absolute, zscore, or relative_volume")
                out["net_buy_threshold_mode"] = mode
            if "net_buy_zscore_window" in params:
                out["net_buy_zscore_window"] = _finite_int(params["net_buy_zscore_window"], name="chip_flow.net_buy_zscore_window", min_value=5, max_value=500)
            if "net_buy_zscore_threshold" in params:
                out["net_buy_zscore_threshold"] = _finite_float(params["net_buy_zscore_threshold"], name="chip_flow.net_buy_zscore_threshold", min_value=-10.0, max_value=10.0)
            if "net_buy_volume_ratio_threshold" in params:
                out["net_buy_volume_ratio_threshold"] = _finite_float(params["net_buy_volume_ratio_threshold"], name="chip_flow.net_buy_volume_ratio_threshold", min_value=0.0, max_value=1.0)
        return out

    @staticmethod
    def _normalize_risk_config(config: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        percent_keys = {"stop_loss_pct", "trail_activate_return", "trail_drawdown_pct", "min_return_for_hold"}
        for key, value in config.items():
            if key in percent_keys:
                out[key] = _finite_float(value, name=f"risk_config.{key}", min_value=0.0, max_value=1.0)
            elif key == "atr_multiplier":
                out[key] = _finite_float(value, name=f"risk_config.{key}", min_value=0.0, max_value=20.0)
            elif key == "max_holding_days":
                out[key] = _finite_int(value, name=f"risk_config.{key}", min_value=1, max_value=500)
        return out

    @staticmethod
    def _normalize_backtest_config(config: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in config.items():
            if key in {"MAX_ENTRY_DELAY_DAYS"}:
                out[key] = _finite_int(value, name=f"backtest_config.{key}", min_value=0, max_value=60)
            elif key in {"LIMIT_DOWN_FACTOR", "LIMIT_UP_FACTOR"}:
                out[key] = _finite_float(value, name=f"backtest_config.{key}", min_value=0.0, max_value=2.0)
            elif key in {"INITIAL_CAPITAL", "MIN_COMMISSION_FEE", "MIN_DAILY_VOLUME_FOR_FILL"}:
                out[key] = _finite_float(value, name=f"backtest_config.{key}", min_value=0.0)
            else:
                out[key] = _finite_float(value, name=f"backtest_config.{key}", min_value=0.0, max_value=1.0)
        return out

    def strategy_engine_config(self) -> dict[str, Any]:
        cfg = self.validated()
        return {
            "enabled": cfg.enabled,
            "weights": cfg.weights,
            "threshold": cfg.threshold,
            "params": cfg.strategy_params,
        }
