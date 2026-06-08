"""Add trading audit records.

Revision ID: 20260608_0003
Revises: 20260608_0002
Create Date: 2026-06-08 00:00:00
"""

from __future__ import annotations

from alembic import op

from app.db.models import OrderPreviewRecord, PromotionGateRecord, ReconciliationRecord


revision = "20260608_0003"
down_revision = "20260608_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    OrderPreviewRecord.__table__.create(bind=bind, checkfirst=True)
    PromotionGateRecord.__table__.create(bind=bind, checkfirst=True)
    ReconciliationRecord.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    ReconciliationRecord.__table__.drop(bind=bind, checkfirst=True)
    PromotionGateRecord.__table__.drop(bind=bind, checkfirst=True)
    OrderPreviewRecord.__table__.drop(bind=bind, checkfirst=True)
