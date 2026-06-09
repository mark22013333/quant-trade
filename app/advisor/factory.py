from __future__ import annotations

from app.advisor.codex import CodexAdvisor
from app.advisor.interface import TradingAdvisor
from app.advisor.stub import StubTradingAdvisor


def build_advisor(provider: str | None = None) -> TradingAdvisor:
    value = str(provider or "stub").strip().lower()
    if value == "codex":
        return CodexAdvisor()
    return StubTradingAdvisor()
