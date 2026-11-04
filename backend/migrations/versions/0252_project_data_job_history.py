"""Project data jobs carry run-history options and a parted upload (W30 ADM-05 phase 2).

* ``project_data_jobs.options`` JSONB NOT NULL DEFAULT ``'{}'`` -- the export's
  ``ProjectDataOptions`` (``include_history``, ``history_days``, ``max_runs``) or
  ``{"parted": true}`` on an import; a phase-1 row reads ``{}``.
* ``project_data_jobs.upload`` JSONB NULL -- a parted import upload's state
  (parts, bytes, received part numbers + digests) while the job is
  ``uploading``; NULL otherwise.
* ``project_data_jobs.cleanup`` JSONB NULL -- an import's LEDGER of what it wrote
  outside Postgres (new project id, run ids, pipeline ids), committed before the
  first document / object write and set NULL after a success or a finished
  cleanup. Never returned by the API (ORM ``deferred``).
* ``ck_project_data_jobs_status`` gains ``'uploading'`` (the literals are
  ``project_export_vocab.JOB_STATUSES``, held here as ``_STATUSES`` -- a
  migration imports no application code; pinned by ``tests/test_migration_0252.py``);
  NEW ``ck_project_data_jobs_uploading_is_import`` -- only an import uploads.

No index is added: ``ix_pdj_status_requested`` already serves the janitor's
``uploading`` sweep, and ``uq_pdj_import_token`` (kind = 'import' AND status <>
'failed') already covers an ``uploading`` job, so a re-sent start with the same
token answers that job. Adding a column with a constant default is
metadata-only on PostgreSQL 11+; the CHECK swap validates the (small) table
under a brief ACCESS EXCLUSIVE lock -- one row per export / import.

The downgrade FAILS CLOSED while any job is ``uploading`` (restoring the
five-literal CHECK would reject those rows, and dropping ``upload`` would
orphan their parts): let the janitor fail them first (UPLOAD_STALE_HOURS) or
fail them by hand. Otherwise it drops exactly what the upgrade made, in
reverse: the new CHECK, the widened CHECK (restoring 0247's literals), the
three columns.

Revision ID: 0252
Revises: 0251
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0252"
down_revision = "0251"
branch_labels = None
depends_on = None

TABLE = "project_data_jobs"
_STATUSES = ("uploading", "queued", "running", "succeeded", "failed", "expired")   # == project_export_vocab.JOB_STATUSES
_STATUSES_0247 = ("queued", "running", "succeeded", "failed", "expired")           # 0247's literals (the downgrade restores them)
_STATUS_CHECK = "ck_project_data_jobs_status"
_UPLOADING_CHECK = "ck_project_data_jobs_uploading_is_import"
_UPLOADING_IS_IMPORT = "status <> 'uploading' OR kind = 'import'"
_UPLOADING_ROWS_SQL = "SELECT count(*) FROM project_data_jobs WHERE status = 'uploading'"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("options", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(TABLE, sa.Column("upload", postgresql.JSONB(), nullable=True))
    op.add_column(TABLE, sa.Column("cleanup", postgresql.JSONB(), nullable=True))
    op.drop_constraint(_STATUS_CHECK, TABLE, type_="check")
    op.create_check_constraint(_STATUS_CHECK, TABLE, f"status IN ({_in(_STATUSES)})")
    op.create_check_constraint(_UPLOADING_CHECK, TABLE, _UPLOADING_IS_IMPORT)


def downgrade() -> None:
    if not op.get_context().as_sql:
        uploading = op.get_bind().execute(sa.text(_UPLOADING_ROWS_SQL)).scalar() or 0
        if uploading:
            raise RuntimeError(
                f"{uploading} project data job(s) are still 'uploading'; let the janitor fail them "
                "(or set status = 'failed') before downgrading below 0252"
            )
    op.drop_constraint(_UPLOADING_CHECK, TABLE, type_="check")
    op.drop_constraint(_STATUS_CHECK, TABLE, type_="check")
    op.create_check_constraint(_STATUS_CHECK, TABLE, f"status IN ({_in(_STATUSES_0247)})")
    op.drop_column(TABLE, "cleanup")
    op.drop_column(TABLE, "upload")
    op.drop_column(TABLE, "options")
