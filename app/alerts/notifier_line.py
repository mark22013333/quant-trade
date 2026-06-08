from __future__ import annotations

from app.alerts.notifier_base import Notifier
from app.config import load_config


class LineNotifyNotifier(Notifier):
    """
    LINE Notify notifier (legacy token mode).
    """

    def __init__(self, token: str | None = None, timeout_sec: int = 10):
        cfg = load_config()
        self.token = (token if token is not None else cfg.line_notify_token).strip()
        self.timeout_sec = int(timeout_sec)

    def send(self, message: str) -> None:
        import requests

        if not self.token:
            raise RuntimeError("line notify token is not configured in .env")
        resp = requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {self.token}"},
            data={"message": message},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
