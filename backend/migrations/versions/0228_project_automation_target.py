"""A per-project automation target for the library health (W13 TCL-03).

One column and its check on ``projects``:

* ``automation_target_pct`` ``SMALLINT NOT NULL DEFAULT 60``: the share of
  authored test cases a project aims to automate. The library-health answer
  (``GET /api/v1/test-management/health``) measures the "Automated" component
  and the "Automation below target" strategy gap against it; the page's old
  hard-coded "target 60%" becomes this value. A constant default is a
  metadata-only change on PostgreSQL 11 and later (no table rewrite), and every
  existing project reads 60 -- exactly today's target.
* ``ck_projects_automation_target_pct``: ``automation_target_pct BETWEEN 0 AND
  100``. The API refuses an out-of-range value first (``ProjectUpdate``,
  ``ge=0, le=100``); the check keeps any other writer honest.

It is written through the existing ``PUT /api/v1/projects/{project_id}``
(QA_LEAD+), which records ``project.updated`` with the changed field names.
No index: the column is read by primary key.

The downgrade drops the check, then the column.

Revision ID: 0228
Revises: 0227
"""
from alembic import op
import sqlalchemy as sa

revision = "0228"
down_revision = "0227"
branch_labels = None
depends_on = None

TABLE = "projects"
COLUMN = "automation_target_pct"
CHECK_NAME = "ck_projects_automation_target_pct"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.SmallInteger(), nullable=False, server_default=sa.text("60")),
    )
    op.create_check_constraint(CHECK_NAME, TABLE, f"{COLUMN} BETWEEN 0 AND 100")


def downgrade() -> None:
    op.drop_constraint(CHECK_NAME, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
