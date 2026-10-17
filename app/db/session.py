from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import load_config
from app.db.schema import initialize_schema


_ENGINE = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def configure_sqlite_connection(dbapi_connection, _connection_record=None) -> None:
    """在任何交易開始前開啟且核對不可變研究資料依賴的保護。"""
    import sqlite3
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    if dbapi_connection.in_transaction:
        raise RuntimeError("sqlite_protection_requires_connection_outside_transaction")
    cursor = dbapi_connection.cursor()
    try:
        for setting in ("foreign_keys", "recursive_triggers"):
            cursor.execute(f"PRAGMA {setting}=ON")
            if cursor.execute(f"PRAGMA {setting}").fetchone()[0] != 1:
                raise RuntimeError(f"sqlite_{setting}_not_enabled")
    finally:
        cursor.close()


def install_sqlite_protection(engine) -> None:
    if engine.dialect.name == "sqlite" and not event.contains(engine, "connect", configure_sqlite_connection):
        event.listen(engine, "connect", configure_sqlite_connection)


def get_engine():
    global _ENGINE  # noqa: PLW0603
    if _ENGINE is None:
        cfg = load_config()
        connect_args = {"check_same_thread": False} if cfg.database_url.startswith("sqlite") else {}
        _ENGINE = create_engine(cfg.database_url, future=True, pool_pre_ping=True, connect_args=connect_args)
        install_sqlite_protection(_ENGINE)
    return _ENGINE


def get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY  # noqa: PLW0603
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SESSION_FACTORY


def init_db() -> None:
    initialize_schema(get_engine())
