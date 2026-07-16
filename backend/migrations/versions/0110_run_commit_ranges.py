"""run commit ranges — commit attribution (Epic 8 US-8.1/US-8.2)

Revision ID: 0110
Revises: 0109
Create Date: 2026-07-16

Epic 8 (commit attribution) US-8.1 — persist the commit range associated
with a run: the commits landed since the run's last-green baseline
(base = last-green run's ``commit_hash``, head = this run's
``commit_hash``). One table:

* ``run_commit_ranges`` — one row per run (``run_id`` UNIQUE, idempotent
  per run). ``source`` records HOW the range was acquired:

    - ``connector`` — fetched from the configured GitHub integration
      (commits + per-commit changed files via the API).
    - ``supplied``  — a caller (SDK/CI) pushed the commit list on ingest
      (air-gapped path, no VCS call). Supplied always wins over connector.
    - ``unavailable`` — neither path yielded data (honest empty state).

  ``commits`` is a bounded JSONB list of
  ``{sha, author, message, files:[...], committed_at}`` (oldest→newest).
  ``base_commit`` / ``head_commit`` bound the range; ``base_run_id`` is the
  baseline run for the deep link / decision trail. ``resolved_at`` stamps
  when the range was last resolved.

Suspect ranking (US-8.2) is a pure read over this table + the run's
failing test cases — no additional schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_commit_ranges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # base = last-green baseline commit; head = this run's commit. Either
        # can be NULL (unknown baseline / run without a commit_hash).
        sa.Column("base_commit", sa.String(64), nullable=True),
        sa.Column("head_commit", sa.String(64), nullable=True),
        # The baseline run the range was computed against (deep link).
        sa.Column("base_run_id", UUID(as_uuid=True), nullable=True),
        # connector | supplied | unavailable
        sa.Column("source", sa.String(20), nullable=False, server_default="unavailable"),
        # [{sha, author, message, files:[...], committed_at}] oldest→newest.
        sa.Column("commits", JSONB, nullable=False, server_default="[]"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        # One range per run — the acquisition is idempotent per run.
        sa.UniqueConstraint("run_id", name="uq_run_commit_ranges_run"),
    )
    op.create_index(
        "ix_run_commit_ranges_project",
        "run_commit_ranges",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_commit_ranges_project", table_name="run_commit_ranges")
    op.drop_table("run_commit_ranges")
