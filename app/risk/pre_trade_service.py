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
        position_quantity: int | None = None,
        reference_price: float | None = None,
        pending_settlement_amount: float = 0.0,
        is_disposition_or_attention: bool = False,
        avg_volume_20: float | None = None,
        portfolio_equity: float | None = None,
        current_position_value: float = 0.0,
        daily_pnl: float = 0.0,
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

        if intent.side not in {"buy", "sell"}:
            checks.append({"name": "side_supported", "passed": False, "reason": "unsupported_side"})
            return self._reject(intent, "unsupported_side", checks, quality)

        cash = float(available_cash or 0.0)
        adjusted_cash = cash - max(0.0, float(pending_settlement_amount or 0.0))
        if reference_price is not None and float(reference_price) > 0:
            price_deviation_ok = abs(float(intent.price) - float(reference_price)) / float(reference_price) <= 0.01
        else:
            price_deviation_ok = True
        checks.append(
            {
                "name": "price_deviation_guard",
                "passed": bool(price_deviation_ok),
                "reference_price": float(reference_price) if reference_price is not None else None,
            }
        )
        if not price_deviation_ok:
            return self._reject(intent, "price_deviation_guard", checks, quality)

        if intent.side == "sell":
            qty = int(intent.quantity or 0)
            estimated_total_cost = 0.0
            accepted = qty > 0
            reason = "sell_order_no_cash_required" if accepted else "invalid_quantity"
        else:
            sizing = compute_order_size(available_cash=adjusted_cash, current_price=float(intent.price), config=self.sizer_config)
            accepted = bool(sizing.get("accepted"))
            qty = int(intent.quantity or sizing.get("qty", 0) or 0)
            estimated_total_cost = float(sizing.get("estimated_total_cost", 0.0) or 0.0)
            reason = str(sizing.get("reason", "capital_guard_rejected"))
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
                accepted = estimated_total_cost <= adjusted_cash and estimated_total_cost > 0
                reason = "ok" if accepted else "capital_guard_rejected"

        checks.append(
            {
                "name": "capital_guard_first",
                "passed": bool(accepted),
                "reason": "ok" if accepted and intent.side == "buy" else reason,
                "available_cash": cash,
                "settlement_adjusted_cash": adjusted_cash if intent.side == "buy" else None,
                "qty": qty,
                "estimated_total_cost": estimated_total_cost,
            }
        )
        if extra_checks:
            checks.extend(extra_checks)
        if not accepted:
            return self._reject(intent, reason, checks, quality)

        if intent.side == "sell":
            position_ok = position_quantity is None or int(position_quantity) >= int(qty)
        else:
            position_ok = True
        checks.append(
            {
                "name": "position_quantity_for_sell",
                "passed": bool(position_ok),
                "position_quantity": int(position_quantity) if position_quantity is not None else None,
            }
        )
        if not position_ok:
            return self._reject(intent, "position_quantity_for_sell", checks, quality)

        disposition_ok = intent.environment != "live" or not bool(is_disposition_or_attention)
        checks.append({"name": "disposition_attention_guard", "passed": bool(disposition_ok)})
        if not disposition_ok:
            return self._reject(intent, "disposition_attention_guard", checks, quality)

        liquidity_ok = intent.environment != "live" or avg_volume_20 is None or float(avg_volume_20) >= 500_000.0
        checks.append({"name": "liquidity_guard", "passed": bool(liquidity_ok)})
        if not liquidity_ok:
            return self._reject(intent, "liquidity_guard", checks, quality)

        if portfolio_equity is not None and float(portfolio_equity) > 0:
            projected = max(0.0, float(current_position_value or 0.0))
            if intent.side == "buy":
                projected += float(qty) * float(intent.price)
            single_position_ok = projected <= float(portfolio_equity) * 0.10
            daily_loss_ok = float(daily_pnl) >= -(float(portfolio_equity) * 0.02)
        else:
            projected = None
            single_position_ok = True
            daily_loss_ok = True
        checks.append({"name": "single_position_limit", "passed": bool(single_position_ok), "projected_position_value": projected})
        if not single_position_ok:
            return self._reject(intent, "single_position_limit", checks, quality)
        checks.append({"name": "daily_loss_limit", "passed": bool(daily_loss_ok), "daily_pnl": float(daily_pnl)})
        if not daily_loss_ok:
            return self._reject(intent, "daily_loss_limit", checks, quality)

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
