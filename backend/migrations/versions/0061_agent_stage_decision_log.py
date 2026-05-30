"""agent_stage_results: decision_log + fallback_reason + analysis_mode columns

Revision ID: 0061
Revises: 0060
Create Date: 2026-04-14

Adds three columns that make agent decisions traceable end-to-end:

1. ``decision_log`` (JSONB) — ordered list of
   ``{at, decision_point, chosen, rationale, context}`` records produced by
   ``BaseAgent.log_decision()``. Each entry is one conditional branch taken
   by the agent (route selection, fallback trigger, confidence penalty,
   skip reason, etc.). Replaces the need to reconstruct decisions from log
   scraping.

2. ``fallback_reason`` (String(200)) — short reason recorded whenever
   ``fallback_used=True``. Previously only the boolean was persisted; ops
   could see a fallback happened but not *why*. This unblocks fallback-rate
   dashboards and post-incident analysis without Mongo event-log joins.

3. ``analysis_mode`` (String(20)) — which engine actually ran (llm|ml|rules|
   auto). Needed because the router may swap the requested mode for a
   fallback mid-flight; the stage record now captures the final engine.

All three columns are nullable so existing rows remain valid — we do not
backfill historical data.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_stage_results",
        sa.Column("decision_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column("fallback_reason", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column("analysis_mode", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_stage_results", "analysis_mode")
    op.drop_column("agent_stage_results", "fallback_reason")
    op.drop_column("agent_stage_results", "decision_log")
