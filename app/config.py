from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=False)


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    finmind_api_key: str
    finmind_api_url: str
    finmind_user_info_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    line_notify_token: str


def load_config() -> AppConfig:
    default_db = f"sqlite:///{(PROJECT_ROOT / 'data' / 'quant_trade.db').as_posix()}"
    return AppConfig(
        database_url=os.getenv("DATABASE_URL", default_db),
        finmind_api_key=os.getenv("FINMIND_API_KEY", "").strip(),
        finmind_api_url=os.getenv("FINMIND_API_URL", "https://api.finmindtrade.com/api/v4/data").strip(),
        finmind_user_info_url=os.getenv("FINMIND_USER_INFO_URL", "https://api.web.finmindtrade.com/v2/user_info").strip(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        line_notify_token=os.getenv("LINE_NOTIFY_TOKEN", "").strip(),
    )
