"""Add ``test_cases.assigned_to_user_id`` for auto-assigning failed tests to owners.

Revision ID: 0080
Revises: 0079
Create Date: 2026-05-14

When a test run is finalised, every failed/broken TestCase gets assigned to
the resolved owner of its suite (TestSuiteOwner → default QA lead → manager).
This column is the per-execution assignment ledger — historical TestCase rows
retain the owner they were assigned to *at the time of that run*, even if the
suite owner changes later. (We don't want a reassignment to retroactively
rewrite the action queue for already-triaged failures.)

ON DELETE SET NULL so deleting the assigned user doesn't cascade-blow away
TestCase rows. The application layer enforces that the assignee is the
project's QA_LEAD-resolved owner — no CHECK constraint here for the same
reason ``manager_user_id`` and ``default_qa_lead_user_id`` don't have one
(ProjectMember.role is the application-layer source of truth).

Index on the column so the UI can filter "my failed tests" cheaply:
``WHERE assigned_to_user_id = :me AND status IN ('FAILED','BROKEN')``.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column(
            "assigned_to_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_test_cases_assigned_to_user_id",
        "test_cases",
        ["assigned_to_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_cases_assigned_to_user_id", table_name="test_cases")
    op.drop_column("test_cases", "assigned_to_user_id")
