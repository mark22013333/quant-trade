from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.models import Base


SCHEMA_VERSION = "2026-06-09-preview-intent-json"
ALEMBIC_HEAD_REVISION = "20260609_0006"


def initialize_schema(engine: Engine) -> None:
    """
    Central schema bootstrap.

    This compatibility bootstrap is intentionally kept behind one function.
    New schema changes should be added through Alembic revisions instead of
    depending on create_all to mutate existing databases.
    """
    Base.metadata.create_all(bind=engine)
    _ensure_order_preview_intent_json(engine)


def _ensure_order_preview_intent_json(engine: Engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table("order_preview_records"):
        return
    columns = {column["name"] for column in inspector.get_columns("order_preview_records")}
    if "intent_json" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE order_preview_records ADD COLUMN intent_json TEXT DEFAULT '{}'"))
