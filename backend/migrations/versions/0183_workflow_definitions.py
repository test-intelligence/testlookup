"""Add versioned project workflow definitions (E3.1).

The architecture reserved 0177, which is already in origin/main. This new
table therefore takes the next linear revision. Its indexes are created in the
same transaction because the table has no existing rows.

Revision ID: 0183
Revises: 0182
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0183"
down_revision = "0182"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("base", sa.String(16), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "workflow_id", "version", name="uq_workflow_definitions_project_workflow_version"),
        sa.CheckConstraint("version >= 1", name="ck_workflow_definitions_version_positive"),
        sa.CheckConstraint("base IN ('offline', 'deep', 'live')", name="ck_workflow_definitions_base"),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_workflow_definitions_status"),
        sa.CheckConstraint("(status = 'published' AND published_at IS NOT NULL) OR (status = 'draft' AND published_at IS NULL)", name="ck_workflow_definitions_published_at"),
    )
    op.create_index(
        "ix_workflow_definitions_project_status",
        "workflow_definitions",
        ["project_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("workflow_definitions")
