"""Turn the phase-gate enforcement and compliance-pack flags ON for a new install, only for one.

Migration 0158 seeded ``release_phase_gate_enforcement`` OFF and 0066 seeded
``release_compliance_pack`` OFF, so on a new install a phase could be marked
Completed over a failing gate with no reason recorded (REL-10), and the release
page's "Generate pack" answered 503 (REL-04). New installs now start with both
ON: the release page asks for the override / skip reason when the gate says
no, and packs can be generated.

An existing install keeps whatever it has. Each flag is flipped only when the
database has never been used -- no user and no project exists yet, which is
exactly the state ``alembic upgrade head`` runs in on a new install (the first
admin is created after the migrations) -- and only while the row still holds
the value its seed migration wrote (the rule of 0201 / 0207). Any deployment
that already has a user or a project keeps its stored value, on or off; an
operator turns them on there with ``PATCH /api/v1/feature-flags/{key}``.

The downgrade is the mirror: only an untouched database goes back to OFF.

Revision ID: 0212
Revises: 0211
"""
from alembic import op
import sqlalchemy as sa

revision = "0212"
down_revision = "0211"
branch_labels = None
depends_on = None

#: Named ``_FLAGS`` so the feature-flag catalogue guard reads the keys.
_FLAGS = ("release_phase_gate_enforcement", "release_compliance_pack")

# The fresh-install condition, shared by upgrade and downgrade so the two can
# never disagree about which databases they may touch (pattern 0201 / 0207).
FRESH_INSTALL = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

# One statement per key, the key written literally in each so the catalogue
# guard's ``WHERE key = '<key>'`` scan sees it.
UPGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
    "WHERE key = 'release_phase_gate_enforcement' AND enabled_global = false AND "
    + FRESH_INSTALL,
    "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
    "WHERE key = 'release_compliance_pack' AND enabled_global = false AND "
    + FRESH_INSTALL,
)

DOWNGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
    "WHERE key = 'release_phase_gate_enforcement' AND enabled_global = true AND "
    + FRESH_INSTALL,
    "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
    "WHERE key = 'release_compliance_pack' AND enabled_global = true AND "
    + FRESH_INSTALL,
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(sa.text(statement))


def downgrade() -> None:
    # Only an untouched database goes back to the seeded OFF; a used one keeps
    # the value its admins chose, exactly as upgrade() left it alone.
    for statement in DOWNGRADE_SQL:
        op.execute(sa.text(statement))
