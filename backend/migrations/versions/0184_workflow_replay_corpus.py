"""Add workflow replay evaluation evidence (E9.5).

The architecture used ``0182+`` as a planning label. Origin/main already uses
0182 and 0183, so this migration takes the next linear revision. The replay
table is new, therefore its lookup index is created transactionally.

Revision ID: 0184
Revises: 0183
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0184"
down_revision = "0183"
branch_labels = None
depends_on = None

TABLE = "workflow_replay_corpus"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(80), nullable=False),
        sa.Column("agent_id", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=False),
        sa.Column("stage_status", sa.String(20), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "agent_id",
            "prompt_version",
            "input_hash",
            name="uq_workflow_replay_agent_prompt_input",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_replay_input_hash",
        ),
        sa.CheckConstraint("cost_usd >= 0", name="ck_workflow_replay_cost_nonnegative"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_workflow_replay_latency_nonnegative"),
    )
    op.create_index(
        "ix_workflow_replay_project_run",
        TABLE,
        ["project_id", "pipeline_run_id"],
    )
    op.add_column("workflow_definitions", sa.Column("eval_verdict", sa.String(24)))
    op.add_column("workflow_definitions", sa.Column("eval_coverage", sa.Float()))
    op.add_column(
        "workflow_definitions",
        sa.Column(
            "eval_gate_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_eval_gate_runs.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("workflow_definitions", sa.Column("evaluated_at", sa.DateTime(timezone=True)))
    op.add_column(
        "workflow_definitions",
        sa.Column(
            "eval_regression_accepted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("workflow_definitions", sa.Column("eval_regression_reason", sa.Text()))
    op.add_column(
        "workflow_definitions",
        sa.Column(
            "eval_regression_accepted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "workflow_definitions",
        sa.Column("eval_regression_accepted_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_workflow_definitions_eval_verdict",
        "workflow_definitions",
        "eval_verdict IS NULL OR eval_verdict IN ('pass', 'fail', 'insufficient_samples')",
    )
    op.create_check_constraint(
        "ck_workflow_definitions_eval_coverage",
        "workflow_definitions",
        "eval_coverage IS NULL OR (eval_coverage >= 0 AND eval_coverage <= 1)",
    )
    op.create_check_constraint(
        "ck_workflow_definitions_eval_evidence",
        "workflow_definitions",
        "(eval_verdict IS NULL AND eval_coverage IS NULL AND eval_gate_run_id IS NULL AND "
        "evaluated_at IS NULL) OR (eval_verdict IS NOT NULL AND eval_coverage IS NOT NULL AND "
        "eval_gate_run_id IS NOT NULL AND evaluated_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_workflow_definitions_regression_acceptance",
        "workflow_definitions",
        "(eval_regression_accepted IS FALSE AND eval_regression_reason IS NULL AND "
        "eval_regression_accepted_by IS NULL AND eval_regression_accepted_at IS NULL) OR "
        "(eval_regression_accepted IS TRUE AND eval_verdict = 'fail' AND "
        "length(trim(eval_regression_reason)) > 0 AND eval_regression_accepted_by IS NOT NULL "
        "AND eval_regression_accepted_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workflow_definitions_regression_acceptance",
        "workflow_definitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_workflow_definitions_eval_evidence",
        "workflow_definitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_workflow_definitions_eval_coverage",
        "workflow_definitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_workflow_definitions_eval_verdict",
        "workflow_definitions",
        type_="check",
    )
    op.drop_column("workflow_definitions", "eval_regression_accepted_at")
    op.drop_column("workflow_definitions", "eval_regression_accepted_by")
    op.drop_column("workflow_definitions", "eval_regression_reason")
    op.drop_column("workflow_definitions", "eval_regression_accepted")
    op.drop_column("workflow_definitions", "evaluated_at")
    op.drop_column("workflow_definitions", "eval_gate_run_id")
    op.drop_column("workflow_definitions", "eval_coverage")
    op.drop_column("workflow_definitions", "eval_verdict")
    op.drop_table(TABLE)
