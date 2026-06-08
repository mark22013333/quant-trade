from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.security import redact_sensitive


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    cash_diff: float
    position_diffs: list[dict[str, Any]]
    open_order_diffs: list[dict[str, Any]]
    blocking_reasons: list[str]
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checked_at"] = self.checked_at.isoformat()
        return redact_sensitive(payload)


class ReconciliationService:
    def __init__(self, *, repository: Any | None = None):
        self.repository = repository

    def reconcile(
        self,
        *,
        expected_cash: float,
        actual_cash: float | None,
        expected_positions: dict[str, int],
        actual_positions: dict[str, int],
        expected_open_orders: list[dict[str, Any]] | None = None,
        actual_open_orders: list[dict[str, Any]] | None = None,
        cash_tolerance: float = 1.0,
    ) -> ReconciliationResult:
        cash_diff = float(actual_cash or 0.0) - float(expected_cash)
        position_diffs = []
        symbols = set(expected_positions) | set(actual_positions)
        for symbol in sorted(symbols):
            expected = int(expected_positions.get(symbol, 0))
            actual = int(actual_positions.get(symbol, 0))
            if expected != actual:
                position_diffs.append({"symbol": symbol, "expected": expected, "actual": actual})

        expected_orders = expected_open_orders or []
        actual_orders = actual_open_orders or []
        open_order_diffs = []
        if len(expected_orders) != len(actual_orders):
            open_order_diffs.append(
                {"name": "open_order_count", "expected": len(expected_orders), "actual": len(actual_orders)}
            )

        blockers = []
        if abs(cash_diff) > float(cash_tolerance):
            blockers.append("cash_diff_exceeded")
        if position_diffs:
            blockers.append("position_diff")
        if open_order_diffs:
            blockers.append("open_order_diff")
        result = ReconciliationResult(
            matched=not blockers,
            cash_diff=cash_diff,
            position_diffs=position_diffs,
            open_order_diffs=open_order_diffs,
            blocking_reasons=blockers,
        )
        if self.repository is not None and hasattr(self.repository, "add_reconciliation_record"):
            self.repository.add_reconciliation_record(result=result.to_dict())
        return result
