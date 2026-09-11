"""Backfill webhook_deliveries.run_id in committed batches (re-audit N3).

0162 added ``webhook_deliveries.run_id`` and filled it for existing
``run.completed`` rows with ONE cross-table UPDATE inside the migration
transaction: every matching row stayed locked, and every other writer of those
rows waited, until the whole upgrade committed. Its siblings (0082, 0098, 0153)
had already set the batched pattern; this one did not follow it.

That UPDATE is removed from 0162 -- which changes nothing for a database that
already ran 0162: Alembic never runs a revision twice -- and the same backfill
runs here instead:

* in ``autocommit_block``, so each batch commits and releases its row locks;
* keyset-paged by ``id``: a batch looks at the next ``_BATCH`` candidate rows
  after the last one it saw, so a row whose run was deleted (it stays NULL,
  deliberately) is looked at once, not on every batch;
* joined to ``test_runs`` by primary key: the payload's ``run_id`` is cast to
  uuid only when it has a uuid's shape -- a malformed historical payload stays
  NULL instead of failing the cast -- where 0162 compared ``run.id::text``,
  which no index serves;
* idempotent: only ``run_id IS NULL`` rows are candidates, so a second run, or
  a run after 0162's own backfill, updates nothing.

The result is 0162's: every ``run.completed`` delivery whose payload names an
existing run gets that run; everything else stays NULL. One difference, in the
safe direction: 0162 matched ``run_id`` text exactly, so an upper-case uuid in
a payload stayed NULL there and is matched here.

Revision ID: 0171
Revises: 0170
"""
import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "0171"
down_revision = "0170"
branch_labels = None
depends_on = None

_BATCH = 5000

_UUID_SHAPE = (
    "'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"
)

_NEXT_KEYS = sa.text(
    "SELECT id FROM webhook_deliveries "
    "WHERE run_id IS NULL AND event_type = 'run.completed' AND event_payload ? 'run_id' "
    "AND (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid)) "
    "ORDER BY id LIMIT :batch"
)

_FILL = sa.text(
    "UPDATE webhook_deliveries AS delivery SET run_id = run.id "
    "FROM test_runs AS run "
    "WHERE delivery.id = ANY(CAST(:ids AS uuid[])) "
    "AND delivery.run_id IS NULL "
    f"AND delivery.event_payload ->> 'run_id' ~ {_UUID_SHAPE} "
    "AND run.id = CAST(delivery.event_payload ->> 'run_id' AS uuid)"
)


def backfill(bind, batch: int = _BATCH) -> tuple[int, int]:
    """Fill run_id batch by batch; returns (rows filled, candidate rows seen).

    Each statement commits on its own when ``bind`` is in autocommit, as it is
    inside ``autocommit_block``.
    """
    after = None
    scanned = filled = 0
    while True:
        keys = [
            str(k) for k in bind.execute(_NEXT_KEYS, {"after": after, "batch": batch}).scalars()
        ]
        if not keys:
            return filled, scanned
        filled += bind.execute(_FILL, {"ids": keys}).rowcount
        scanned += len(keys)
        after = keys[-1]


def upgrade() -> None:
    if op.get_context().as_sql:
        # Offline: one statement, the same result, without the batching.
        op.execute(
            "UPDATE webhook_deliveries AS delivery SET run_id = run.id FROM test_runs AS run "
            "WHERE delivery.run_id IS NULL AND delivery.event_type = 'run.completed' "
            f"AND delivery.event_payload ->> 'run_id' ~ {_UUID_SHAPE} "
            "AND run.id = CAST(delivery.event_payload ->> 'run_id' AS uuid)"
        )
        return
    with op.get_context().autocommit_block():
        filled, scanned = backfill(op.get_bind())
    logger.info(
        "0171: webhook_deliveries.run_id filled for %s of %s candidate row(s)", filled, scanned
    )


def downgrade() -> None:
    # The values are 0162's to own: its downgrade drops the column.
    pass
