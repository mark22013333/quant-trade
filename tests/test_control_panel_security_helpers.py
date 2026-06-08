from __future__ import annotations

from types import SimpleNamespace

from fastapi.responses import JSONResponse

from web.control_panel_app import _is_loopback_host, _request_token, _with_security_headers


def test_is_loopback_host_accepts_local_and_test_hosts():
    assert _is_loopback_host("127.0.0.1")
    assert _is_loopback_host("localhost:8000")
    assert _is_loopback_host("testclient")
    assert not _is_loopback_host("0.0.0.0")
    assert not _is_loopback_host("192.168.1.10")


def test_request_token_accepts_bearer_or_header_token():
    bearer_request = SimpleNamespace(headers={"authorization": "Bearer secret-token"})
    header_request = SimpleNamespace(headers={"x-control-panel-token": "header-token"})

    assert _request_token(bearer_request) == "secret-token"
    assert _request_token(header_request) == "header-token"


def test_security_headers_disable_cache_and_sniffing():
    response = _with_security_headers(JSONResponse({"ok": True}))

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
