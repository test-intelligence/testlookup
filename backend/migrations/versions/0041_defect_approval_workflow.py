"""Add approval workflow columns to defects table (Phase 4 Safety & HITL).

Adds approval_status, approved_by, approved_at, and policy_evaluation to
support human-in-the-loop approval before high-impact actions execute.

Existing defects default to 'approved' (backward compatible).

Revision ID: 0041
Revises: 0040
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "defects",
        sa.Column(
            "approval_status",
            sa.String(20),
            nullable=False,
            server_default="approved",
        ),
    )
    op.add_column(
        "defects",
        sa.Column(
            "approved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "defects",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "defects",
        sa.Column("policy_evaluation", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_defects_approval_status", "defects", ["approval_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_defects_approval_status", table_name="defects")
    op.drop_column("defects", "policy_evaluation")
    op.drop_column("defects", "approved_at")
    op.drop_column("defects", "approved_by")
    op.drop_column("defects", "approval_status")
