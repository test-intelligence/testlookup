"""seed the release_phase_gate_enforcement feature flag

The epic specified S0, S3a, S6b and S8 as flag-gated and shipped all four
ungated — no release feature flag existed at all, which is why S6b's gate could
be made *answerable* but not *enforcing*: turning `update_phase` into a refusal
is a breaking change to a live endpoint, and there was nothing to put it behind.

Seeded DISABLED, like `release_compliance_pack` (migration 0066). A deployment
that does nothing sees zero behaviour change; enabling it is a deliberate act by
someone who has decided their team is ready to have phase completion refused.

Revision ID: 0158
Revises: 0157
"""
import sqlalchemy as sa
from alembic import op

revision = "0158"
down_revision = "0157"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'release_phase_gate_enforcement', "
            "'Refuse to complete a release phase whose gate does not say GO, and "
            "require a recorded reason to override or to skip a phase. Off means "
            "the gate still ANSWERS at /releases/{id}/phases/gate but obliges "
            "nobody. S6b.', false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM feature_flags WHERE key = 'release_phase_gate_enforcement'"
        )
    )
