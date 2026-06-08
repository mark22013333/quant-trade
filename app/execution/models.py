from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.security import redact_sensitive


OrderSource = Literal["web", "cli", "scheduler", "live_trader", "paper", "test"]
OrderEnvironment = Literal["simulation", "live"]
ExecutionEnvironment = Literal["simulation", "live", "paper"]
OrderSide = Literal["buy", "sell"]
OrderLot = Literal["IntradayOdd", "Common"]


def normalize_symbol(symbol: str) -> str:
    code = str(symbol or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    price: float
    source: OrderSource = "test"
    environment: OrderEnvironment = "simulation"
    quantity: int | None = None
    order_lot: OrderLot = "IntradayOdd"
    strategy_name: str | None = None
    signal_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    intent_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "price", float(self.price))
        if self.quantity is not None:
            object.__setattr__(self, "quantity", int(self.quantity))
        object.__setattr__(self, "metadata", redact_sensitive(dict(self.metadata or {})))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return redact_sensitive(payload)


@dataclass(frozen=True)
class PreTradeDecision:
    intent_id: str
    accepted: bool
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    approved_quantity: int = 0
    estimated_total_cost: float = 0.0
    available_cash_before: float | None = None
    data_quality_status: str = "unknown"
    decided_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decided_at"] = self.decided_at.isoformat()
        return redact_sensitive(payload)


@dataclass(frozen=True)
class ExecutionResult:
    intent_id: str
    accepted: bool
    executed: bool
    environment: ExecutionEnvironment
    symbol: str
    side: str
    price: float
    quantity: int
    status: str
    broker_order_id: str | None
    raw_trade: dict[str, Any] | None
    pretrade: PreTradeDecision
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["pretrade"] = self.pretrade.to_dict()
        payload["raw_trade"] = redact_sensitive(payload.get("raw_trade"))
        return redact_sensitive(payload)
