"""Defect promotion fields — nullable test_case_id + cluster metadata.

Revision ID: 0019
Revises: 0018
Create Date: 2026-03-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make test_case_id nullable (cluster promotion may not map to single test case)
    op.alter_column("defects", "test_case_id", nullable=True)
    # Add cluster promotion fields
    op.add_column("defects", sa.Column("cluster_id", sa.String(255), nullable=True))
    op.add_column("defects", sa.Column("title", sa.String(255), nullable=True))
    op.add_column("defects", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("defects", sa.Column("severity", sa.String(20), nullable=True))
    op.add_column("defects", sa.Column("component", sa.String(255), nullable=True))
    op.add_column("defects", sa.Column("owner_team", sa.String(255), nullable=True))
    op.add_column("defects", sa.Column("labels", sa.JSON(), nullable=True))
    op.add_column("defects", sa.Column("criticality_scores", sa.JSON(), nullable=True))
    op.add_column("defects", sa.Column("evidence_bundle", sa.JSON(), nullable=True))
    op.add_column(
        "defects",
        sa.Column("duplicate_of", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "defects",
        sa.Column(
            "is_duplicate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("defects", "is_duplicate")
    op.drop_column("defects", "duplicate_of")
    op.drop_column("defects", "evidence_bundle")
    op.drop_column("defects", "criticality_scores")
    op.drop_column("defects", "labels")
    op.drop_column("defects", "owner_team")
    op.drop_column("defects", "component")
    op.drop_column("defects", "severity")
    op.drop_column("defects", "description")
    op.drop_column("defects", "title")
    op.drop_column("defects", "cluster_id")
    op.alter_column("defects", "test_case_id", nullable=False)
