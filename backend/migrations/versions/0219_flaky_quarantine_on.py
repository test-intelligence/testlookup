"""Turn the quarantine workflow ON where nobody ever set its flag (W09 FLK-01, owner decision D11).

Migration 0065 seeded ``flaky_auto_quarantine`` OFF ("observable-zero until an
admin turns it on"). The workflow has since graduated: nothing is quarantined
until a QA lead approves a proposal, so it now ships ON.

The flip touches ONE row, and only while it is still 0065's untouched seed:
``enabled_global = false``, no actor (``updated_by_user_id IS NULL``) and
``updated_at = created_at`` -- 0065 wrote ``created_at = updated_at = now()``
with no actor, and every admin PATCH through ``PATCH /api/v1/feature-flags``
stamps the actor and bumps ``updated_at``. That covers a fresh install (its
row is the untouched seed by construction) and an existing install that never
touched the flag. A row an admin changed -- on or off, a project allow-list,
a later toggle -- keeps its value.

The flipped row's description gains ``MARKER`` so the downgrade can find
exactly that row again.

The downgrade reverts only the row this upgrade flipped: still ON, still no
actor, still carrying the marker. It goes back to the untouched seed exactly
(``enabled_global = false``, ``updated_at = created_at``, the marker removed),
so a re-upgrade flips it again. A row an admin touched since (actor stamped)
stays as the admin left it.

Revision ID: 0219
Revises: 0218
"""
from alembic import op
import sqlalchemy as sa

revision = "0219"
down_revision = "0218"
branch_labels = None
depends_on = None

#: Named ``_FLAGS`` so the feature-flag catalogue guard reads the key.
_FLAGS = ("flaky_auto_quarantine",)

#: Appended to the flipped row's description. No ``'``, ``%`` or ``_`` in it:
#: it is written literally inside the SQL text and matched by ``strpos``.
MARKER = " Default ON since migration 0219: a test is quarantined only after a QA lead approves it."

#: 0065's seed as nobody has touched it (shared by upgrade and downgrade).
UNTOUCHED_SEED = "enabled_global = false AND updated_by_user_id IS NULL AND updated_at = created_at"

UPGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = true, updated_at = now(), "
    f"description = COALESCE(description, '') || '{MARKER}' "
    "WHERE key = 'flaky_auto_quarantine' AND " + UNTOUCHED_SEED
)

DOWNGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = false, updated_at = created_at, "
    f"description = REPLACE(description, '{MARKER}', '') "
    "WHERE key = 'flaky_auto_quarantine' AND enabled_global = true AND updated_by_user_id IS NULL "
    f"AND strpos(description, '{MARKER}') > 0"
)


def upgrade() -> None:
    op.execute(sa.text(UPGRADE_SQL))


def downgrade() -> None:
    # Only the row upgrade() flipped (still ON, no actor, the marker) goes back
    # to the untouched seed; a row an admin touched since keeps its value.
    op.execute(sa.text(DOWNGRADE_SQL))
