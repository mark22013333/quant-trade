"""Add advisor observation records.

Revision ID: 20260612_0007
Revises: 20260609_0006
Create Date: 2026-06-12 00:00:00
"""

from __future__ import annotations

from alembic import op

from app.db.models import AdvisorObservationRecord


revision = "20260612_0007"
down_revision = "20260609_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    AdvisorObservationRecord.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    AdvisorObservationRecord.__table__.drop(bind=bind, checkfirst=True)
