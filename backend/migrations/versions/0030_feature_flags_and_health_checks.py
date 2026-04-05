"""Add feature_flags and integration_health_checks tables.

Revision ID: 0030
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("flag_key", sa.String(100), primary_key=True),
        sa.Column("scope", sa.String(50), nullable=False, server_default="global"),  # global | environment | project
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "integration_health_checks",
        sa.Column("provider", sa.String(50), primary_key=True),   # jira | splunk | ocp | slack | teams | chromadb | ollama
        sa.Column("status", sa.String(20), nullable=False, server_default="unknown"),  # healthy | degraded | down | unknown
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("response_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("integration_health_checks")
    op.drop_table("feature_flags")
