"""hash share link tokens at rest + add refresh token rotation tracking

Revision ID: 0057
Revises: 0056
Create Date: 2026-04-13

Security hardening:
  - report_share_links.token (plaintext) → token_hash (SHA-256 hex)
  - new refresh_token_records table for jti/rotation/replay detection
"""
import hashlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0057"
down_revision = "0056"


def upgrade() -> None:
    # ── report_share_links: hash tokens at rest ────────────────────────────
    op.add_column(
        "report_share_links",
        sa.Column("token_hash", sa.String(64), nullable=True),
    )

    # Backfill: hash any existing plaintext tokens so old links keep working
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, token FROM report_share_links")).fetchall()
    for row in rows:
        h = hashlib.sha256(row.token.encode("utf-8")).hexdigest()
        bind.execute(
            sa.text("UPDATE report_share_links SET token_hash = :h WHERE id = :id"),
            {"h": h, "id": row.id},
        )

    op.alter_column("report_share_links", "token_hash", nullable=False)
    op.drop_index("ix_rsl_token", table_name="report_share_links")
    op.create_index("ix_rsl_token_hash", "report_share_links", ["token_hash"], unique=True)
    op.drop_column("report_share_links", "token")

    # ── refresh_token_records: jti rotation + replay detection ─────────────
    op.create_table(
        "refresh_token_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jti_hash", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_to_id", UUID(as_uuid=True), nullable=True),
        sa.Column("replay_detected", sa.Boolean, server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_rtr_jti_hash", "refresh_token_records", ["jti_hash"], unique=True)
    op.create_index("ix_rtr_user_id", "refresh_token_records", ["user_id"])
    op.create_index("ix_rtr_expires_at", "refresh_token_records", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_rtr_expires_at", table_name="refresh_token_records")
    op.drop_index("ix_rtr_user_id", table_name="refresh_token_records")
    op.drop_index("ix_rtr_jti_hash", table_name="refresh_token_records")
    op.drop_table("refresh_token_records")

    op.add_column("report_share_links", sa.Column("token", sa.String(64), nullable=True))
    op.drop_index("ix_rsl_token_hash", table_name="report_share_links")
    op.create_index("ix_rsl_token", "report_share_links", ["token"], unique=True)
    op.drop_column("report_share_links", "token_hash")
