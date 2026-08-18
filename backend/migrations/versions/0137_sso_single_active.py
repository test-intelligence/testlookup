"""Enforce one active system-level SSO configuration.

Revision ID: 0137
Revises: 0136
"""
from alembic import op
import sqlalchemy as sa


revision = "0137"
down_revision = "0136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the most recently changed active policy and deactivate any
    # legacy duplicates before adding the invariant. UUID is the stable tie-break.
    op.execute(
        """
        WITH ranked_active AS (
            SELECT id,
                   row_number() OVER (
                       ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                   ) AS position
            FROM sso_configurations
            WHERE is_active IS TRUE
        )
        UPDATE sso_configurations
        SET is_active = FALSE
        WHERE id IN (
            SELECT id FROM ranked_active WHERE position > 1
        )
        """
    )
    op.create_index(
        "uq_sso_config_single_active",
        "sso_configurations",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_sso_config_single_active", table_name="sso_configurations")
