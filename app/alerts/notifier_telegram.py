from __future__ import annotations

from app.alerts.notifier_base import Notifier
from app.config import load_config


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str | None = None, chat_id: str | None = None, timeout_sec: int = 10):
        cfg = load_config()
        self.bot_token = (bot_token if bot_token is not None else cfg.telegram_bot_token).strip()
        self.chat_id = (chat_id if chat_id is not None else cfg.telegram_chat_id).strip()
        self.timeout_sec = timeout_sec

    def send(self, message: str) -> None:
        import requests

        if not self.bot_token or not self.chat_id:
            raise RuntimeError("telegram token/chat_id not configured in .env")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message}
        resp = requests.post(url, json=payload, timeout=self.timeout_sec)
        resp.raise_for_status()
