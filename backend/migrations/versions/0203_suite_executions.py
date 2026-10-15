"""How long each suite of a run, and the run itself, actually took (EXEC-02).

``test_runs.duration_ms`` is the SUM of the run's test durations, and
``start_time`` / ``end_time`` are the ingest and finalize times, so nothing
stored how long a suite or a run took on the clock. Tests that run in
parallel make the sum larger than that wall clock.

* ``suite_executions``: one row per run per effective suite, keyed by
  ``suite_key`` (``run_compare_service.normalize_suite_name`` of the suite)
  with ``UNIQUE (test_run_id, suite_key)`` so a re-finalize or a live re-drain
  upserts the same row. It holds the wall clock and the summed time, the
  outcome counts, and ``source``: ``report`` | ``derived`` | ``derived_sum``
  (no wall clock known; ``wall_ms`` stays NULL rather than repeat the sum).
  ``run_created_at`` and ``branch`` are copied from the run: derived rows have
  no ``started_at``, and suite-duration history windows on the run's time,
  which ``(project_id, suite_key, run_created_at DESC)`` serves. Rows die with
  their run or project (CASCADE), so no retention code is needed.
* ``test_runs`` gains ``exec_started_at``, ``exec_finished_at``,
  ``wall_clock_ms`` and ``timing_source`` (same vocabulary; NULL = not timed
  yet). ``ADD COLUMN ... NULL`` without a default is a catalog change in
  PostgreSQL 11+: the table is not rewritten.

Existing runs are not timed here: ``test_cases`` is the 1M-row table, so the
chunked, re-entrant Celery task ``backfill_suite_executions`` (beat entry
``backfill-suite-executions``, Redis cursor ``suite_exec.backfill_cursor``)
writes them as ``derived_sum`` rows after the deploy, in the worker.

Revision ID: 0203
Revises: 0202
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0203"
down_revision = "0202"
branch_labels = None
depends_on = None

_RUN_COLUMNS = ("exec_started_at", "exec_finished_at", "wall_clock_ms", "timing_source")


def upgrade() -> None:
    op.create_table(
        "suite_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suite_name", sa.String(length=500), nullable=False),
        sa.Column("suite_key", sa.String(length=500), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wall_ms", sa.BigInteger(), nullable=True),
        sa.Column("summed_ms", sa.BigInteger(), nullable=True),
        sa.Column("setup_ms", sa.BigInteger(), nullable=True),
        sa.Column("teardown_ms", sa.BigInteger(), nullable=True),
        sa.Column("test_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("timed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("passed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("broken", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unknown", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("parallelism", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("run_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.UniqueConstraint("test_run_id", "suite_key", name="uq_suite_executions_run_suite"),
        sa.CheckConstraint(
            "source IN ('report', 'derived', 'derived_sum')", name="ck_suite_executions_source"
        ),
    )
    # Raw SQL so the DESC ordering is applied (op.create_index builds ASC).
    op.execute(
        "CREATE INDEX ix_suite_executions_project_suite_run ON suite_executions "
        "(project_id, suite_key, run_created_at DESC)"
    )
    op.create_index("ix_suite_executions_test_run_id", "suite_executions", ["test_run_id"])

    op.add_column("test_runs", sa.Column("exec_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("test_runs", sa.Column("exec_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("test_runs", sa.Column("wall_clock_ms", sa.BigInteger(), nullable=True))
    op.add_column("test_runs", sa.Column("timing_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    for column in reversed(_RUN_COLUMNS):
        op.drop_column("test_runs", column)
    op.drop_index("ix_suite_executions_test_run_id", table_name="suite_executions")
    op.drop_index("ix_suite_executions_project_suite_run", table_name="suite_executions")
    op.drop_table("suite_executions")
