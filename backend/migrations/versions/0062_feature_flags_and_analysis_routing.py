"""feature_flags + ai_analysis.routing_metadata

Revision ID: 0062
Revises: 0061
Create Date: 2026-04-14

Tier-0 infrastructure for the enterprise-QA release train:

1. ``feature_flags`` table — generic capability toggle store gated by
   project allow-list, role allow-list, and rollout percent. Replaces the
   hand-rolled ``KNOWLEDGE_RAG_ENABLED`` Redis→DB→env fallback and becomes
   the single home for every new feature gate going forward.

2. ``ai_analysis.routing_metadata`` (JSONB) — per-test decision audit
   persisted from ``analysis_agent._analyse_one`` so the decision-trail UI
   can render "why did the AI choose engine X for this test" without
   reconstructing the decision from log scraping or Mongo event lookups.

Migration also seeds a ``knowledge_rag`` row in ``feature_flags`` that
mirrors the current env-var default so the RAG code can switch to the new
service without an observable behaviour change.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import os

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── feature_flags ───────────────────────────────────────────────────────
    # Migration 0030 created a legacy single-column toggle table with
    # (flag_key, scope, enabled, config). Tier 0A rewrites it onto the
    # per-project/per-role/rollout schema below. Drop the legacy shape
    # first so create_table doesn't collide on a fresh upgrade chain.
    op.execute(sa.text("DROP TABLE IF EXISTS feature_flags CASCADE"))
    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled_global", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled_projects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rollout_percent", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("key", name="uq_feature_flags_key"),
    )
    op.create_index("ix_feature_flags_key", "feature_flags", ["key"])

    # Seed the KNOWLEDGE_RAG_ENABLED replacement row so the new service
    # can switch over without changing effective behaviour. We read the
    # current env value to decide the initial state — if it's unset, we
    # default to ``false`` (safer for existing deployments).
    initial_rag_enabled = os.getenv("KNOWLEDGE_RAG_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    )
    op.execute(
        sa.text(
            "INSERT INTO feature_flags (id, key, description, enabled_global, "
            "rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'knowledge_rag', "
            "'RAG-backed knowledge grounding for test case generation.', "
            ":enabled, 100, now(), now())"
        ).bindparams(enabled=initial_rag_enabled)
    )

    # ── ai_analysis.routing_metadata ───────────────────────────────────────
    op.add_column(
        "ai_analysis",
        sa.Column(
            "routing_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_analysis", "routing_metadata")
    op.drop_index("ix_feature_flags_key", table_name="feature_flags")
    op.drop_table("feature_flags")
