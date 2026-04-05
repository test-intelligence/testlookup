"""Add secret_refs table, alter app_settings, add settings_audit_log.

Revision ID: 0023
Revises: 0022

Phase 1 of secret storage migration:
  - Create secret_refs for sensitive values (API keys, tokens, passwords)
  - Add secret_ref_id / is_secret_backed to app_settings for future dual-read
  - Add settings_audit_log for change tracking (QAI-104)
  - Add updated_by to app_settings for attribution
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Secret References ────────────────────────────────────────────────────
    op.create_table(
        "secret_refs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(100), nullable=False),             # e.g. "smtp", "ai_config", "integrations"
        sa.Column("provider", sa.String(50), nullable=False, server_default="db"),  # "db" | "vault" | "aws_sm"
        sa.Column("key_name", sa.String(255), nullable=False),          # e.g. "jira_api_token", "openai_api_key"
        sa.Column("encrypted_value", sa.Text(), nullable=True),         # encrypted at rest; NULL if external provider
        sa.Column("masked_value", sa.String(50), nullable=True),        # e.g. "sk-...abc1" for display
        sa.Column("rotation_status", sa.String(30), server_default="active"),  # active | rotating | expired
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_secret_refs_scope_key", "secret_refs", ["scope", "key_name"], unique=True)

    # ── App Settings — add secret backing columns ────────────────────────────
    op.add_column("app_settings", sa.Column("secret_ref_id", UUID(as_uuid=True), sa.ForeignKey("secret_refs.id", ondelete="SET NULL"), nullable=True))
    op.add_column("app_settings", sa.Column("is_secret_backed", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("app_settings", sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))

    # ── Settings Audit Log ───────────────────────────────────────────────────
    op.create_table(
        "settings_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("setting_key", sa.String(100), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),             # "created" | "updated" | "secret_rotated"
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_name", sa.String(200), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=True),          # list of field names changed (no values for secrets)
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_settings_audit_key", "settings_audit_log", ["setting_key"])
    op.create_index("ix_settings_audit_actor", "settings_audit_log", ["actor_id"])


def downgrade() -> None:
    op.drop_table("settings_audit_log")
    op.drop_column("app_settings", "updated_by")
    op.drop_column("app_settings", "is_secret_backed")
    op.drop_column("app_settings", "secret_ref_id")
    op.drop_table("secret_refs")
