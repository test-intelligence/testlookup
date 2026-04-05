"""Add observability columns to agent_stage_results (Phase 6).

Adds per-stage token counting, cost tracking, error categorisation,
confidence, evidence count, and route rationale for enterprise-grade
agent observability.

Revision ID: 0043
Revises: 0042
"""
from alembic import op
import sqlalchemy as sa

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_stage_results", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_stage_results", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_stage_results", sa.Column("total_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_stage_results", sa.Column("llm_calls_count", sa.Integer(), nullable=True))
    op.add_column("agent_stage_results", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("agent_stage_results", sa.Column("error_category", sa.String(30), nullable=True))
    op.add_column("agent_stage_results", sa.Column("confidence_score", sa.Integer(), nullable=True))
    op.add_column("agent_stage_results", sa.Column("evidence_count", sa.Integer(), nullable=True))
    op.add_column("agent_stage_results", sa.Column("route_rationale", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_stage_results", "route_rationale")
    op.drop_column("agent_stage_results", "evidence_count")
    op.drop_column("agent_stage_results", "confidence_score")
    op.drop_column("agent_stage_results", "error_category")
    op.drop_column("agent_stage_results", "cost_usd")
    op.drop_column("agent_stage_results", "llm_calls_count")
    op.drop_column("agent_stage_results", "total_tokens")
    op.drop_column("agent_stage_results", "output_tokens")
    op.drop_column("agent_stage_results", "input_tokens")
