from __future__ import annotations

import json

from web.control_panel_app import (
    OrderApproveExecuteRequest,
    StockOrderPreviewRequest,
    AdvisorProposalRequest,
    advisor_proposals,
    tw_live_order_approve_execute,
    tw_live_order_preview,
)


def _payload(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _use_temp_db(monkeypatch, tmp_path, name: str) -> None:
    import app.db.session as db_session

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(db_session, "_ENGINE", None)
    monkeypatch.setattr(db_session, "_SESSION_FACTORY", None)


def test_direct_advisor_proposal_endpoint_uses_temp_db(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path, "advisor.db")

    response = advisor_proposals(
        AdvisorProposalRequest(
            symbol="2330",
            trade_date="2026-01-01",
            available_cash=10000,
            create_preview=False,
        )
    )

    payload = _payload(response)
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["data"]["decision"]["request"]["symbol"] == "2330"


def test_direct_order_approve_execute_requires_manual_confirmation(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path, "approve.db")

    response = tw_live_order_approve_execute(
        OrderApproveExecuteRequest(
            preview_id="missing-preview",
            manual_confirmed=False,
            promotion_gate_accepted=True,
            simulation=True,
        )
    )

    payload = _payload(response)
    assert response.status_code == 400
    assert payload["error"] == "manual_confirmation_required"


def test_direct_order_approve_execute_rejects_preview_environment_mismatch(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path, "env-mismatch.db")

    preview_response = tw_live_order_preview(
        StockOrderPreviewRequest(
            simulation=False,
            symbol="2330",
            side="buy",
            price=100,
            quantity=1,
            available_cash=10000,
            position_before=0,
            strategy_name="advisor:stub",
            strategy_version="v1",
            signal_id="S1",
        )
    )
    preview_payload = _payload(preview_response)
    assert preview_response.status_code == 200

    response = tw_live_order_approve_execute(
        OrderApproveExecuteRequest(
            preview_id=preview_payload["data"]["preview_id"],
            manual_confirmed=True,
            promotion_gate_accepted=True,
            simulation=True,
        )
    )

    payload = _payload(response)
    assert response.status_code == 400
    assert payload["error"] == "preview_environment_mismatch"
    assert payload["preview_environment"] == "live"
    assert payload["requested_environment"] == "simulation"
