"""Add flaky_score — a continuous, explainable per-test flakiness score.

Phase 2 (P2-B) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

Replaces a binary flaky/not-flaky label with a bounded 0–1 score fused from four
signals. Every component is stored alongside the score, not just the total, for
two reasons: a score nobody can decompose is a number users are asked to trust
on faith, and a score that cannot be recomputed from its inputs cannot be
audited when it disagrees with a human.

``confidence`` is a separate, first-class column rather than folded into the
score. A test seen 4 times and a test seen 400 times can produce the same 0.5,
and collapsing that distinction is precisely how a thin-history guess comes to
look like a measurement.

**Deliberately NOT a Bayesian posterior.** The Phase 0 census measured every
genuine project on the reference deployment at 12–15 fingerprints with a median
of 5–12 runs each — far below what a moving-window posterior needs, where it
would mostly report its prior back. The four signals here degrade honestly at
that volume. See the gate reading in the plan.

Revision ID: 0131
Revises: 0130
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flaky_score",
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
        # test_fingerprint is only unique WITHIN a project — never query it
        # unscoped.
        sa.Column("test_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("test_name", sa.String(length=1000), nullable=True),
        # The composite, clamped to [0, 1].
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        # Per-signal contributions, so the score is decomposable and
        # reproducible: {"result_volatility": .., "retry_rate": .., ...}.
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # The weights used for THIS row's fusion. Stored per row so a later
        # re-weighting does not silently invalidate history.
        sa.Column(
            "weights",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # How much history the score is built on, and how much to trust it.
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default="none"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # One current score per (project, fingerprint) — the scorer upserts.
    op.create_index(
        "ux_flaky_score_project_fingerprint",
        "flaky_score",
        ["project_id", "test_fingerprint"],
        unique=True,
    )
    # Leaderboard reads: "most flaky in this project".
    op.create_index(
        "ix_flaky_score_project_score",
        "flaky_score",
        ["project_id", "score"],
    )


def downgrade() -> None:
    op.drop_index("ix_flaky_score_project_score", table_name="flaky_score")
    op.drop_index("ux_flaky_score_project_fingerprint", table_name="flaky_score")
    op.drop_table("flaky_score")
