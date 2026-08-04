"""run_commit_ranges.base_source + (project_id, resolved_at) index

Revision ID: 0116
Revises: 0115
Create Date: 2026-08-04

Commit ranges: make the corpus real and measurable (Epic 8 → Epic 10).

1. **``base_source``** — records WHICH anchor a range's ``base_commit`` came
   from. Until now the only way to get a base was a fully all-green prior
   run, which meant projects with a persistent failing/flaky tail (exactly
   the attribution audience) resolved to ``unavailable`` forever. The
   service now falls back to the most recent completed prior run with a
   commit hash, pass/fail irrelevant — a genuinely weaker anchor, so the
   row has to say so rather than let a weak base masquerade as a green one:

     - ``supplied``            — the caller pushed the base ref on ingest.
     - ``green_baseline``      — last all-green prior run (strongest).
     - ``last_completed_run``  — most recent completed prior run regardless
       of result. "Landed since" is then only true relative to a run that
       was itself failing.
     - ``unavailable``         — no base could be determined (the default,
       and what every pre-0116 row backfills to unless it has a base).

   Backfill is deliberately conservative. Every pre-0116 row with a
   ``base_run_id`` was produced by the green-baseline path (the only path
   that existed), so those become ``green_baseline``. Rows with no
   ``base_commit`` at all — including every ``supplied`` row, since the old
   ``store_supplied_range`` hard-coded ``base_commit=None`` — stay
   ``unavailable``. We do not invent an anchor for a row that never had one.

2. **``ix_run_commit_ranges_project_resolved``** replaces
   ``ix_run_commit_ranges_project``. The TIA-readiness metric
   (``GET /api/v1/metrics/tia-readiness``) scans one project's ranges
   within a time window ordered by ``resolved_at``; the single-column index
   forced a sort over every range the project ever had. The composite still
   serves the old project-only lookups via its leftmost prefix, so keeping
   both would only cost write amplification.
"""
from alembic import op
import sqlalchemy as sa


revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_commit_ranges",
        sa.Column(
            "base_source",
            sa.String(30),
            nullable=False,
            server_default="unavailable",
        ),
    )
    # Only rows that actually have a base get a non-default label, and the
    # only pre-0116 producer of a base was the green-baseline path.
    op.execute(
        """
        UPDATE run_commit_ranges
           SET base_source = 'green_baseline'
         WHERE base_run_id IS NOT NULL
           AND base_commit IS NOT NULL
           AND base_commit <> ''
        """
    )

    op.create_index(
        "ix_run_commit_ranges_project_resolved",
        "run_commit_ranges",
        ["project_id", "resolved_at"],
    )
    op.drop_index("ix_run_commit_ranges_project", table_name="run_commit_ranges")


def downgrade() -> None:
    op.create_index(
        "ix_run_commit_ranges_project",
        "run_commit_ranges",
        ["project_id"],
    )
    op.drop_index(
        "ix_run_commit_ranges_project_resolved", table_name="run_commit_ranges"
    )
    op.drop_column("run_commit_ranges", "base_source")
