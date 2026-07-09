"""enable cypress_ingest and playwright_ingest by default

Revision ID: 0099
Revises: 0098
Create Date: 2026-07-09

PMF backlog US-1.5: Cypress and Playwright ingestion are advertised,
first-class formats, but migration 0063 seeded their feature flags
``enabled_global=false`` — so a fresh install 503s on two documented
formats until an admin discovers the flag. That is a bad first-run
experience for a local-first product.

Flips both flags to ``enabled_global=true``. The flag machinery is kept
(an admin can still disable either format per project / globally from
Settings > Feature Flags); only the DEFAULT changes. Deployments where an
admin deliberately disabled the formats will need to re-disable once —
called out in the CHANGELOG.
"""
from alembic import op
import sqlalchemy as sa

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None

_FLAG_KEYS = ("cypress_ingest", "playwright_ingest")


def upgrade() -> None:
    for key in _FLAG_KEYS:
        op.execute(
            sa.text(
                "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
                "WHERE key = :key"
            ).bindparams(key=key)
        )


def downgrade() -> None:
    for key in _FLAG_KEYS:
        op.execute(
            sa.text(
                "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
                "WHERE key = :key"
            ).bindparams(key=key)
        )
