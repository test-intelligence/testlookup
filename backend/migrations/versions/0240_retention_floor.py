"""The keep-last floor and the purged-baseline stamp (W22a ACT-04).

* ``project_retention_policies.keep_last_runs`` ``INTEGER NOT NULL DEFAULT 10``
  with ``ck_retention_keep_last_runs`` (0-1000): the scheduled purge always
  keeps the newest N runs of a project (plus its newest PASSED run and the
  newest PASSED run of each suite). Every existing policy row reads 10 at once
  -- the behaviour change for existing installs, in the safe direction only
  (purges keep more); ``0`` restores the old count behaviour. ``ADD COLUMN ...
  DEFAULT <constant> NOT NULL`` is metadata-only on Postgres >= 11 (no table
  rewrite); the CHECK validates a one-row-per-project table.
* ``run_baselines.baseline_purged_at`` ``TIMESTAMPTZ NULL``: stamped by the
  purge executor before it deletes a run that other runs used as their
  baseline (the FIRST purge date sticks), so the run page can say "Baseline
  build N was purged on D." after the SET NULL clears ``baseline_run_id``.
  Nullable, no default: metadata-only.

Two columns on EXISTING tables and no index (nothing for the concurrent-build
rule). The downgrade undoes it in reverse order: the ``run_baselines`` column,
the CHECK, then ``keep_last_runs``.

Revision ID: 0240
Revises: 0239
"""
from alembic import op
import sqlalchemy as sa

revision = "0240"
down_revision = "0239"
branch_labels = None
depends_on = None

_KEEP_LAST_RUNS_CHECK = "keep_last_runs >= 0 AND keep_last_runs <= 1000"


def upgrade() -> None:
    op.add_column(
        "project_retention_policies",
        sa.Column("keep_last_runs", sa.Integer(), server_default=sa.text("10"), nullable=False),
    )
    op.create_check_constraint("ck_retention_keep_last_runs", "project_retention_policies", _KEEP_LAST_RUNS_CHECK)
    op.add_column("run_baselines", sa.Column("baseline_purged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("run_baselines", "baseline_purged_at")
    op.drop_constraint("ck_retention_keep_last_runs", "project_retention_policies", type_="check")
    op.drop_column("project_retention_policies", "keep_last_runs")
