"""Add suite_name to managed_test_cases

Revision ID: 0013
Revises: 0012
Create Date: 2026-03-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managed_test_cases",
        sa.Column("suite_name", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("managed_test_cases", "suite_name")
