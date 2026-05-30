"""compliance_packs + release_compliance_pack feature flag

Revision ID: 0066
Revises: 0065
Create Date: 2026-04-14

Tier 1 item 4 — Release compliance export pack. Adds a single table that
tracks every generated ZIP so enterprise customers have an authoritative
index of all audit packs produced for a given release. Pack contents live
in MinIO; this table holds the manifest SHA-256 and metadata snapshot.

Also seeds the ``release_compliance_pack`` feature flag (disabled by
default) so the new endpoint and UI are observable-zero on existing
deployments until an admin enables it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compliance_packs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("minio_key", sa.String(length=500), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "retention_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "generated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "metadata_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_compliance_packs_release", "compliance_packs", ["release_id"],
    )
    op.create_index(
        "ix_compliance_packs_project",
        "compliance_packs",
        ["project_id", "generated_at"],
    )

    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'release_compliance_pack', "
            "'Generate signed ZIP compliance packs for release decisions. "
            "Tier 1 item 4.', false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM feature_flags WHERE key = 'release_compliance_pack'")
    )
    op.drop_index("ix_compliance_packs_project", table_name="compliance_packs")
    op.drop_index("ix_compliance_packs_release", table_name="compliance_packs")
    op.drop_table("compliance_packs")
