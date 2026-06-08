from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import load_config
from app.db.schema import initialize_schema


_ENGINE = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def get_engine():
    global _ENGINE  # noqa: PLW0603
    if _ENGINE is None:
        cfg = load_config()
        connect_args = {"check_same_thread": False} if cfg.database_url.startswith("sqlite") else {}
        _ENGINE = create_engine(cfg.database_url, future=True, pool_pre_ping=True, connect_args=connect_args)
    return _ENGINE


def get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY  # noqa: PLW0603
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SESSION_FACTORY


def init_db() -> None:
    initialize_schema(get_engine())
