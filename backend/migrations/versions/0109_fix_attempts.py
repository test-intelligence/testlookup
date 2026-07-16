"""fix attempts table (Agentic plan AI-2 — the Fixer)

Revision ID: 0109
Revises: 0108
Create Date: 2026-07-15

Agentic plan Wave D — AI-2 (the Fixer): a scheduled, budgeted agent that
selects flaky/quarantined tests, generates test-code-only candidate fixes,
validates them by rerunning the test in a sandbox, and (suggest mode only)
opens a DRAFT PR. One table:

* ``fix_attempts`` — one row per (fixer run, candidate test, attempt). It is
  the progress surface the UI polls (``GET .../fixer/attempts``) and the
  audit trail for every pipeline stage: the per-attempt status walks the
  pinned lifecycle (selected → diagnosing → generating → validating →
  {validated | rejected_globs | failed_validation | pr_opened | error |
  skipped_budget}), the generated patch + its summary, the validation
  rerun tally, an optional draft-PR link + its polled state, and the
  ``agent_runs`` ledger row id for the deep link. Indexed on
  (project_id, created_at) for the paged list and (fixer_run_id) for
  per-run rollups.

The Fixer's CONFIG is NOT a new table — it rides the existing
``agent_policies`` row for ``agent_id='fixer'``: ``enabled`` + ``mode``
map to the columns, and the ``budgets`` JSONB carries the fixer-specific
runner block, test globs, per-run/per-test budgets, validation rerun
count, concurrent-open-PR cap, and schedule. See
``services/fixer_service.py``.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fix_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Groups every attempt produced by one scheduler/manual fixer run.
        # Not a FK — a fixer run has no standalone row (the run's ledger
        # entry is the agent_runs row); this is a correlation id.
        sa.Column("fixer_run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(500), nullable=True),
        # selected | diagnosing | generating | validating | validated |
        # rejected_globs | failed_validation | pr_opened | error | skipped_budget
        sa.Column("status", sa.String(30), nullable=False, server_default="selected"),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        # One-line human summary of the generated diff (files touched, +/-).
        sa.Column("patch_summary", sa.Text(), nullable=True),
        # The full unified diff (test-code-only). NULL when nothing generated.
        sa.Column("patch", sa.Text(), nullable=True),
        # Validation rerun tally — serialized to {"reruns","passed"} | null.
        sa.Column("validation_reruns", sa.Integer(), nullable=True),
        sa.Column("validation_passed", sa.Integer(), nullable=True),
        sa.Column("runner_type", sa.String(30), nullable=True),
        sa.Column("runner_log_digest", sa.String(128), nullable=True),
        # True when the policy opened container egress for this validation
        # (default is --network=none). Recorded so the ledger is honest.
        sa.Column("egress_opened", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pr_url", sa.String(1000), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        # open | merged | closed — maintained by the outcome poller beat.
        sa.Column("pr_state", sa.String(20), nullable=True),
        # agent_runs row id for the run this attempt belongs to (deep link).
        sa.Column("ledger_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_fix_attempts_project_created",
        "fix_attempts",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_fix_attempts_fixer_run",
        "fix_attempts",
        ["fixer_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_fix_attempts_fixer_run", table_name="fix_attempts")
    op.drop_index("ix_fix_attempts_project_created", table_name="fix_attempts")
    op.drop_table("fix_attempts")
