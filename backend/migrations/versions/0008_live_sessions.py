"""Add live_sessions table for real-time test execution streaming.

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(100), nullable=False),
        sa.Column("client_name", sa.String(255), nullable=False),
        sa.Column("machine_id", sa.String(255), nullable=True),
        sa.Column("build_number", sa.String(100), nullable=True),
        sa.Column("framework", sa.String(50), nullable=True),
        sa.Column("branch", sa.String(255), nullable=True),
        sa.Column("commit_hash", sa.String(64), nullable=True),
        # SHA-256 hash of the plaintext session token — never store plaintext
        sa.Column("session_token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("total_tests", sa.Integer, nullable=False, server_default="0"),
        sa.Column("events_received", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extra_metadata", postgresql.JSON, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Indexes for common query patterns
    op.create_index(
        "ix_live_sessions_run_id", "live_sessions", ["run_id"]
    )
    op.create_index(
        "ix_live_sessions_project_status", "live_sessions", ["project_id", "status"]
    )
    op.create_index(
        "ix_live_sessions_token_hash", "live_sessions", ["session_token_hash"]
    )
    op.create_index(
        "ix_live_sessions_started_at", "live_sessions", ["started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_live_sessions_started_at", table_name="live_sessions")
    op.drop_index("ix_live_sessions_token_hash", table_name="live_sessions")
    op.drop_index("ix_live_sessions_project_status", table_name="live_sessions")
    op.drop_index("ix_live_sessions_run_id", table_name="live_sessions")
    op.drop_table("live_sessions")
