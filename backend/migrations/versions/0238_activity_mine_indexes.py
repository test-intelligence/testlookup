"""Two indexes for ``GET /api/v1/activity/mine`` (W21d ACT-02).

* ``ix_pae_actor_time`` on ``project_activity_events (actor_id, occurred_at
  DESC, id DESC) WHERE actor_type = 'user'``: what one person did while signed
  in, across every project, in the feed's keyset order. The existing
  ``ix_pae_project_actor`` is project-first and cannot serve a read across
  projects. Partial on ``actor_type = 'user'``: an API key's rows carry its
  owner's ``actor_id`` (thousands of ``run.received`` a day) and are not
  "mine".
* ``ix_mtc_assignee`` on ``managed_test_cases (assignee_id) WHERE assignee_id
  IS NOT NULL``: "test cases assigned to me", beside the existing
  ``ix_mtc_author``. Most cases are unassigned.

Both tables exist and are written by every deployment, so each index is built
CONCURRENTLY inside ``autocommit_block()`` (the 0166+ rule,
``tests/regression/test_migration_index_builds_are_concurrent.py``). A
concurrent build that failed half-way leaves an INVALID index under its name,
which ``CREATE ... IF NOT EXISTS`` would then skip: each build first drops an
INVALID leftover of its own name, CONCURRENTLY (0223's pattern; skipped
offline, where there is no catalog to read).

No column, no backfill. The downgrade drops both indexes CONCURRENTLY, in
reverse order.

Revision ID: 0238
Revises: 0237
"""
from alembic import op
import sqlalchemy as sa

revision = "0238"
down_revision = "0237"
branch_labels = None
depends_on = None

ACTOR_INDEX = "ix_pae_actor_time"
ASSIGNEE_INDEX = "ix_mtc_assignee"

BUILD_ACTOR_INDEX = (
    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {ACTOR_INDEX} "
    "ON project_activity_events (actor_id, occurred_at DESC, id DESC) "
    "WHERE actor_type = 'user'"
)
BUILD_ASSIGNEE_INDEX = (
    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {ASSIGNEE_INDEX} "
    "ON managed_test_cases (assignee_id) "
    "WHERE assignee_id IS NOT NULL"
)
DROP_ASSIGNEE_INDEX = f"DROP INDEX CONCURRENTLY IF EXISTS {ASSIGNEE_INDEX}"
DROP_ACTOR_INDEX = f"DROP INDEX CONCURRENTLY IF EXISTS {ACTOR_INDEX}"


def upgrade() -> None:
    offline = op.get_context().as_sql

    if not offline:
        _drop_invalid_leftover(op.get_bind(), ACTOR_INDEX)
    with op.get_context().autocommit_block():
        op.execute(BUILD_ACTOR_INDEX)

    if not offline:
        _drop_invalid_leftover(op.get_bind(), ASSIGNEE_INDEX)
    with op.get_context().autocommit_block():
        op.execute(BUILD_ASSIGNEE_INDEX)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(DROP_ASSIGNEE_INDEX)
    with op.get_context().autocommit_block():
        op.execute(DROP_ACTOR_INDEX)


def _drop_invalid_leftover(bind, index: str) -> None:
    """Drop ``index`` CONCURRENTLY when a failed concurrent build left it INVALID.

    ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` would skip such a leftover. A
    valid index of that name is left alone.
    """
    invalid = bind.execute(
        sa.text(
            "SELECT NOT i.indisvalid FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.oid = to_regclass(:index)"
        ),
        {"index": index},
    ).scalar_one_or_none()
    if invalid:
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index}")
