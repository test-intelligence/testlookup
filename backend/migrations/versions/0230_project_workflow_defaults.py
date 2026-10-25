"""Project default workflows, one per base (W14 AGT-08).

One new table, its primary key and three checks; no index, no flag row:

* ``project_workflow_defaults``: at most one row per ``(project_id, base)``.
  ``base`` is the built-in workflow a trigger that names no workflow starts
  from (``offline`` or ``deep``); ``workflow_id`` is the published FORK
  (``wf.*``) that runs instead, and ``workflow_version`` pins one version of it
  (NULL = the latest PUBLISHED version at each run's start, so publishing v2
  makes v2 the default without a second click). A run that names no workflow
  (the automatic post-ingest job, ``Trigger pipeline``, bulk, the deep page)
  resolves this row at task start; no row means the built-in runs. There is NO
  foreign key to ``workflow_definitions``: a NULL version has no single target
  row, and a default that no longer resolves is recorded on the pipeline as
  ``default_unavailable`` while the built-in runs instead. Deleting the project
  deletes its defaults (``ON DELETE CASCADE``); deleting the person who set one
  keeps it (``updated_by`` ``SET NULL``).
* ``pk_project_workflow_defaults`` (``project_id, base``) is every read (one
  project, one or two bases), so no extra index is created.
* ``ck_pwd_base`` (the two bases), ``ck_pwd_workflow_is_fork`` (only a fork can
  be a default: a built-in is the fallback, not a row) and
  ``ck_pwd_version_positive`` (a pinned version is >= 1).

The downgrade drops the table (and with it the key and the checks).

Revision ID: 0230
Revises: 0229
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0230"
down_revision = "0229"
branch_labels = None
depends_on = None

TABLE = "project_workflow_defaults"
PK_NAME = "pk_project_workflow_defaults"
BASE_CHECK = "ck_pwd_base"
FORK_CHECK = "ck_pwd_workflow_is_fork"
VERSION_CHECK = "ck_pwd_version_positive"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base", sa.String(20), nullable=False),
        sa.Column("workflow_id", sa.String(80), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=True),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("project_id", "base", name=PK_NAME),
        sa.CheckConstraint("base IN ('offline','deep')", name=BASE_CHECK),
        sa.CheckConstraint("workflow_id LIKE 'wf.%'", name=FORK_CHECK),
        sa.CheckConstraint(
            "workflow_version IS NULL OR workflow_version >= 1", name=VERSION_CHECK,
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
