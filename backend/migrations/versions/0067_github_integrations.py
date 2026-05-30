"""github_integrations + github_checks feature flag

Revision ID: 0067
Revises: 0066
Create Date: 2026-04-14

Tier 1 item 5 — per-project GitHub Checks API integration. One row per
project holds the repo + API base URL + has-token hint. The actual PAT
lives in ``secret_service`` under scope ``github_integration`` so a
DB dump cannot recover active tokens.

Also seeds the ``github_checks`` feature flag (disabled by default).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("repo_owner", sa.String(length=255), nullable=False),
        sa.Column("repo_name", sa.String(length=255), nullable=False),
        sa.Column(
            "api_base_url",
            sa.String(length=500),
            nullable=False,
            server_default="https://api.github.com",
        ),
        sa.Column("has_pat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("project_id", name="uq_github_integrations_project"),
    )
    op.create_index(
        "ix_github_integrations_project",
        "github_integrations",
        ["project_id"],
        unique=True,
    )

    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'github_checks', "
            "'Post GitHub Checks API check runs on test run completion. "
            "Tier 1 item 5.', false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM feature_flags WHERE key = 'github_checks'")
    )
    op.drop_index("ix_github_integrations_project", table_name="github_integrations")
    op.drop_table("github_integrations")
