"""Scope invocation idempotency by user, project and agent route.

Revision ID: 0190
Revises: 0189
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0190"
down_revision = "0189"
branch_labels = None
depends_on = None

OLD_INDEX = "ux_agent_invocations_user_idempotency_key"
NEW_INDEX = "ux_agent_invocations_scoped_idempotency_key"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A failed concurrent build leaves an invalid same-name index.  Drop
        # any residue before IF NOT EXISTS so a retry cannot skip the rebuild
        # and then remove the still-valid legacy authority.
        op.drop_index(
            NEW_INDEX,
            table_name="agent_invocations",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            NEW_INDEX,
            "agent_invocations",
            ["requested_by", "project_id", "agent_id", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            OLD_INDEX,
            table_name="agent_invocations",
            postgresql_concurrently=True,
            if_exists=True,
        )


def downgrade() -> None:
    # The wider scope intentionally permits the same user/key pair on several
    # projects or agent routes.  The legacy index cannot represent that.  Keep
    # every invocation row and deterministically retain the key on the oldest
    # row; older code treats the NULL keys as non-idempotent history.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY requested_by, idempotency_key
                           ORDER BY created_at, id
                       ) AS collision_rank
                FROM agent_invocations
                WHERE requested_by IS NOT NULL AND idempotency_key IS NOT NULL
            )
            UPDATE agent_invocations AS invocation
            SET idempotency_key = NULL
            FROM ranked
            WHERE invocation.id = ranked.id AND ranked.collision_rank > 1
            """
        )
    )
    with op.get_context().autocommit_block():
        # A failed concurrent build can leave an invalid index with this name.
        # Remove it before IF NOT EXISTS so a retry cannot silently keep it.
        op.drop_index(
            OLD_INDEX,
            table_name="agent_invocations",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            OLD_INDEX,
            "agent_invocations",
            ["requested_by", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            NEW_INDEX,
            table_name="agent_invocations",
            postgresql_concurrently=True,
            if_exists=True,
        )
