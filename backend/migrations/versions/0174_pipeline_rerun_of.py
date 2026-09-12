"""``agent_pipeline_runs.rerun_of``: link a fresh attempt to the run it replaces (E7.4).

Manual retry has two outcomes (see ``app/services/pipeline_retry_config.py``):
resume the same row when the configuration is unchanged, or start a NEW run
when it changed -- because the completed checkpoints were authorised under the
old configuration and replaying them under a new one can re-enter a tool the
new allowlist forbids.

Without a link, the second outcome is indistinguishable from someone triggering
an unrelated pipeline: the history shows two rows and no reason. ``rerun_of``
makes the relationship explicit, so ``/pipelines`` can show "attempt 6, rerun of
<id>" and an operator can follow a run's whole lineage.

``ON DELETE SET NULL`` matches ``parent_pipeline_run_id`` on the same table: the
successor is a real run in its own right and must survive its predecessor being
cleaned up.

The index is partial (``WHERE rerun_of IS NOT NULL``) because reruns are the
rare case -- the overwhelming majority of rows are NULL and do not belong in it.

Revision ID: 0174
Revises: 0173
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0174"
down_revision = "0173"
branch_labels = None
depends_on = None

TABLE = "agent_pipeline_runs"
COLUMN = "rerun_of"
FK = "fk_agent_pipeline_rerun_of"
INDEX = "ix_pipeline_runs_rerun_of"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        FK, TABLE, TABLE, [COLUMN], ["id"], ondelete="SET NULL"
    )
    # CONCURRENTLY: agent_pipeline_runs is written by every pipeline attempt, and
    # a plain CREATE INDEX takes a lock that queues those writes behind it.
    # ``tests/regression/test_migration_index_builds_are_concurrent.py`` holds
    # this for every index added to a hot table.
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} ({COLUMN}) "
            f"WHERE {COLUMN} IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    op.drop_constraint(FK, TABLE, type_="foreignkey")
    op.drop_column(TABLE, COLUMN)
