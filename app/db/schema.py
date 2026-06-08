from __future__ import annotations

from sqlalchemy.engine import Engine

from app.db.models import Base


SCHEMA_VERSION = "2026-06-08-trading-audit-records"
ALEMBIC_HEAD_REVISION = "20260608_0003"


def initialize_schema(engine: Engine) -> None:
    """
    Central schema bootstrap.

    This compatibility bootstrap is intentionally kept behind one function.
    New schema changes should be added through Alembic revisions instead of
    depending on create_all to mutate existing databases.
    """
    Base.metadata.create_all(bind=engine)
