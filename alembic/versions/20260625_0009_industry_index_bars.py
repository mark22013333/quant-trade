"""Add industry index bars.

Revision ID: 20260625_0009
Revises: 20260625_0008
Create Date: 2026-06-25 00:00:00
"""

from __future__ import annotations

from app.db.models import IndustryIndexBar
from alembic import op


revision = "20260625_0009"
down_revision = "20260625_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    IndustryIndexBar.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    IndustryIndexBar.__table__.drop(bind=bind, checkfirst=True)
