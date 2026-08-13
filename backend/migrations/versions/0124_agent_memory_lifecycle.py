"""Add provenance and lifecycle authority to agent memory entries.

Revision ID: 0124
Revises: 0123
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_memory_entries",
        sa.Column("source_type", sa.String(length=40), nullable=False, server_default="pipeline_agent"),
    )
    op.add_column(
        "agent_memory_entries",
        sa.Column("trust_level", sa.String(length=30), nullable=False, server_default="derived"),
    )
    op.add_column(
        "agent_memory_entries",
        sa.Column("lifecycle_status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.add_column("agent_memory_entries", sa.Column("source_snapshot_id", sa.String(length=128), nullable=True))
    op.add_column("agent_memory_entries", sa.Column("source_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "agent_memory_entries",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_memory_entries",
        sa.Column(
            "superseded_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_memory_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_memory_entries",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_ame_lifecycle_status",
        "agent_memory_entries",
        "lifecycle_status IN ('active', 'superseded', 'expired', 'revoked')",
    )
    op.create_check_constraint(
        "ck_ame_trust_level",
        "agent_memory_entries",
        "trust_level IN ('authoritative', 'derived', 'human_verified', 'unverified')",
    )
    op.create_index(
        "ix_ame_project_lifecycle",
        "agent_memory_entries",
        ["project_id", "lifecycle_status", "expires_at"],
    )
    op.create_index(
        "ix_ame_source_snapshot",
        "agent_memory_entries",
        ["source_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ame_source_snapshot", table_name="agent_memory_entries")
    op.drop_index("ix_ame_project_lifecycle", table_name="agent_memory_entries")
    op.drop_constraint("ck_ame_trust_level", "agent_memory_entries", type_="check")
    op.drop_constraint("ck_ame_lifecycle_status", "agent_memory_entries", type_="check")
    op.drop_column("agent_memory_entries", "superseded_at")
    op.drop_column("agent_memory_entries", "superseded_by_id")
    op.drop_column("agent_memory_entries", "expires_at")
    op.drop_column("agent_memory_entries", "source_hash")
    op.drop_column("agent_memory_entries", "source_snapshot_id")
    op.drop_column("agent_memory_entries", "lifecycle_status")
    op.drop_column("agent_memory_entries", "trust_level")
    op.drop_column("agent_memory_entries", "source_type")
