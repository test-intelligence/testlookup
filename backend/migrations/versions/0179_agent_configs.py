"""Per-project agent configuration (architecture E4.1, section 4.2).

One row per ``(project_id, agent_id)``. A missing row means the defaults, as
``agent_policies`` does today.

* ``mode`` and ``enabled`` are columns, not keys inside ``config``: ``mode`` has
  exactly one writable home (section 4.1), and a copy inside the JSON document
  would be a second one.
* ``config`` holds the rest of the validated ``AgentConfigV1`` document.
* ``config_version`` starts at 1 and every PUT bumps it; pipeline runs freeze
  it into ``execution_metadata``.

``agent_configs`` is new, so its constraints and indexes are built in the
ordinary way (the concurrent-build rule covers tables a migration did not
create).

Downgrade drops the table.

Revision ID: 0179
Revises: 0178
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0179"
down_revision = "0178"
branch_labels = None
depends_on = None

TABLE = "agent_configs"

# Frozen copy of app.models.postgres.AGENT_CONFIG_MODES. A migration must not
# import application code; tests/test_agent_configs.py holds the two in step.
MODES = ("shadow", "suggest", "act")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mode", sa.String(10), nullable=False, server_default="shadow"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "agent_id", name="uq_agent_configs_project_agent"),
        sa.CheckConstraint(
            "mode IN (" + ", ".join(f"'{m}'" for m in MODES) + ")",
            name="ck_agent_configs_mode",
        ),
        sa.CheckConstraint("config_version >= 1", name="ck_agent_configs_version_positive"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
