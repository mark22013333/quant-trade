"""Immutable swing research schema, sourced only from its canonical deploy.sql.

Revision ID: 20261016_0015
Revises: 20260729_0014
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from alembic import op
from sqlalchemy.engine import Connection

revision = "20261016_0015"
down_revision = "20260729_0014"
branch_labels = None
depends_on = None

CANONICAL_SQL = Path(__file__).resolve().parents[2] / ".spec" / "tw-swing-ai-validation" / "deploy.sql"


def _statements(sql: str):
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if any(line.strip() and not line.lstrip().startswith("--") for line in pending.splitlines()):
        raise RuntimeError("incomplete_canonical_swing_sql")


def _sql(*, rollback: bool) -> str:
    if not CANONICAL_SQL.is_file():
        raise RuntimeError(f"canonical_swing_sql_missing: 必須部署已納版的 {CANONICAL_SQL}")
    forward, backward = CANONICAL_SQL.read_text(encoding="utf-8").split("-- ===== Rollback", 1)
    if not rollback:
        return forward
    # Only executable commands in the approved commented rollback; prose stays comments.
    lines = []
    for line in backward.splitlines():
        command = line.removeprefix("-- ").strip()
        if command.startswith(("DROP TRIGGER ", "DROP TABLE ", "PRAGMA defer_foreign_keys")):
            lines.append(command)
    return "\n".join(lines)


def _check_sqlite(bind: Connection):
    if bind.dialect.name != "sqlite":
        raise RuntimeError("swing_research_requires_sqlite")
    version = tuple(int(part) for part in bind.exec_driver_sql("SELECT sqlite_version()").scalar().split("."))
    if version < (3, 37):
        raise RuntimeError("swing_research_requires_sqlite_3_37")
    if bind.exec_driver_sql("SELECT json_valid('{}')").scalar() != 1:
        raise RuntimeError("swing_research_requires_json1")
    for setting in ("foreign_keys", "recursive_triggers"):
        if bind.exec_driver_sql(f"PRAGMA {setting}").scalar() != 1:
            raise RuntimeError(f"sqlite_{setting}_not_enabled_before_transaction")
    # SQLite's legacy driver transaction control does not BEGIN for DDL. Make the
    # Alembic-owned transaction real without committing the caller's transaction.
    if not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    bind = op.get_bind()
    _check_sqlite(bind)
    for statement in _statements(_sql(rollback=False)):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    _check_sqlite(bind)
    for statement in _statements(_sql(rollback=True)):
        bind.exec_driver_sql(statement)
