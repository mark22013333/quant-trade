from __future__ import annotations

import pytest

from run_web import _is_loopback_host, validate_bind_security


def test_run_web_loopback_host_detection():
    assert _is_loopback_host("127.0.0.1")
    assert _is_loopback_host("localhost")
    assert _is_loopback_host("::1")
    assert not _is_loopback_host("0.0.0.0")
    assert not _is_loopback_host("192.168.1.10")


def test_run_web_rejects_external_bind_without_token(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)

    with pytest.raises(SystemExit, match="CONTROL_PANEL_TOKEN"):
        validate_bind_security("0.0.0.0")


def test_run_web_allows_external_bind_with_token(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")

    validate_bind_security("0.0.0.0")


def test_run_web_allows_localhost_without_token(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)

    validate_bind_security("127.0.0.1")
