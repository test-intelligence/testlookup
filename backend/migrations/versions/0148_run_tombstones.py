"""S2c — a run that was deliberately deleted must not come back.

Five code paths create a ``TestRun`` from a caller-supplied id on a SELECT
miss: the stream stub, ``persist_live_session``, the live-session drainer, the
live-persist Celery task, and ``ingestion_pipeline`` when a ``run_id`` is
passed. Each exists for a good reason — a live session that races the drainer
must still land in ``test_runs``.

Together they are why a per-run delete could not be offered. Delete a run while
any of them is in flight and the row reappears seconds later, with its Mongo
events, MinIO objects and event archive already gone: a run that looks real and
has nothing behind it. Refusing to delete an ``IN_PROGRESS`` run narrows that
window; it does not close it, because a Celery task already holding the id does
not re-read the status.

``run_id`` is the primary key and is deliberately NOT a foreign key to
``test_runs``: the row it names has been deleted, which is the point. A FK here
would make the tombstone impossible to write.

Retention retires tombstones on the audit clock, so this does not become a
table that only grows.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0148"
down_revision = "0147"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_tombstones",
        # No ForeignKey to test_runs: the run is gone. That is the record.
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "deleted_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "deletion_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deletion_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_run_tombstones_project_deleted",
        "run_tombstones",
        ["project_id", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_run_tombstones_project_deleted", table_name="run_tombstones"
    )
    op.drop_table("run_tombstones")
