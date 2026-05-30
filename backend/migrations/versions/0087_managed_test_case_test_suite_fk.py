"""Add ``managed_test_cases.test_suite_id`` FK to ``test_suites``.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-17

Phase I follow-up. Authored / AI-generated test cases ("managed" cases) carry
``suite_name`` as a free-text ``String(500)`` column. That's fine for display
but doesn't participate in the same catalog graph that executed cases now use
via ``canonical_test_cases.test_suite_id``. The two surfaces diverge in
predictable ways:

  * A rename of a TestSuite (via ``PATCH /api/v1/suites/{id}``) updates every
    executed case's view but leaves managed cases pointing at the old name
    string. Two cases that used to be "in the same suite" silently split.
  * Cross-project moves are blocked for executed cases (see
    ``test_suite_service.link_canonical_to_suite``); managed cases have no
    such safeguard because there's no relational anchor to enforce against.
  * Suite-scoped queries (e.g. "every test case in this suite, authored or
    executed") need a UNION of structured + fuzzy-string lookups.

This migration adds a nullable FK so managed cases can opt into the same
graph. ``ON DELETE SET NULL`` so deleting a suite doesn't cascade-blow away
authored cases — they survive with the legacy ``suite_name`` column intact
and can be relinked later by the suite reaper / a future migration.

Columns added:

  * ``test_suite_id`` (``UUID``, nullable, FK → ``test_suites.id`` ON DELETE
    SET NULL) — the structured anchor. Nullable so existing rows + the
    service backfill stays opt-in until the read path is ready.

Index added:

  * ``ix_mtc_test_suite_id`` — mirrors ``ix_ctc_test_suite_id`` on
    ``canonical_test_cases`` so suite-scoped queries against the merged
    catalog are symmetric.

Downgrade drops the index then the column. The legacy ``suite_name`` column
is left in place unchanged — the FK is purely additive.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managed_test_cases",
        sa.Column(
            "test_suite_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_suites.id", ondelete="SET NULL"),
            nullable=True,
            comment=(
                "Optional structured anchor to the test_suites entity. "
                "Nullable so historical rows survive without backfill; the "
                "service layer resolves or creates the TestSuite when a "
                "create/update payload supplies a suite_name. SET NULL on "
                "delete so a suite removal doesn't cascade managed cases "
                "into nothing — they keep their legacy suite_name string."
            ),
        ),
    )
    op.create_index(
        "ix_mtc_test_suite_id",
        "managed_test_cases",
        ["test_suite_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mtc_test_suite_id", table_name="managed_test_cases")
    op.drop_column("managed_test_cases", "test_suite_id")
