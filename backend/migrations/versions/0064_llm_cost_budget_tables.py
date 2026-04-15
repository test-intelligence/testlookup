"""project_llm_quota + project_llm_usage + llm_cost_budget feature flag

Revision ID: 0064
Revises: 0063
Create Date: 2026-04-14

Tier 1 item 2 — usage-based billing primitive. Two new tables:

1. ``project_llm_quota`` — optional per-project billing config. One row per
   project, holds included dollars, hard cap, and the at-cap action
   (soft_warn | auto_downgrade_to_ml | auto_downgrade_to_rules | hard_block).
   Projects without a row are treated as unlimited — usage is still recorded
   for reporting but no gate fires.

2. ``project_llm_usage`` — running cost meter. One row per
   ``(project_id, period_start)`` with a unique constraint so the atomic
   upsert-increment in ``services/llm_cost_budget.record_usage`` is safe
   under concurrent worker writes.

Also seeds the ``llm_cost_budget`` feature flag (disabled by default). When
the flag is off both ``record_usage`` and ``check_and_apply_cap`` are no-ops,
so existing deployments see zero behaviour change until an admin turns it on.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── project_llm_quota ──────────────────────────────────────────────────
    op.create_table(
        "project_llm_quota",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("period_type", sa.String(length=20), nullable=False, server_default="MONTHLY"),
        sa.Column("included_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overage_rate_usd", sa.Float(), nullable=False, server_default="1"),
        sa.Column("hard_cap_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("soft_warn_threshold_pct", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "at_cap_action",
            sa.String(length=32),
            nullable=False,
            server_default="AUTO_DOWNGRADE_TO_ML",
        ),
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
        sa.UniqueConstraint("project_id", name="uq_project_llm_quota_project"),
    )

    # ── project_llm_usage ──────────────────────────────────────────────────
    op.create_table(
        "project_llm_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cap_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "period_start", name="uq_project_period"),
    )
    op.create_index(
        "ix_llm_usage_period", "project_llm_usage", ["period_start"],
    )

    # ── Seed feature flag ──────────────────────────────────────────────────
    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'llm_cost_budget', "
            "'Usage-based LLM cost budget with at-cap downgrade. Tier 1 item 2.', "
            "false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM feature_flags WHERE key = 'llm_cost_budget'")
    )
    op.drop_index("ix_llm_usage_period", table_name="project_llm_usage")
    op.drop_table("project_llm_usage")
    op.drop_table("project_llm_quota")
