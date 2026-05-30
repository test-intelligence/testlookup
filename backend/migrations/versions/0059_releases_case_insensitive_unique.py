"""releases: case-insensitive unique (project_id, name)

Revision ID: 0059
Revises: 0058
Create Date: 2026-04-13

Closes the race window in ``services/release_linker.resolve_or_create_release``
where two concurrent ingestion requests for the same release name could each
SELECT-and-miss, then each INSERT, producing duplicate ``releases`` rows with
the same name (different case or identical). The resulting split-brain caused
test runs from the same release to be silently attributed to different
``release_id`` values, breaking release gate and metrics aggregation.

The fix is a database-level ``UNIQUE`` functional index on
``(project_id, lower(name))`` so duplicate inserts now fail fast with an
``IntegrityError`` that the service layer catches and turns into a retry.

Upgrade strategy:
    1. Coalesce existing duplicates: pick the oldest release per
       ``(project_id, lower(name))`` group as canonical, re-point every
       ``release_test_run_links.release_id`` at the canonical row, then
       delete the duplicate ``releases`` rows (CASCADE removes any orphan
       phases they owned).
    2. Create the functional unique index.

Downgrade is a simple index drop; we do not attempt to re-create duplicate
rows.
"""
from alembic import op

revision = "0059"
down_revision = "0058"


def upgrade() -> None:
    # 1. Re-point all test-run links from duplicate releases to the canonical
    #    (oldest) release in each (project_id, lower(name)) group. We do this
    #    before deleting the duplicates so no CASCADE drops existing links.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                project_id,
                lower(name) AS lname,
                ROW_NUMBER() OVER (
                    PARTITION BY project_id, lower(name)
                    ORDER BY created_at ASC, id ASC
                ) AS rn,
                FIRST_VALUE(id) OVER (
                    PARTITION BY project_id, lower(name)
                    ORDER BY created_at ASC, id ASC
                ) AS canonical_id
            FROM releases
        ),
        dupe_map AS (
            SELECT id AS duplicate_id, canonical_id
            FROM ranked
            WHERE rn > 1
        )
        UPDATE release_test_run_links rtl
        SET release_id = dm.canonical_id
        FROM dupe_map dm
        WHERE rtl.release_id = dm.duplicate_id
          AND NOT EXISTS (
              SELECT 1 FROM release_test_run_links existing
              WHERE existing.release_id = dm.canonical_id
                AND existing.test_run_id = rtl.test_run_id
          )
        """
    )

    # 2. Any remaining links on duplicate releases are exact duplicates of a
    #    link already on the canonical row (blocked above by the NOT EXISTS
    #    guard against the uq_release_test_run unique constraint). Delete
    #    them so the parent release can be dropped.
    op.execute(
        """
        DELETE FROM release_test_run_links rtl
        USING (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY project_id, lower(name)
                    ORDER BY created_at ASC, id ASC
                ) AS rn
            FROM releases
        ) ranked
        WHERE ranked.id = rtl.release_id
          AND ranked.rn > 1
        """
    )

    # 3. Delete the duplicate releases (CASCADE cleans up any orphan phases).
    op.execute(
        """
        DELETE FROM releases
        WHERE id IN (
            SELECT id FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY project_id, lower(name)
                        ORDER BY created_at ASC, id ASC
                    ) AS rn
                FROM releases
            ) ranked
            WHERE rn > 1
        )
        """
    )

    # 4. Enforce the invariant going forward. A functional unique index
    #    doubles as the lookup index for ``func.lower(Release.name)`` in
    #    ``release_linker.resolve_or_create_release``.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_releases_project_lower_name "
        "ON releases (project_id, lower(name))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_releases_project_lower_name")
