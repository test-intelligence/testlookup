"""value_metric_assumptions — per-project hours-saved tunables

Revision ID: 0112
Revises: 0111
Create Date: 2026-08-01

PMF US-12.1 — the engineer-hours-saved model on ``/value-metrics`` is
computed from three tunable per-project assumptions. One row per project;
a missing row resolves to the code defaults in
``value_metrics_service.EffectiveAssumptions`` (triage 20 min/failure,
blocked-run wait 30 min, defect filing 15 min) — no backfill needed.

Server defaults exactly match the ORM/Python defaults (the #433 drift
lesson).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "value_metric_assumptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "triage_minutes_per_failure",
            sa.Float(),
            nullable=False,
            server_default=sa.text("20.0"),
        ),
        sa.Column(
            "blocked_run_wait_minutes",
            sa.Float(),
            nullable=False,
            server_default=sa.text("30.0"),
        ),
        sa.Column(
            "defect_filing_minutes",
            sa.Float(),
            nullable=False,
            server_default=sa.text("15.0"),
        ),
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
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("project_id", name="uq_value_metric_assumptions_project"),
    )
    op.create_index(
        "ix_value_metric_assumptions_project",
        "value_metric_assumptions",
        ["project_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_value_metric_assumptions_project",
        table_name="value_metric_assumptions",
    )
    op.drop_table("value_metric_assumptions")
