"""Add SSO/SAML/SCIM tables for enterprise identity management (ENT-01).

Tables: sso_configurations, federated_identities, scim_tokens, identity_events.

Revision ID: 0031
Revises: 0030
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── SSO Configuration ────────────────────────────────────────
    op.create_table(
        "sso_configurations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("provider_type", sa.String(20), nullable=False, server_default="SAML"),
        sa.Column("idp_entity_id", sa.String(1000), nullable=False),
        sa.Column("idp_sso_url", sa.String(2000), nullable=False),
        sa.Column("idp_slo_url", sa.String(2000), nullable=True),
        sa.Column("idp_certificate", sa.Text(), nullable=False),
        sa.Column("sp_entity_id", sa.String(1000), nullable=False),
        sa.Column("sp_acs_url", sa.String(2000), nullable=False),
        sa.Column("audience", sa.String(1000), nullable=True),
        sa.Column("role_mapping", sa.JSON(), nullable=True),
        sa.Column("default_role", sa.String(20), nullable=False, server_default="VIEWER"),
        sa.Column("group_attribute", sa.String(255), nullable=True),
        sa.Column("enforcement_mode", sa.String(20), nullable=False, server_default="OPTIONAL"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_success", sa.Boolean(), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sso_config_active", "sso_configurations", ["is_active"])

    # ── Federated Identities ─────────────────────────────────────
    op.create_table(
        "federated_identities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sso_config_id", UUID(as_uuid=True), sa.ForeignKey("sso_configurations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(1000), nullable=False),
        sa.Column("external_email", sa.String(255), nullable=True),
        sa.Column("external_display_name", sa.String(500), nullable=True),
        sa.Column("external_groups", sa.JSON(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_federated_identity", "federated_identities", ["sso_config_id", "external_id"])
    op.create_index("ix_federated_user", "federated_identities", ["user_id"])

    # ── SCIM Tokens ──────────────────────────────────────────────
    op.create_table(
        "scim_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_hint", sa.String(12), nullable=False),
        sa.Column("sso_config_id", UUID(as_uuid=True), sa.ForeignKey("sso_configurations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scim_token_hash", "scim_tokens", ["token_hash"], unique=True)

    # ── Identity Events ──────────────────────────────────────────
    op.create_table(
        "identity_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sso_config_id", UUID(as_uuid=True), sa.ForeignKey("sso_configurations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_name", sa.String(200), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_identity_event_type", "identity_events", ["event_type"])
    op.create_index("ix_identity_event_user", "identity_events", ["user_id"])
    op.create_index("ix_identity_event_created", "identity_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("identity_events")
    op.drop_table("scim_tokens")
    op.drop_table("federated_identities")
    op.drop_table("sso_configurations")
