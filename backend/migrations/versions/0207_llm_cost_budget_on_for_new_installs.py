"""Turn the ``llm_cost_budget`` flag ON for a new install, and only for one.

Migration 0064 seeded ``llm_cost_budget`` OFF, so on a new install the Billing
page's "Included per month" / "Overage rate" fields (AIC-06) recorded nothing:
with the flag off ``record_usage`` and ``check_and_apply_cap`` are no-ops and
the usage meter stays at $0. New installs now start with it ON, so a quota an
admin saves is metered and its overage computed.

An existing install keeps whatever it has. The flag is flipped only when the
database has never been used — no user and no project exists yet, which is
exactly the state ``alembic upgrade head`` runs in on a new install (the first
admin is created after the migrations) — and only while the row still holds
the value 0064 seeded (the rule of 0201 / 0204). Any deployment that already
has a user or a project keeps its stored value, on or off.

Self-hosted and unpriced models cost $0 in the price table, so with the flag
on they never reach the quota read (``llm_cost_reservation.reserve`` returns
before it for a zero estimate).

Revision ID: 0207
Revises: 0206
"""
from alembic import op
import sqlalchemy as sa

revision = "0207"
down_revision = "0206"
branch_labels = None
depends_on = None

# The fresh-install condition, shared by upgrade and downgrade so the two can
# never disagree about which databases they may touch (pattern 0201).
FRESH_INSTALL = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

UPGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
    "WHERE key = 'llm_cost_budget' AND enabled_global = false AND "
    + FRESH_INSTALL
)

DOWNGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
    "WHERE key = 'llm_cost_budget' AND enabled_global = true AND "
    + FRESH_INSTALL
)


def upgrade() -> None:
    op.execute(sa.text(UPGRADE_SQL))


def downgrade() -> None:
    # Only an untouched database goes back to 0064's OFF; a used one keeps the
    # value its admins chose, exactly as upgrade() left it alone.
    op.execute(sa.text(DOWNGRADE_SQL))
