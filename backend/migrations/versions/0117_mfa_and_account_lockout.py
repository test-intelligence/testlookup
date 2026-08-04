"""TOTP MFA columns on users + mfa_recovery_codes + account lockout state

Revision ID: 0117
Revises: 0116
Create Date: 2026-08-05

Closes the two gaps our own shipped compliance mapping admits: no MFA, and no
account lockout.

1. **``users`` MFA columns.**

   - ``mfa_enabled`` — the single authority on whether a second factor applies
     to the account. The TOTP seed lives in ``secret_refs`` (scope
     ``user_totp``), and the two can disagree: after an ``APP_SECRET_KEY``
     rotation without ``APP_SECRET_KEY_PREVIOUS`` every seed reads back as
     absent. This flag is what stops that from silently meaning "MFA is off".
   - ``mfa_enrolled_at`` — when the factor was confirmed.
   - ``mfa_last_used_step`` — the highest TOTP time-step already accepted. A
     code is honoured only when its step is strictly greater, which makes
     replaying a code inside its own ±1-step validity window impossible.
     ``BIGINT`` because a step is unix-seconds/30 and will outlive INT32 in
     2038 + change.

   Deliberately NOT backfilled to anything but the default: every pre-0117
   account has no second factor, so ``false`` / ``NULL`` is the truth.

2. **``users`` lockout columns** — ``failed_login_attempts``, ``locked_until``,
   ``last_login_at``.

   Postgres rather than Redis on purpose. The existing in-process rate limiter
   (``main.rate_limit_auth``) keeps counters in worker memory, so it is a
   per-worker ceiling and cannot express "this account has failed N times
   across the fleet". Postgres can, it is already being read on the login path
   (the user is fetched by username anyway), it survives a restart, and it has
   no fail-open/fail-closed dilemma — a Redis outage cannot unlock every
   locked account or lock out every user.

3. **``mfa_recovery_codes``** — single-use fallback codes, SHA-256 digests
   only. A used code keeps its row with ``used_at`` set rather than being
   deleted, so "a recovery code was burned on <date>" stays answerable; rows
   are removed only when the set is reissued or MFA is disabled.

The workspace MFA/lockout policy needs no schema: it is a single
``app_settings`` row keyed ``mfa_policy``.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users", sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("mfa_last_used_step", sa.BigInteger(), nullable=True))
    op.add_column(
        "users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "mfa_recovery_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_mfa_recovery_user", "mfa_recovery_codes", ["user_id"])
    op.create_index(
        "ix_mfa_recovery_code_hash", "mfa_recovery_codes", ["code_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_mfa_recovery_code_hash", table_name="mfa_recovery_codes")
    op.drop_index("ix_mfa_recovery_user", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")

    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "mfa_last_used_step")
    op.drop_column("users", "mfa_enrolled_at")
    op.drop_column("users", "mfa_enabled")
    # NOTE: the TOTP seeds in ``secret_refs`` (scope 'user_totp') and the
    # ``mfa_policy`` row in ``app_settings`` are intentionally left in place.
    # A downgrade is a rollback, not a data purge — dropping them would
    # silently destroy every enrolled factor and the operator's policy on what
    # is meant to be a reversible step. Remove them by hand if that is
    # genuinely what you want.
