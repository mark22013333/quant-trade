from __future__ import annotations

from typing import Any

from app.broker.pre_trade_check import PreTradeCheckError
from app.broker.shioaji_gateway import ShioajiGateway
from app.execution.models import ExecutionResult, OrderIntent, PreTradeDecision
from app.market_data import DataQualityStatus
from app.security import redact_sensitive


class TradingExecutionService:
    def __init__(
        self,
        gateway: ShioajiGateway | None = None,
        repository: Any | None = None,
        preview_service: Any | None = None,
    ):
        self.gateway = gateway
        self.repository = repository
        self.preview_service = preview_service
        self.records: list[dict[str, Any]] = []

    def execute_intent(
        self,
        intent: OrderIntent,
        *,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
    ) -> ExecutionResult:
        gate_rejection = self._validate_live_gate(intent)
        if gate_rejection is not None:
            return gate_rejection
        metadata = dict(intent.metadata or {})

        if self.gateway is None:
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=False,
                reason="execution_gateway_missing",
                checks=[{"name": "execution_gateway", "passed": False, "reason": "missing"}],
                data_quality_status="unknown",
            )
            return self._record_result(intent, pretrade, executed=False, status="rejected", raw_trade=None)

        if hasattr(self.gateway, "execute_intent"):
            result = self.gateway.execute_intent(intent, data_quality=data_quality, require_chip=require_chip)
            self._persist_completed_result(intent, result)
            return result

        try:
            payload = self.gateway.safe_place_stock_order(
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
                current_portfolio_exposure=self._metadata_portfolio_exposure(metadata),
                daily_pnl=float(metadata.get("daily_pnl") or 0.0),
                max_single_position_pct=self._positive_float_or_default(
                    metadata.get("max_single_position_pct"),
                    0.10,
                ),
                max_portfolio_exposure_pct=self._positive_float_or_default(
                    metadata.get("max_portfolio_exposure_pct"),
                    0.95,
                ),
                max_daily_loss_pct=self._positive_float_or_default(
                    metadata.get("max_daily_loss_pct"),
                    0.02,
                ),
            )
            checks = list(payload.get("pretrade_checks") or [])
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=True,
                reason="ok",
                checks=checks,
                approved_quantity=int(payload.get("qty") or intent.quantity or 0),
                estimated_total_cost=float(payload.get("estimated_total_cost") or 0.0),
                available_cash_before=float(payload.get("available_cash_before") or 0.0),
                data_quality_status="unknown",
            )
            return self._record_result(
                intent,
                pretrade,
                executed=True,
                status=str(payload.get("status") or "submitted"),
                raw_trade=payload.get("trade"),
            )
        except PreTradeCheckError as exc:
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=False,
                reason=str(exc),
                checks=[{"name": "pre_trade", "passed": False, "reason": str(exc)}],
                data_quality_status="unknown",
            )
            return self._record_result(intent, pretrade, executed=False, status="rejected", raw_trade=None)
        except Exception as exc:  # noqa: BLE001
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=False,
                reason="execution_error",
                checks=[{"name": "execution", "passed": False, "reason": type(exc).__name__}],
                data_quality_status="unknown",
            )
            return self._record_result(intent, pretrade, executed=False, status="error", raw_trade={"error": str(exc)})

    def reject_intent(
        self,
        intent: OrderIntent,
        reason: str,
        *,
        checks: list[dict[str, Any]] | None = None,
    ) -> ExecutionResult:
        return self._live_rejected(intent, reason, checks=checks)

    def _validate_live_gate(self, intent: OrderIntent) -> ExecutionResult | None:
        if intent.environment != "live":
            return None
        metadata = dict(intent.metadata or {})
        preview_id = str(metadata.get("preview_id") or "")
        strategy_version = str(metadata.get("strategy_version") or "")
        manual_confirmed = bool(metadata.get("manual_confirmed", False))
        promotion_gate_accepted = bool(metadata.get("promotion_gate_accepted", False))
        if not preview_id:
            return self._live_rejected(intent, "preview_required")
        if not strategy_version or not intent.strategy_name or not intent.signal_id:
            return self._live_rejected(intent, "live_metadata_required")
        if not promotion_gate_accepted:
            return self._live_rejected(intent, "promotion_gate_required")
        if self.preview_service is not None and hasattr(self.preview_service, "approve"):
            decision = self.preview_service.approve(
                preview_id=preview_id,
                intent=intent,
                manual_confirmed=manual_confirmed,
                repository=self.repository,
            )
            if not bool(decision.accepted):
                return self._live_rejected(intent, str(decision.reason))
        elif not manual_confirmed:
            return self._live_rejected(intent, "manual_confirmation_required")
        position_rejection = self._validate_live_sell_position(intent, metadata)
        if position_rejection is not None:
            return position_rejection
        trading_restriction = self._validate_live_trading_restrictions(intent, metadata)
        if trading_restriction is not None:
            return trading_restriction
        price_rejection = self._validate_live_price_deviation(intent, metadata)
        if price_rejection is not None:
            return price_rejection
        portfolio_risk_rejection = self._validate_live_portfolio_risk(intent, metadata)
        if portfolio_risk_rejection is not None:
            return portfolio_risk_rejection
        return None

    def _validate_live_sell_position(self, intent: OrderIntent, metadata: dict[str, Any]) -> ExecutionResult | None:
        if str(intent.side).lower() != "sell":
            return None
        quantity = int(intent.quantity or 0)
        requested_shares = self.stock_order_quantity_to_shares(quantity, intent.order_lot)
        position_quantity = self._optional_int(metadata.get("position_quantity"))
        sellable_quantity = self._optional_int(metadata.get("sellable_quantity"))
        effective_quantity = sellable_quantity if sellable_quantity is not None else position_quantity
        if effective_quantity is None:
            check = {
                "name": "position_quantity_for_sell",
                "passed": False,
                "reason": "position_quantity_required_for_sell",
                "quantity": int(quantity),
                "requested_shares": int(requested_shares),
                "position_quantity": None,
                "sellable_quantity": None,
            }
            return self._live_rejected(intent, "position_quantity_required_for_sell", checks=[check])
        passed = requested_shares > 0 and int(effective_quantity) >= requested_shares
        check = {
            "name": "position_quantity_for_sell",
            "passed": bool(passed),
            "quantity": int(quantity),
            "requested_shares": int(requested_shares),
            "position_quantity": int(position_quantity) if position_quantity is not None else None,
            "sellable_quantity": int(sellable_quantity) if sellable_quantity is not None else None,
            "effective_quantity": int(effective_quantity),
            "effective_quantity_source": "sellable_quantity" if sellable_quantity is not None else "position_quantity",
        }
        if passed:
            return None
        reason = "sellable_quantity_for_sell" if sellable_quantity is not None else "position_quantity_for_sell"
        return self._live_rejected(intent, reason, checks=[check])

    def _validate_live_trading_restrictions(self, intent: OrderIntent, metadata: dict[str, Any]) -> ExecutionResult | None:
        disposition_active = self._metadata_bool(metadata.get("disposition_active"))
        attention_active = self._metadata_bool(metadata.get("attention_active"))
        is_restricted = (
            self._metadata_bool(metadata.get("is_disposition_or_attention"))
            or disposition_active
            or attention_active
        )
        if is_restricted:
            check = {
                "name": "disposition_attention_guard",
                "passed": False,
                "disposition_active": bool(disposition_active),
                "attention_active": bool(attention_active),
                "periods": metadata.get("disposition_periods") or [],
                "attention_records": metadata.get("attention_records") or [],
            }
            return self._live_rejected(intent, "disposition_attention_guard", checks=[check])

        current_price = float(intent.price)
        limit_down = self._optional_float(metadata.get("limit_down_price") or metadata.get("limit_down"))
        limit_up = self._optional_float(metadata.get("limit_up_price") or metadata.get("limit_up"))
        if limit_down is not None and current_price < float(limit_down):
            check = {
                "name": "price_band",
                "passed": False,
                "reason": "price_below_limit_down",
                "current_price": current_price,
                "limit_down": float(limit_down),
                "limit_up": float(limit_up) if limit_up is not None else None,
            }
            return self._live_rejected(intent, "price_out_of_band", checks=[check])
        if limit_up is not None and current_price > float(limit_up):
            check = {
                "name": "price_band",
                "passed": False,
                "reason": "price_above_limit_up",
                "current_price": current_price,
                "limit_down": float(limit_down) if limit_down is not None else None,
                "limit_up": float(limit_up),
            }
            return self._live_rejected(intent, "price_out_of_band", checks=[check])

        normalized_side = str(intent.side).lower()
        limit_reason = ""
        if normalized_side == "buy" and limit_up is not None and current_price >= float(limit_up):
            limit_reason = "limit_up_buy_blocked"
        elif normalized_side == "sell" and limit_down is not None and current_price <= float(limit_down):
            limit_reason = "limit_down_sell_blocked"
        if limit_reason:
            check = {
                "name": "limit_up_down_fillability",
                "passed": False,
                "reason": limit_reason,
                "current_price": current_price,
                "limit_down": float(limit_down) if limit_down is not None else None,
                "limit_up": float(limit_up) if limit_up is not None else None,
            }
            return self._live_rejected(intent, limit_reason, checks=[check])
        return None

    def _validate_live_price_deviation(self, intent: OrderIntent, metadata: dict[str, Any]) -> ExecutionResult | None:
        reference_price = self._optional_float(metadata.get("reference_price"))
        if reference_price is None or reference_price <= 0:
            return None
        current_price = float(intent.price)
        tick_distance = self._tw_tick_distance(current_price, reference_price)
        pct_distance = abs(current_price - reference_price) / reference_price
        default_ticks, default_pct = self.default_price_deviation_limits(intent.order_lot)
        max_ticks_value = self._optional_int(metadata.get("max_price_deviation_ticks"))
        max_pct_value = self._optional_float(metadata.get("max_price_deviation_pct"))
        max_ticks = int(max_ticks_value) if max_ticks_value is not None else default_ticks
        max_pct = float(max_pct_value) if max_pct_value is not None else default_pct
        passed = tick_distance <= max_ticks and pct_distance <= max_pct
        check = {
            "name": "price_deviation_guard",
            "passed": bool(passed),
            "reference_price": float(reference_price),
            "current_price": float(current_price),
            "tick_distance": int(tick_distance),
            "max_ticks": int(max_ticks),
            "pct_distance": float(pct_distance),
            "max_pct": float(max_pct),
        }
        if passed:
            return None
        return self._live_rejected(intent, "price_deviation_guard", checks=[check])

    def _validate_live_portfolio_risk(self, intent: OrderIntent, metadata: dict[str, Any]) -> ExecutionResult | None:
        portfolio_equity = self._optional_float(metadata.get("portfolio_equity"))
        if portfolio_equity is None or portfolio_equity <= 0:
            return None

        quantity = max(0, int(intent.quantity or 0))
        share_quantity = self.stock_order_quantity_to_shares(quantity, intent.order_lot)
        order_value = float(share_quantity) * max(0.0, float(intent.price))
        normalized_side = str(intent.side or "").strip().lower()

        max_single_position_pct = self._positive_float_or_default(metadata.get("max_single_position_pct"), 0.10)
        current_position_value = self._optional_float(metadata.get("current_position_value"))
        if current_position_value is None:
            current_position_value = 0.0
        projected_position_value = max(0.0, float(current_position_value))
        if normalized_side == "buy":
            projected_position_value += order_value
        elif normalized_side == "sell":
            projected_position_value = max(0.0, projected_position_value - order_value)
        single_position_limit = float(portfolio_equity) * float(max_single_position_pct)
        single_position_ok = projected_position_value <= single_position_limit
        if not single_position_ok:
            check = {
                "name": "single_position_limit",
                "passed": False,
                "portfolio_equity": float(portfolio_equity),
                "current_position_value": float(current_position_value),
                "projected_position_value": float(projected_position_value),
                "max_single_position_pct": float(max_single_position_pct),
                "limit_value": float(single_position_limit),
                "order_value": float(order_value),
            }
            return self._live_rejected(intent, "single_position_limit", checks=[check])

        max_daily_loss_pct = self._positive_float_or_default(metadata.get("max_daily_loss_pct"), 0.02)
        daily_pnl = self._optional_float(metadata.get("daily_pnl"))
        if daily_pnl is not None:
            daily_loss_floor = -(float(portfolio_equity) * float(max_daily_loss_pct))
            daily_loss_ok = float(daily_pnl) >= daily_loss_floor
            if not daily_loss_ok:
                check = {
                    "name": "daily_loss_limit",
                    "passed": False,
                    "portfolio_equity": float(portfolio_equity),
                    "daily_pnl": float(daily_pnl),
                    "max_daily_loss_pct": float(max_daily_loss_pct),
                    "daily_loss_floor": float(daily_loss_floor),
                }
                return self._live_rejected(intent, "daily_loss_limit", checks=[check])

        current_exposure = self._metadata_portfolio_exposure(metadata)
        if current_exposure is None:
            return None
        max_portfolio_exposure_pct = self._positive_float_or_default(
            metadata.get("max_portfolio_exposure_pct"),
            0.95,
        )
        projected_exposure = max(0.0, float(current_exposure))
        if normalized_side == "buy":
            projected_exposure += order_value
        elif normalized_side == "sell":
            projected_exposure = max(0.0, projected_exposure - order_value)
        max_exposure_value = float(portfolio_equity) * float(max_portfolio_exposure_pct)
        exposure_ok = projected_exposure <= max_exposure_value
        if exposure_ok:
            return None
        check = {
            "name": "max_portfolio_exposure",
            "passed": False,
            "portfolio_equity": float(portfolio_equity),
            "current_portfolio_exposure": float(current_exposure),
            "projected_portfolio_exposure": float(projected_exposure),
            "max_portfolio_exposure_pct": float(max_portfolio_exposure_pct),
            "limit_value": float(max_exposure_value),
            "order_value": float(order_value),
        }
        return self._live_rejected(intent, "max_portfolio_exposure", checks=[check])

    @staticmethod
    def default_price_deviation_limits(order_lot: str) -> tuple[int, float]:
        if str(order_lot or "").strip() == "Common":
            return 2, 0.005
        return 3, 0.01

    @staticmethod
    def stock_order_quantity_to_shares(quantity: int | float | None, order_lot: str) -> int:
        qty = max(0, int(quantity or 0))
        return qty * 1000 if str(order_lot or "").strip() == "Common" else qty

    def _live_rejected(self, intent: OrderIntent, reason: str, checks: list[dict[str, Any]] | None = None) -> ExecutionResult:
        pretrade = PreTradeDecision(
            intent_id=intent.intent_id,
            accepted=False,
            reason=str(reason),
            checks=checks or [{"name": "live_preview_gate", "passed": False, "reason": str(reason)}],
            data_quality_status="unknown",
        )
        return self._record_result(intent, pretrade, executed=False, status="rejected", raw_trade=None)

    def _persist_completed_result(self, intent: OrderIntent, result: ExecutionResult) -> None:
        record = {"intent": intent.to_dict(), "result": result.to_dict()}
        self.records.append(record)
        if self.repository is not None and hasattr(self.repository, "add_trading_execution_record"):
            self.repository.add_trading_execution_record(intent=record["intent"], result=record["result"])

    def _record_result(
        self,
        intent: OrderIntent,
        pretrade: PreTradeDecision,
        *,
        executed: bool,
        status: str,
        raw_trade: dict[str, Any] | None,
    ) -> ExecutionResult:
        result = ExecutionResult(
            intent_id=intent.intent_id,
            accepted=bool(pretrade.accepted),
            executed=bool(executed),
            environment="live" if intent.environment == "live" else "simulation",
            symbol=intent.symbol,
            side=intent.side,
            price=float(intent.price),
            quantity=int(pretrade.approved_quantity or intent.quantity or 0),
            status=str(status),
            broker_order_id=self._extract_broker_order_id(raw_trade),
            raw_trade=redact_sensitive(raw_trade) if raw_trade is not None else None,
            pretrade=pretrade,
        )
        record = {"intent": intent.to_dict(), "result": result.to_dict()}
        self.records.append(record)
        if self.repository is not None and hasattr(self.repository, "add_trading_execution_record"):
            self.repository.add_trading_execution_record(intent=record["intent"], result=record["result"])
        return result

    @staticmethod
    def _extract_broker_order_id(raw_trade: dict[str, Any] | None) -> str | None:
        if not isinstance(raw_trade, dict):
            return None
        for key in ("order_id", "id", "broker_order_id"):
            value = raw_trade.get(key)
            if value:
                return str(value)
        order = raw_trade.get("order")
        if isinstance(order, dict):
            value = order.get("id") or order.get("order_id")
            if value:
                return str(value)
        return None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _metadata_portfolio_exposure(self, metadata: dict[str, Any]) -> float | None:
        exposure = self._optional_float(metadata.get("current_portfolio_exposure"))
        if exposure is None:
            exposure = self._optional_float(metadata.get("portfolio_exposure"))
        return exposure

    def _positive_float_or_default(self, value: Any, default: float) -> float:
        parsed = self._optional_float(value)
        if parsed is None or parsed <= 0:
            return float(default)
        return float(parsed)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _metadata_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "active"}

    @staticmethod
    def _tw_tick_size(price: float) -> float:
        p = abs(float(price))
        if p < 10:
            return 0.01
        if p < 50:
            return 0.05
        if p < 100:
            return 0.1
        if p < 500:
            return 0.5
        if p < 1000:
            return 1.0
        return 5.0

    @classmethod
    def _tw_tick_distance(cls, left: float, right: float) -> int:
        low = min(float(left), float(right))
        high = max(float(left), float(right))
        if high <= 0 or low <= 0:
            return 0
        ticks = 0
        cursor = low
        while cursor < high and ticks < 1_000_000:
            cursor += cls._tw_tick_size(cursor)
            ticks += 1
        return ticks
