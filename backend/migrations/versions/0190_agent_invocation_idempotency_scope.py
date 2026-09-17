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
    with op.get_context().autocommit_block():
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
