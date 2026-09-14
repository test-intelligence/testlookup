"""G2 tier-comparison manifests and bounded shadow pairs (E9.3).

The roadmap reserved 0178 for this change, but origin/main already used 0178
for invocation idempotency and 0179 for agent configs.  The next single head is
therefore 0180.

``ai_eval_gate_runs`` already exists, so its lookup index is built concurrently
inside an autocommit block.  ``ai_eval_shadow_pairs`` is new and may create its
own indexes transactionally.  Downgrade removes every addition.

Revision ID: 0180
Revises: 0179
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0180"
down_revision = "0179"
branch_labels = None
depends_on = None

GATE_TABLE = "ai_eval_gate_runs"
PAIR_TABLE = "ai_eval_shadow_pairs"
GATE_INDEX = "ix_aeg_tier_comparison_lookup"


def upgrade() -> None:
    op.add_column(GATE_TABLE, sa.Column("gate_type", sa.String(40), nullable=True))
    op.add_column(
        GATE_TABLE,
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE")),
    )
    op.add_column(GATE_TABLE, sa.Column("agent_id", sa.String(80), nullable=True))
    op.add_column(GATE_TABLE, sa.Column("baseline_tier", sa.String(20), nullable=True))
    op.add_column(GATE_TABLE, sa.Column("candidate_tier", sa.String(20), nullable=True))
    op.add_column(GATE_TABLE, sa.Column("sample_count", sa.Integer(), nullable=True))

    op.create_table(
        PAIR_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("sample_key", sa.String(128), nullable=False),
        sa.Column("incumbent_tier", sa.String(20), nullable=False),
        sa.Column("candidate_tier", sa.String(20), nullable=False),
        sa.Column("incumbent_output", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_output", postgresql.JSONB(), nullable=False),
        sa.Column("incumbent_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("label", postgresql.JSONB(), nullable=True),
        sa.Column("labelled_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("labelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "agent_id", "sample_key", name="uq_ai_eval_shadow_pair_sample"),
        sa.CheckConstraint(
            "label_status IN ('pending', 'human_labelled', 'golden_match')",
            name="ck_aesp_label_status",
        ),
        sa.CheckConstraint("total_tokens >= 0", name="ck_aesp_tokens_nonnegative"),
    )
    op.create_index(
        "ix_aesp_project_agent_created",
        PAIR_TABLE,
        ["project_id", "agent_id", "created_at"],
    )
    with op.get_context().autocommit_block():
        op.create_index(
            GATE_INDEX,
            GATE_TABLE,
            ["project_id", "agent_id", "candidate_tier", "evaluated_at"],
            postgresql_where=sa.text("gate_type = 'tier_comparison'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            GATE_INDEX,
            table_name=GATE_TABLE,
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_table(PAIR_TABLE)
    op.drop_column(GATE_TABLE, "sample_count")
    op.drop_column(GATE_TABLE, "candidate_tier")
    op.drop_column(GATE_TABLE, "baseline_tier")
    op.drop_column(GATE_TABLE, "agent_id")
    op.drop_column(GATE_TABLE, "project_id")
    op.drop_column(GATE_TABLE, "gate_type")
