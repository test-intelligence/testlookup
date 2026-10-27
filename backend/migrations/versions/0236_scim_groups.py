"""SCIM groups and group → project-role mappings (W20 ADM-09).

* ``scim_groups``: groups an IdP pushes over SCIM ``/Groups`` (``display_name`` unique,
  ``external_id``, ``version`` for the resource's ETag-style meta.version).
* ``scim_group_members``: (group, user) pairs; both cascade.
* ``scim_group_mappings``: an administrator's group → (project, project role) grants;
  one row per (group, project).
* ``project_members.source`` ``VARCHAR(80) NULL``: NULL for a membership granted by a
  person (every existing row), ``scim:<group>`` for one SCIM reconciliation created —
  reconciliation only ever changes or removes its own rows.

New tables and a nullable column; no backfill. The downgrade drops them.

Revision ID: 0236
Revises: 0235
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0236"
down_revision = "0235"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scim_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False, unique=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "scim_group_members",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scim_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_scim_group_members_user", "scim_group_members", ["user_id"])
    op.create_table(
        "scim_group_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scim_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_role", sa.String(20), nullable=False),
        sa.UniqueConstraint("group_id", "project_id", name="uq_scim_group_mapping"),
    )
    op.add_column("project_members", sa.Column("source", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("project_members", "source")
    op.drop_table("scim_group_mappings")
    op.drop_index("ix_scim_group_members_user", table_name="scim_group_members")
    op.drop_table("scim_group_members")
    op.drop_table("scim_groups")
