"""Seed the rich test-case detail rollout flag.

The endpoint is additive and the frontend falls back to the legacy detail
response when this flag is disabled. It is enabled for existing deployments
after migration so the feature is immediately available while retaining a
project-scoped kill switch for rollout and incident response.
"""
from alembic import op
import sqlalchemy as sa


revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None

_KEY = "test_case_rich_detail"
_DESCRIPTION = (
    "Expose versioned test-case detail metadata, provenance, optional steps, "
    "attachments, links, and authored definition data."
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :key, :description, true, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        ).bindparams(key=_KEY, description=_DESCRIPTION)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=_KEY))
