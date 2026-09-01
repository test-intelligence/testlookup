"""Add per-user UI dismissals + seed the retention-activation nudge flag.

Retention has shipped for some time but ``project_retention_policies.enabled``
defaults to ``false`` and nothing in the product ever prompted an operator to
turn it on, so the feature is present, discoverable, and inert. S1 adds a
first-run nudge; this revision adds the store that remembers dismissing it.

**This migration deliberately does NOT touch ``project_retention_policies``.**
Enabling retention on upgrade would start deleting data on deployments that
never opted in. The nudge asks; the migration does not decide.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0145"
down_revision = "0144"
branch_labels = None
depends_on = None


_FLAG_KEY = "retention_activation_nudge"
# A stable id lets downgrade distinguish the row seeded here from an
# operator-owned row that happens to use the same key (the 0144 convention).
_FLAG_ID = "b5c1f0a8-7d64-4e2b-9f3a-6c8e1d4a90b7"
_FLAG_DESCRIPTION = (
    "Show the first-run prompt inviting an operator to enable data retention. "
    "Kill switch for the prompt only — it never enables or disables retention "
    "itself."
)


def upgrade() -> None:
    op.create_table(
        "user_ui_dismissals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dismissal_key", sa.String(length=100), nullable=False),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "dismissal_key", name="uq_user_ui_dismissal"),
    )
    op.create_index("ix_uuid_user", "user_ui_dismissals", ["user_id"])

    # Enabled by default: the prompt is the whole point of S1, and it is
    # advisory — it asks, it never acts. ON CONFLICT keeps upgrade
    # non-destructive if an operator already created this key by hand.
    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (:id, :key, :description, true, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        ).bindparams(id=_FLAG_ID, key=_FLAG_KEY, description=_FLAG_DESCRIPTION)
    )


def downgrade() -> None:
    # Only remove the flag row this migration owns — an operator-created row
    # with the same key keeps its own id and survives.
    op.execute(
        sa.text("DELETE FROM feature_flags WHERE key = :key AND id = :id").bindparams(
            key=_FLAG_KEY, id=_FLAG_ID
        )
    )
    op.drop_index("ix_uuid_user", table_name="user_ui_dismissals")
    op.drop_table("user_ui_dismissals")
