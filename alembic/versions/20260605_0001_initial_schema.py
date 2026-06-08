"""Initial application schema.

Revision ID: 20260605_0001
Revises:
Create Date: 2026-06-05 00:00:00
"""

from __future__ import annotations

from alembic import op

from app.db.models import Base


revision = "20260605_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind=bind, checkfirst=True)
