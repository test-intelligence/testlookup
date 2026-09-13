"""Agent invocations: one call of a single agent through the public API (E1.2).

``POST /api/v1/agents/{agent_id}/invoke`` records one row here. The invocation
does not carry its own status: it runs as an ordinary pipeline run whose frozen
plan selects only the agent and its declared dependencies, and the status,
attempts, retries and review are read from that run. ``pipeline_run_id`` is
minted when the invocation is accepted, before the worker creates the run, so
it is a plain unique UUID rather than a foreign key -- the row it names does
not exist yet at insert time.

``agent_invocations`` is new, so its indexes are built in the ordinary way (the
concurrent-build rule covers tables a migration did not create).

Downgrade drops the table.

Revision ID: 0176
Revises: 0175
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0176"
down_revision = "0175"
branch_labels = None
depends_on = None

TABLE = "agent_invocations"

# Frozen copy of app.models.postgres.INVOCATION_MODES. A migration must not
# import application code; tests/test_agent_invocations.py holds the two in step.
MODES = ("sync", "async")


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
        sa.Column("stage_name", sa.String(60), nullable=False),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_type", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False, server_default="async"),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN (" + ", ".join(f"'{m}'" for m in MODES) + ")",
            name="ck_agent_invocations_mode",
        ),
    )
    op.create_index("ix_agent_invocations_project_created", TABLE, ["project_id", "created_at"])
    op.create_index("ix_agent_invocations_run_agent", TABLE, ["test_run_id", "agent_id", "created_at"])
    op.create_index("ux_agent_invocations_pipeline_run", TABLE, ["pipeline_run_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_agent_invocations_pipeline_run", table_name=TABLE)
    op.drop_index("ix_agent_invocations_run_agent", table_name=TABLE)
    op.drop_index("ix_agent_invocations_project_created", table_name=TABLE)
    op.drop_table(TABLE)
