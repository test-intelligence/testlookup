"""Project export / import jobs (W24 ADM-05 phase 1: settings + catalog).

* ``project_data_jobs``: one row per export or import. ``kind`` is
  ``export`` / ``import`` and ``status`` ``queued`` / ``running`` /
  ``succeeded`` / ``failed`` / ``expired`` (CHECKed with the literals of
  ``project_export_vocab.JOB_KINDS`` / ``JOB_STATUSES``, pinned by
  ``tests/test_migration_0247.py``). ``project_id`` is NULL for an import until
  its transaction commits and ``ON DELETE SET NULL`` (a job outlives the
  project it exported or created); ``requested_by_id`` is ``SET NULL`` too.
  ``storage_key`` (the archive / the upload) is never returned.
* ``uq_pdj_one_live_export`` -- partial unique on ``project_id`` WHERE an
  export is queued / running: one live export per project.
  ``uq_pdj_import_token`` -- partial unique on ``(requested_by_id,
  client_token)`` WHERE an import has not failed: the idempotency key (a
  failed job's token may be reused). ``ix_pdj_project_requested`` serves the
  project's exports, ``ix_pdj_requester_kind`` "your imports",
  ``ix_pdj_status_requested`` the janitor.

A NEW table, so its indexes are built in the same transaction,
non-concurrently (the 0166+ rule exempts a table the migration created
itself). No change to any existing table, no backfill, no seed, no flag. The
downgrade drops exactly what the upgrade created.

Revision ID: 0247
Revises: 0246
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0247"
down_revision = "0246"
branch_labels = None
depends_on = None

TABLE = "project_data_jobs"
_KINDS = ("export", "import")                                          # == project_export_vocab.JOB_KINDS
_STATUSES = ("queued", "running", "succeeded", "failed", "expired")    # == project_export_vocab.JOB_STATUSES


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_name", sa.String(255), nullable=True),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("client_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("archive_sha256", sa.String(64), nullable=True),
        sa.Column("signature", sa.String(16), nullable=True),
        sa.Column("signing_key_id", sa.String(64), nullable=True),
        sa.Column("counts", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column("progress", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"kind IN ({_in(_KINDS)})", name="ck_project_data_jobs_kind"),
        sa.CheckConstraint(f"status IN ({_in(_STATUSES)})", name="ck_project_data_jobs_status"),
    )
    op.create_index("ix_pdj_project_requested", TABLE, ["project_id", sa.text("requested_at DESC")])
    op.create_index(
        "uq_pdj_one_live_export",
        TABLE,
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'export' AND status IN ('queued', 'running')"),
    )
    op.create_index(
        "uq_pdj_import_token",
        TABLE,
        ["requested_by_id", "client_token"],
        unique=True,
        postgresql_where=sa.text("kind = 'import' AND status <> 'failed'"),
    )
    op.create_index("ix_pdj_status_requested", TABLE, ["status", "requested_at"])
    op.create_index("ix_pdj_requester_kind", TABLE, ["requested_by_id", "kind", sa.text("requested_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_pdj_requester_kind", table_name=TABLE)
    op.drop_index("ix_pdj_status_requested", table_name=TABLE)
    op.drop_index("uq_pdj_import_token", table_name=TABLE)
    op.drop_index("uq_pdj_one_live_export", table_name=TABLE)
    op.drop_index("ix_pdj_project_requested", table_name=TABLE)
    op.drop_table(TABLE)
