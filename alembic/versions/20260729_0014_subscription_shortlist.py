"""Add subscription-backed short-term shortlist tables.

Revision ID: 20260729_0014
Revises: 20260629_0013
Create Date: 2026-07-29 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260729_0014"
down_revision = "20260629_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("shortlist_runs"):
        op.create_table(
            "shortlist_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("run_date", sa.Date(), nullable=False),
            sa.Column("source_radar_run_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("cutoff_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="quant_ready"),
            sa.Column("ai_status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("model_version", sa.String(80), nullable=False, server_default="daily-radar-balanced-v1"),
            sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("selected_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", name="uq_shortlist_runs_run_id"),
        )
        op.create_index("ix_shortlist_runs_run_id", "shortlist_runs", ["run_id"])
        op.create_index("ix_shortlist_runs_run_date", "shortlist_runs", ["run_date"])
        op.create_index("ix_shortlist_runs_status", "shortlist_runs", ["status"])

    if not inspector.has_table("research_evidence"):
        op.create_table(
            "research_evidence",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("evidence_id", sa.String(64), nullable=False),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("symbol", sa.String(20), nullable=False),
            sa.Column("research_scope", sa.String(24), nullable=False, server_default="daily"),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="accepted"),
            sa.Column("event_type", sa.String(60), nullable=False, server_default=""),
            sa.Column("sentiment", sa.String(16), nullable=False, server_default="neutral"),
            sa.Column("relevance", sa.Float(), nullable=False, server_default="0"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("impact_horizon", sa.String(40), nullable=False, server_default=""),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("risk_tags_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("sources_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("exclude_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("validation_errors_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("provider", sa.String(40), nullable=False, server_default="codex_subscription"),
            sa.Column("model", sa.String(80), nullable=False, server_default=""),
            sa.Column("prompt_version", sa.String(40), nullable=False, server_default="shortlist-research-v1"),
            sa.Column("cutoff_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "symbol", "research_scope", name="uq_research_evidence_run_symbol_scope"),
        )
        op.create_index("ix_research_evidence_evidence_id", "research_evidence", ["evidence_id"], unique=True)
        op.create_index("ix_research_evidence_input_hash", "research_evidence", ["input_hash"])

    if not inspector.has_table("shortlist_items"):
        op.create_table(
            "shortlist_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("symbol", sa.String(20), nullable=False),
            sa.Column("name", sa.String(100), nullable=False, server_default=""),
            sa.Column("quant_rank", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("final_rank", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("quant_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("price", sa.Float(), nullable=False, server_default="0"),
            sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("ai_status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("ai_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("ai_sentiment", sa.String(16), nullable=False, server_default="neutral"),
            sa.Column("ai_as_of", sa.DateTime(), nullable=True),
            sa.Column("evidence_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("reason_tags_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("risk_tags_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("blocker_tags_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("excluded_by_ai_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("forward_return_5d", sa.Float(), nullable=True),
            sa.Column("forward_return_20d", sa.Float(), nullable=True),
            sa.Column("forward_return_40d", sa.Float(), nullable=True),
            sa.Column("forward_return_60d", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "symbol", name="uq_shortlist_item_run_symbol"),
        )
        op.create_index("ix_shortlist_items_run_id", "shortlist_items", ["run_id"])
        op.create_index("ix_shortlist_items_symbol", "shortlist_items", ["symbol"])
        op.create_index("ix_shortlist_items_selected", "shortlist_items", ["selected"])

    if not inspector.has_table("scheduled_job_runs"):
        op.create_table(
            "scheduled_job_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_name", sa.String(80), nullable=False),
            sa.Column("run_key", sa.String(120), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("rows_affected", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.UniqueConstraint("run_key", name="uq_scheduled_job_runs_run_key"),
        )
        op.create_index("ix_scheduled_job_runs_job_name", "scheduled_job_runs", ["job_name"])
        op.create_index("ix_scheduled_job_runs_status", "scheduled_job_runs", ["status"])


def downgrade() -> None:
    for table_name in ("scheduled_job_runs", "shortlist_items", "research_evidence", "shortlist_runs"):
        if inspect(op.get_bind()).has_table(table_name):
            op.drop_table(table_name)
