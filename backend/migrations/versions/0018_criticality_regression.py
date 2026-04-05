"""Add score_model_version to release_decisions and regression_classification to failure_clusters

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-31

Changes (all additive — nullable, no backfill required):
  release_decisions:
    - score_model_version  INTEGER  — scoring model version when this decision was produced
  failure_clusters:
    - regression_classification  VARCHAR(50) — deterministic regression classification
      values: new_regression | known_flaky | environmental | product_bug | infrastructure | unclassified
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── release_decisions ───────────────────────────────────────
    op.add_column(
        "release_decisions",
        sa.Column(
            "score_model_version",
            sa.Integer(),
            nullable=True,
            comment="Scoring model version (from criticality_service.SCORE_MODEL_VERSION) "
                    "when this decision was produced",
        ),
    )

    # ── failure_clusters ────────────────────────────────────────
    op.add_column(
        "failure_clusters",
        sa.Column(
            "regression_classification",
            sa.String(50),
            nullable=True,
            comment="Deterministic regression classification: "
                    "new_regression | known_flaky | environmental | product_bug | unclassified",
        ),
    )


def downgrade() -> None:
    op.drop_column("failure_clusters", "regression_classification")
    op.drop_column("release_decisions", "score_model_version")
