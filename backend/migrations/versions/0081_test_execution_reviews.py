"""Add ``test_execution_reviews`` for human review of AI-flagged failures.

Revision ID: 0081
Revises: 0080
Create Date: 2026-05-15

The AI analysis pipeline flags failures as ``requires_human_review=True``
when the LLM can't reach a confident verdict (model missing, low confidence,
fallback path, etc.). The UI surfaces this as a "Pending Human Review" tag
on the test case detail page — but until now there was no way for the
reviewer to transition that tag once they'd actually looked.

This migration adds a per-TestCase review overlay table so a human can mark
each AI-flagged failure as ``reviewed``, ``defect_filed``, ``false_positive``,
or ``reproducible``. Naming note: the existing ``test_case_reviews`` table
belongs to the managed-test-authoring workflow (different concept entirely).
This one is keyed on the execution row (``test_cases.id``), not the catalog.

One row per ``test_case_id`` (UNIQUE) — same pattern as
``suite_run_reviews`` (migration 0076). Each transition mutates the row;
prior states aren't preserved here because the action log lives in
``test_case_audit_logs`` for the cases that need a timeline. Keep this
table small + queryable.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_execution_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "test_case_id", UUID(as_uuid=True),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # State machine: pending_review (default) -> reviewed | defect_filed
        # | false_positive | reproducible. Enforced here as well as in the
        # service so bulk imports/direct SQL cannot create impossible rows.
        sa.Column("state", sa.String(30), nullable=False, server_default="pending_review"),
        sa.Column(
            "reviewed_by_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Defect tracker link captured when state = defect_filed. Optional
        # in all other states. Bounded to keep payloads small in the UI list.
        sa.Column("defect_link", sa.String(2000), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("test_case_id", name="uq_ter_test_case_id"),
        sa.CheckConstraint(
            "state IN ('pending_review', 'reviewed', 'defect_filed', "
            "'false_positive', 'reproducible')",
            name="ck_ter_state_valid",
        ),
        sa.CheckConstraint(
            "state <> 'defect_filed' OR NULLIF(BTRIM(defect_link), '') IS NOT NULL",
            name="ck_ter_defect_link_required",
        ),
    )
    op.create_index("ix_ter_project_state", "test_execution_reviews", ["project_id", "state"])
    op.create_index("ix_ter_reviewed_by", "test_execution_reviews", ["reviewed_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_ter_reviewed_by", table_name="test_execution_reviews")
    op.drop_index("ix_ter_project_state", table_name="test_execution_reviews")
    op.drop_table("test_execution_reviews")
