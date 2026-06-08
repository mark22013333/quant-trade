from __future__ import annotations

from app.data.finmind_client import FinMindClient


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_user_info_normalizes_payload(monkeypatch):
    payload = {
        "status": 200,
        "msg": "success",
        "data": {
            "user_count": 12345,
            "api_request_limit": 600,
            "api_request_count": 150,
        },
    }

    def fake_get(url, headers=None, timeout=None):  # noqa: ANN001, ARG001
        return DummyResponse(payload)

    monkeypatch.setattr("app.data.finmind_client.requests.get", fake_get)

    client = FinMindClient(
        api_key="dummy-token",
        user_info_url="https://api.web.finmindtrade.com/v2/user_info",
    )
    info = client.fetch_user_info()

    assert info["status"] == 200
    assert info["api_request_limit"] == 600
    assert info["api_request_count"] == 150
    assert info["api_request_remaining"] == 450
    assert info["usage_ratio"] == 0.25


def test_request_endpoint_uses_authorization_header_not_token_query(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):  # noqa: ANN001, ARG001
        captured["url"] = url
        captured["params"] = params or {}
        captured["headers"] = headers or {}
        return DummyResponse({"status": 200, "data": []})

    monkeypatch.setattr("app.data.finmind_client.requests.get", fake_get)

    client = FinMindClient(api_key="dummy-token", base_url="https://api.finmindtrade.com/api/v4/data")
    assert client._request_endpoint("some_endpoint", stock_id="2330") == []

    assert captured["params"] == {"stock_id": "2330"}
    assert captured["headers"]["Authorization"] == "Bearer dummy-token"
