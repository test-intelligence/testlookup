"""Add ``ingestion_source`` to ``test_runs``.

Revision ID: 0091
Revises: 0090
Create Date: 2026-06-06

Records HOW a run's results entered TestLookup so the UI can badge manual
uploads and dashboards can distinguish CI-driven runs from operator uploads.

Values (app-level enum ``IngestionSource``):

  * ``live``    — SDK live-stream session.
  * ``sdk``     — SDK/CI batch POST to /api/v1/ingest.
  * ``upload``  — operator manually uploaded a report file via the UI.
  * ``file``    — Allure/TestNG file via the MinIO webhook (sentinel) path.
  * ``unknown`` — pre-migration rows whose origin couldn't be inferred.

The column is ``NOT NULL`` with ``server_default='unknown'`` so existing rows
get a value immediately. Existing rows are then backfilled with a best-effort
heuristic (live > sdk > file > unknown). Downgrade drops the column.
"""
from alembic import op
import sqlalchemy as sa


revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column(
            "ingestion_source",
            sa.String(length=20),
            nullable=False,
            server_default="unknown",
            comment=(
                "How the run entered TestLookup: live | sdk | upload | file | "
                "unknown. Drives the 'Uploaded' badge and source filtering."
            ),
        ),
    )

    # Best-effort backfill for historical rows. Order matters: a live run that
    # was also archived keeps 'live'; an explicit API batch (trigger_source
    # 'api') is 'sdk'; anything with a MinIO prefix arrived via the file
    # webhook; the rest stay 'unknown'.
    op.execute(
        "UPDATE test_runs SET ingestion_source = 'live' "
        "WHERE event_archive IS NOT NULL OR trigger_source = 'live_stream'"
    )
    op.execute(
        "UPDATE test_runs SET ingestion_source = 'sdk' "
        "WHERE ingestion_source = 'unknown' AND trigger_source = 'api'"
    )
    op.execute(
        "UPDATE test_runs SET ingestion_source = 'file' "
        "WHERE ingestion_source = 'unknown' AND minio_prefix IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("test_runs", "ingestion_source")
