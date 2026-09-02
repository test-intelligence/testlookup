"""S2b — an operational record of deletions actually running.

Retention already writes a purge record into ``settings_audit_log``, which is
never purged and remains the compliance evidence. That row is written *after*
the purge commits, so it can only ever describe work that finished: there is no
way to see that a destructive job is currently running, and no way to record
that one failed.

``deletion_jobs`` is that surface. The retention purge deletes TERMINAL rows on
the audit clock, so it does not become a second never-purged table; a row still
marked ``running`` past the window survives, because that is a sweep that hung
and a clock-based delete would erase the only evidence of it.

No ``cancelled`` status is declared. Nothing in this slice can produce one, and
a state nothing writes is a filter that matches nothing forever — the defect
this epic catalogues elsewhere. It arrives with the mechanism, or not at all.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0147"
down_revision = "0146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deletion_jobs",
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
        sa.Column("job_kind", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("criteria", postgresql.JSONB(), nullable=True),
        sa.Column("resolved_run_ids", postgresql.JSONB(), nullable=True),
        sa.Column("candidate_hash", sa.String(length=64), nullable=True),
        sa.Column("counts", postgresql.JSONB(), nullable=True),
        # Nullable with NO default: NULL means "not measured". A zero default
        # would report every job as having reclaimed nothing.
        sa.Column("bytes_reclaimed", sa.BigInteger(), nullable=True),
        sa.Column("holds_honoured", postgresql.JSONB(), nullable=True),
        sa.Column("references_broken", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "requested_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_deletion_jobs_project_started",
        "deletion_jobs",
        ["project_id", "started_at"],
    )
    op.create_index("ix_deletion_jobs_status", "deletion_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_deletion_jobs_status", table_name="deletion_jobs")
    op.drop_index("ix_deletion_jobs_project_started", table_name="deletion_jobs")
    op.drop_table("deletion_jobs")
