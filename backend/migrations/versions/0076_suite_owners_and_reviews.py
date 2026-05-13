"""Suite owners + per-run review overlay (human-in-the-loop for AI analysis).

Revision ID: 0076
Revises: 0075
Create Date: 2026-05-12

Adds three pieces:

  1. ``projects.manager_user_id`` — nullable FK to ``users.id`` (SET NULL on
     delete). Used as the default owner when a suite has no explicit owner.
  2. ``test_suite_owners(project_id, suite_name, owner_user_id)`` — explicit
     per-suite owner. Keyed by suite_name (string) so we match what the Test
     Suites tab already shows (the legacy aggregated view). Unique on
     ``(project_id, suite_name)``.
  3. ``suite_run_reviews(project_id, suite_name, test_run_id, state, …)`` —
     one review record per (run, suite). State is a small string set:
     ``pending`` (default on insert), ``confirmed``, ``acknowledged``,
     ``review_later``. Non-gating overlay — AI analysis still completes
     independently; this just records the owner's verdict on it.

All three FK columns to ``users`` use ON DELETE SET NULL so a user delete
doesn't cascade-blow away suites or reviews. The CASCADE is reserved for
project deletes — deleting a project should wipe its suite owners + reviews.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "manager_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_projects_manager_user_id", "projects", ["manager_user_id"])

    op.create_table(
        "test_suite_owners",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suite_name", sa.String(length=500), nullable=False),
        sa.Column(
            "owner_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id", "suite_name", name="uq_test_suite_owners_proj_suite"
        ),
    )
    op.create_index(
        "ix_test_suite_owners_project_id", "test_suite_owners", ["project_id"]
    )

    op.create_table(
        "suite_run_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suite_name", sa.String(length=500), nullable=False),
        sa.Column(
            "test_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "state",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "test_run_id", "suite_name", name="uq_suite_run_reviews_run_suite"
        ),
    )
    op.create_index(
        "ix_suite_run_reviews_project_suite",
        "suite_run_reviews",
        ["project_id", "suite_name"],
    )
    op.create_index("ix_suite_run_reviews_state", "suite_run_reviews", ["state"])
    op.create_index(
        "ix_suite_run_reviews_test_run_id", "suite_run_reviews", ["test_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_suite_run_reviews_test_run_id", table_name="suite_run_reviews")
    op.drop_index("ix_suite_run_reviews_state", table_name="suite_run_reviews")
    op.drop_index(
        "ix_suite_run_reviews_project_suite", table_name="suite_run_reviews"
    )
    op.drop_table("suite_run_reviews")
    op.drop_index("ix_test_suite_owners_project_id", table_name="test_suite_owners")
    op.drop_table("test_suite_owners")
    op.drop_index("ix_projects_manager_user_id", table_name="projects")
    op.drop_column("projects", "manager_user_id")
