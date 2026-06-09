from __future__ import annotations

from app.advisor.interface import TradingAdvisor
from app.advisor.models import AdvisorDecision, AdvisorRequest, decision_from_payload


class StubTradingAdvisor(TradingAdvisor):
    name = "stub"
    version = "v1"

    def advise(self, request: AdvisorRequest) -> AdvisorDecision:
        radar = dict(request.radar_item or {})
        action_label = str(radar.get("action_label", "")).upper()
        price = float(radar.get("price") or (request.bars[-1].get("close") if request.bars else 0.0) or 0.0)
        if action_label == "ENTRY_READY" and price > 0 and request.available_cash >= price:
            qty = max(1, int(min(request.available_cash // price, 1_000_000)))
            payload = {
                "symbol": request.symbol,
                "action": "buy",
                "price": price,
                "quantity": qty,
                "confidence": min(float(radar.get("entry_score", 60.0) or 60.0) / 100.0, 1.0),
                "rationale": "Stub advisor: 每日雷達標記 ENTRY_READY，產生可人工確認的買進提案。",
                "risks": [{"code": "stub_mode", "message": "此為 deterministic stub，僅供流程驗證。", "severity": "medium"}],
                "data_quality": str(radar.get("data_quality", "unknown")),
            }
        else:
            payload = {
                "symbol": request.symbol,
                "action": "hold",
                "price": price if price > 0 else None,
                "quantity": None,
                "confidence": 0.5,
                "rationale": "Stub advisor: 尚未達到 ENTRY_READY 或資金不足，建議觀察。",
                "risks": [{"code": "wait_trigger", "message": "訊號尚未完整觸發，避免過早進場。", "severity": "low"}],
                "data_quality": str(radar.get("data_quality", "unknown")),
            }
        return decision_from_payload(
            request=request,
            payload=payload,
            advisor_name=self.name,
            advisor_version=self.version,
        )
