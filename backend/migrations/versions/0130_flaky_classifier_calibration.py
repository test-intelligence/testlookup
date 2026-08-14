"""Add flaky_classifier_calibration — measured per-project classifier quality.

Phase 0 (P0-2) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

The product classifies a failure as flaky partly by matching its error signature
against previously-seen flaky failures. Published measurement of that exact
technique (Alshammari et al., ICST 2024, 230,439 failures across 22 projects)
found specificity ranging from **100% on some projects to no better than random
on others**, driven by whether a project's failures carry distinctive exception
types. A single global matching strategy therefore cannot be trusted.

This table stores the *measured* answer per project, so a later phase can gate
behaviour on it instead of assuming the classifier transfers. It is written by a
scheduled backtest and read by nobody yet — Phase 0 measures, it does not act.

``specificity`` is deliberately nullable: a project without enough labelled
history must report as insufficient rather than as a number, which is why
``sample_count`` and ``insufficient_reason`` travel with it.

Revision ID: 0130
Revises: 0129
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flaky_classifier_calibration",
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
        # Which matching strategy was measured. Kept as a plain string so a new
        # strategy can be measured alongside the current one without a
        # migration, and so the two can be compared on the same corpus.
        sa.Column("method", sa.String(length=50), nullable=False),
        # True-negative rate: of the failures this method did NOT call flaky,
        # how many really were not flaky. Specificity rather than accuracy
        # because the costly error here is waving a real failure through.
        sa.Column("specificity", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("true_negatives", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("false_positives", sa.Integer(), nullable=False, server_default="0"),
        # Populated exactly when specificity is NULL.
        sa.Column("insufficient_reason", sa.String(length=200), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # One current row per (project, method) — the backtest upserts.
    op.create_index(
        "ux_flaky_calibration_project_method",
        "flaky_classifier_calibration",
        ["project_id", "method"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_flaky_calibration_project_method",
        table_name="flaky_classifier_calibration",
    )
    op.drop_table("flaky_classifier_calibration")
