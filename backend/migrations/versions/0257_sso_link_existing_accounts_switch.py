"""SSO: the per-configuration switch "link existing accounts by verified e-mail".

* ``sso_configurations.link_existing_accounts_by_email`` -- ``NOT NULL DEFAULT
  false``, so every existing configuration reads false. While it is false, a
  first SSO sign-in whose e-mail belongs to an existing local account is
  refused instead of being linked to that account (the behaviour before this
  migration was to link it). An administrator turns it on per configuration;
  an ADMIN account is never linked, whatever it says. Adding a NOT NULL column
  with a constant default is metadata-only on PostgreSQL 11+.

No data migration, no backfill, no seed, no flag. Existing
``federated_identities`` rows are untouched: a subject that is already linked
keeps signing in.

The downgrade drops the column.

Revision ID: 0257
Revises: 0256
"""
from alembic import op
import sqlalchemy as sa

revision = "0257"
down_revision = "0256"
branch_labels = None
depends_on = None

TABLE = "sso_configurations"
COLUMN = "link_existing_accounts_by_email"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
