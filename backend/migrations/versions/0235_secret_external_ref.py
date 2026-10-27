"""External secret references (W20 ADM-10): ``secret_refs.external_ref``.

* ``secret_refs.external_ref`` ``VARCHAR(500) NULL``: for ``provider = 'vault'`` the
  reference ``vault:<path>#<key>`` an administrator entered instead of a value; the
  secret itself stays in the Vault-compatible store (HashiCorp Vault / OpenBao KV v2)
  and ``encrypted_value`` is NULL. Every existing row is ``provider = 'db'`` and keeps
  ``external_ref`` NULL.

Adding a nullable column is metadata-only on PostgreSQL 11+. The downgrade refuses
while any reference row exists (dropping the column would leave rows that resolve to
nothing); re-enter those secrets as values first.

Revision ID: 0235
Revises: 0234
"""
from alembic import op
import sqlalchemy as sa

revision = "0235"
down_revision = "0234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("secret_refs", sa.Column("external_ref", sa.String(500), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    n = bind.execute(sa.text("SELECT count(*) FROM secret_refs WHERE provider = 'vault'")).scalar() or 0
    if n:
        raise RuntimeError(
            f"{n} secret(s) are Vault references; re-enter them as values before downgrading below 0235"
        )
    op.drop_column("secret_refs", "external_ref")
