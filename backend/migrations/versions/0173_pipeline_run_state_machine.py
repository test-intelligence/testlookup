"""Pipeline run state machine: closed status vocabulary + lease/retry columns (E7.1).

``agent_pipeline_runs.status`` had five documented values and two undocumented
writers. ``partial`` (a graph that finished with failed stages) was neither a
success nor a failure, so nothing downstream could decide what to do with it;
the Investigator wrote ``cancelled``, which no reader expected; and the
/agents router rewrote ``running`` rows to ``failed`` at read time after a
fixed 30 minutes. This revision closes the vocabulary and adds the columns the
lease, retry and review work (E7.2-E7.5) will fill:

* backfill ``partial -> completed`` and stamp ``execution_metadata.stage_quality
  = "degraded"`` so the information is kept, on a column that already exists;
* backfill ``cancelled -> failed`` with a ``cancelled:`` error prefix;
* add ``attempt``, ``max_attempts``, ``next_retry_at``, ``lease_owner``,
  ``lease_expires_at``, ``fencing_token``, ``heartbeat_at``,
  ``cancel_requested``, ``review_policy``;
* add ``ck_agent_pipeline_status`` allowing exactly
  ``pending running retry_wait completed passed failed``.

Every write now goes through ``app/services/workflow_run_state.py``; the
``agents.pipeline-status-writes-via-state-machine`` quality gate holds that.

Downgrade drops the constraint and the columns. It does not resurrect
``partial``: the degraded marker is kept in ``execution_metadata`` and is the
same information.

Revision ID: 0173
Revises: 0172
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0173"
down_revision = "0172"
branch_labels = None
depends_on = None

TABLE = "agent_pipeline_runs"
CHECK = "ck_agent_pipeline_status"
ALLOWED = ("pending", "running", "retry_wait", "completed", "passed", "failed")
LEASE_INDEX = "ix_pipeline_runs_lease_expiry"


def upgrade() -> None:
    # 1. Backfill BEFORE the constraint so it can be created VALID in one step.
    #    execution_metadata is JSON (not JSONB); cast through jsonb for jsonb_set.
    op.execute(
        f"""
        UPDATE {TABLE}
           SET status = 'completed',
               completed_at = COALESCE(completed_at, now()),
               execution_metadata = jsonb_set(
                   COALESCE(execution_metadata::jsonb, '{{}}'::jsonb),
                   '{{stage_quality}}', '"degraded"'::jsonb, true
               )::json
         WHERE status = 'partial'
        """
    )
    op.execute(
        f"""
        UPDATE {TABLE}
           SET status = 'failed',
               completed_at = COALESCE(completed_at, now()),
               error = CASE
                   WHEN error IS NULL OR error = '' THEN 'cancelled: by request'
                   WHEN error LIKE 'cancelled: %' THEN error
                   ELSE left('cancelled: ' || error, 2000)
               END
         WHERE status IN ('cancelled', 'canceled')
        """
    )
    # Anything else outside the vocabulary is an invalid state by definition.
    op.execute(
        f"""
        UPDATE {TABLE}
           SET status = 'failed',
               completed_at = COALESCE(completed_at, now()),
               error = COALESCE(NULLIF(error, ''), 'invalid status ' || status || ' normalised by migration 0173')
         WHERE status IS NULL OR status NOT IN ({", ".join(repr(s) for s in ALLOWED)})
        """
    )

    # 2. Columns for E7.2-E7.5 (nullable or server-defaulted so this is online).
    op.add_column(TABLE, sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(TABLE, sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"))
    op.add_column(TABLE, sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("lease_owner", sa.String(length=255), nullable=True))
    op.add_column(TABLE, sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("fencing_token", sa.String(length=64), nullable=True))
    op.add_column(TABLE, sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        TABLE,
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        TABLE,
        sa.Column("review_policy", sa.String(length=40), nullable=False, server_default="human_required"),
    )

    # 3. The closed vocabulary.
    op.create_check_constraint(
        CHECK,
        TABLE,
        "status IN ('pending', 'running', 'retry_wait', 'completed', 'passed', 'failed')",
    )
    op.create_check_constraint(
        "ck_agent_pipeline_attempts",
        TABLE,
        "attempt >= 1 AND max_attempts >= 1 AND attempt <= max_attempts + 1",
    )
    # Partial index the reaper (E7.3) will read. Built CONCURRENTLY: a plain
    # CREATE INDEX holds a SHARE lock for the whole build and every pipeline
    # write would queue behind it.
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {LEASE_INDEX} ON {TABLE} (lease_expires_at) "
            "WHERE status = 'running'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {LEASE_INDEX}")
    op.drop_constraint("ck_agent_pipeline_attempts", TABLE, type_="check")
    op.drop_constraint(CHECK, TABLE, type_="check")
    for column in (
        "review_policy",
        "cancel_requested",
        "heartbeat_at",
        "fencing_token",
        "lease_expires_at",
        "lease_owner",
        "next_retry_at",
        "max_attempts",
        "attempt",
    ):
        op.drop_column(TABLE, column)
