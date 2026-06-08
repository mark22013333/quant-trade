from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, time, timedelta
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from app.backtest.costs import estimate_buy_total_cost
from app.broker.pre_trade_check import (
    LiveOrderUnlockConfig,
    PreTradeCheckError,
    enforce_absolute_capital_guard,
    validate_live_order_unlock,
)
from app.execution.models import ExecutionResult, OrderIntent, PreTradeDecision
from app.market_data import DataQualityStatus
from app.security import redact_sensitive


logger = logging.getLogger(__name__)


@dataclass
class ShioajiConfig:
    simulation: bool = True
    env_path: str = ".env"
    order_timeout_sec: int = 20
    max_daily_orders: int = 20
    duplicate_window_sec: int = 10
    enforce_session_check: bool = False
    allow_live_order: bool = False
    live_order_nonce: str = ""


class ShioajiGateway:
    """
    Phase 3 live-trading gateway:
    1) read live available cash
    2) absolute-capital pre-trade guard
    3) second cash check immediately before place_order
    4) place order only when all checks pass
    """

    def __init__(self, config: ShioajiConfig | None = None):
        self.config = config or ShioajiConfig()
        load_dotenv(self.config.env_path, override=False)
        self.api = None
        self._daily_order_count: dict[str, int] = {}
        self._recent_order_ts: dict[tuple[str, str, int], datetime] = {}

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        code = (symbol or "").strip().upper()
        for suffix in (".TW", ".TWO"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
        return code

    def login(self):
        try:
            import shioaji as sj
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("shioaji is not installed") from exc

        api_key = os.getenv("SHIOAJI_APIKEY", "").strip()
        secret = os.getenv("SHIOAJI_SECRET", "").strip()
        if not api_key or not secret:
            raise RuntimeError("SHIOAJI_APIKEY/SHIOAJI_SECRET missing")
        self.api = sj.Shioaji(simulation=self.config.simulation)
        self.api.login(api_key=api_key, secret_key=secret)
        return self.api

    @staticmethod
    def _extract_balance_value(payload: Any) -> float | None:
        if payload is None:
            return None
        if isinstance(payload, (int, float)):
            return float(payload)
        if isinstance(payload, (list, tuple)):
            for item in payload:
                value = ShioajiGateway._extract_balance_value(item)
                if value is not None:
                    return value
            return None

        for key in ("acc_balance", "available_balance", "available_funds", "available_amount", "available_cash"):
            if isinstance(payload, dict) and isinstance(payload.get(key), (int, float)):
                return float(payload[key])
            value = getattr(payload, key, None)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    @staticmethod
    def _tw_tick_size(price: float) -> float:
        value = float(price)
        if value < 10:
            return 0.01
        if value < 50:
            return 0.05
        if value < 100:
            return 0.1
        if value < 500:
            return 0.5
        if value < 1000:
            return 1.0
        return 5.0

    @staticmethod
    def _is_valid_tick(price: float) -> bool:
        tick = ShioajiGateway._tw_tick_size(price)
        if tick <= 0:
            return False
        scaled = round(float(price) / tick)
        return abs((scaled * tick) - float(price)) < 1e-8

    @staticmethod
    def _is_trading_session(now_dt: datetime | None = None) -> bool:
        now_dt = now_dt or datetime.now()
        if now_dt.weekday() >= 5:
            return False
        now_time = now_dt.time()
        return time(9, 0) <= now_time <= time(13, 30)

    @staticmethod
    def _is_within_price_band(contract: Any, price: float) -> bool:
        limit_down = getattr(contract, "limit_down", None)
        limit_up = getattr(contract, "limit_up", None)
        if isinstance(limit_down, (int, float)) and float(price) < float(limit_down):
            return False
        if isinstance(limit_up, (int, float)) and float(price) > float(limit_up):
            return False
        return True

    @classmethod
    def _tick_distance(cls, left: float, right: float) -> int:
        low = min(float(left), float(right))
        high = max(float(left), float(right))
        if high <= low:
            return 0
        distance = 0
        cursor = low
        while cursor < high and distance <= 10_000:
            cursor += cls._tw_tick_size(cursor)
            distance += 1
        return distance

    def _is_duplicate_order(self, symbol: str, qty: int, price: float) -> bool:
        now_dt = datetime.now()
        key = (self._normalize_symbol(symbol), str(int(qty)), int(round(float(price) * 100)))
        last = self._recent_order_ts.get(key)
        if last is None:
            return False
        return (now_dt - last) <= timedelta(seconds=max(1, int(self.config.duplicate_window_sec)))

    def _mark_order_seen(self, symbol: str, qty: int, price: float) -> None:
        key = (self._normalize_symbol(symbol), str(int(qty)), int(round(float(price) * 100)))
        self._recent_order_ts[key] = datetime.now()

    def _check_daily_order_limit(self) -> bool:
        day_key = datetime.now().strftime("%Y-%m-%d")
        count = int(self._daily_order_count.get(day_key, 0))
        return count < max(1, int(self.config.max_daily_orders))

    def _increase_daily_order_count(self) -> None:
        day_key = datetime.now().strftime("%Y-%m-%d")
        self._daily_order_count[day_key] = int(self._daily_order_count.get(day_key, 0)) + 1

    def get_available_cash(self) -> float:
        if self.api is None:
            self.login()
        account = getattr(self.api, "stock_account", None)
        if account is None:
            raise RuntimeError("stock account not found")

        for call in (
            lambda: self.api.account_balance(account=account),
            lambda: self.api.account_balance(account),
            lambda: self.api.account_balance(),
        ):
            try:
                result = call()
            except Exception:
                continue
            value = self._extract_balance_value(result)
            if value is not None:
                return float(value)
        raise RuntimeError("unable to read account balance")

    def _get_stock_contract(self, symbol: str):
        if self.api is None:
            self.login()
        code = self._normalize_symbol(symbol)
        stocks = getattr(getattr(self.api, "Contracts", None), "Stocks", None)
        if stocks is None:
            raise RuntimeError("unable to access Contracts.Stocks")
        for getter in (
            lambda: stocks[code],
            lambda: getattr(stocks, "TSE", {})[code],
            lambda: getattr(stocks, "OTC", {})[code],
        ):
            try:
                return getter()
            except Exception:
                continue
        raise RuntimeError(f"stock contract not found: {code}")

    def _get_futures_contract(self, symbol: str):
        if self.api is None:
            self.login()
        code = self._normalize_symbol(symbol)
        futures = getattr(getattr(self.api, "Contracts", None), "Futures", None)
        if futures is None:
            raise RuntimeError("unable to access Contracts.Futures")
        for getter in (
            lambda: futures[code],
            lambda: getattr(futures, code)[code],
            lambda: getattr(futures, "TXF", {})[code],
            lambda: getattr(futures, "MXF", {})[code],
        ):
            try:
                return getter()
            except Exception:
                continue
        raise RuntimeError(f"futures contract not found: {code}")

    def resolve_order_price(self, symbol: str, explicit_price: float | None = None, price_offset_ticks: int = 0) -> float:
        if explicit_price is not None and float(explicit_price) > 0:
            base_price = float(explicit_price)
        else:
            contract = self._get_stock_contract(symbol)
            for key in ("reference", "close"):
                value = getattr(contract, key, None)
                if isinstance(value, (int, float)) and float(value) > 0:
                    base_price = float(value)
                    break
            else:
                raise RuntimeError("unable to resolve reference price")

        adjusted = float(base_price)
        steps = int(price_offset_ticks)
        if steps > 0:
            for _ in range(steps):
                adjusted += self._tw_tick_size(adjusted)
        elif steps < 0:
            for _ in range(abs(steps)):
                adjusted = max(self._tw_tick_size(adjusted), adjusted - self._tw_tick_size(adjusted))
        return float(adjusted)

    @staticmethod
    def _resolve_constant(module: Any, enum_name: str, member_name: str, fallback: str):
        const = getattr(getattr(module, "constant", None), enum_name, None)
        if const is None:
            return fallback
        return getattr(const, member_name, fallback)

    def _build_order(self, *, price: float, qty: int, account: Any, use_odd_lot: bool, side: str = "buy"):
        try:
            import shioaji as sj
        except Exception:  # noqa: BLE001
            sj = None

        action_buy = self._resolve_constant(sj, "Action", "Buy", "Buy")
        action_sell = self._resolve_constant(sj, "Action", "Sell", "Sell")
        price_type_lmt = self._resolve_constant(sj, "StockPriceType", "LMT", "LMT")
        order_type_rod = self._resolve_constant(sj, "OrderType", "ROD", "ROD")
        lot_enum = "IntradayOdd" if use_odd_lot else "Common"
        order_lot = self._resolve_constant(sj, "StockOrderLot", lot_enum, lot_enum)

        order_kwargs = {
            "price": float(price),
            "quantity": int(qty),
            "action": action_sell if str(side).lower() == "sell" else action_buy,
            "price_type": price_type_lmt,
            "order_type": order_type_rod,
            "account": account,
        }

        if use_odd_lot:
            order_kwargs["order_lot"] = order_lot
        try:
            return self.api.Order(**order_kwargs)
        except TypeError:
            order_kwargs.pop("order_lot", None)
            try:
                return self.api.Order(**order_kwargs)
            except TypeError:
                order_kwargs.pop("account", None)
                return self.api.Order(**order_kwargs)

    def _build_futures_order(self, *, price: float, qty: int, account: Any, side: str = "buy"):
        try:
            import shioaji as sj
        except Exception:  # noqa: BLE001
            sj = None

        action_buy = self._resolve_constant(sj, "Action", "Buy", "Buy")
        action_sell = self._resolve_constant(sj, "Action", "Sell", "Sell")
        price_type_lmt = self._resolve_constant(sj, "FuturesPriceType", "LMT", "LMT")
        order_type_rod = self._resolve_constant(sj, "OrderType", "ROD", "ROD")
        octype_auto = self._resolve_constant(sj, "FuturesOCType", "Auto", "Auto")
        order_kwargs = {
            "price": float(price),
            "quantity": int(qty),
            "action": action_sell if str(side).lower() == "sell" else action_buy,
            "price_type": price_type_lmt,
            "order_type": order_type_rod,
            "octype": octype_auto,
            "account": account,
        }
        try:
            return self.api.Order(**order_kwargs)
        except TypeError:
            order_kwargs.pop("account", None)
            return self.api.Order(**order_kwargs)

    @staticmethod
    def _extract_trade_status(trade: Any) -> str:
        if trade is None:
            return "UNKNOWN"
        status_obj = getattr(trade, "status", None)
        if status_obj is None:
            return "UNKNOWN"
        status = getattr(status_obj, "status", None)
        if status:
            return str(status)
        return str(status_obj) if status_obj is not None else "UNKNOWN"

    @staticmethod
    def _serialize(obj: Any):
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, list):
            return [ShioajiGateway._serialize(item) for item in obj]
        if isinstance(obj, tuple):
            return [ShioajiGateway._serialize(item) for item in obj]
        if isinstance(obj, dict):
            return redact_sensitive({key: ShioajiGateway._serialize(value) for key, value in obj.items()})
        if hasattr(obj, "_asdict"):
            try:
                return redact_sensitive(ShioajiGateway._serialize(obj._asdict()))
            except Exception:
                return str(obj)
        if hasattr(obj, "__dict__"):
            try:
                return redact_sensitive(ShioajiGateway._serialize(vars(obj)))
            except Exception:
                return str(obj)
        return str(obj)

    def safe_place_stock_order(
        self,
        symbol: str,
        current_price: float,
        side: str = "buy",
        quantity: int | None = None,
        use_odd_lot: bool = True,
        enforce_session_check: bool | None = None,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
        position_quantity: int | None = None,
        reference_price: float | None = None,
        pending_settlement_amount: float = 0.0,
        is_disposition_or_attention: bool = False,
        avg_volume_20: float | None = None,
        portfolio_equity: float | None = None,
        current_position_value: float = 0.0,
        daily_pnl: float = 0.0,
        max_price_deviation_ticks: int = 3,
        max_price_deviation_pct: float = 0.01,
        min_avg_volume_20: float = 500_000.0,
        max_single_position_pct: float = 0.10,
        max_daily_loss_pct: float = 0.02,
    ) -> dict[str, Any]:
        """
        Absolute capital guard (strict):
        - first check with current live balance
        - second check immediately before place_order
        """
        if self.api is None:
            self.login()

        checks: list[dict[str, Any]] = []
        normalized_side = str(side or "buy").strip().lower()
        if normalized_side not in {"buy", "sell"}:
            raise PreTradeCheckError("pre-trade check rejected: unsupported_side")

        live_unlock = validate_live_order_unlock(
            LiveOrderUnlockConfig(
                simulation=bool(self.config.simulation),
                allow_live_order=bool(self.config.allow_live_order),
                live_order_nonce=str(self.config.live_order_nonce or ""),
            )
        )
        checks.append({"name": "live_order_unlock", "passed": bool(live_unlock.accepted), "reason": live_unlock.reason})
        if not live_unlock.accepted:
            raise PreTradeCheckError(f"pre-trade check rejected: {live_unlock.reason}")

        quality = data_quality or DataQualityStatus()
        data_quality_ok = not (not self.config.simulation and quality.blocks_live_order(require_chip=require_chip))
        checks.append({"name": "data_quality", "passed": bool(data_quality_ok), "reason": quality.status})
        checks.append({"name": "data_freshness", "passed": bool(data_quality_ok), "reason": quality.freshness_status})
        if not data_quality_ok:
            raise PreTradeCheckError(f"pre-trade check rejected: {quality.status}")

        enforce_session = self.config.enforce_session_check if enforce_session_check is None else bool(enforce_session_check)
        session_ok = (not enforce_session) or self._is_trading_session()
        checks.append({"name": "trading_session", "passed": bool(session_ok), "enforced": bool(enforce_session)})
        if not session_ok:
            raise PreTradeCheckError("pre-trade check rejected: trading_session_closed")

        tick_ok = self._is_valid_tick(float(current_price))
        checks.append({"name": "tick_size", "passed": bool(tick_ok)})
        if not tick_ok:
            raise PreTradeCheckError("pre-trade check rejected: invalid_tick_size")

        if reference_price is not None and float(reference_price) > 0:
            tick_distance = self._tick_distance(float(current_price), float(reference_price))
            pct_distance = abs(float(current_price) - float(reference_price)) / float(reference_price)
            deviation_ok = tick_distance <= int(max_price_deviation_ticks) and pct_distance <= float(max_price_deviation_pct)
        else:
            tick_distance = 0
            pct_distance = 0.0
            deviation_ok = True
        checks.append(
            {
                "name": "price_deviation_guard",
                "passed": bool(deviation_ok),
                "reference_price": float(reference_price) if reference_price is not None else None,
                "tick_distance": int(tick_distance),
                "pct_distance": float(pct_distance),
            }
        )
        if not deviation_ok:
            raise PreTradeCheckError("pre-trade check rejected: price_deviation_guard")

        if not self._check_daily_order_limit():
            checks.append({"name": "daily_order_limit", "passed": False})
            raise PreTradeCheckError("pre-trade check rejected: daily_order_limit")
        checks.append({"name": "daily_order_limit", "passed": True})

        if normalized_side == "buy":
            first_available_cash = self.get_available_cash()
            settlement_adjusted_cash = float(first_available_cash) - max(0.0, float(pending_settlement_amount or 0.0))
            if quantity is not None:
                approved_qty = int(quantity)
                order_value = float(approved_qty) * float(current_price)
                estimated_total_cost = estimate_buy_total_cost(order_value)
                capital_reason = "ok" if approved_qty > 0 and estimated_total_cost <= settlement_adjusted_cash else "capital_guard_rejected"
                capital_ok = approved_qty > 0 and estimated_total_cost <= settlement_adjusted_cash
            else:
                check = enforce_absolute_capital_guard(available_cash=settlement_adjusted_cash, current_price=float(current_price))
                approved_qty = int(check.qty)
                estimated_total_cost = float(check.estimated_total_cost)
                capital_reason = str(check.reason)
                capital_ok = bool(check.accepted)
        else:
            first_available_cash = None
            settlement_adjusted_cash = None
            approved_qty = int(quantity or 0)
            estimated_total_cost = 0.0
            capital_reason = "sell_order_no_cash_required"
            capital_ok = approved_qty > 0
        checks.append(
            {
                "name": "capital_guard_first",
                "passed": bool(capital_ok),
                "available_cash": float(first_available_cash) if first_available_cash is not None else None,
                "settlement_adjusted_cash": float(settlement_adjusted_cash) if settlement_adjusted_cash is not None else None,
                "qty": int(approved_qty),
                "estimated_total_cost": float(estimated_total_cost),
                "reason": capital_reason,
            }
        )
        if not capital_ok:
            logger.error(
                "pre-trade rejected before order build: symbol=%s price=%s reason=%s cash=%s",
                symbol,
                current_price,
                capital_reason,
                first_available_cash,
            )
            raise PreTradeCheckError(f"pre-trade check rejected: {capital_reason}")

        contract = self._get_stock_contract(symbol)
        band_ok = self._is_within_price_band(contract, float(current_price))
        checks.append({"name": "price_band", "passed": bool(band_ok)})
        if not band_ok:
            raise PreTradeCheckError("pre-trade check rejected: price_out_of_band")

        limit_up = getattr(contract, "limit_up", None)
        limit_down = getattr(contract, "limit_down", None)
        limit_fillable = True
        limit_reason = "ok"
        if normalized_side == "buy" and isinstance(limit_up, (int, float)) and float(current_price) >= float(limit_up):
            limit_fillable = False
            limit_reason = "limit_up_buy_blocked"
        if normalized_side == "sell" and isinstance(limit_down, (int, float)) and float(current_price) <= float(limit_down):
            limit_fillable = False
            limit_reason = "limit_down_sell_blocked"
        checks.append({"name": "limit_up_down_fillability", "passed": bool(limit_fillable), "reason": limit_reason})
        if not limit_fillable:
            raise PreTradeCheckError(f"pre-trade check rejected: {limit_reason}")

        duplicate_ok = not self._is_duplicate_order(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        checks.append({"name": "duplicate_order", "passed": bool(duplicate_ok)})
        if not duplicate_ok:
            raise PreTradeCheckError("pre-trade check rejected: duplicate_order")

        if normalized_side == "sell":
            position_ok = position_quantity is None or int(position_quantity) >= int(approved_qty)
        else:
            position_ok = True
        checks.append(
            {
                "name": "position_quantity_for_sell",
                "passed": bool(position_ok),
                "position_quantity": int(position_quantity) if position_quantity is not None else None,
                "requested_quantity": int(approved_qty),
            }
        )
        if not position_ok:
            raise PreTradeCheckError("pre-trade check rejected: position_quantity_for_sell")

        second_available_cash = self.get_available_cash() if normalized_side == "buy" else None
        second_adjusted_cash = (
            float(second_available_cash) - max(0.0, float(pending_settlement_amount or 0.0))
            if second_available_cash is not None
            else None
        )
        second_capital_ok = normalized_side == "sell" or float(estimated_total_cost) <= float(second_adjusted_cash or 0.0)
        checks.append(
            {
                "name": "capital_guard_second",
                "passed": bool(second_capital_ok),
                "available_cash": float(second_available_cash) if second_available_cash is not None else None,
                "settlement_adjusted_cash": float(second_adjusted_cash) if second_adjusted_cash is not None else None,
                "estimated_total_cost": float(estimated_total_cost),
            }
        )
        if not second_capital_ok:
            logger.error(
                "pre-trade rejected by second balance check: symbol=%s est_cost=%.2f cash=%.2f",
                symbol,
                estimated_total_cost,
                second_available_cash,
            )
            raise PreTradeCheckError("estimated total cost exceeds live available cash")

        disposition_ok = self.config.simulation or not bool(is_disposition_or_attention)
        checks.append({"name": "disposition_attention_guard", "passed": bool(disposition_ok)})
        if not disposition_ok:
            raise PreTradeCheckError("pre-trade check rejected: disposition_attention_guard")

        liquidity_ok = self.config.simulation or avg_volume_20 is None or float(avg_volume_20) >= float(min_avg_volume_20)
        checks.append(
            {
                "name": "liquidity_guard",
                "passed": bool(liquidity_ok),
                "avg_volume_20": float(avg_volume_20) if avg_volume_20 is not None else None,
                "min_avg_volume_20": float(min_avg_volume_20),
            }
        )
        if not liquidity_ok:
            raise PreTradeCheckError("pre-trade check rejected: liquidity_guard")

        if portfolio_equity is not None and float(portfolio_equity) > 0:
            projected_position_value = max(0.0, float(current_position_value or 0.0))
            if normalized_side == "buy":
                projected_position_value += float(approved_qty) * float(current_price)
            else:
                projected_position_value = max(0.0, projected_position_value - float(approved_qty) * float(current_price))
            single_position_ok = projected_position_value <= float(portfolio_equity) * float(max_single_position_pct)
        else:
            projected_position_value = None
            single_position_ok = True
        checks.append(
            {
                "name": "single_position_limit",
                "passed": bool(single_position_ok),
                "projected_position_value": projected_position_value,
                "portfolio_equity": float(portfolio_equity) if portfolio_equity is not None else None,
            }
        )
        if not single_position_ok:
            raise PreTradeCheckError("pre-trade check rejected: single_position_limit")

        if portfolio_equity is not None and float(portfolio_equity) > 0:
            daily_loss_ok = float(daily_pnl) >= -(float(portfolio_equity) * float(max_daily_loss_pct))
        else:
            daily_loss_ok = True
        checks.append(
            {
                "name": "daily_loss_limit",
                "passed": bool(daily_loss_ok),
                "daily_pnl": float(daily_pnl),
                "portfolio_equity": float(portfolio_equity) if portfolio_equity is not None else None,
            }
        )
        if not daily_loss_ok:
            raise PreTradeCheckError("pre-trade check rejected: daily_loss_limit")

        checks.extend(
            [
                {"name": "position_limit", "passed": bool(position_ok), "reason": "checked"},
                {"name": "max_portfolio_exposure", "passed": True, "reason": "not_enforced"},
                {"name": "disposition_stock_block", "passed": bool(disposition_ok), "reason": "checked"},
                {"name": "liquidity_threshold", "passed": bool(liquidity_ok), "reason": "checked"},
            ]
        )

        account = getattr(self.api, "stock_account", None)
        if account is None:
            raise RuntimeError("stock account not found")
        order = self._build_order(
            price=float(current_price),
            qty=int(approved_qty),
            account=account,
            use_odd_lot=use_odd_lot,
            side=normalized_side,
        )

        trade = self.api.place_order(contract, order)
        self._increase_daily_order_count()
        self._mark_order_seen(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        status = self._extract_trade_status(trade)
        return {
            "passed": True,
            "symbol": self._normalize_symbol(symbol),
            "side": normalized_side,
            "price": float(current_price),
            "qty": int(approved_qty),
            "estimated_total_cost": float(estimated_total_cost),
            "available_cash_before": float(first_available_cash) if first_available_cash is not None else None,
            "available_cash_before_order": float(second_available_cash) if second_available_cash is not None else None,
            "settlement_adjusted_cash": float(second_adjusted_cash) if second_adjusted_cash is not None else None,
            "simulation": self.config.simulation,
            "order_lot": "IntradayOdd" if use_odd_lot else "Common",
            "status": status,
            "data_quality_status": quality.status,
            "pretrade_checks": checks,
            "trade": self._serialize(trade),
        }

    def safe_place_futures_order(
        self,
        symbol: str,
        current_price: float,
        side: str = "buy",
        quantity: int | None = None,
        enforce_session_check: bool | None = None,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
    ) -> dict[str, Any]:
        if self.api is None:
            self.login()

        checks: list[dict[str, Any]] = []
        normalized_side = str(side or "buy").strip().lower()
        if normalized_side not in {"buy", "sell"}:
            raise PreTradeCheckError("pre-trade check rejected: unsupported_side")

        live_unlock = validate_live_order_unlock(
            LiveOrderUnlockConfig(
                simulation=bool(self.config.simulation),
                allow_live_order=bool(self.config.allow_live_order),
                live_order_nonce=str(self.config.live_order_nonce or ""),
            )
        )
        checks.append({"name": "live_order_unlock", "passed": bool(live_unlock.accepted), "reason": live_unlock.reason})
        if not live_unlock.accepted:
            raise PreTradeCheckError(f"pre-trade check rejected: {live_unlock.reason}")

        quality = data_quality or DataQualityStatus()
        data_quality_ok = not (not self.config.simulation and quality.blocks_live_order(require_chip=require_chip))
        checks.append({"name": "data_quality", "passed": bool(data_quality_ok), "reason": quality.status})
        checks.append({"name": "data_freshness", "passed": bool(data_quality_ok), "reason": quality.freshness_status})
        if not data_quality_ok:
            raise PreTradeCheckError(f"pre-trade check rejected: {quality.status}")

        enforce_session = self.config.enforce_session_check if enforce_session_check is None else bool(enforce_session_check)
        session_ok = (not enforce_session) or self._is_trading_session()
        checks.append({"name": "trading_session", "passed": bool(session_ok), "enforced": bool(enforce_session)})
        if not session_ok:
            raise PreTradeCheckError("pre-trade check rejected: trading_session_closed")

        price_ok = float(current_price) > 0
        checks.append({"name": "tick_size", "passed": bool(price_ok), "reason": "positive_price"})
        if not price_ok:
            raise PreTradeCheckError("pre-trade check rejected: invalid_price")

        if not self._check_daily_order_limit():
            checks.append({"name": "daily_order_limit", "passed": False})
            raise PreTradeCheckError("pre-trade check rejected: daily_order_limit")
        checks.append({"name": "daily_order_limit", "passed": True})

        approved_qty = int(quantity or 0)
        quantity_ok = approved_qty > 0
        checks.append({"name": "capital_guard_first", "passed": bool(quantity_ok), "reason": "futures_margin_external"})
        if not quantity_ok:
            raise PreTradeCheckError("pre-trade check rejected: invalid_quantity")

        contract = self._get_futures_contract(symbol)
        band_ok = self._is_within_price_band(contract, float(current_price))
        checks.append({"name": "price_band", "passed": bool(band_ok)})
        if not band_ok:
            raise PreTradeCheckError("pre-trade check rejected: price_out_of_band")

        duplicate_ok = not self._is_duplicate_order(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        checks.append({"name": "duplicate_order", "passed": bool(duplicate_ok)})
        if not duplicate_ok:
            raise PreTradeCheckError("pre-trade check rejected: duplicate_order")

        checks.append({"name": "capital_guard_second", "passed": True, "reason": "futures_margin_external"})
        checks.extend(
            [
                {"name": "position_limit", "passed": True, "reason": "not_enforced"},
                {"name": "max_portfolio_exposure", "passed": True, "reason": "not_enforced"},
                {"name": "disposition_stock_block", "passed": True, "reason": "not_applicable"},
                {"name": "liquidity_threshold", "passed": True, "reason": "not_enforced"},
            ]
        )

        account = getattr(self.api, "futopt_account", None) or getattr(self.api, "futures_account", None)
        if account is None:
            raise RuntimeError("futures account not found")
        order = self._build_futures_order(
            price=float(current_price),
            qty=int(approved_qty),
            account=account,
            side=normalized_side,
        )
        trade = self.api.place_order(contract, order)
        self._increase_daily_order_count()
        self._mark_order_seen(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        status = self._extract_trade_status(trade)
        return {
            "passed": True,
            "symbol": self._normalize_symbol(symbol),
            "side": normalized_side,
            "price": float(current_price),
            "qty": int(approved_qty),
            "estimated_total_cost": 0.0,
            "available_cash_before": None,
            "available_cash_before_order": None,
            "simulation": self.config.simulation,
            "order_lot": "Futures",
            "status": status,
            "data_quality_status": quality.status,
            "pretrade_checks": checks,
            "trade": self._serialize(trade),
        }

    def execute_intent(
        self,
        intent: OrderIntent,
        *,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
    ) -> ExecutionResult:
        try:
            metadata = dict(intent.metadata or {})
            if str(intent.metadata.get("instrument_type") or "").lower() == "futures":
                payload = self.safe_place_futures_order(
                    symbol=intent.symbol,
                    current_price=float(intent.price),
                    side=intent.side,
                    quantity=int(intent.quantity) if intent.quantity is not None else None,
                    data_quality=data_quality,
                    require_chip=require_chip,
                )
            else:
                payload = self.safe_place_stock_order(
                    symbol=intent.symbol,
                    current_price=float(intent.price),
                    side=intent.side,
                    quantity=int(intent.quantity) if intent.quantity is not None else None,
                    use_odd_lot=(intent.order_lot == "IntradayOdd"),
                    data_quality=data_quality,
                    require_chip=require_chip,
                    position_quantity=self._optional_int(metadata.get("position_quantity")),
                    reference_price=self._optional_float(metadata.get("reference_price")),
                    pending_settlement_amount=float(metadata.get("pending_settlement_amount") or 0.0),
                    is_disposition_or_attention=bool(metadata.get("is_disposition_or_attention", False)),
                    avg_volume_20=self._optional_float(metadata.get("avg_volume_20")),
                    portfolio_equity=self._optional_float(metadata.get("portfolio_equity")),
                    current_position_value=float(metadata.get("current_position_value") or 0.0),
                    daily_pnl=float(metadata.get("daily_pnl") or 0.0),
                )
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=True,
                reason="ok",
                checks=list(payload.get("pretrade_checks") or []),
                approved_quantity=int(payload.get("qty") or 0),
                estimated_total_cost=float(payload.get("estimated_total_cost") or 0.0),
                available_cash_before=float(payload.get("available_cash_before") or 0.0),
                data_quality_status=str(payload.get("data_quality_status") or "unknown"),
            )
            return ExecutionResult(
                intent_id=intent.intent_id,
                accepted=True,
                executed=True,
                environment="live" if intent.environment == "live" else "simulation",
                symbol=str(payload.get("symbol") or intent.symbol),
                side=str(payload.get("side") or intent.side),
                price=float(payload.get("price") or intent.price),
                quantity=int(payload.get("qty") or 0),
                status=str(payload.get("status") or "submitted"),
                broker_order_id=None,
                raw_trade=payload.get("trade"),
                pretrade=pretrade,
            )
        except PreTradeCheckError as exc:
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=False,
                reason=str(exc),
                checks=[{"name": "pre_trade", "passed": False, "reason": str(exc)}],
                data_quality_status=(data_quality or DataQualityStatus()).status,
            )
            return ExecutionResult(
                intent_id=intent.intent_id,
                accepted=False,
                executed=False,
                environment="live" if intent.environment == "live" else "simulation",
                symbol=intent.symbol,
                side=intent.side,
                price=float(intent.price),
                quantity=int(intent.quantity or 0),
                status="rejected",
                broker_order_id=None,
                raw_trade=None,
                pretrade=pretrade,
            )

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    def execute_stock_order_in_thread(
        self,
        symbol: str,
        current_price: float,
        use_odd_lot: bool = True,
        timeout_sec: int | None = None,
        enforce_session_check: bool | None = None,
    ) -> dict[str, Any]:
        """
        Execute one guarded order in a dedicated thread.
        If pre-trade check fails, log error and end that thread immediately.
        """
        result_box: dict[str, Any] = {}
        timeout = int(timeout_sec or self.config.order_timeout_sec)

        def _worker() -> None:
            try:
                result_box["result"] = self.safe_place_stock_order(
                    symbol=symbol,
                    current_price=float(current_price),
                    use_odd_lot=use_odd_lot,
                    enforce_session_check=enforce_session_check,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("order thread aborted: symbol=%s error=%s", symbol, exc)
                result_box["error"] = exc

        thread = threading.Thread(target=_worker, name=f"shioaji-order-{self._normalize_symbol(symbol)}", daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            raise TimeoutError(f"order thread timeout (> {timeout}s)")
        if "error" in result_box:
            raise result_box["error"]
        return result_box["result"]
