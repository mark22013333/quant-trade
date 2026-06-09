from __future__ import annotations

from typing import Protocol

from app.advisor.models import AdvisorDecision, AdvisorRequest


class TradingAdvisor(Protocol):
    name: str
    version: str

    def advise(self, request: AdvisorRequest) -> AdvisorDecision:
        """Return a validated trading proposal without executing orders."""
