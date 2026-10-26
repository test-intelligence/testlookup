"""GitHub App authentication beside PATs (W19 INT-06), and the git-host flags ON for a new install (INT-06, INT-05).

* ``github_integrations.auth_kind`` ``VARCHAR(10) NOT NULL DEFAULT 'pat'``: how the
  project authenticates to GitHub — ``pat`` (a personal access token in
  ``secret_service``, as before; every existing row stays ``pat``) or ``app``
  (an installation token minted from the deployment's GitHub App).
* ``github_integrations.installation_id`` ``BIGINT NULL``: the App installation
  that covers the project's repository, recorded by the install callback.
* The ``github_checks`` and ``gitlab`` flags (seeded OFF by earlier migrations)
  are turned ON for a NEW install only — no user and no project yet, and the row
  still holds its seeded OFF — the 0201 pattern. An existing install keeps its
  stored values; its admins flip them in Settings › Feature flags.

Adding a nullable column and a column with a constant default is a metadata-only
change on PostgreSQL 11+; no index is built (rows are read by project id, which
is already indexed). The downgrade drops both columns and puts an untouched new
install's flags back to OFF.

Revision ID: 0234
Revises: 0233
"""
from alembic import op
import sqlalchemy as sa

revision = "0234"
down_revision = "0233"
branch_labels = None
depends_on = None

TABLE = "github_integrations"
FLAGS = ("github_checks", "gitlab")
FRESH_INSTALL = "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"


def _flag_sql(value: bool) -> str:
    keys = ", ".join(f"'{k}'" for k in FLAGS)
    return (
        f"UPDATE feature_flags SET enabled_global = {'true' if value else 'false'}, updated_at = now() "
        f"WHERE key IN ({keys}) AND enabled_global = {'false' if value else 'true'} AND " + FRESH_INSTALL
    )


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("auth_kind", sa.String(10), nullable=False, server_default="pat"))
    op.add_column(TABLE, sa.Column("installation_id", sa.BigInteger(), nullable=True))
    op.execute(sa.text(_flag_sql(True)))


def downgrade() -> None:
    op.execute(sa.text(_flag_sql(False)))
    op.drop_column(TABLE, "installation_id")
    op.drop_column(TABLE, "auth_kind")
