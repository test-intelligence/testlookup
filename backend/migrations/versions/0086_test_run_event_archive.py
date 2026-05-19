"""Add ``event_archive`` JSONB + ``event_archive_at`` timestamp on ``test_runs``.

Revision ID: 0086
Revises: 0085
Create Date: 2026-05-16

User-reported feature ask: "the run intelligence should be available for
at least 15 days". The original recovery path read from a Redis buffer
with a 25-hour TTL. Once the buffer expired the user had no way to
backfill ``test_cases`` for a live-stream run whose initial persist
crashed (worker death, transient error). The fix archives the buffered
SDK events durably on ``test_runs`` at session-close time, giving the
"Recover from buffer" CTA a 15-day fallback window instead of 25 hours.

Columns:

  * ``event_archive`` (``JSONB``, nullable) — the JSON-encoded list of
    raw SDK events. Stored compact (no metadata, no per-event wrapper)
    so the row stays close to the row-size budget. Typical size: a few
    KB per run; pathological large suites top out around 1 MB.
  * ``event_archive_at`` (``TIMESTAMPTZ``, nullable) — the wall-clock
    timestamp the archive was written. Drives the 15-day expiry check
    on read AND the future purge job that NULLs out the column once
    the data is no longer load-bearing.

Both columns are nullable so existing rows (live-stream runs before
this migration, and every non-live run) leave them empty. Downgrade
drops both columns; no data preserved.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column(
            "event_archive",
            JSONB,
            nullable=True,
            comment=(
                "JSON list of raw SDK events buffered during a live-stream "
                "run. Populated at session-close time so /runs/{id}/recover-live "
                "can replay per-test rows for at least 15 days after the run "
                "completes — well past the 25-hour Redis buffer TTL."
            ),
        ),
    )
    op.add_column(
        "test_runs",
        sa.Column(
            "event_archive_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Wall-clock timestamp the event_archive column was written. "
                "Used to enforce the 15-day recovery window on read."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("test_runs", "event_archive_at")
    op.drop_column("test_runs", "event_archive")
