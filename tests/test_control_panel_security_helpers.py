from __future__ import annotations

from types import SimpleNamespace

from fastapi.responses import JSONResponse

from web.control_panel_app import (
    _is_loopback_host,
    _is_trusted_proxy_auth_source,
    _request_token,
    _trusted_proxy_user,
    _with_security_headers,
)


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


def test_trusted_proxy_user_requires_flag_and_trusted_source(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TRUST_PROXY_AUTH", raising=False)
    monkeypatch.delenv("CONTROL_PANEL_TRUST_PROXY_AUTH_SOURCES", raising=False)
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"x-authenticated-user": "quanttrade"},
    )

    assert _trusted_proxy_user(request) == ""

    monkeypatch.setenv("CONTROL_PANEL_TRUST_PROXY_AUTH", "1")
    assert _trusted_proxy_user(request) == "quanttrade"

    request.client.host = "203.0.113.10"
    assert _trusted_proxy_user(request) == ""

    monkeypatch.setenv("CONTROL_PANEL_TRUST_PROXY_AUTH_SOURCES", "192.168.3.235")
    request.client.host = "192.168.3.235"
    assert _trusted_proxy_user(request) == "quanttrade"


def test_trusted_proxy_auth_source_supports_exact_ip_and_cidr(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TRUST_PROXY_AUTH_SOURCES", raising=False)

    assert _is_trusted_proxy_auth_source("127.0.0.1")
    assert not _is_trusted_proxy_auth_source("192.168.3.235")

    monkeypatch.setenv("CONTROL_PANEL_TRUST_PROXY_AUTH_SOURCES", "192.168.3.235,10.10.0.0/16")

    assert _is_trusted_proxy_auth_source("192.168.3.235")
    assert _is_trusted_proxy_auth_source("10.10.2.3")
    assert not _is_trusted_proxy_auth_source("203.0.113.10")


def test_security_headers_disable_cache_and_sniffing():
    response = _with_security_headers(JSONResponse({"ok": True}))

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
