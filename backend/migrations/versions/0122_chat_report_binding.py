"""Bind chat sessions to immutable DecisionReport versions.

Revision ID: 0122
Revises: 0121
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("active_test_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("active_report_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("active_report_version", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_sessions_active_test_run",
        "chat_sessions",
        "test_runs",
        ["active_test_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_chat_sessions_active_report_version_positive",
        "chat_sessions",
        "active_report_version IS NULL OR active_report_version >= 1",
    )
    op.create_index(
        "ix_chat_sessions_active_report",
        "chat_sessions",
        ["active_test_run_id", "active_report_id", "active_report_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_active_report", table_name="chat_sessions")
    op.drop_constraint(
        "ck_chat_sessions_active_report_version_positive",
        "chat_sessions",
        type_="check",
    )
    op.drop_constraint(
        "fk_chat_sessions_active_test_run",
        "chat_sessions",
        type_="foreignkey",
    )
    op.drop_column("chat_sessions", "active_report_version")
    op.drop_column("chat_sessions", "active_report_id")
    op.drop_column("chat_sessions", "active_test_run_id")