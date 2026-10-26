"""Why a setting changed, and when / by whom a project was archived (W17 ADM-07, ADM-04).

Three nullable columns and one foreign key; no index, no backfill, no flag row:

* ``settings_audit_log.reason`` ``TEXT NULL``: the optional sentence an
  administrator gives with a change (today: a feature-flag create / update /
  delete, up to 500 characters, refused longer by the API). The table stays
  append-only -- the column is written once with the row it belongs to, never
  updated -- so the ``backend.audit-write-discipline`` gate is unaffected. Every
  existing row reads NULL ("no reason given"): nothing was ever asked.
* ``projects.archived_at`` ``TIMESTAMPTZ NULL`` and
  ``projects.archived_by_user_id`` ``UUID NULL`` (``fk_projects_archived_by``
  -> ``users.id`` ``ON DELETE SET NULL``): stamped by ``DELETE
  /api/v1/projects/{id}`` (archive), cleared by the new ``POST
  /api/v1/projects/{id}/restore`` (ADMIN). A project archived before this
  migration reads NULL for both (when and by whom are unknown), and the page
  says "Archived (date not recorded)".

Adding nullable columns without a default is a metadata-only change; the
foreign key validates a column that is all NULL. Both tables are read by
primary key or by ``setting_key`` (already indexed), so no index is created.

The downgrade drops the foreign key, then the three columns.

Revision ID: 0232
Revises: 0231
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0232"
down_revision = "0231"
branch_labels = None
depends_on = None

AUDIT_TABLE = "settings_audit_log"
PROJECTS = "projects"
ARCHIVED_BY_FK = "fk_projects_archived_by"


def upgrade() -> None:
    op.add_column(AUDIT_TABLE, sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(PROJECTS, sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(PROJECTS, sa.Column("archived_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        ARCHIVED_BY_FK, PROJECTS, "users", ["archived_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(ARCHIVED_BY_FK, PROJECTS, type_="foreignkey")
    op.drop_column(PROJECTS, "archived_by_user_id")
    op.drop_column(PROJECTS, "archived_at")
    op.drop_column(AUDIT_TABLE, "reason")
