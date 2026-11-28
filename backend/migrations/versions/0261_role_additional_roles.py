"""Extra job roles on users and project memberships (enterprise roles, epic E00).

* ``users.additional_roles`` -- nullable JSONB list of role names.
* ``project_members.additional_roles`` -- nullable JSONB list of role names.
* ``user_invitations.additional_roles`` -- nullable JSONB list: the extra roles
  the invitee gets on accept (their per-project extra roles ride in the
  existing ``project_memberships`` JSON, one ``additional_roles`` key per entry).

A person's primary ``role`` stays the only source of their rank; these lists add
the permissions of job roles they also hold (RELEASE_MANAGER, PROJECT_MANAGER,
PRODUCT_MANAGER, DEVELOPER), so a tech lead can be QA_ENGINEER with
RELEASE_MANAGER on top. On a write into a project the instance roles and the
project roles intersect (``app/core/permissions.py``). NULL means none. Adding a
nullable column with no default is metadata-only on PostgreSQL; no index (the
lists are read with the row they belong to).

The five new ``UserRole`` values (RELEASE_MANAGER, PROJECT_MANAGER,
PRODUCT_MANAGER, DEVELOPER, AGENT) need no schema change: the role columns are
``String(20)`` with no CHECK constraint (migration 0079 says why).

The downgrade drops the three columns and first maps every stored new role value
back to VIEWER, in every role column, because code before this revision
serialises roles through the strict five-value enum and would answer 500 on any
other value. Role columns: ``users.role``, ``project_members.role``,
``user_invitations.role``, ``sso_configurations.default_role``,
``scim_group_mappings.project_role``; and the ``role`` of each entry of
``user_invitations.project_memberships`` (accepting an invitation writes it to
``project_members.role``), whose ``additional_roles`` keys are dropped. Values
in JSON settings (an SSO ``role_mapping``, the MFA policy's ``required_roles``,
a feature flag's ``enabled_roles``) need nothing: older code skips a role it
does not know.

Revision ID: 0261
Revises: 0260
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0261"
down_revision = "0260"
branch_labels = None
depends_on = None

COLUMN = "additional_roles"
TABLES = ("users", "project_members", "user_invitations")

NEW_ROLES = ("RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER", "DEVELOPER", "AGENT")
ROLE_COLUMNS = (
    ("users", "role"),
    ("project_members", "role"),
    ("user_invitations", "role"),
    ("sso_configurations", "default_role"),
    ("scim_group_mappings", "project_role"),
)


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column(COLUMN, JSONB(), nullable=True))


def downgrade() -> None:
    new_roles = sa.bindparam("new_roles", value=list(NEW_ROLES), expanding=True)
    for table, column in ROLE_COLUMNS:
        op.execute(
            sa.text(f"UPDATE {table} SET {column} = 'VIEWER' WHERE {column} IN :new_roles").bindparams(new_roles)
        )
    op.execute(
        sa.text(
            "UPDATE user_invitations SET project_memberships = ("
            " SELECT jsonb_agg(CASE WHEN elem->>'role' IN :new_roles"
            " THEN jsonb_set(elem - 'additional_roles', '{role}', '\"VIEWER\"')"
            " ELSE elem - 'additional_roles' END)"
            " FROM jsonb_array_elements(project_memberships) AS elem)"
            " WHERE project_memberships IS NOT NULL AND jsonb_typeof(project_memberships) = 'array'"
            " AND jsonb_array_length(project_memberships) > 0"
        ).bindparams(new_roles)
    )
    for table in TABLES:
        op.drop_column(table, COLUMN)
