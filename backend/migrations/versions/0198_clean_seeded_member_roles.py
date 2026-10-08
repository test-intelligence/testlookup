"""Clean 'UserRole.X' role strings again: the dev seed re-created them after 0045.

``scripts/seed_dev_data.py`` wrote every project membership as
``str(UserRole.X)``, which is ``'UserRole.QA_LEAD'`` on Python 3.11+, so every
seeded install (local, homelab demo) had memberships no role check matches: a
seeded QA lead got 403 updating the triage status of a failure in their own
Inbox ("Only the assigned user or a QA_LEAD/ADMIN on the project ...", the UX
redesign's browser E2E pass, 2026-10-07). The seed now writes ``.value``; this
repairs the rows it already wrote, exactly as 0045 did. Idempotent.

Revision ID: 0198
Revises: 0197
"""
from alembic import op

revision = "0198"
down_revision = "0197"
branch_labels = None
depends_on = None

# The tables with a UserRole-typed ``role`` column (as 0045).
_TABLES_WITH_ROLE = ["users", "project_members", "user_invitations"]


def upgrade() -> None:
    for table in _TABLES_WITH_ROLE:
        op.execute(
            f"UPDATE {table} SET role = REPLACE(role, 'UserRole.', '') "
            f"WHERE role LIKE 'UserRole.%'"
        )


def downgrade() -> None:
    # Intentionally a no-op: there is no reason to reintroduce the malformed data.
    pass
