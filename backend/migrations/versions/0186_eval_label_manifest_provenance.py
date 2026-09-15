"""Stamp feedback and review labels with eval-manifest provenance (E9.10).

Revision ID: 0186
Revises: 0185
Create Date: 2026-09-14

Both tables already exist. The new nullable columns need no index: feedback
dataset construction continues to use the existing created-at index and caps
the result at 1,000 rows; review-label consumers use the existing scoped
indexes. Legacy rows stay NULL and are excluded from release gates.
"""

import sqlalchemy as sa
from alembic import op

revision = "0186"
down_revision = "0185"
branch_labels = None
depends_on = None

_CHECK = "eval_manifest_checksum IS NULL OR eval_manifest_checksum ~ '^[0-9a-f]{64}$'"


def upgrade() -> None:
    op.add_column(
        "ai_feedback",
        sa.Column("eval_manifest_checksum", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_ai_feedback_eval_manifest_checksum", "ai_feedback", _CHECK
    )
    op.add_column(
        "review_requests",
        sa.Column("eval_manifest_checksum", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_review_requests_eval_manifest_checksum", "review_requests", _CHECK
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_review_requests_eval_manifest_checksum",
        "review_requests",
        type_="check",
    )
    op.drop_column("review_requests", "eval_manifest_checksum")
    op.drop_constraint(
        "ck_ai_feedback_eval_manifest_checksum",
        "ai_feedback",
        type_="check",
    )
    op.drop_column("ai_feedback", "eval_manifest_checksum")
