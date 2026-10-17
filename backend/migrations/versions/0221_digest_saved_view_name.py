"""Snapshot the bound saved view's name on a digest subscription (W09 NTF-04).

``digest_subscriptions.saved_view_id`` is ``ON DELETE SET NULL``: once a view
is deleted the row cannot be told apart from one that never had a view, so a
Daily / Weekly digest would silently widen to the whole project. The new
nullable ``saved_view_name`` is written with every bind
(``digest_scope_service.validate_subscription``); ``saved_view_id IS NULL AND
saved_view_name IS NOT NULL`` = the view was deleted, and the row, the preview
and the delivered digest say so.

The upgrade backfills the name for rows the API already bound to a view that
still exists (the count is logged). No index: nothing filters on the column.

The downgrade drops the column.

Revision ID: 0221
Revises: 0220
"""
import logging

from alembic import op
import sqlalchemy as sa

revision = "0221"
down_revision = "0220"
branch_labels = None
depends_on = None

TABLE = "digest_subscriptions"
COLUMN = "saved_view_name"

BACKFILL_SQL = (
    "UPDATE digest_subscriptions d SET saved_view_name = v.name "
    "FROM saved_views v WHERE v.id = d.saved_view_id AND d.saved_view_name IS NULL"
)

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(255), nullable=True))
    result = op.get_bind().execute(sa.text(BACKFILL_SQL))
    logger.info("0221: saved_view_name backfilled on %s digest subscription(s)", result.rowcount)


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
