"""Index retained SQL-intent acceptance cohorts without blocking writers.

Revision ID: 0264
Revises: 0263

Both indexes have ORM twins. No columns, rows, policy defaults or historical
outcomes change. Failed concurrent builds can leave invalid indexes; a retry
drops only such a named index resolved in the current schema before rebuilding.
"""
import sqlalchemy as sa
from alembic import op

revision = "0264"
down_revision = "0263"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_run_downstream_outbox_created_at", "run_downstream_outbox"),
    ("ix_agent_pipeline_runs_created_at", "agent_pipeline_runs"),
)


def _index_is_invalid(bind, name: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT 1 FROM pg_index WHERE indexrelid = to_regclass(:name) AND NOT indisvalid"
    ), {"name": name}).first())


def upgrade() -> None:
    invalid = set() if op.get_context().as_sql else {
        name for name, _table in INDEXES if _index_is_invalid(op.get_bind(), name)
    }
    with op.get_context().autocommit_block():
        for name, table in INDEXES:
            if name in invalid:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} (created_at)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _table in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
