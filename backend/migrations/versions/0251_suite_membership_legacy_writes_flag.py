"""Seed the suite-membership cutover switch ON (W25d HYG-04 phase 2).

``suite_membership_legacy_writes`` is the ONE switch of the HYG-04 cutover
(``services/suite_membership_cutover.py``): ON, the legacy suite-membership
tables (``suite_memberships`` / ``suite_membership_events``) are still written
by both legacy writers and read by the three Test Management suite routes and
the coverage map's ``needs_review_count``, and the daily parity beat keeps
comparing them with the canonical catalog (the D26 seven-clean-days clock);
OFF, they are neither written nor read and the routes read the catalog.

The row is seeded **ON on every install, new or upgraded** (D-W25d-2): the
0208 pattern minus its fresh-install clause. Deploying this migration changes
no membership behaviour; the cutover is a later, server-guarded admin action
(``POST /api/v1/admin/maintenance/suite-membership-cutover``, refused unless
the parity history says ``cutover_ready``). The flag service answers OFF for a
MISSING key, so the row must exist for legacy writes to continue.

A row that already exists (created by hand) is left untouched (``ON CONFLICT
(key) DO NOTHING``). The downgrade deletes the row only while it still equals
the seed (the 0208 rule): same description, never edited (``created_at =
updated_at``; an admin toggle -- i.e. a cutover -- stamps ``updated_at``), no
project/role lists, rollout 100. A cut-over row therefore survives a
downgrade, and the pre-W25d code never reads it, so legacy writes simply
resume.

No other row is read or written; no table change.

Revision ID: 0251
Revises: 0250
"""
from alembic import op
import sqlalchemy as sa

revision = "0251"
down_revision = "0250"
branch_labels = None
depends_on = None

#: (key, description). Named ``_FLAGS`` so the feature-flag catalogue guard reads the key.
_FLAGS = (
    (
        "suite_membership_legacy_writes",
        "ON: the legacy suite-membership tables are still written and read (before the HYG-04 "
        "cutover). Turn OFF only from Settings > Storage when the cutover card says it is ready.",
    ),
)

INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "SELECT gen_random_uuid(), :key, :description, true, 100, now(), now() "
    "ON CONFLICT (key) DO NOTHING"
)

DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def upgrade() -> None:
    for key, description in _FLAGS:
        op.execute(sa.text(INSERT_SQL).bindparams(key=key, description=description))


def downgrade() -> None:
    for key, description in _FLAGS:
        op.execute(sa.text(DELETE_SQL).bindparams(key=key, description=description))
