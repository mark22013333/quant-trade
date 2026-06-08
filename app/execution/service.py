from __future__ import annotations

from typing import Any

from app.broker.pre_trade_check import PreTradeCheckError
from app.broker.shioaji_gateway import ShioajiGateway
from app.execution.models import ExecutionResult, OrderIntent, PreTradeDecision
from app.market_data import DataQualityStatus
from app.security import redact_sensitive


class TradingExecutionService:
    def __init__(self, gateway: ShioajiGateway | None = None, repository: Any | None = None):
        self.gateway = gateway
        self.repository = repository
        self.records: list[dict[str, Any]] = []

    def execute_intent(
        self,
        intent: OrderIntent,
        *,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
    ) -> ExecutionResult:
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
