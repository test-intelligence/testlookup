"""Record the user who triggered an Investigator run.

Revision ID: 0189
Revises: 0188
Create Date: 2026-09-16

Automatic and historical investigations keep NULL. The foreign key uses SET
NULL so deleting a user preserves the investigation and its review history.
No index is added: the value is copied while the investigation is already
loaded by primary key and is never used as a filter.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0189"
down_revision = "0188"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_investigations",
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_investigations_requested_by_users",
        "agent_investigations",
        "users",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_investigations_requested_by_users",
        "agent_investigations",
        type_="foreignkey",
    )
    op.drop_column("agent_investigations", "requested_by")
