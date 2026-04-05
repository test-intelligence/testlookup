"""Add AI enhancement columns for tools_used, structured summary, and 7-dimension risk scoring

Revision ID: 0015
Revises: 0014
Create Date: 2026-03-31

Changes:
  - ai_analysis: add tools_used JSONB (list of ReAct tool names actually invoked)
  - release_decisions: add dimension_scores JSONB, composite_risk FLOAT
    (stores the 7-dimension deterministic scores from ReleaseRiskAgent)
  - No new tables in this migration — new MongoDB collections (run_summaries
    structured layers) require no DDL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ai_analysis: record which ReAct tools were actually invoked
    op.add_column(
        "ai_analysis",
        sa.Column(
            "tools_used",
            sa.JSON(),
            nullable=True,
            comment="List of LangChain tool names invoked during ReAct investigation",
        ),
    )

    # release_decisions: store the 7-dimension risk scores for auditability
    op.add_column(
        "release_decisions",
        sa.Column(
            "dimension_scores",
            sa.JSON(),
            nullable=True,
            comment="7-dimension deterministic risk scores: user_impact, env_sensitivity, etc.",
        ),
    )
    op.add_column(
        "release_decisions",
        sa.Column(
            "composite_risk",
            sa.Float(),
            nullable=True,
            comment="Weighted composite risk score 0-100 derived from dimension_scores",
        ),
    )


def downgrade() -> None:
    op.drop_column("release_decisions", "composite_risk")
    op.drop_column("release_decisions", "dimension_scores")
    op.drop_column("ai_analysis", "tools_used")
