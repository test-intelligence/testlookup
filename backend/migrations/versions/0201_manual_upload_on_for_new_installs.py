"""Turn the ``manual_upload`` flag ON for a new install, and only for one.

Migration 0092 seeded ``manual_upload`` OFF, so a fresh install had no Upload
button: the first-run path every guide leads with ("upload a report file") was
hidden until an admin found Settings -> Feature flags (OSS-readiness audit,
D3). New installs now start with it ON.

An existing install keeps whatever it has. The flag is flipped only when the
database has never been used — no user and no project exists yet, which is
exactly the state ``alembic upgrade head`` runs in on a new install (the first
admin is created after the migrations) — and only while the row still holds
the value 0092 seeded. Any deployment that already has a user or a project
keeps its stored value, on or off.

Revision ID: 0201
Revises: 0200
"""
from alembic import op
import sqlalchemy as sa

revision = "0201"
down_revision = "0200"
branch_labels = None
depends_on = None

# The fresh-install condition, shared by upgrade and downgrade so the two can
# never disagree about which databases they may touch.
FRESH_INSTALL = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

UPGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
    "WHERE key = 'manual_upload' AND enabled_global = false AND "
    + FRESH_INSTALL
)

DOWNGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
    "WHERE key = 'manual_upload' AND enabled_global = true AND "
    + FRESH_INSTALL
)


def upgrade() -> None:
    op.execute(sa.text(UPGRADE_SQL))


def downgrade() -> None:
    # Only an untouched database goes back to 0092's OFF; a used one keeps the
    # value its admins chose, exactly as upgrade() left it alone.
    op.execute(sa.text(DOWNGRADE_SQL))
