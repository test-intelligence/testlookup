"""api_keys.minted_by_key_id: which key minted this key (re-audit N35).

Revoking a key did not revoke the keys it had minted. A leaked CI key could
mint itself a replacement (``POST /api/v1/keys`` authenticated by that key),
and revoking the leaked key left the replacement working. The revoke route
now cascades down this column.

* ``NULL`` means a signed-in user minted the key (or it predates this column).
* ``ON DELETE SET NULL``: a hard-deleted parent (a deleted user's keys go by
  ``users`` CASCADE) makes its children roots rather than deleting them or
  failing the delete. Revocation is a soft delete, so the chain survives it.

No backfill is possible: nothing recorded which key minted which before this
revision. Every existing key becomes a root, so revoking a key minted before
this upgrade does NOT cascade to keys it minted before this upgrade. Operators
who revoke a leaked key after upgrading should also review keys created by the
same owner around the leak (upgrade note).

``api_keys`` is small (one row per credential), so the index is built inside
the migration transaction; it does not need CONCURRENTLY.

Revision ID: 0168
Revises: 0167
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0168"
down_revision = "0167"
branch_labels = None
depends_on = None

INDEX = "ix_api_keys_minted_by_key_id"
FK = "fk_api_keys_minted_by_key_id"


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("minted_by_key_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        FK, "api_keys", "api_keys", ["minted_by_key_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index(INDEX, "api_keys", ["minted_by_key_id"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name="api_keys")
    op.drop_constraint(FK, "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "minted_by_key_id")
