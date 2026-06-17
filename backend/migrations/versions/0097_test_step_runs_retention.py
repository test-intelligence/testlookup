"""Per-run step-outcome retention (test_step_runs) for cross-run step-flip.

Revision ID: 0097
Revises: 0096
Create Date: 2026-06-17

``test_steps`` (migration 0093) is a LATEST-RUN-ONLY snapshot — delete+reinsert
per ``canonical_test_cases`` on every ingest — so cross-run step-flip ("which
step flipped between run N-1 and run N") cannot be computed from it.

This adds ``test_step_runs``: one compact, flat row per
``(canonical_test_case_id, source_test_run_id, ordinal)`` that RETAINS step
outcomes across runs. Only the step identity (ordinal/depth/name/keyword) and
the per-run signal (status/duration_ms) are stored — the heavy, PII-bearing
columns (assertion_message/trace, expected/actual, parameters, attachments)
stay ONLY on the latest-run ``test_steps`` snapshot and are NOT duplicated here.

``source_test_run_id`` is CASCADE (the row IS about that run; deleting the run
deletes its step history), unlike the snapshot's SET NULL provenance pointer.
Ingestion writes these rows alongside the snapshot, idempotent per
``(canonical, run)`` (delete this run's rows, reinsert). This is the foundation
slice; the cross-run step-flip analysis + surfacing land in a follow-up.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_step_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "canonical_test_case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("canonical_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CASCADE: the row is per-run history — when the run is deleted its step
        # history goes with it (NOT the snapshot's SET NULL provenance pointer).
        sa.Column(
            "source_test_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("name", sa.String(2000), nullable=False),
        sa.Column("keyword", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    # One row per step-position per run (idempotency invariant) + the composite
    # index for the per-(canonical, run) overwrite delete and the per-canonical
    # cross-run scan the step-flip analysis walks.
    op.create_unique_constraint(
        "uq_test_step_runs_canonical_run_ordinal",
        "test_step_runs",
        ["canonical_test_case_id", "source_test_run_id", "ordinal"],
    )
    # FK index for the run-deletion CASCADE.
    op.create_index("ix_test_step_runs_source_run", "test_step_runs", ["source_test_run_id"])


def downgrade() -> None:
    op.drop_index("ix_test_step_runs_source_run", table_name="test_step_runs")
    op.drop_constraint(
        "uq_test_step_runs_canonical_run_ordinal", "test_step_runs", type_="unique"
    )
    op.drop_table("test_step_runs")
