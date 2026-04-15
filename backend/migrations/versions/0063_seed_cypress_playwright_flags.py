"""seed cypress_ingest and playwright_ingest feature flags

Revision ID: 0063
Revises: 0062
Create Date: 2026-04-14

Seeds two feature-flag rows (disabled by default) so the new Cypress and
Playwright parsers introduced in Tier 1 item 1 can be switched on per
project / role / rollout without a code deploy. ADMIN toggles them from
Settings > Feature Flags.

Both flags ship ``enabled_global=false`` — the ingest router refuses the
matching ``format=cypress|playwright`` path with a 503 until an admin
flips the switch. Existing deployments therefore see no behaviour change.
"""
from alembic import op
import sqlalchemy as sa

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


_FLAGS = [
    (
        "cypress_ingest",
        "Accept and parse Cypress Mochawesome JSON test reports. Tier 1 item 1.",
    ),
    (
        "playwright_ingest",
        "Accept and parse Playwright --reporter=json test reports. Tier 1 item 1.",
    ),
]


def upgrade() -> None:
    for key, description in _FLAGS:
        op.execute(
            sa.text(
                "INSERT INTO feature_flags "
                "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :key, :desc, false, 100, now(), now()) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(key=key, desc=description)
        )


def downgrade() -> None:
    for key, _ in _FLAGS:
        op.execute(
            sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=key)
        )
