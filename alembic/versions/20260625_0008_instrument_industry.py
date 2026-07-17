"""Add industry to instruments.

Revision ID: 20260625_0008
Revises: 20260612_0007
Create Date: 2026-06-25 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "20260625_0008"
down_revision = "20260612_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("instruments"):
        return
    columns = {column["name"] for column in inspector.get_columns("instruments")}
    if "industry" not in columns:
        op.add_column("instruments", sa.Column("industry", sa.String(length=80), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("instruments"):
        return
    columns = {column["name"] for column in inspector.get_columns("instruments")}
    if "industry" in columns:
        op.drop_column("instruments", "industry")
