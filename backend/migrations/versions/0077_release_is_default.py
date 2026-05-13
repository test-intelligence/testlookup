"""Per-project default release flag.

Revision ID: 0077
Revises: 0076
Create Date: 2026-05-12

Adds ``releases.is_default`` so each project can designate one release as its
fallback. Mirrors the ``test_suites.is_default`` pattern (migration 0075):

  * BOOLEAN NOT NULL DEFAULT FALSE — every existing row backfills to FALSE.
  * Partial unique index ``ix_releases_project_default`` on ``(project_id)``
    WHERE ``is_default IS TRUE`` — at most one default per project; lets the
    set-default path swap atomically via a single UPDATE without a transient
    "two defaults" state if it temporarily violates the constraint.

Used by ``release_linker.get_or_create_default_release`` and the ingestion +
live-session fallbacks when the caller didn't supply ``release_name``.
"""
from alembic import op
import sqlalchemy as sa


revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "releases",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_releases_project_default",
        "releases",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_default IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_releases_project_default", table_name="releases")
    op.drop_column("releases", "is_default")
