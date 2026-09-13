"""Idempotency-Key for agent invocations (architecture E1.3).

* ``agent_invocations.idempotency_key``: the client's ``Idempotency-Key`` header,
  when one was sent.
* ``agent_invocations.request_sha256``: the fingerprint of the request that key
  created. A replay with a different request is refused with 422.
* ``ux_agent_invocations_user_idempotency_key``: a key belongs to one user. The
  index is partial (``idempotency_key IS NOT NULL``), so invocations without a
  key are unconstrained.

``agent_invocations`` already exists (0176), so the index is built CONCURRENTLY
inside an autocommit block, as the migration-concurrency rule requires for
existing tables. The two columns are nullable with no default: a metadata-only
change.

Downgrade drops the index, then both columns.

Revision ID: 0178
Revises: 0177
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0178"
down_revision = "0177"
branch_labels = None
depends_on = None

TABLE = "agent_invocations"
INDEX = "ux_agent_invocations_user_idempotency_key"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column(TABLE, sa.Column("request_sha256", sa.String(64), nullable=True))
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX,
            TABLE,
            ["requested_by", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX, table_name=TABLE, postgresql_concurrently=True, if_exists=True)
    op.drop_column(TABLE, "request_sha256")
    op.drop_column(TABLE, "idempotency_key")
