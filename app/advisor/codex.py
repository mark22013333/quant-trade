from __future__ import annotations

import os
from typing import Callable

from app.advisor.interface import TradingAdvisor
from app.advisor.models import AdvisorDecision, AdvisorRequest
from app.advisor.openai_transport import OpenAIResponsesTransport
from app.advisor.prompt import build_advisor_prompt, parse_advisor_response


class CodexAdvisor(TradingAdvisor):
    name = "codex"
    version = "disabled-shell-v1"

    def __init__(self, *, transport: Callable[[str], str] | None = None, enabled: bool | None = None):
        self.transport = transport
        self.enabled = bool(enabled) if enabled is not None else os.getenv("CODEX_ADVISOR_ENABLED", "").strip() == "1"

    def advise(self, request: AdvisorRequest) -> AdvisorDecision:
        if not self.enabled:
            return AdvisorDecision(
                advisor_name=self.name,
                advisor_version=self.version,
                request=request,
                proposal=None,
                status="rejected",
                validation_errors=["CODEX_ADVISOR_ENABLED is not 1"],
                rejected_reason="codex_advisor_disabled",
            )
        if self.transport is None:
            self.transport = OpenAIResponsesTransport()
        prompt = build_advisor_prompt(request)
        try:
            response_text = self.transport(prompt)
        except Exception as exc:  # noqa: BLE001
            return AdvisorDecision(
                advisor_name=self.name,
                advisor_version=self.version,
                request=request,
                proposal=None,
                status="rejected",
                validation_errors=[str(exc)],
                rejected_reason="codex_transport_error",
            )
        return parse_advisor_response(
            response_text,
            request=request,
            advisor_name=self.name,
            advisor_version=self.version,
        )
