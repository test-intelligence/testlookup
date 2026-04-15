"""Tier 2 items 9, 10, 12 — RAG faithfulness + perf baselines + retro schedule

Revision ID: 0069
Revises: 0068
Create Date: 2026-04-14

Three related Tier 2 items bundled so we don't ship three single-table
migrations in a row:

1. ``managed_test_cases`` — add ``faithfulness_score``,
   ``faithfulness_evaluator``, ``faithfulness_evaluated_at``, and
   ``needs_review_reason`` columns so the RAG faithfulness guardrail
   (T2-9) can score generated cases and route low-confidence ones to
   the "Needs review" queue instead of auto-accepting them.

2. ``perf_baselines`` — new table tracking rolling per-test duration
   statistics. Used by ``perf_regression_service`` (T2-10) to detect
   3σ latency spikes without scanning the full TestCase history on
   every release gate evaluation. The Welford accumulator column
   (``m2``) lets the nightly refresh task extend the series in O(1).

3. Seeds three feature flags:
   * ``rag_faithfulness_gate`` — T2-9 kill switch
   * ``perf_regression_detection`` — T2-10 kill switch
   * ``weekly_retro_digest`` — T2-12 kill switch

T2-12 does not need a schema change — the ``DigestSchedule`` enum is
stored as ``String(20)`` so adding ``WEEKLY_RETRO`` is a code-only
change. The flag seeded here is the runtime kill switch.
T2-11 (team-level value metrics) has no schema changes at all.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── T2-9: faithfulness columns on managed_test_cases ───────────────────
    op.add_column(
        "managed_test_cases",
        sa.Column("faithfulness_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("faithfulness_evaluator", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column(
            "faithfulness_evaluated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("needs_review_reason", sa.String(length=500), nullable=True),
    )

    # ── T2-10: perf_baselines table ────────────────────────────────────────
    op.create_table(
        "perf_baselines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("test_name", sa.String(length=500), nullable=True),
        sa.Column("suite_name", sa.String(length=500), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mean_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("m2", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stddev_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("p95_ms", sa.Float(), nullable=True),
        sa.Column("last_observed_ms", sa.Integer(), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id", "test_fingerprint", name="uq_perf_baseline_fingerprint",
        ),
    )
    op.create_index("ix_perf_baseline_project", "perf_baselines", ["project_id"])

    # ── Feature flag seeds ─────────────────────────────────────────────────
    for key, description in [
        (
            "rag_faithfulness_gate",
            "Score RAG-generated test cases for faithfulness and gate "
            "auto-accept on the threshold. Tier 2 item 9.",
        ),
        (
            "perf_regression_detection",
            "Per-test duration regression detection using Welford rolling "
            "stats. Tier 2 item 10.",
        ),
        (
            "weekly_retro_digest",
            "Weekly retro digest subscription type. Tier 2 item 12.",
        ),
    ]:
        op.execute(
            sa.text(
                "INSERT INTO feature_flags "
                "(id, key, description, enabled_global, rollout_percent, "
                "created_at, updated_at) "
                "VALUES (gen_random_uuid(), :key, :desc, false, 100, now(), now()) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(key=key, desc=description)
        )


def downgrade() -> None:
    for key in (
        "rag_faithfulness_gate",
        "perf_regression_detection",
        "weekly_retro_digest",
    ):
        op.execute(
            sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=key)
        )
    op.drop_index("ix_perf_baseline_project", table_name="perf_baselines")
    op.drop_table("perf_baselines")
    op.drop_column("managed_test_cases", "needs_review_reason")
    op.drop_column("managed_test_cases", "faithfulness_evaluated_at")
    op.drop_column("managed_test_cases", "faithfulness_evaluator")
    op.drop_column("managed_test_cases", "faithfulness_score")
