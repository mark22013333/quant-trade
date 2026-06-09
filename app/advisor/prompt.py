from __future__ import annotations

import json
from typing import Any

from app.advisor.models import AdvisorDecision, AdvisorRequest, decision_from_payload


def build_advisor_prompt(request: AdvisorRequest) -> str:
    payload = request.model_dump(mode="json")
    return (
        "你是台股交易決策輔助 AI，只能提出建議，不能下單。\n"
        "請輸出 JSON，欄位必須包含 symbol/action/price/quantity/confidence/rationale/risks/data_quality。\n"
        "action 只能是 buy、sell、hold、reject；risks 至少一筆。\n"
        f"資料如下：\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )


def parse_advisor_response(
    text: str,
    *,
    request: AdvisorRequest,
    advisor_name: str = "codex",
    advisor_version: str = "v1",
) -> AdvisorDecision:
    try:
        payload: Any = json.loads(str(text or ""))
        if not isinstance(payload, dict):
            raise ValueError("response_not_object")
    except Exception as exc:  # noqa: BLE001
        return AdvisorDecision(
            advisor_name=advisor_name,
            advisor_version=advisor_version,
            request=request,
            proposal=None,
            status="rejected",
            validation_errors=[str(exc)],
            rejected_reason="json_parse_failed",
        )
    return decision_from_payload(
        request=request,
        payload=payload,
        advisor_name=advisor_name,
        advisor_version=advisor_version,
    )
