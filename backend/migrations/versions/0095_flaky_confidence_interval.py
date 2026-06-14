"""FLK-P2 — statistical confidence interval on flaky verdicts.

Revision ID: 0095
Revises: 0094
Create Date: 2026-06-14

Adds the Wilson 95% confidence band on the failure ratio to
``flaky_coach_results``:

- ``flaky_confidence_low``  — Wilson lower bound, [0, 1], nullable.
- ``flaky_confidence_high`` — Wilson upper bound, [0, 1], nullable.

Both are nullable: historical cached rows and manual-triage leaderboard entries
carry no statistical interval until ``refresh_flaky_coach`` recomputes them.
The lower bound doubles as a deterministic statistical-strength tie-breaker in
the leaderboard ordering (a flake confirmed over many runs outranks a thin one
with the same point failure rate).
"""
from alembic import op
import sqlalchemy as sa


revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flaky_coach_results",
        sa.Column("flaky_confidence_low", sa.Float(), nullable=True),
    )
    op.add_column(
        "flaky_coach_results",
        sa.Column("flaky_confidence_high", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flaky_coach_results", "flaky_confidence_high")
    op.drop_column("flaky_coach_results", "flaky_confidence_low")
