"""Granular test-step + attachment snapshot (Phase 1 of granular test detail).

Revision ID: 0093
Revises: 0092
Create Date: 2026-06-09

LOCKED retention model = LATEST RUN ONLY. Granular step / assertion /
attachment detail is **one snapshot per logical test** — i.e. per
``(project_id, test_fingerprint)`` — overwritten via delete-then-insert on
each new run's ingestion. There is no per-step time-series.

The project-scoped ``canonical_test_cases`` table (migration 0075) already IS
that grain: unique on ``(project_id, test_fingerprint)``, one row per logical
test per project. So ``test_steps`` / ``test_attachments`` anchor to
``canonical_test_case_id`` (CASCADE) rather than to the per-run ``test_cases``
rows that accumulate. ``source_test_run_id`` records which run the current
snapshot came from (provenance); on ingest of a newer run for the same
canonical test, ingestion DELETEs the prior steps/attachments for that
canonical id and INSERTs the new ones — inside the existing ingestion-pipeline
transaction (no service-level commit).

Also adds four nullable per-run metadata columns to ``test_cases``
(retry_count, is_flaky_run, stack_trace, step_count) populated by the parsers.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── test_steps ───────────────────────────────────────────────
    # Anchored to the canonical (project, fingerprint) test identity so there
    # is exactly ONE step snapshot per logical test. CASCADE: dropping the
    # canonical row drops its steps. parent_step_id self-FK (CASCADE) models
    # nested steps (Allure before/after + child steps); NULL = top level.
    op.create_table(
        "test_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "canonical_test_case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("canonical_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Provenance only — which run produced this snapshot. SET NULL so a run
        # deletion doesn't drop the latest-run snapshot we already materialised.
        sa.Column(
            "source_test_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "parent_step_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_steps.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("name", sa.String(2000), nullable=False),
        sa.Column("keyword", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("start_ms", sa.BigInteger(), nullable=True),
        sa.Column("assertion_message", sa.Text(), nullable=True),
        sa.Column("assertion_trace", sa.Text(), nullable=True),
        sa.Column("expected_value", sa.Text(), nullable=True),
        sa.Column("actual_value", sa.Text(), nullable=True),
        sa.Column("parameters", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    # Snapshot fetch / delete-then-insert overwrite by canonical anchor, ordered.
    op.create_index(
        "ix_test_steps_canonical_ordinal",
        "test_steps",
        ["canonical_test_case_id", "ordinal"],
    )
    # Recursive walk of nested steps.
    op.create_index("ix_test_steps_parent", "test_steps", ["parent_step_id"])
    # FK index for the provenance pointer.
    op.create_index("ix_test_steps_source_run", "test_steps", ["source_test_run_id"])

    # ── test_attachments ─────────────────────────────────────────
    # Index-only metadata (Phase 1): store refs (source_ref), do not proxy
    # bytes. Anchored to the canonical test (CASCADE) like steps; optional
    # test_step_id (CASCADE) links a step-scoped attachment, NULL = test-level.
    op.create_table(
        "test_attachments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "canonical_test_case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("canonical_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_step_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_steps.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "source_test_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("source_ref", sa.String(1000), nullable=True),
        sa.Column("media_type", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_test_attachments_canonical",
        "test_attachments",
        ["canonical_test_case_id"],
    )
    op.create_index("ix_test_attachments_step", "test_attachments", ["test_step_id"])

    # ── test_cases per-run metadata columns ──────────────────────
    # Nullable, populated by the Allure/pytest parsers; no backfill of history.
    op.add_column("test_cases", sa.Column("retry_count", sa.Integer(), nullable=True))
    op.add_column("test_cases", sa.Column("is_flaky_run", sa.Boolean(), nullable=True))
    op.add_column("test_cases", sa.Column("stack_trace", sa.Text(), nullable=True))
    op.add_column("test_cases", sa.Column("step_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_cases", "step_count")
    op.drop_column("test_cases", "stack_trace")
    op.drop_column("test_cases", "is_flaky_run")
    op.drop_column("test_cases", "retry_count")

    op.drop_index("ix_test_attachments_step", table_name="test_attachments")
    op.drop_index("ix_test_attachments_canonical", table_name="test_attachments")
    op.drop_table("test_attachments")

    op.drop_index("ix_test_steps_source_run", table_name="test_steps")
    op.drop_index("ix_test_steps_parent", table_name="test_steps")
    op.drop_index("ix_test_steps_canonical_ordinal", table_name="test_steps")
    op.drop_table("test_steps")
