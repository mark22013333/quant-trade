from __future__ import annotations

from app.alerts.notifier_line import LineNotifyNotifier


class DummyResp:
    def raise_for_status(self):
        return None


def test_line_notifier_sends_with_bearer_token(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, data=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["timeout"] = timeout
        return DummyResp()

    monkeypatch.setattr("requests.post", fake_post)
    notifier = LineNotifyNotifier(token="line-token", timeout_sec=5)
    notifier.send("hello")

    assert captured["url"] == "https://notify-api.line.me/api/notify"
    assert captured["headers"]["Authorization"] == "Bearer line-token"
    assert captured["data"]["message"] == "hello"
    assert captured["timeout"] == 5
