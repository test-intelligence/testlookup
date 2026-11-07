"""Suggest-only Jira epics for recurring systemic failure clusters (W36 STRAT-03, owner decision D30).

* ``projects.cluster_epic_suggestions`` -- the per-project opt-in. ``NOT NULL
  DEFAULT false``, so every existing project reads false: the cluster list
  offers no epic action until a QA lead turns it on (the 0222
  ``require_case_review`` pattern). Adding a NOT NULL column with a constant
  default is metadata-only on PostgreSQL 11+.
* ``systemic_cluster_epics`` -- one row per epic a person filed for a cluster.
  Keyed by ``membership_key`` (``systemic_cluster_service.membership_key``: the
  only cluster identity that survives the nightly delete + re-insert, for an
  UNCHANGED member set), so ``uq_sce_project_membership`` (project_id,
  membership_key) is both the one-epic-per-cluster rule and the status lookup.
  A member joining or leaving gives a new key and so no link (no overlap
  matching, OD-4). ``project_id`` is ``ON DELETE CASCADE`` (the link is the
  project's), ``created_by`` ``ON DELETE SET NULL`` like every other user
  reference. ``cluster_label`` / ``member_fingerprints`` are a snapshot of the
  cluster at filing time. A NEW table, so its unique index is built in the
  same transaction, non-concurrently.

No data migration, no backfill, no seed, no flag.

The downgrade drops exactly what the upgrade made, in reverse: the unique
index, the table, the projects column.

Revision ID: 0255
Revises: 0254
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0255"
down_revision = "0254"
branch_labels = None
depends_on = None

TABLE = "systemic_cluster_epics"
UNIQUE_INDEX = "uq_sce_project_membership"
PROJECTS = "projects"
PROJECTS_COLUMN = "cluster_epic_suggestions"


def upgrade() -> None:
    op.add_column(
        PROJECTS,
        sa.Column(PROJECTS_COLUMN, sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE", name="fk_sce_project_id_projects"),
            nullable=False,
        ),
        sa.Column("membership_key", sa.String(32), nullable=False),
        sa.Column("jira_key", sa.String(50), nullable=False),
        sa.Column("jira_url", sa.String(1000), nullable=True),
        sa.Column("cluster_label", sa.String(500), nullable=True),
        sa.Column("member_fingerprints", sa.JSON(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_sce_created_by_users"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(UNIQUE_INDEX, TABLE, ["project_id", "membership_key"], unique=True)


def downgrade() -> None:
    op.drop_index(UNIQUE_INDEX, table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_column(PROJECTS, PROJECTS_COLUMN)
