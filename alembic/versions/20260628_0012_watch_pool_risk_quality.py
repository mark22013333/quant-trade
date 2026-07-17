"""Add watch pool risk quality fields.

Revision ID: 20260628_0012
Revises: 20260627_0011
Create Date: 2026-06-28 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260628_0012"
down_revision = "20260627_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("watch_pool_entries") as batch_op:
        batch_op.add_column(sa.Column("risk_quality_score", sa.Float(), nullable=False, server_default="0.0"))
        batch_op.add_column(sa.Column("risk_quality_label", sa.String(length=24), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("risk_quality_reason", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("watch_pool_entries") as batch_op:
        batch_op.drop_column("risk_quality_reason")
        batch_op.drop_column("risk_quality_label")
        batch_op.drop_column("risk_quality_score")
