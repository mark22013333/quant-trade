from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.models import Base


SCHEMA_VERSION = "2026-10-16-swing-research"
ALEMBIC_HEAD_REVISION = "20261016_0015"


def initialize_schema(engine: Engine) -> None:
    """
    Central schema bootstrap.

    This compatibility bootstrap is intentionally kept behind one function.
    New schema changes should be added through Alembic revisions instead of
    depending on create_all to mutate existing databases.
    """
    Base.metadata.create_all(bind=engine)
    _ensure_order_preview_intent_json(engine)
    _ensure_instrument_industry(engine)
    _ensure_market_fundamental_columns(engine)


def _ensure_order_preview_intent_json(engine: Engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table("order_preview_records"):
        return
    columns = {column["name"] for column in inspector.get_columns("order_preview_records")}
    if "intent_json" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE order_preview_records ADD COLUMN intent_json TEXT DEFAULT '{}'"))


def _ensure_instrument_industry(engine: Engine) -> None:
    _ensure_columns(engine, "instruments", {"industry": "VARCHAR(80) NOT NULL DEFAULT ''"})


def _ensure_market_fundamental_columns(engine: Engine) -> None:
    """Keep pre-Alembic local SQLite files usable after market scoring columns were added."""
    _ensure_columns(
        engine,
        "feature_snapshots",
        {
            "revenue_score": "FLOAT NOT NULL DEFAULT 0.5",
            "quality_score": "FLOAT NOT NULL DEFAULT 0.5",
            "valuation_or_growth_score": "FLOAT NOT NULL DEFAULT 0.5",
            "news_risk_score": "FLOAT NOT NULL DEFAULT 0.5",
            "fundamental_data_quality": "VARCHAR(20) NOT NULL DEFAULT 'missing'",
        },
    )
    _ensure_columns(
        engine,
        "daily_radar_snapshots",
        {
            "risk_adjusted_score": "FLOAT NOT NULL DEFAULT 0.0",
            "risk_quality_score": "FLOAT NOT NULL DEFAULT 0.0",
            "risk_quality_label": "VARCHAR(24) NOT NULL DEFAULT ''",
            "risk_quality_reason": "TEXT NOT NULL DEFAULT ''",
            "fundamental_data_quality": "VARCHAR(20) NOT NULL DEFAULT 'missing'",
            "revenue_score": "FLOAT NOT NULL DEFAULT 0.5",
            "quality_score": "FLOAT NOT NULL DEFAULT 0.5",
            "valuation_or_growth_score": "FLOAT NOT NULL DEFAULT 0.5",
            "news_risk_score": "FLOAT NOT NULL DEFAULT 0.5",
            "fundamental_summary_json": "TEXT NOT NULL DEFAULT '{}'",
            "news_summary_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    )
    _ensure_columns(
        engine,
        "watch_pool_entries",
        {
            "risk_adjusted_score": "FLOAT NOT NULL DEFAULT 0.0",
            "risk_quality_score": "FLOAT NOT NULL DEFAULT 0.0",
            "risk_quality_label": "VARCHAR(24) NOT NULL DEFAULT ''",
            "risk_quality_reason": "TEXT NOT NULL DEFAULT ''",
        },
    )


def _ensure_columns(engine: Engine, table_name: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    missing = [(name, ddl) for name, ddl in columns.items() if name not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for name, ddl in missing:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))
