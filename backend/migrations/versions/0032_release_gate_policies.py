"""Add release gate policies for per-project policy-based release gates (ENT-02).

Tables: release_gate_policies.
Columns added: release_decisions.policy_id, release_decisions.policy_evaluation.

Revision ID: 0032
Revises: 0031
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Release Gate Policies ────────────────────────────────────
    op.create_table(
        "release_gate_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("is_draft", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False),
        sa.Column("activated_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_rgp_project_active", "release_gate_policies", ["project_id", "is_active"])
    op.create_unique_constraint("uq_rgp_project_version", "release_gate_policies", ["project_id", "version"])

    # ── Extend release_decisions with policy context ─────────────
    op.add_column(
        "release_decisions",
        sa.Column("policy_id", UUID(as_uuid=True), sa.ForeignKey("release_gate_policies.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "release_decisions",
        sa.Column("policy_evaluation", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("release_decisions", "policy_evaluation")
    op.drop_column("release_decisions", "policy_id")
    op.drop_table("release_gate_policies")
