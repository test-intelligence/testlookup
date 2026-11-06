"""The drop-folder polling watcher: ``drop_folder_builds`` + the ``drop_folder_watcher`` flag ON (W34 STRAT-13).

The bundled object store (versitygw) sends no bucket notifications, so a drop
folder ``<project>/runs/<build>/`` (results, then ``upload_complete.json``) is
found by a beat sweep (``worker/tasks.py:watch_drop_folders``, every minute)
instead of ``POST /webhooks/minio``, which stays for an external MinIO.

* ``drop_folder_builds``: one row per build folder the watcher examined.
  ``build_prefix`` (unique: ``uq_dfb_build_prefix``, the lookups and the upsert
  target), ``project_segment`` (the key's first segment: a project id or slug),
  ``status`` ``pending`` / ``queued`` / ``baseline`` / ``abandoned`` /
  ``refused`` (``ck_dfb_status``: the literals of
  ``drop_folder_watcher.BUILD_STATUSES``, held here as ``_STATUSES`` -- a
  migration imports no application code; pinned by
  ``tests/test_migration_0254.py``), a queued row carries its task id and time
  (``ck_dfb_queued_has_task``), the sentinel's ETag and LastModified,
  ``first_seen_at`` / ``last_checked_at``. ``ix_dfb_pending_first_seen``
  (partial, ``status = 'pending'``) serves the 24-hour abandonment. No FK: the
  rows are keyed by the bucket, and are NOT deleted with runs (a deleted
  slug-prefixed run, whose objects stay, must not come back). A NEW table, so
  its indexes are built in the same transaction, non-concurrently.
* the ``drop_folder_watcher`` feature flag, seeded **ON** on every install, new
  or upgraded (features ship ON; the 0251 pattern): ``ON CONFLICT (key) DO
  NOTHING`` leaves a row created by hand untouched.

The downgrade deletes the flag row only while it still equals the seed (the
0208 / 0251 rule: same description, never edited -- ``created_at =
updated_at`` -- no project / role lists, rollout 100), then drops the two
indexes and the table, in reverse.

Revision ID: 0254
Revises: 0253
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0254"
down_revision = "0253"
branch_labels = None
depends_on = None

TABLE = "drop_folder_builds"
_STATUSES = ("pending", "queued", "baseline", "abandoned", "refused")   # == drop_folder_watcher.BUILD_STATUSES
_QUEUED_HAS_TASK = "status <> 'queued' OR (queued_at IS NOT NULL AND task_id IS NOT NULL)"
_PENDING = "status = 'pending'"

#: (key, description). Named ``_FLAGS`` so the feature-flag catalogue guard reads the key.
_FLAGS = (
    (
        "drop_folder_watcher",
        "ON: a beat sweep polls <project>/runs/<build>/ in the object store every minute and queues each new "
        "upload_complete.json for ingestion (no webhook needed). OFF: the sweep does nothing.",
    ),
)

INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "SELECT gen_random_uuid(), :key, :description, true, 100, now(), now() "
    "ON CONFLICT (key) DO NOTHING"
)

DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("build_prefix", sa.String(1024), nullable=False),
        sa.Column("project_segment", sa.String(100), nullable=False),
        sa.Column("status", sa.String(10), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("sentinel_etag", sa.String(255), nullable=True),
        sa.Column("sentinel_last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.CheckConstraint(f"status IN ({_in(_STATUSES)})", name="ck_dfb_status"),
        sa.CheckConstraint(_QUEUED_HAS_TASK, name="ck_dfb_queued_has_task"),
    )
    op.create_index("uq_dfb_build_prefix", TABLE, ["build_prefix"], unique=True)
    op.create_index("ix_dfb_pending_first_seen", TABLE, ["first_seen_at"], postgresql_where=sa.text(_PENDING))
    for key, description in _FLAGS:
        op.execute(sa.text(INSERT_SQL).bindparams(key=key, description=description))


def downgrade() -> None:
    for key, description in _FLAGS:
        op.execute(sa.text(DELETE_SQL).bindparams(key=key, description=description))
    op.drop_index("ix_dfb_pending_first_seen", table_name=TABLE)
    op.drop_index("uq_dfb_build_prefix", table_name=TABLE)
    op.drop_table(TABLE)
