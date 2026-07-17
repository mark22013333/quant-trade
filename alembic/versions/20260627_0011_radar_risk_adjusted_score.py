"""Add radar risk adjusted score.

Revision ID: 20260627_0011
Revises: 20260626_0010
Create Date: 2026-06-27 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260627_0011"
down_revision = "20260626_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("daily_radar_snapshots") as batch_op:
        batch_op.add_column(sa.Column("risk_adjusted_score", sa.Float(), nullable=False, server_default="0.0"))
    with op.batch_alter_table("watch_pool_entries") as batch_op:
        batch_op.add_column(sa.Column("risk_adjusted_score", sa.Float(), nullable=False, server_default="0.0"))


def downgrade() -> None:
    with op.batch_alter_table("watch_pool_entries") as batch_op:
        batch_op.drop_column("risk_adjusted_score")
    with op.batch_alter_table("daily_radar_snapshots") as batch_op:
        batch_op.drop_column("risk_adjusted_score")
