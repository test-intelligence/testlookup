"""add project_id to api_keys for project-scoped keys

Revision ID: 0056
Revises: 0055
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0056"
down_revision = "0055"


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_project_id", table_name="api_keys")
    op.drop_column("api_keys", "project_id")
