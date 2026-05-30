"""Add per-failure triage workflow columns on ``test_cases``.

Revision ID: 0088
Revises: 0087
Create Date: 2026-05-18

Adds the columns the new triage workflow needs:

* ``triage_status``         — enum-as-String(30); default ``PENDING_REVIEW``
  so every existing FAILED/BROKEN row stays on its assignee's inbox
  until they explicitly resolve it.
* ``triage_notes``          — optional free-form context, capped at 2000
  chars. Typically the Jira link for ``DEFECT_CREATED`` or a rationale
  for ``WONT_FIX`` / ``REVIEWED_APPROVED``.
* ``triage_updated_at``     — when the most recent status change landed.
* ``triage_updated_by_user_id`` — who changed the status. ``SET NULL`` on
  user deletion so the audit trail outlives account deactivation
  (matches the rubric in ``backend/CLAUDE.md`` § Foreign-key ondelete).

Composite index ``(assigned_to_user_id, triage_status)`` so the
``/my-failures`` inbox stays a single index scan instead of a full
table scan that filters in memory. Pre-existing
``ix_test_cases_assigned_to_user_id`` is retained — other queries
(reassignment audits, "who owns what") still benefit from the
single-column index.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column(
            "triage_status",
            sa.String(length=30),
            nullable=False,
            server_default="PENDING_REVIEW",
        ),
    )
    op.add_column(
        "test_cases",
        sa.Column("triage_notes", sa.String(length=2000), nullable=True),
    )
    op.add_column(
        "test_cases",
        sa.Column(
            "triage_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "test_cases",
        sa.Column(
            "triage_updated_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_test_cases_assignee_triage",
        "test_cases",
        ["assigned_to_user_id", "triage_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_cases_assignee_triage", table_name="test_cases")
    op.drop_column("test_cases", "triage_updated_by_user_id")
    op.drop_column("test_cases", "triage_updated_at")
    op.drop_column("test_cases", "triage_notes")
    op.drop_column("test_cases", "triage_status")
