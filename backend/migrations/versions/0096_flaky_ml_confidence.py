"""FLK-P3 — ML flakiness-confidence score on flaky verdicts.

Revision ID: 0096
Revises: 0095
Create Date: 2026-06-14

Adds ``flaky_coach_results.is_flaky_confidence`` — the [0, 1] probability from
the FLK-P3 ML model (`services/ml/flaky_confidence.py`) that a verdict is a
genuine flake, learned from human quarantine approve/reject decisions. Nullable:
populated only when a trained model is available at refresh time; absent models
leave it NULL and the leaderboard falls back to the deterministic Wilson /
intermittency signals.
"""
from alembic import op
import sqlalchemy as sa


revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flaky_coach_results",
        sa.Column("is_flaky_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flaky_coach_results", "is_flaky_confidence")
