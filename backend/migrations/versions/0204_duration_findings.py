"""A run's duration findings, and duration regression detection ON for a new install.

EXEC-05 (absorbs GATE-07): the run page, the release gate's opt-in
``perf_regression`` rule and the "became slower" notifications all read one
column, ``test_runs.duration_findings`` (JSONB, NULL = not evaluated yet). It
holds ``{measured, reason, spikes[], suites[]}`` written by
``services/duration_regression_service.evaluate_duration_regressions``. Adding
a nullable column with no default is metadata-only in PostgreSQL 11+, so the
table is not rewritten and existing runs stay NULL ("not evaluated", never "no
regressions").

``perf_regression_detection`` was seeded OFF by migration 0069, so per-test
spikes were never computed anywhere. A new install now starts with it ON, by
the same rule migration 0201 uses for ``manual_upload``: the row is flipped
only when the database has never been used (no user and no project yet,
which is the state ``alembic upgrade head`` runs in on a new install) and only
while the row still holds 0069's value. An upgraded install keeps whatever it
has, on or off.

The new notification events ``test.newly_slow`` / ``suite.newly_slow`` need no
schema change: ``notification_logs.event_type`` is a ``String(50)`` and the
preference lists are JSONB, with no CHECK constraint on either. The vocabulary
is constrained in code only (``NotificationEventType``, which the delivery
relay converts every staged row through), and that is where they are added.

Revision ID: 0204
Revises: 0203
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0204"
down_revision = "0203"
branch_labels = None
depends_on = None

TABLE = "test_runs"
COLUMN = "duration_findings"

# The fresh-install condition, shared by upgrade and downgrade so the two can
# never disagree about which databases they may touch (pattern 0201).
FRESH_INSTALL = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

UPGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
    "WHERE key = 'perf_regression_detection' AND enabled_global = false AND "
    + FRESH_INSTALL
)

DOWNGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
    "WHERE key = 'perf_regression_detection' AND enabled_global = true AND "
    + FRESH_INSTALL
)


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(sa.text(UPGRADE_SQL))


def downgrade() -> None:
    # Only an untouched database goes back to 0069's OFF; a used one keeps the
    # value its admins chose, exactly as upgrade() left it alone.
    op.execute(sa.text(DOWNGRADE_SQL))
    op.drop_column(TABLE, COLUMN)
