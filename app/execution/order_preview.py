from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.execution.models import OrderIntent
from app.security import redact_sensitive


@dataclass(frozen=True)
class OrderPreview:
    strategy_name: str
    strategy_version: str
    signal_id: str
    symbol: str
    side: str
    price: float
    quantity: int
    estimated_total_cost: float
    available_cash: float | None
    position_before: int
    checks: list[dict[str, Any]]
    preview_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(seconds=120))
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["expires_at"] = self.expires_at.isoformat()
        payload["created_at"] = self.created_at.isoformat()
        return redact_sensitive(payload)


@dataclass(frozen=True)
class PreviewDecision:
    preview_id: str
    accepted: bool
    reason: str
    approved_intent: OrderIntent | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["approved_intent"] = self.approved_intent.to_dict() if self.approved_intent else None
        return redact_sensitive(payload)


class OrderPreviewService:
    def __init__(self, *, ttl_seconds: int = 120, repository: Any | None = None):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.repository = repository
        self._previews: dict[str, OrderPreview] = {}

    def create_preview(
        self,
        *,
        intent: OrderIntent,
        estimated_total_cost: float,
        available_cash: float | None,
        position_before: int,
        checks: list[dict[str, Any]] | None = None,
        strategy_version: str | None = None,
        signal_id: str | None = None,
        repository: Any | None = None,
    ) -> OrderPreview:
        preview = OrderPreview(
            strategy_name=str(intent.strategy_name or intent.metadata.get("strategy_name") or "manual"),
            strategy_version=str(strategy_version or intent.metadata.get("strategy_version") or "manual"),
            signal_id=str(signal_id or intent.signal_id or intent.metadata.get("signal_id") or ""),
            symbol=intent.symbol,
            side=intent.side,
            price=float(intent.price),
            quantity=int(intent.quantity or 0),
            estimated_total_cost=float(estimated_total_cost),
            available_cash=available_cash,
            position_before=int(position_before),
            checks=list(checks or []),
            expires_at=datetime.utcnow() + timedelta(seconds=self.ttl_seconds),
        )
        self._previews[preview.preview_id] = preview
        repo = repository or self.repository
        if repo is not None and hasattr(repo, "add_order_preview_record"):
            repo.add_order_preview_record(preview=preview.to_dict(), intent=intent.to_dict())
        return preview

    def approve(
        self,
        *,
        preview_id: str,
        intent: OrderIntent,
        manual_confirmed: bool,
        now: datetime | None = None,
        repository: Any | None = None,
    ) -> PreviewDecision:
        repo = repository or self.repository
        preview = self._previews.get(str(preview_id or ""))
        if preview is None:
            preview = self._load_preview_from_repository(str(preview_id or ""), repository=repo)
        if preview is None:
            return self._record_decision(
                PreviewDecision(preview_id=str(preview_id or ""), accepted=False, reason="preview_not_found"),
                repository=repo,
            )
        now_dt = now or datetime.utcnow()
        if now_dt > preview.expires_at:
            return self._record_decision(
                PreviewDecision(preview_id=preview.preview_id, accepted=False, reason="preview_expired"),
                repository=repo,
            )
        if intent.environment == "live" and not manual_confirmed:
            return self._record_decision(
                PreviewDecision(preview_id=preview.preview_id, accepted=False, reason="manual_confirmation_required"),
                repository=repo,
            )
        for key, expected in (
            ("symbol", preview.symbol),
            ("side", preview.side),
            ("price", preview.price),
            ("quantity", preview.quantity),
        ):
            actual = getattr(intent, key)
            if key == "price":
                same = abs(float(actual) - float(expected)) < 1e-8
            else:
                same = actual == expected
            if not same:
                return self._record_decision(
                    PreviewDecision(preview_id=preview.preview_id, accepted=False, reason="preview_intent_mismatch"),
                    repository=repo,
                )
        if intent.environment == "live":
            if not intent.strategy_name or not intent.signal_id or not intent.metadata.get("strategy_version"):
                return self._record_decision(
                    PreviewDecision(preview_id=preview.preview_id, accepted=False, reason="live_metadata_required"),
                    repository=repo,
                )
        return self._record_decision(
            PreviewDecision(preview_id=preview.preview_id, accepted=True, reason="ok", approved_intent=intent),
            repository=repo,
        )

    def get(self, preview_id: str) -> OrderPreview | None:
        return self._previews.get(str(preview_id or ""))

    def _load_preview_from_repository(self, preview_id: str, *, repository: Any | None = None) -> OrderPreview | None:
        repo = repository or self.repository
        if repo is None or not hasattr(repo, "get_order_preview_record"):
            return None
        record = repo.get_order_preview_record(preview_id)
        if not isinstance(record, dict):
            return None
        try:
            expires_raw = record.get("expires_at")
            created_raw = record.get("created_at")
            preview = OrderPreview(
                preview_id=str(record["preview_id"]),
                strategy_name=str(record.get("strategy_name") or "manual"),
                strategy_version=str(record.get("strategy_version") or "manual"),
                signal_id=str(record.get("signal_id") or ""),
                symbol=str(record["symbol"]),
                side=str(record["side"]),
                price=float(record["price"]),
                quantity=int(record["quantity"]),
                estimated_total_cost=float(record.get("estimated_total_cost") or 0.0),
                available_cash=(
                    float(record["available_cash"])
                    if record.get("available_cash") is not None
                    else None
                ),
                position_before=int(record.get("position_before") or 0),
                checks=list((record.get("preview") or {}).get("checks") or []),
                expires_at=(
                    datetime.fromisoformat(str(expires_raw))
                    if expires_raw
                    else datetime.utcnow() + timedelta(seconds=self.ttl_seconds)
                ),
                created_at=(
                    datetime.fromisoformat(str(created_raw))
                    if created_raw
                    else datetime.utcnow()
                ),
            )
            self._previews[preview.preview_id] = preview
            return preview
        except Exception:
            return None

    def _record_decision(self, decision: PreviewDecision, *, repository: Any | None = None) -> PreviewDecision:
        repo = repository or self.repository
        if repo is not None and hasattr(repo, "update_order_preview_decision"):
            repo.update_order_preview_decision(decision=decision.to_dict())
        return decision
