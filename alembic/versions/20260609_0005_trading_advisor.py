"""Add trading advisor decision records.

Revision ID: 20260609_0005
Revises: 20260608_0004
Create Date: 2026-06-09 00:00:00
"""

from __future__ import annotations

from alembic import op

from app.db.models import AdvisorDecisionRecord


revision = "20260609_0005"
down_revision = "20260608_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    AdvisorDecisionRecord.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    AdvisorDecisionRecord.__table__.drop(bind=bind, checkfirst=True)
