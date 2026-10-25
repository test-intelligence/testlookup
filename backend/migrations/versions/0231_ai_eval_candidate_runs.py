"""Candidate evaluation runs and their per-item outputs (W16 AGT-09).

Grows two existing tables and creates one; no index is built on an existing
table, no flag row:

* ``ai_eval_runs`` gains what a REPLAY run needs: ``metric_kind``
  ``VARCHAR(20) NOT NULL DEFAULT 'label_agreement'`` (checked by
  ``ck_aer_metric_kind``: ``label_agreement`` = the legacy metric over the
  dataset's own labels, ``replay`` = the candidate replayed through the real
  engine) and ``status`` ``VARCHAR(20) NOT NULL DEFAULT 'completed'`` (checked
  by ``ck_aer_status``: queued / running / completed / partial / failed). Both
  are constant defaults, so every existing row -- the legacy route's and the
  nightly gate's -- reads ``label_agreement`` / ``completed`` and is never
  recomputed; on PostgreSQL 11+ that is a metadata-only change (no rewrite).
  Then ``candidate`` JSONB (what the run actually replays), ``project_id``
  (``fk_aer_project_id`` -> ``projects``, ``ON DELETE SET NULL``: the project
  whose LLM budget and release policy the replay used) and ``requested_by``
  (``fk_aer_requested_by`` -> ``users``, ``ON DELETE SET NULL``),
  ``started_at`` / ``completed_at`` / ``heartbeat_at`` (set explicitly by the
  worker -- no ``onupdate`` column), the counters ``scored_items`` /
  ``unscored_items`` / ``error_items`` ``INTEGER NOT NULL DEFAULT 0``,
  ``items_capped`` ``BOOLEAN NOT NULL DEFAULT false``, ``error_code`` and the
  replay's ``metrics`` JSONB. The two checks and the two foreign keys scan a
  small table (one row per nightly gate) under a brief lock.
* ``ai_eval_baselines`` gains ``metric_kind`` with the same default and
  ``ck_aeb_metric_kind``: every existing baseline is a legacy one.
* NEW ``ai_eval_item_outputs``: one row per replayed item, written one at a
  time by the worker. ``eval_run_id`` -> ``ai_eval_runs`` ``ON DELETE
  CASCADE`` (deleting a run, or its dataset, deletes its items);
  ``uq_aeio_run_item`` (``eval_run_id, item_index``) makes a redelivered item
  an ``ON CONFLICT DO NOTHING``; ``ix_aeio_run_status`` (``eval_run_id,
  status``) serves the per-run status filter and counts; ``ck_aeio_status``
  (correct / incorrect / unscored / error) and
  ``ck_aeio_item_index_nonnegative``. The key and the index are built with the
  table, inside the migration's transaction (a table the migration creates
  itself is outside the concurrent-index rule).

The unused ``ai_eval_runs.item_results`` JSONB is left as it is (never written,
not dropped).

The downgrade, in reverse: drops ``ai_eval_item_outputs`` (with its key, index
and checks); drops ``ck_aeb_metric_kind`` and ``ai_eval_baselines.metric_kind``;
drops the two run checks, the two foreign keys and every column the upgrade
added to ``ai_eval_runs``.

Revision ID: 0231
Revises: 0230
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0231"
down_revision = "0230"
branch_labels = None
depends_on = None

RUNS = "ai_eval_runs"
BASELINES = "ai_eval_baselines"
ITEMS = "ai_eval_item_outputs"

RUN_METRIC_KIND_CHECK = "ck_aer_metric_kind"
RUN_STATUS_CHECK = "ck_aer_status"
RUN_PROJECT_FK = "fk_aer_project_id"
RUN_REQUESTED_BY_FK = "fk_aer_requested_by"
BASELINE_METRIC_KIND_CHECK = "ck_aeb_metric_kind"
ITEM_UNIQUE = "uq_aeio_run_item"
ITEM_STATUS_INDEX = "ix_aeio_run_status"
ITEM_STATUS_CHECK = "ck_aeio_status"
ITEM_INDEX_CHECK = "ck_aeio_item_index_nonnegative"

METRIC_KIND_SQL = "metric_kind IN ('label_agreement','replay')"
RUN_STATUS_SQL = "status IN ('queued','running','completed','partial','failed')"
ITEM_STATUS_SQL = "status IN ('correct','incorrect','unscored','error')"
ITEM_INDEX_SQL = "item_index >= 0"

#: The ai_eval_runs columns, in the order the upgrade adds them (the downgrade
#: drops them in reverse).
RUN_COLUMNS = (
    "metric_kind",
    "status",
    "candidate",
    "project_id",
    "requested_by",
    "started_at",
    "completed_at",
    "heartbeat_at",
    "scored_items",
    "unscored_items",
    "error_items",
    "items_capped",
    "error_code",
    "metrics",
)


def _run_columns() -> list[sa.Column]:
    return [
        sa.Column("metric_kind", sa.String(20), nullable=False, server_default=sa.text("'label_agreement'")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'completed'")),
        sa.Column("candidate", postgresql.JSONB(), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scored_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unscored_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("items_capped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
    ]


def upgrade() -> None:
    for column in _run_columns():
        op.add_column(RUNS, column)
    op.create_foreign_key(RUN_PROJECT_FK, RUNS, "projects", ["project_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(RUN_REQUESTED_BY_FK, RUNS, "users", ["requested_by"], ["id"], ondelete="SET NULL")
    op.create_check_constraint(RUN_METRIC_KIND_CHECK, RUNS, METRIC_KIND_SQL)
    op.create_check_constraint(RUN_STATUS_CHECK, RUNS, RUN_STATUS_SQL)

    op.add_column(
        BASELINES,
        sa.Column("metric_kind", sa.String(20), nullable=False, server_default=sa.text("'label_agreement'")),
    )
    op.create_check_constraint(BASELINE_METRIC_KIND_CHECK, BASELINES, METRIC_KIND_SQL)

    op.create_table(
        ITEMS,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "eval_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(160), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("expected", postgresql.JSONB(), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("score", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("mode_used", sa.String(20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("eval_run_id", "item_index", name=ITEM_UNIQUE),
        sa.CheckConstraint(ITEM_STATUS_SQL, name=ITEM_STATUS_CHECK),
        sa.CheckConstraint(ITEM_INDEX_SQL, name=ITEM_INDEX_CHECK),
    )
    op.create_index(ITEM_STATUS_INDEX, ITEMS, ["eval_run_id", "status"])


def downgrade() -> None:
    op.drop_table(ITEMS)

    op.drop_constraint(BASELINE_METRIC_KIND_CHECK, BASELINES, type_="check")
    op.drop_column(BASELINES, "metric_kind")

    op.drop_constraint(RUN_STATUS_CHECK, RUNS, type_="check")
    op.drop_constraint(RUN_METRIC_KIND_CHECK, RUNS, type_="check")
    op.drop_constraint(RUN_REQUESTED_BY_FK, RUNS, type_="foreignkey")
    op.drop_constraint(RUN_PROJECT_FK, RUNS, type_="foreignkey")
    for name in reversed(RUN_COLUMNS):
        op.drop_column(RUNS, name)
