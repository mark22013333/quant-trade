from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.execution.models import normalize_symbol


AdvisorAction = Literal["buy", "sell", "hold", "reject"]


class AdvisorRisk(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    severity: Literal["low", "medium", "high"] = "medium"


class AdvisorProposal(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    action: AdvisorAction = "hold"
    price: float | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=2000)
    risks: list[AdvisorRisk] = Field(min_length=1)
    data_quality: str = "unknown"

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return normalize_symbol(value)

    @field_validator("price")
    @classmethod
    def _price_required_for_trade(cls, value: float | None, info):
        action = (info.data or {}).get("action")
        if action in {"buy", "sell"} and value is None:
            raise ValueError("price_required_for_trade")
        return value

    @field_validator("quantity")
    @classmethod
    def _quantity_required_for_trade(cls, value: int | None, info):
        action = (info.data or {}).get("action")
        if action in {"buy", "sell"} and value is None:
            raise ValueError("quantity_required_for_trade")
        return value


class AdvisorRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    trade_date: date
    available_cash: float = Field(default=10_000.0, ge=0.0)
    position_qty: int = Field(default=0, ge=0)
    market_context: dict[str, Any] = Field(default_factory=dict)
    bars: list[dict[str, Any]] = Field(default_factory=list)
    radar_item: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return normalize_symbol(value)


class AdvisorDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    advisor_name: str = Field(default="stub", min_length=1, max_length=80)
    advisor_version: str = Field(default="v1", min_length=1, max_length=80)
    request: AdvisorRequest
    proposal: AdvisorProposal | None = None
    status: Literal["accepted", "rejected"] = "accepted"
    validation_errors: list[str] = Field(default_factory=list)
    rejected_reason: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def actionable(self) -> bool:
        return self.status == "accepted" and self.proposal is not None and self.proposal.action in {"buy", "sell"}


def decision_from_payload(
    *,
    request: AdvisorRequest,
    payload: dict[str, Any],
    advisor_name: str,
    advisor_version: str,
) -> AdvisorDecision:
    try:
        proposal = AdvisorProposal.model_validate(payload)
        return AdvisorDecision(
            advisor_name=advisor_name,
            advisor_version=advisor_version,
            request=request,
            proposal=proposal,
            status="accepted",
        )
    except ValidationError as exc:
        return AdvisorDecision(
            advisor_name=advisor_name,
            advisor_version=advisor_version,
            request=request,
            proposal=None,
            status="rejected",
            validation_errors=[f"{'.'.join(str(part) for part in err.get('loc', []))}: {err.get('msg')}" for err in exc.errors()],
            rejected_reason="schema_validation_failed",
        )
