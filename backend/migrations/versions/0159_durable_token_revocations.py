"""Persist access-token revocations outside evictable Redis."""
from alembic import op

revision = "0159"
down_revision = "0158"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("CREATE TABLE auth_token_revocations (jti VARCHAR(255) PRIMARY KEY, user_id UUID NULL REFERENCES users(id) ON DELETE CASCADE, revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(), valid_from TIMESTAMPTZ NULL, expires_at TIMESTAMPTZ NULL)")
    op.execute("CREATE INDEX ix_auth_token_revocations_user ON auth_token_revocations(user_id)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_token_revocations")
