"""Add radar risk adjusted score.

Revision ID: 20260627_0011
Revises: 20260626_0010
Create Date: 2026-06-27 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260627_0011"
down_revision = "20260626_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name in ("daily_radar_snapshots", "watch_pool_entries"):
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "risk_adjusted_score" not in columns:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.add_column(sa.Column("risk_adjusted_score", sa.Float(), nullable=False, server_default="0.0"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name in ("watch_pool_entries", "daily_radar_snapshots"):
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "risk_adjusted_score" in columns:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.drop_column("risk_adjusted_score")
