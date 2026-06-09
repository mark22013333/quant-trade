"""Add intent JSON to order preview records.

Revision ID: 20260609_0006
Revises: 20260609_0005
Create Date: 2026-06-09 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "20260609_0006"
down_revision = "20260609_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("order_preview_records"):
        return
    columns = {column["name"] for column in inspector.get_columns("order_preview_records")}
    if "intent_json" not in columns:
        op.add_column("order_preview_records", sa.Column("intent_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("order_preview_records"):
        return
    columns = {column["name"] for column in inspector.get_columns("order_preview_records")}
    if "intent_json" in columns:
        op.drop_column("order_preview_records", "intent_json")
