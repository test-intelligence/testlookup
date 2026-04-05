"""Clean legacy 'UserRole.X' string values in role columns.

Strips the 'UserRole.' prefix from any stored role values so the
_normalize_user_role() workaround in deps.py can be simplified.

Affected tables: users, project_members, api_keys, user_invitations.

Revision ID: 0045
Revises: 0044
"""
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

# Tables with a `role` column stored as String(20)
_TABLES_WITH_ROLE = ["users", "project_members", "api_keys", "user_invitations"]


def upgrade() -> None:
    for table in _TABLES_WITH_ROLE:
        op.execute(
            f"UPDATE {table} SET role = REPLACE(role, 'UserRole.', '') "
            f"WHERE role LIKE 'UserRole.%'"
        )


def downgrade() -> None:
    # Intentionally a no-op — there's no reason to reintroduce the malformed data.
    pass
