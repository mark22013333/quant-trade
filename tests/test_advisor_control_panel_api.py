from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from web.control_panel_app import app


def test_advisor_proposal_endpoint_returns_decision(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/proposals",
        json={"symbol": "2330", "trade_date": "2026-01-01", "available_cash": 10000, "create_preview": False},
    )

    assert response.status_code == 200
    assert response.json()["data"]["decision"]["request"]["symbol"] == "2330"


def test_order_approve_execute_requires_manual_and_promotion(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": "missing-preview", "manual_confirmed": False, "promotion_gate_accepted": False},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "manual_confirmation_required"


def test_order_approve_execute_rejects_preview_environment_mismatch(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "price": 100,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": True},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "preview_environment_mismatch"


def test_advisor_backtest_endpoint_handles_missing_data(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/backtest",
        json={
            "symbol": "NOPE",
            "start_date": "2026-01-01",
            "end_date": "2026-01-10",
            "initial_cash": 10000,
            "max_days": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["passed"] is False


def test_advisor_backtest_export_endpoint_returns_report_urls(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/backtest/export",
        json={
            "symbol": "NOPE",
            "start_date": "2026-01-01",
            "end_date": "2026-01-10",
            "initial_cash": 10000,
            "max_days": 5,
        },
    )

    assert response.status_code == 200
    urls = response.json()["data"]["export"]["urls"]
    assert urls["summary_json"].startswith("/reports/")
    assert urls["html_report"].endswith(".html")


def test_advisor_proposal_endpoint_codex_provider_fails_closed(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_ADVISOR_ENABLED", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/proposals",
        json={
            "symbol": "2330",
            "trade_date": "2026-01-01",
            "available_cash": 10000,
            "create_preview": False,
            "advisor_provider": "codex",
        },
    )

    assert response.status_code == 200
    decision = response.json()["data"]["decision"]
    assert decision["status"] == "rejected"
    assert decision["rejected_reason"] == "codex_advisor_disabled"
