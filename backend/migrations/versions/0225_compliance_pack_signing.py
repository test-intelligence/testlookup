"""Compliance packs are signed with the deployment's Ed25519 key (W11 REL-11, D14).

One new table and two nullable columns, no backfill:

* ``compliance_signing_keys``: the deployment's signing keys. ``key_id`` is
  ``"k-"`` plus the first 16 hex of SHA-256 over the raw 32-byte public key;
  ``public_key_pem`` (SubjectPublicKeyInfo PEM) stays here for ever, so a pack
  signed before a rotation keeps verifying. The PRIVATE key is never in this
  table: it lives in ``secret_refs`` (scope ``compliance_signing``, key_name =
  the key id), Fernet-encrypted by ``secret_service``, and its ciphertext is
  cleared when the key is retired.
* ``uq_compliance_signing_keys_active``: a partial unique index on
  ``(algorithm) WHERE retired_at IS NULL`` -- at most one active key per
  algorithm. It is built on the table this migration creates (empty), so a
  plain build: the concurrent-index rule is for existing tables.
* ``compliance_packs.signature`` (base64 Ed25519 signature over the signed
  statement) and ``compliance_packs.signing_key_id`` -- both NULL for every
  existing pack, which therefore verifies as ``unsigned``. No index and no
  foreign key: an id the table does not know verifies as ``unknown_key``.

No key is generated here. A migration imports no application code and cannot
reach the running app's ``APP_SECRET_KEY`` (the Fernet key), so the first
signed pack creates the key (``services/compliance_signing.py``).

The downgrade drops the two columns, the index and the table, in reverse
order. The ``secret_refs`` rows of scope ``compliance_signing`` are LEFT in
place: a downgrade must not destroy key material an operator may still want,
and ``secret_refs`` was not created here.

Revision ID: 0225
Revises: 0224
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0225"
down_revision = "0224"
branch_labels = None
depends_on = None

KEYS_TABLE = "compliance_signing_keys"
PACKS_TABLE = "compliance_packs"
ACTIVE_INDEX = "uq_compliance_signing_keys_active"


def upgrade() -> None:
    op.create_table(
        KEYS_TABLE,
        sa.Column("key_id", sa.String(64), primary_key=True),
        sa.Column("algorithm", sa.String(20), nullable=False, server_default="ed25519"),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retired_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("retired_reason", sa.String(500), nullable=True),
    )
    # On the table created just above (empty): a plain build is instant.
    op.create_index(
        ACTIVE_INDEX,
        KEYS_TABLE,
        ["algorithm"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.add_column(PACKS_TABLE, sa.Column("signature", sa.Text(), nullable=True))
    op.add_column(PACKS_TABLE, sa.Column("signing_key_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column(PACKS_TABLE, "signing_key_id")
    op.drop_column(PACKS_TABLE, "signature")
    op.drop_index(ACTIVE_INDEX, table_name=KEYS_TABLE)
    op.drop_table(KEYS_TABLE)
