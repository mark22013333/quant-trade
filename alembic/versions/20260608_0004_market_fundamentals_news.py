"""Add market fundamentals and news records.

Revision ID: 20260608_0004
Revises: 20260608_0003
Create Date: 2026-06-08 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app.db.models import FinancialStatementSummary, MonthlyRevenue, NewsEvent


revision = "20260608_0004"
down_revision = "20260608_0003"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name: str, column) -> None:
    bind = op.get_bind()
    existing = {item["name"] for item in inspect(bind).get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    MonthlyRevenue.__table__.create(bind=bind, checkfirst=True)
    FinancialStatementSummary.__table__.create(bind=bind, checkfirst=True)
    NewsEvent.__table__.create(bind=bind, checkfirst=True)

    feature = "feature_snapshots"
    daily = "daily_radar_snapshots"
    for column in _feature_columns():
        _add_column_if_missing(feature, column)
    for column in _daily_radar_columns():
        _add_column_if_missing(daily, column)


def downgrade() -> None:
    bind = op.get_bind()
    NewsEvent.__table__.drop(bind=bind, checkfirst=True)
    FinancialStatementSummary.__table__.drop(bind=bind, checkfirst=True)
    MonthlyRevenue.__table__.drop(bind=bind, checkfirst=True)


def _feature_columns():
    return [
        sa.Column("revenue_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("valuation_or_growth_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("news_risk_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("fundamental_data_quality", sa.String(length=20), nullable=False, server_default="missing"),
    ]


def _daily_radar_columns():
    return [
        sa.Column("fundamental_data_quality", sa.String(length=20), nullable=False, server_default="missing"),
        sa.Column("revenue_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("valuation_or_growth_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("news_risk_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("fundamental_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("news_summary_json", sa.Text(), nullable=False, server_default="{}"),
    ]
