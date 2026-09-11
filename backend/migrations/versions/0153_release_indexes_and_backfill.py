"""S0-S2 — the index builds and the one backfill that must commit per batch.

Why this is a separate migration
--------------------------------
0150, 0151 and 0152 add columns and run small transactional backfills. This one
holds everything that cannot live inside a transaction, for a reason that only
shows up when something goes wrong:

``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction block, so Alembic
has to commit everything before it. Put one at the end of a migration that also
adds columns, and a failure at the index build leaves the columns committed and
the migration marked incomplete — and the next ``alembic upgrade head`` re-runs
``op.add_column`` against a column that already exists and dies. Recovering
needs someone to hand-edit the schema at whatever hour it happened.

Splitting it means every statement here is independently re-runnable:
``if_not_exists`` on the index builds, and a self-terminating loop for the
backfill. Run it twice and the second run is a no-op. This is the shape
0082 and 0098 already use.

The backfill is here rather than in 0152 for a related reason. Batching a
backfill inside a migration transaction buys nothing — Alembic wraps the whole
upgrade, so every row lock from every batch is held until the migration
commits, which is precisely the lock profile batching exists to avoid. Inside
``autocommit_block`` each batch really does commit and release.
"""

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "0153"
down_revision = "0152"
branch_labels = None
depends_on = None

_BATCH = 5000
_MAX_BATCHES = 5000

#: (name, table, columns, unique, where) — one row per index, so the upgrade and
#: downgrade cannot drift apart.
_INDEXES = (
    ("ix_releases_project_active", "releases", ["project_id"], True, "is_active IS TRUE"),
    ("ix_rtr_links_test_run", "release_test_run_links", ["test_run_id"], False, None),
    ("ix_rtr_links_primary", "release_test_run_links", ["test_run_id"], True, "is_primary IS TRUE"),
    ("ix_releases_project_sort", "releases", ["project_id", "sort_key"], False, None),
    (
        "ix_test_runs_project_release_created",
        "test_runs",
        ["project_id", "primary_release_id", "created_at"],
        False,
        None,
    ),
)


def upgrade() -> None:
    bind = op.get_bind()

    # ── Backfill primary_release_id, one committed batch at a time ───────────
    # Self-terminating: each pass selects only rows whose value still differs,
    # so a row fixed by one pass cannot be selected by the next. That is also
    # what makes re-running this migration safe.
    backfill = sa.text(
        """
        WITH batch AS (
            SELECT tr.id AS run_id, l.release_id AS release_id
              FROM test_runs tr
              JOIN release_test_run_links l
                ON l.test_run_id = tr.id AND l.is_primary IS TRUE
             WHERE tr.primary_release_id IS DISTINCT FROM l.release_id
             LIMIT :batch
        )
        UPDATE test_runs
           SET primary_release_id = batch.release_id
          FROM batch
         WHERE test_runs.id = batch.run_id
        """
    )

    total = 0
    with op.get_context().autocommit_block():
        for _ in range(_MAX_BATCHES):
            updated = bind.execute(backfill, {"batch": _BATCH}).rowcount
            total += updated
            if updated == 0:
                break
        else:
            # Do not fail: the column is correct for everything backfilled so
            # far, and reconcile-primary-releases finishes the job hourly. But
            # say so — a half-populated column reads exactly like a complete
            # one, because every query still returns rows, just fewer.
            logger.warning(
                "0153: backfill hit the %s-batch ceiling after %s rows; "
                "reconcile-primary-releases will finish the remainder",
                _MAX_BATCHES,
                total,
            )
    logger.info("0153: primary_release_id backfilled for %s run(s)", total)

    # ── Indexes ──────────────────────────────────────────────────────────────
    # One autocommit_block each. Not required: inside a block every statement
    # autocommits on its own, and PostgreSQL 16 accepts several CONCURRENTLY
    # builds in one block (re-audit N24 tested it). What Postgres rejects is a
    # CONCURRENTLY inside a transaction. One block per index just keeps each
    # build separately visible in the log.
    for name, table, cols, unique, where in _INDEXES:
        kwargs = {}
        if where is not None:
            kwargs["postgresql_where"] = sa.text(where)
        with op.get_context().autocommit_block():
            op.create_index(
                name,
                table,
                cols,
                unique=unique,
                postgresql_concurrently=True,
                if_not_exists=True,
                **kwargs,
            )


def downgrade() -> None:
    for name, table, _cols, _unique, _where in reversed(_INDEXES):
        with op.get_context().autocommit_block():
            op.drop_index(
                name,
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
    # The backfilled values are left in place: primary_release_id itself is
    # dropped by 0152's downgrade, so clearing it here would be redundant work
    # on a large table for no benefit.
