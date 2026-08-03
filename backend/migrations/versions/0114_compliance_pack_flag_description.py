"""correct the release_compliance_pack flag description: signed -> tamper-evident

Revision ID: 0114
Revises: 0113
Create Date: 2026-08-03

Honesty fix from the US-13.3 compliance audit.

Migration 0066 seeded the ``release_compliance_pack`` feature flag with the
description "Generate **signed** ZIP compliance packs for release decisions."
Compliance packs are **not** signed: there is no HMAC and no PKI. Integrity
is a SHA-256 chain — ``manifest.json`` digests every file in the pack, and
``compliance_packs.manifest_sha256`` digests the manifest. That is
tamper-EVIDENT, not tamper-PROOF: an actor who can rewrite both the stored
object and the database row forges a consistent pack.

That description is user-visible in Settings -> Feature Flags, so correcting
the source of 0066 would only help fresh installs. This data migration fixes
already-deployed instances too (and re-corrects fresh installs, since it runs
after 0066 inserts the original text).

Guarded by ``LIKE '%signed%'`` so an operator who has already edited the
description is not clobbered. Downgrade restores the original wording.
"""
from alembic import op
import sqlalchemy as sa


revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


_OLD = (
    "Generate signed ZIP compliance packs for release decisions. "
    "Tier 1 item 4."
)
_NEW = (
    "Generate ZIP compliance packs for release decisions, sealed with a "
    "tamper-evident SHA-256 checksum chain (not cryptographically signed). "
    "Tier 1 item 4."
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE feature_flags SET description = :new, updated_at = now() "
            "WHERE key = 'release_compliance_pack' "
            "AND description LIKE '%signed%'"
        ).bindparams(new=_NEW)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE feature_flags SET description = :old, updated_at = now() "
            "WHERE key = 'release_compliance_pack' "
            "AND description = :new"
        ).bindparams(old=_OLD, new=_NEW)
    )
