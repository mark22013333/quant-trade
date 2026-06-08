"""Add trading execution records.

Revision ID: 20260608_0002
Revises: 20260605_0001
Create Date: 2026-06-08 00:00:00
"""

from __future__ import annotations

from alembic import op

from app.db.models import TradingExecutionRecord


revision = "20260608_0002"
down_revision = "20260605_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    TradingExecutionRecord.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    TradingExecutionRecord.__table__.drop(bind=op.get_bind(), checkfirst=True)
