"""Add watch pool risk quality fields.

Revision ID: 20260628_0012
Revises: 20260627_0011
Create Date: 2026-06-28 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260628_0012"
down_revision = "20260627_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("watch_pool_entries"):
        return
    columns = {column["name"] for column in inspector.get_columns("watch_pool_entries")}
    missing_columns = [
        sa.Column("risk_quality_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("risk_quality_label", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("risk_quality_reason", sa.Text(), nullable=False, server_default=""),
    ]
    missing_columns = [column for column in missing_columns if column.name not in columns]
    if missing_columns:
        with op.batch_alter_table("watch_pool_entries") as batch_op:
            for column in missing_columns:
                batch_op.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("watch_pool_entries"):
        return
    columns = {column["name"] for column in inspector.get_columns("watch_pool_entries")}
    removable = [name for name in ("risk_quality_reason", "risk_quality_label", "risk_quality_score") if name in columns]
    if removable:
        with op.batch_alter_table("watch_pool_entries") as batch_op:
            for column_name in removable:
                batch_op.drop_column(column_name)
