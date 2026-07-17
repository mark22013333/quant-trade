"""Add attention stocks.

Revision ID: 20260626_0010
Revises: 20260625_0009
Create Date: 2026-06-26 00:00:00
"""

from __future__ import annotations

from app.db.models import AttentionStock
from alembic import op


revision = "20260626_0010"
down_revision = "20260625_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    AttentionStock.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    AttentionStock.__table__.drop(bind=bind, checkfirst=True)
