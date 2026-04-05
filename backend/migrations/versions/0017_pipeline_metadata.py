"""Add pipeline execution metadata and stage skip context columns

Revision ID: 0017
Revises: 0016
Create Date: 2026-03-31

Changes (all additive — nullable, no backfill required):
  agent_pipeline_runs:
    - execution_metadata  JSONB   — {tools_used, schema_version, fallback_used}
    - provenance_metadata JSONB   — {generated_by, tools_used_count, generated_at}
  agent_stage_results:
    - skipped_reason  TEXT          — human-readable reason why a stage was skipped
    - execution_path  VARCHAR(50)   — ExecutionPath enum value
    - fallback_used   BOOLEAN       — true when deterministic fallback ran instead of LLM
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── agent_pipeline_runs ─────────────────────────────────────
    op.add_column(
        "agent_pipeline_runs",
        sa.Column("execution_metadata", sa.JSON(), nullable=True,
                  comment="Pipeline-level execution context: tools_used, schema_version, fallback_used"),
    )
    op.add_column(
        "agent_pipeline_runs",
        sa.Column("provenance_metadata", sa.JSON(), nullable=True,
                  comment="Provenance: generated_by, tools_used_count, generated_at"),
    )

    # ── agent_stage_results ─────────────────────────────────────
    op.add_column(
        "agent_stage_results",
        sa.Column("skipped_reason", sa.Text(), nullable=True,
                  comment="Why this stage was skipped (e.g. 'no failures detected')"),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column("execution_path", sa.String(50), nullable=True,
                  comment="ExecutionPath enum: executed | all_green_skip | low_confidence_skip | conditional_skip"),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column("fallback_used", sa.Boolean(), nullable=True,
                  comment="True when deterministic fallback ran instead of LLM"),
    )


def downgrade() -> None:
    op.drop_column("agent_stage_results", "fallback_used")
    op.drop_column("agent_stage_results", "execution_path")
    op.drop_column("agent_stage_results", "skipped_reason")
    op.drop_column("agent_pipeline_runs", "provenance_metadata")
    op.drop_column("agent_pipeline_runs", "execution_metadata")
