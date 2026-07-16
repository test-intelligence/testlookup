"""gitlab_integrations + gitlab feature flag

Revision ID: 0111
Revises: 0110
Create Date: 2026-07-16

PMF Epic 3 US-3.1 — per-project GitLab integration. One row per project
holds the base URL (self-managed or gitlab.com), the ``group/project`` path,
the MR-note mode, and the commit-status toggle. The actual PAT lives in
``secret_service`` under scope ``gitlab_integration`` so a DB dump cannot
recover active tokens.

Also seeds the ``gitlab`` feature flag (disabled by default) — the outbound
kill switch for MR notes + commit statuses, mirroring ``github_checks``.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gitlab_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "base_url",
            sa.String(length=500),
            nullable=False,
            server_default="https://gitlab.com",
        ),
        sa.Column("project_path", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "mr_comment_mode",
            sa.String(length=20),
            nullable=False,
            server_default="failures_only",
        ),
        sa.Column(
            "commit_status_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
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
        sa.UniqueConstraint("project_id", name="uq_gitlab_integrations_project"),
    )
    op.create_index(
        "ix_gitlab_integrations_project",
        "gitlab_integrations",
        ["project_id"],
        unique=True,
    )

    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'gitlab', "
            "'Post GitLab MR notes + commit statuses on test run completion. "
            "PMF Epic 3.', false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM feature_flags WHERE key = 'gitlab'"))
    op.drop_index("ix_gitlab_integrations_project", table_name="gitlab_integrations")
    op.drop_table("gitlab_integrations")
