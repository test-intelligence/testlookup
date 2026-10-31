"""Suite-membership parity runs (W25a HYG-04 phase 1: the 7-day cutover clock).

* ``suite_membership_parity_runs``: one row per comparison of the legacy suite
  memberships with the canonical model (an instance-wide run: no
  ``project_id`` column; project ids live inside the JSON for display only).
  ``trigger`` is ``beat`` / ``admin`` and ``status`` ``running`` / ``clean`` /
  ``diffs`` / ``failed`` (CHECKed with the literals of
  ``suite_parity_vocab.RUN_TRIGGERS`` / ``RUN_STATUSES``, pinned by
  ``tests/test_migration_0248.py``); ``ck_smpr_finished`` ties
  ``finished_at`` to a finished status. ``requested_by_id`` is
  ``ON DELETE SET NULL`` (the evidence outlives the admin who asked for it).
* ``uq_smpr_one_running`` -- partial unique on ``status`` WHERE it is
  ``running``: one comparison at a time (beat vs admin). ``ix_smpr_started``
  serves the history read (newest first).

A NEW table, so its indexes are built in the same transaction,
non-concurrently (the 0166+ rule exempts a table the migration created
itself). No change to any existing table, no backfill, no seed, no flag. The
downgrade drops exactly what the upgrade created, in reverse.

Revision ID: 0248
Revises: 0247
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0248"
down_revision = "0247"
branch_labels = None
depends_on = None

TABLE = "suite_membership_parity_runs"
_TRIGGERS = ("beat", "admin")                            # == suite_parity_vocab.RUN_TRIGGERS
_STATUSES = ("running", "clean", "diffs", "failed")      # == suite_parity_vocab.RUN_STATUSES


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_runs", sa.Integer(), nullable=False),
        sa.Column("projects_checked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("keys_compared", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("blocking_diffs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expected_divergences", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("counts", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("per_project", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("sample", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.CheckConstraint(f"trigger IN ({_in(_TRIGGERS)})", name="ck_smpr_trigger"),
        sa.CheckConstraint(f"status IN ({_in(_STATUSES)})", name="ck_smpr_status"),
        sa.CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="ck_smpr_finished"),
    )
    op.create_index("ix_smpr_started", TABLE, [sa.text("started_at DESC")])
    op.create_index(
        "uq_smpr_one_running",
        TABLE,
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_smpr_one_running", table_name=TABLE)
    op.drop_index("ix_smpr_started", table_name=TABLE)
    op.drop_table(TABLE)
