"""Add flaky_detection_state — when a test entered the corpus, and when we
first had anything to say about it.

Phase 6 of ``architecture/TEST_INTELLIGENCE_PLAN.md`` — the last phase, and the
one whose headline justification did not survive review.

## What this is NOT justified by

The plan originally leaned on "75% of flaky tests are already flaky at their
introducing commit". That claim was **refuted 1–2** in the evidence review and
is not used here. What survives is a weaker and heavily qualified finding:
**85/15 among order- and implementation-dependent flaky tests in 55 Java OSS
projects** — 85% were catchable by screening tests that were new or directly
modified, the remaining 15% became flaky from changes elsewhere. That corpus was
245 flaky tests found by two detectors, skewed towards order- and
implementation-dependent flakiness with async-wait, concurrency and network
flakiness under-sampled, and its authors state the results may not generalize.

It is enough to justify the *shape* of detection — screen the new and the
directly-modified first, but keep sweeping everything else, because the 15%
tail is exactly the environment- and dependency-induced flakiness this product
sees most. It is not enough to justify a constant, so nothing here hard-codes
one.

## Why a table rather than a computed metric

Detection latency is "time from a test entering the corpus to the system first
having a defensible thing to say about it". Both ends have to be *observed*: an
inferred first-seen would be an artefact of whatever window happened to be read.
So the first observation is written down once and never re-derived.

``first_seen_is_exact`` is the honesty column. It is true only when the
fingerprint's earliest surviving run is inside the window that screened it —
i.e. we genuinely watched it appear. For everything that predates screening it
is false, and those rows are **excluded from latency statistics** rather than
contributing a fabricated zero. Retention purges can delete the older runs that
would have made a fingerprint look old; that limitation is reported in the
payload rather than hidden.

Revision ID: 0135
Revises: 0134
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0135"
down_revision = "0134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flaky_detection_state",
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
        # Project-scoped by construction: test_fingerprint is unique only
        # WITHIN a project, so an unscoped read would blend tenants.
        sa.Column("test_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("test_name", sa.String(length=1000), nullable=True),
        # When the fingerprint's earliest surviving run happened. Written once,
        # on first observation, and never recomputed — a re-derived first-seen
        # would track the reader's window rather than the test's history.
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        # True only when we watched it appear. False rows are excluded from
        # latency statistics instead of contributing a fabricated zero.
        sa.Column(
            "first_seen_is_exact",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Which tier-1 population it landed in: new_test, modified_test, or
        # corpus_sweep for anything only tier 2 ever reached.
        sa.Column("screen_reason", sa.String(length=32), nullable=False),
        sa.Column("first_screened_at", sa.DateTime(timezone=True), nullable=True),
        # When a score first cleared the evidence floor. NULL means "still
        # unscoreable", which is a real state, not a missing value.
        sa.Column("first_scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_scored_confidence", sa.String(length=20), nullable=True),
        sa.Column(
            "observation_count", sa.Integer(), nullable=False, server_default="0"
        ),
        # Tier 2's footprint: the continuous background pass over the whole
        # corpus. Coverage is measured from this, so a project cannot appear
        # swept because the beat merely ran.
        sa.Column("last_swept_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ux_flaky_detection_state_project_fingerprint",
        "flaky_detection_state",
        ["project_id", "test_fingerprint"],
        unique=True,
    )
    # Latency reads: "which of this project's tests have been scored, and how
    # long did it take" — and its complement, the still-unscoreable backlog.
    op.create_index(
        "ix_flaky_detection_state_project_scored",
        "flaky_detection_state",
        ["project_id", "first_scored_at"],
    )
    # Coverage reads: the staleness sweep.
    op.create_index(
        "ix_flaky_detection_state_project_swept",
        "flaky_detection_state",
        ["project_id", "last_swept_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_flaky_detection_state_project_swept", table_name="flaky_detection_state"
    )
    op.drop_index(
        "ix_flaky_detection_state_project_scored", table_name="flaky_detection_state"
    )
    op.drop_index(
        "ux_flaky_detection_state_project_fingerprint",
        table_name="flaky_detection_state",
    )
    op.drop_table("flaky_detection_state")
