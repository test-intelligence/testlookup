"""S2 — denormalize the primary release onto ``test_runs``.

Why denormalize at all
----------------------
Every windowed analytics read filters ``test_runs`` by ``project_id`` and a
date range. Adding a release axis by joining ``release_test_run_links`` on each
of them would add a join to dozens of endpoints and, worse, would double-count
a multi-linked run in any aggregate — silent fan-out, invisible in tests.

``test_runs.primary_release_id`` makes the release one more indexed predicate
on a query shape that already exists. It follows the ``primary_suite_name``
precedent (migration 0072), including that precedent's caveat: the denormalized
column answers for PRIMARY membership only. ``/runs`` still reads the link
table and so returns the wider set. That divergence is deliberate and pinned by
a regression test.

Online migration
----------------
``test_runs`` is one of the largest tables here, so this is sequenced not to
lock it:

  * the column is added nullable with no default — no table rewrite;
  * the backfill runs in bounded batches rather than one statement, so no
    single UPDATE holds row locks across the whole table;
  * the index is built CONCURRENTLY inside ``autocommit_block``, per 0082/0098.

The batch loop is self-terminating: each pass only selects rows whose value
still differs, so a row fixed by one pass cannot be selected by the next.

Nullable forever
----------------
A run legitimately has no primary release — an in-flight live run has no link
yet by design, and a swallowed linker error leaves one permanently unattributed
(see F3). So NULL is a real state meaning "not attributed", not a backfill gap,
and release-scoped reads must treat it as such rather than assuming every run
has a release.
"""

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "0152"
down_revision = "0151"
branch_labels = None
depends_on = None

#: Rows per backfill statement. Large enough that the loop is short on a big
#: table, small enough that no single statement holds locks for long.
_BATCH = 5000

#: Hard stop on the loop. At 5k a batch this covers 25M rows; if it is ever
#: reached something is wrong (a row oscillating between two values would loop
#: forever otherwise) and the migration says so rather than hanging.
_MAX_BATCHES = 5000


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column(
            "primary_release_id",
            sa.UUID(as_uuid=True),
            # SET NULL: deleting a release must not delete its runs. The
            # sibling release FKs on this schema are CASCADE, so leaving this
            # unspecified would inherit exactly the wrong behaviour and a
            # release deletion would take the test history with it.
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # The backfill lives in 0153, not here. Batching it inside a migration
    # transaction buys nothing: Alembic wraps the whole upgrade in one
    # transaction, so every row lock taken by every batch is held until the
    # migration commits — exactly the lock profile the batching was meant to
    # avoid. 0153 runs it inside an autocommit block, where each batch really
    # does commit and release.


def downgrade() -> None:
    op.drop_column("test_runs", "primary_release_id")
