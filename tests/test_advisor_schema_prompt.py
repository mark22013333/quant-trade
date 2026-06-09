from __future__ import annotations

from datetime import date

from pydantic import ValidationError

from app.advisor.models import AdvisorProposal, AdvisorRequest
from app.advisor.prompt import build_advisor_prompt, parse_advisor_response


def _request() -> AdvisorRequest:
    return AdvisorRequest(symbol="2330.TW", trade_date=date(2026, 6, 9), available_cash=10_000)


def test_advisor_proposal_schema_accepts_valid_trade() -> None:
    proposal = AdvisorProposal(
        symbol="2330.TW",
        action="buy",
        price=100,
        quantity=1,
        confidence=0.7,
        rationale="量價與風險條件可接受。",
        risks=[{"code": "market", "message": "大盤回檔風險"}],
    )

    assert proposal.symbol == "2330"
    assert proposal.confidence == 0.7


def test_advisor_proposal_schema_rejects_invalid_trade() -> None:
    try:
        AdvisorProposal(
            symbol="2330",
            action="buy",
            price=0,
            quantity=0,
            confidence=1.2,
            rationale="",
            risks=[],
        )
    except ValidationError as exc:
        text = str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected validation error")

    assert "greater than 0" in text
    assert "less than or equal to 1" in text


def test_prompt_is_stable_and_parse_rejects_bad_json() -> None:
    request = _request()
    prompt = build_advisor_prompt(request)
    assert "只能提出建議" in prompt
    assert "2330" in prompt

    bad = parse_advisor_response("not-json", request=request)
    assert bad.status == "rejected"
    assert bad.rejected_reason == "json_parse_failed"

    ok = parse_advisor_response(
        '{"symbol":"2330","action":"hold","confidence":0.5,"rationale":"等待","risks":[{"code":"wait","message":"訊號不足"}]}',
        request=request,
    )
    assert ok.status == "accepted"
    assert ok.proposal is not None
    assert ok.proposal.action == "hold"
