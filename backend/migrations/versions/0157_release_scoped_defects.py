"""release-scoped defects — found-in and affects

Revision ID: 0157
Revises: 0156
Create Date: 2026-09-04

Two columns, because a defect has two relationships to a release and conflating
them makes the gate wrong in opposite directions.

``release_id`` — where the defect was FOUND. Derived from the failing test's
run, so it is a fact about history and never changes.

``affects_releases`` — which releases the defect IMPACTS. A defect found in
2.3.0 and still open blocks 2.4.0 too; a defect found in 2.4.0 and fixed before
2.5.0 branched blocks neither. Gating on "found in this release" would let every
inherited defect through, and gating on it alone would also block a release for
a defect somebody already fixed.

The backfill sets ``release_id`` only. ``affects_releases`` is left NULL rather
than seeded with ``[release_id]``: a guess written into a column is
indistinguishable from a human's assertion later, and the read path treats NULL
as "falls back to found-in" so nothing is lost by not guessing.

Backfilled in batches with an explicit bound, the same shape as 0153. An
unbounded UPDATE over a large defects table takes a lock for its whole duration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0157"
down_revision = "0156"
branch_labels = None
depends_on = None

BATCH = 5000


def upgrade() -> None:
    op.add_column(
        "defects",
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("defects", sa.Column("affects_releases", sa.JSON(), nullable=True))

    # Where the defect was found, derived through the failing test's run.
    #
    # Batched and self-terminating: the WHERE clause excludes rows already set,
    # so each pass shrinks the candidate set and the loop ends. A single
    # unbounded UPDATE would hold a lock over the whole table for its duration.
    conn = op.get_bind()
    while True:
        result = conn.execute(
            sa.text(
                """
                UPDATE defects d
                SET release_id = sub.primary_release_id
                FROM (
                    SELECT dd.id, tr.primary_release_id
                    FROM defects dd
                    JOIN test_cases tc ON tc.id = dd.test_case_id
                    JOIN test_runs tr ON tr.id = tc.test_run_id
                    WHERE dd.release_id IS NULL
                      AND dd.test_case_id IS NOT NULL
                      AND tr.primary_release_id IS NOT NULL
                      -- The defect and the run must belong to the same project.
                      -- Nothing at the database level enforces that a test case
                      -- and a defect share one, and a cross-project row would
                      -- otherwise stamp another tenant's release onto this
                      -- defect.
                      AND tr.project_id = dd.project_id
                    LIMIT :batch
                ) AS sub
                WHERE d.id = sub.id
                """
            ),
            {"batch": BATCH},
        )
        if result.rowcount is None or result.rowcount < BATCH:
            break

    # Partial: most defects predate the release axis or were filed by hand and
    # carry NULL here. Postgres treats NULLs as distinct, so an unfiltered index
    # would be mostly dead weight over rows the gate never asks about.
    op.create_index(
        "ix_defects_release_open",
        "defects",
        ["release_id", "resolution_status"],
        postgresql_where=sa.text("release_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_defects_release_open", table_name="defects")
    op.drop_column("defects", "affects_releases")
    op.drop_column("defects", "release_id")
