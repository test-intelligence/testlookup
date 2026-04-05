"""Add defect_candidates staging table and promotion_source on defects.

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Defect Candidates — staging area before promotion ────────────────────
    op.create_table(
        "defect_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="HIGH"),
        sa.Column("owner_team", sa.String(255), nullable=True),
        sa.Column("component", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duplicate_of", UUID(as_uuid=True), nullable=True),
        sa.Column("is_duplicate", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("evidence_bundle", sa.JSON(), nullable=True),
        sa.Column("criticality_scores", sa.JSON(), nullable=True),
        sa.Column("composite_score", sa.Float(), nullable=True),
        sa.Column("failure_category", sa.String(30), nullable=True),
        sa.Column("member_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),  # pending | promoted | dismissed
        sa.Column("promoted_defect_id", UUID(as_uuid=True), sa.ForeignKey("defects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_defect_cand_run", "defect_candidates", ["run_id"])
    op.create_index("ix_defect_cand_status", "defect_candidates", ["status"])

    # ── Add promotion_source to defects ──────────────────────────────────────
    op.add_column("defects", sa.Column("promotion_source", sa.String(50), nullable=True))  # "cluster_promotion" | "manual" | "jira_import"


def downgrade() -> None:
    op.drop_column("defects", "promotion_source")
    op.drop_table("defect_candidates")
