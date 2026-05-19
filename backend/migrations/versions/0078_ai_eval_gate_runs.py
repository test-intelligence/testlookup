"""Persist AI evaluation release gate run history.

Revision ID: 0078
Revises: 0077
Create Date: 2026-05-13

Stores aggregate agent-stack release gate decisions keyed by change id and
manifest checksum so release managers can audit historical PASS/FAIL/WARN
decisions without replaying golden datasets.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_eval_gate_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("change_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("manifest_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gate_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("blocking_gates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version_changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "evaluated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_aeg_change_id", "ai_eval_gate_runs", ["change_id"])
    op.create_index("ix_aeg_status", "ai_eval_gate_runs", ["status"])
    op.create_index(
        "ix_aeg_manifest_checksum",
        "ai_eval_gate_runs",
        ["manifest_checksum_sha256"],
    )
    op.create_index("ix_aeg_evaluated_at", "ai_eval_gate_runs", ["evaluated_at"])


def downgrade() -> None:
    op.drop_index("ix_aeg_evaluated_at", table_name="ai_eval_gate_runs")
    op.drop_index("ix_aeg_manifest_checksum", table_name="ai_eval_gate_runs")
    op.drop_index("ix_aeg_status", table_name="ai_eval_gate_runs")
    op.drop_index("ix_aeg_change_id", table_name="ai_eval_gate_runs")
    op.drop_table("ai_eval_gate_runs")
