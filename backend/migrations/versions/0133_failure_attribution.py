"""Add failure_attribution — one verdict per failure, with its evidence.

Phase 4 of ``architecture/TEST_INTELLIGENCE_PLAN.md`` — the flagship.

At Google roughly **84% of pass→fail transitions involve a flaky test**. A raw
transition is therefore a weak signal of a real regression, and presenting one as
"new failure" floods engineers with false positives until they dismiss the real
ones. This table holds the composed answer instead: given everything we know, is
this failure attributable to the change, to flakiness, or to infrastructure?

## Why the inputs are stored, not just the verdict

``inputs`` is a snapshot of the five signals the verdict was composed from. Two
reasons:

* **Auditability.** When a verdict disagrees with an engineer, the useful
  question is *which input was wrong*, and that is unanswerable from a bare
  label. Practitioners reject generic explanations and want the specific files
  and prior failures named.
* **Replay.** Signals move — a flaky score is recomputed nightly, clusters are
  rebuilt on a window. Without the snapshot, yesterday's verdict cannot be
  explained with today's data.

## Why there is no "suppressed" column

There is deliberately no way to record that a verdict silenced a failure.
Google's follow-up found that when a previously stable test turned flaky,
roughly **1 in 6 times the cause was a real production bug**. A wrongly-shown
verdict costs a minute; a wrongly-suppressed failure ships the bug. The schema
does not offer the option.

Revision ID: 0133
Revises: 0132
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0133"
down_revision = "0132"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failure_attribution",
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
        sa.Column(
            "test_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_fingerprint", sa.String(length=64), nullable=False),
        # LIKELY_YOUR_CHANGE | LIKELY_FLAKY | LIKELY_INFRA | UNCERTAIN.
        # UNCERTAIN is a first-class answer, not a failure to decide.
        sa.Column("verdict", sa.String(length=32), nullable=False),
        # 0-1. Never used to hide anything — only to rank what a human sees.
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        # Why this verdict, in words, for the person whose test it is.
        sa.Column("rationale", sa.String(length=1000), nullable=False, server_default=""),
        # Snapshot of the five composed signals.
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # One current verdict per test case.
    op.create_index(
        "ux_failure_attribution_test_case",
        "failure_attribution",
        ["test_case_id"],
        unique=True,
    )
    # Run-detail reads every verdict for a run at once.
    op.create_index(
        "ix_failure_attribution_run",
        "failure_attribution",
        ["test_run_id"],
    )
    # Ranking a project's failures by probability-of-real-regression.
    op.create_index(
        "ix_failure_attribution_project_verdict",
        "failure_attribution",
        ["project_id", "verdict"],
    )


def downgrade() -> None:
    op.drop_index("ix_failure_attribution_project_verdict", table_name="failure_attribution")
    op.drop_index("ix_failure_attribution_run", table_name="failure_attribution")
    op.drop_index("ux_failure_attribution_test_case", table_name="failure_attribution")
    op.drop_table("failure_attribution")
