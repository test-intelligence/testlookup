"""Mark machine accounts so they can be enrolled in projects automatically.

The MCP server authenticates as ``mcp_service``. Two things were wrong:

  * no deploy path ever created that account, so every authenticated MCP tool
    returned 401 (the secret held credentials for a user that did not exist);
  * once created as a QA_LEAD it saw nothing, because non-admin users only see
    projects they are a member of — ``list_projects`` answered "No active
    projects found." while five projects existed.

Enrolling it once is not enough: a project created afterwards is invisible to
it. Rather than hard-coding one username in the backend, flag the account as a
service account so project creation can enrol every one of them.

Revision ID: 0136
Revises: 0135
"""
from alembic import op
import sqlalchemy as sa

revision = "0136"
down_revision = "0135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_service_account",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Partial index: the auto-enrol path queries only for service accounts, and
    # they are a handful of rows among all users.
    op.create_index(
        "ix_users_is_service_account",
        "users",
        ["is_service_account"],
        postgresql_where=sa.text("is_service_account"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_is_service_account", table_name="users")
    op.drop_column("users", "is_service_account")
