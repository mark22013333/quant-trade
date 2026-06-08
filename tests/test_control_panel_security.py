from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from web.control_panel_app import app


REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


@pytest.fixture()
def report_file():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "security_test_report.txt"
    path.write_text("report-ok", encoding="utf-8")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_control_panel_token_protects_api(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    blocked = client.get("/api/ping")
    allowed = client.get("/api/ping", headers={"Authorization": "Bearer secret-token"})

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "ok"


def test_control_panel_token_protects_reports(monkeypatch, report_file):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    blocked_static = client.get(f"/reports/{report_file.name}")
    blocked_list = client.get("/api/reports")
    allowed_static = client.get(f"/reports/{report_file.name}", headers={"X-Control-Panel-Token": "secret-token"})
    allowed_list = client.get("/api/reports", headers={"Authorization": "Bearer secret-token"})

    assert blocked_static.status_code == 401
    assert blocked_list.status_code == 401
    assert allowed_static.status_code == 200
    assert allowed_static.text == "report-ok"
    assert allowed_list.status_code == 200
    assert any(item["name"] == report_file.name for item in allowed_list.json()["reports"])


def test_control_panel_allows_local_reports_without_token(monkeypatch, report_file):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.get(f"/reports/{report_file.name}")

    assert response.status_code == 200
    assert response.text == "report-ok"


def test_control_panel_rejects_external_bind_without_token(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "0.0.0.0")
    client = TestClient(app)

    response = client.get("/api/ping")

    assert response.status_code == 403
    assert response.json()["error"] == "control panel requires CONTROL_PANEL_TOKEN outside localhost"


def test_control_panel_echo_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_DEBUG", raising=False)
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post("/api/echo", json={"token": "secret-token-value"})

    assert response.status_code == 404
    assert response.json()["error"] == "debug endpoint disabled"


def test_tw_live_api_is_token_protected(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    blocked = client.get("/api/tw-live/health?simulation=true")
    allowed = client.get("/api/tw-live/health?simulation=true", headers={"Authorization": "Bearer secret-token"})

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "ok"


def test_tw_live_order_preview_endpoint(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/tw-live/order-preview",
        json={"symbol": "2330", "side": "buy", "price": 100, "quantity": 1},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["symbol"] == "2330"
    assert data["preview_id"]
