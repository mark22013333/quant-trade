from __future__ import annotations

from datetime import date

from app.advisor.codex import CodexAdvisor
from app.advisor.factory import build_advisor
from app.advisor.models import AdvisorRequest
from app.advisor.openai_transport import OpenAIResponsesTransport
from app.advisor.stub import StubTradingAdvisor


def _request() -> AdvisorRequest:
    return AdvisorRequest(symbol="2330", trade_date=date(2026, 6, 9), available_cash=10_000)


def test_codex_advisor_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_ADVISOR_ENABLED", raising=False)

    decision = CodexAdvisor().advise(_request())

    assert decision.status == "rejected"
    assert decision.rejected_reason == "codex_advisor_disabled"


def test_codex_advisor_transport_parses_valid_json() -> None:
    def transport(_prompt: str) -> str:
        return (
            '{"symbol":"2330","action":"hold","confidence":0.55,'
            '"rationale":"等待人工確認","risks":[{"code":"wait","message":"訊號不足"}]}'
        )

    decision = CodexAdvisor(enabled=True, transport=transport).advise(_request())

    assert decision.status == "accepted"
    assert decision.proposal is not None
    assert decision.proposal.action == "hold"


def test_codex_advisor_enabled_without_key_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_ADVISOR_MODEL", "test-model")

    decision = CodexAdvisor(enabled=True).advise(_request())

    assert decision.status == "rejected"
    assert decision.rejected_reason == "codex_transport_error"
    assert any("OPENAI_API_KEY" in error for error in decision.validation_errors)


def test_openai_transport_extracts_output_text() -> None:
    text = OpenAIResponsesTransport._extract_text({"output_text": "  hello advisor  "})

    assert text == "  hello advisor  "


def test_openai_transport_extracts_nested_output() -> None:
    data = {
        "output": [
            {"content": [{"text": '{"action":"hold",'}, {"text": '"confidence":0.5}'}]},
        ]
    }

    assert OpenAIResponsesTransport._extract_text(data) == '{"action":"hold","confidence":0.5}'


def test_openai_transport_posts_to_responses_api(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"output_text": "advisor-json"}

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("app.advisor.openai_transport.requests.post", fake_post)
    transport = OpenAIResponsesTransport(
        api_key="sk-test",
        model="test-model",
        timeout_sec=7,
        base_url="https://example.test/responses",
    )

    assert transport("prompt body") == "advisor-json"
    assert calls == [
        {
            "url": "https://example.test/responses",
            "headers": {
                "Authorization": "Bearer sk-test",
                "Content-Type": "application/json",
            },
            "json": {"model": "test-model", "input": "prompt body"},
            "timeout": 7,
        }
    ]


def test_advisor_factory_defaults_to_stub() -> None:
    assert isinstance(build_advisor(None), StubTradingAdvisor)
    assert build_advisor("codex").name == "codex"
