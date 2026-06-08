from __future__ import annotations

from datetime import datetime
from typing import Any

from app.backtest.sizer_absolute_capital import AbsoluteSizerConfig, compute_order_size
from app.broker.pre_trade_check import LiveOrderUnlockConfig, validate_live_order_unlock
from app.execution.models import OrderIntent, PreTradeDecision
from app.market_data import DataQualityStatus


class PreTradeCheckService:
    def __init__(
        self,
        *,
        max_daily_orders: int = 20,
        duplicate_window_sec: int = 10,
        sizer_config: AbsoluteSizerConfig | None = None,
    ):
        self.max_daily_orders = max(1, int(max_daily_orders))
        self.duplicate_window_sec = max(1, int(duplicate_window_sec))
        self.sizer_config = sizer_config

    def evaluate(
        self,
        intent: OrderIntent,
        *,
        available_cash: float | None = None,
        data_quality: DataQualityStatus | None = None,
        live_order_allowed: bool = False,
        live_order_nonce: str = "",
        require_chip: bool = False,
        extra_checks: list[dict[str, Any]] | None = None,
    ) -> PreTradeDecision:
        checks: list[dict[str, Any]] = []
        quality = data_quality or DataQualityStatus()

        unlock = validate_live_order_unlock(
            LiveOrderUnlockConfig(
                simulation=intent.environment != "live",
                allow_live_order=bool(live_order_allowed),
                live_order_nonce=str(live_order_nonce or ""),
            )
        )
        checks.append({"name": "live_order_unlock", "passed": bool(unlock.accepted), "reason": unlock.reason})
        if not unlock.accepted:
            return self._reject(intent, unlock.reason, checks, quality)

        data_quality_ok = not (intent.environment == "live" and quality.blocks_live_order(require_chip=require_chip))
        checks.append({"name": "data_quality", "passed": data_quality_ok, "reason": quality.status})
        checks.append({"name": "data_freshness", "passed": data_quality_ok, "reason": quality.freshness_status})
        if not data_quality_ok:
            return self._reject(intent, quality.status, checks, quality)

        if intent.side != "buy":
            checks.append({"name": "side_supported", "passed": False, "reason": "unsupported_side"})
            return self._reject(intent, "unsupported_side", checks, quality)

        cash = float(available_cash or 0.0)
        sizing = compute_order_size(available_cash=cash, current_price=float(intent.price), config=self.sizer_config)
        accepted = bool(sizing.get("accepted"))
        qty = int(intent.quantity or sizing.get("qty", 0) or 0)
        estimated_total_cost = float(sizing.get("estimated_total_cost", 0.0) or 0.0)
        if intent.quantity is not None and qty > 0:
            order_value = float(qty) * float(intent.price)
            sizing_for_requested = compute_order_size(
                available_cash=cash,
                current_price=float(intent.price),
                config=AbsoluteSizerConfig(
                    min_trade_value=0.0,
                    max_allocation_per_trade=max(order_value, 0.0),
                    fee_rate=(self.sizer_config or AbsoluteSizerConfig()).fee_rate,
                    min_fee=(self.sizer_config or AbsoluteSizerConfig()).min_fee,
                ),
            )
            estimated_total_cost = float(sizing_for_requested.get("estimated_total_cost", 0.0) or 0.0)
            accepted = estimated_total_cost <= cash and estimated_total_cost > 0

        checks.append(
            {
                "name": "capital_guard_first",
                "passed": bool(accepted),
                "reason": "ok" if accepted else str(sizing.get("reason", "capital_guard_rejected")),
                "available_cash": cash,
                "qty": qty,
                "estimated_total_cost": estimated_total_cost,
            }
        )
        if extra_checks:
            checks.extend(extra_checks)
        if not accepted:
            return self._reject(intent, str(sizing.get("reason", "capital_guard_rejected")), checks, quality)

        return PreTradeDecision(
            intent_id=intent.intent_id,
            accepted=True,
            reason="ok",
            checks=checks,
            approved_quantity=qty,
            estimated_total_cost=estimated_total_cost,
            available_cash_before=cash,
            data_quality_status=quality.status,
            decided_at=datetime.utcnow(),
        )

    @staticmethod
    def _reject(
        intent: OrderIntent,
        reason: str,
        checks: list[dict[str, Any]],
        quality: DataQualityStatus,
    ) -> PreTradeDecision:
        return PreTradeDecision(
            intent_id=intent.intent_id,
            accepted=False,
            reason=str(reason),
            checks=checks,
            approved_quantity=0,
            estimated_total_cost=0.0,
            available_cash_before=None,
            data_quality_status=quality.status,
            decided_at=datetime.utcnow(),
        )
