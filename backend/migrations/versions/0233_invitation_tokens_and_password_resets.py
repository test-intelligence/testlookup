"""Hashed invitation tokens and password-reset tokens (W17b ADM-01, ADM-02).

``user_invitations`` (ADM-01: the invite link becomes redeemable):

* ``token_hash`` ``VARCHAR(64) NULL`` with the unique index
  ``uq_user_invitations_token_hash`` (built CONCURRENTLY): ``sha256(token)`` hex. The raw
  token now lives only in the link handed to the invitee (a URL fragment, never
  sent to the server); the database cannot leak a usable invite.
* ``token`` becomes NULLABLE and every existing value is set to NULL. Nothing
  has ever read the column (no accept route existed), so no link that works
  today stops working -- none ever worked.
* ``used_at``, ``revoked_at`` ``TIMESTAMPTZ NULL``; ``accepted_user_id`` ``UUID
  NULL`` -> ``users.id`` ``ON DELETE SET NULL``; ``project_memberships``
  ``JSONB NULL`` (a list of ``{project_id, role}`` granted on accept).

``password_reset_tokens`` (ADM-02): one row per issued link, hash only, single
use, short expiry; ``issued_by_user_id`` is NULL for a self-service request and
the administrator for an admin-issued link. ``request_ip_hash`` is a sha256 of
the requesting address, never the address itself.

Downgrade: drops ``password_reset_tokens`` and the new invitation columns, and
restores ``token NOT NULL`` by copying ``token_hash`` into it for rows whose raw
token was cleared. Those rows can no longer be redeemed by anyone (the raw
tokens are not recoverable) -- which is what the pre-0233 schema did anyway.

Revision ID: 0233
Revises: 0232
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0233"
down_revision = "0232"
branch_labels = None
depends_on = None

INVITES = "user_invitations"
RESETS = "password_reset_tokens"
ACCEPTED_FK = "fk_user_invitations_accepted_user"
TOKEN_HASH_INDEX = "uq_user_invitations_token_hash"
BUILD_TOKEN_HASH_INDEX = (
    f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {TOKEN_HASH_INDEX} ON {INVITES} (token_hash)"
)
DROP_TOKEN_HASH_INDEX = f"DROP INDEX CONCURRENTLY IF EXISTS {TOKEN_HASH_INDEX}"


def upgrade() -> None:
    op.add_column(INVITES, sa.Column("token_hash", sa.String(64), nullable=True))
    op.add_column(INVITES, sa.Column("used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(INVITES, sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(INVITES, sa.Column("accepted_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(INVITES, sa.Column("project_memberships", postgresql.JSONB(), nullable=True))
    op.create_foreign_key(ACCEPTED_FK, INVITES, "users", ["accepted_user_id"], ["id"], ondelete="SET NULL")
    op.alter_column(INVITES, "token", existing_type=sa.String(64), nullable=True)
    op.execute(f"UPDATE {INVITES} SET token = NULL")

    op.create_table(
        RESETS,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "issued_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_ip_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user_id", RESETS, ["user_id"])

    # An existing table: built CONCURRENTLY (the concurrent-index guard), outside
    # the transaction; IF NOT EXISTS because the block commits what ran before it.
    with op.get_context().autocommit_block():
        op.execute(BUILD_TOKEN_HASH_INDEX)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(DROP_TOKEN_HASH_INDEX)
    op.drop_index("ix_password_reset_tokens_user_id", table_name=RESETS)
    op.drop_table(RESETS)
    op.execute(f"UPDATE {INVITES} SET token = token_hash WHERE token IS NULL AND token_hash IS NOT NULL")
    op.execute(f"UPDATE {INVITES} SET token = md5(id::text) WHERE token IS NULL")
    op.alter_column(INVITES, "token", existing_type=sa.String(64), nullable=False)
    op.drop_constraint(ACCEPTED_FK, INVITES, type_="foreignkey")
    op.drop_column(INVITES, "project_memberships")
    op.drop_column(INVITES, "accepted_user_id")
    op.drop_column(INVITES, "revoked_at")
    op.drop_column(INVITES, "used_at")
    op.drop_column(INVITES, "token_hash")
