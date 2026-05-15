"""Add ``projects.default_qa_lead_user_id`` for first-class default QA-lead fallback.

Revision ID: 0079
Revises: 0078
Create Date: 2026-05-14

``manager_user_id`` (migration 0076) was repurposed as the suite-owner fallback,
but the user-facing model distinguishes "program manager" from "default QA
lead." A project can have multiple QA leads (via ``project_members.role =
'QA_LEAD'``); one of them is the *default* — every new TestSuite materialised
during ingest gets a ``TestSuiteOwner`` row pointing at this user, and the
owner-resolution chain falls back to this user before ``manager_user_id``.

Backfill: leave ``default_qa_lead_user_id`` NULL on existing rows. The owner
resolution service keeps the legacy ``manager_user_id`` fallback as a second
step, so deployed projects keep their current behaviour until an admin sets
the new field. New projects can wire it in their create payload.

ON DELETE SET NULL so deleting the chosen user doesn't cascade-blow away the
project row. ``ProjectMember`` enforces project-scoped role separately — the
column doesn't carry a CHECK constraint because role checks happen in the
application layer (mirrors ``manager_user_id`` from 0076).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "default_qa_lead_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_projects_default_qa_lead_user_id",
        "projects",
        ["default_qa_lead_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_default_qa_lead_user_id", table_name="projects")
    op.drop_column("projects", "default_qa_lead_user_id")
