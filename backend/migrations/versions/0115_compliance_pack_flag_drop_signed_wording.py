"""release_compliance_pack flag description: drop the word "signed" entirely

Revision ID: 0115
Revises: 0114
Create Date: 2026-08-03

Follow-up to 0114, same US-13.3 compliance-audit finding 4.

0066 seeded the ``release_compliance_pack`` feature flag as "Generate
**signed** ZIP compliance packs…". Packs are not signed — there is no HMAC and
no PKI, only a SHA-256 chain (``manifest.json`` digests every file in the pack;
``compliance_packs.manifest_sha256`` digests the manifest). 0114 replaced that
with wording ending "(not cryptographically signed)": accurate, but it still
puts the word *signed* in front of an admin skimming Settings → Feature Flags,
and it makes "does this description claim signing?" un-assertable as a plain
substring check. The final wording names the mechanism and disclaims the two
things people actually mean by "signed":

    …sealed with a tamper-evident SHA-256 checksum chain (no HMAC, no PKI).

That is tamper-EVIDENT, not tamper-PROOF: an actor who can rewrite both the
stored object and the database row forges a self-consistent pack.

0066's source now seeds ``_FINAL`` directly, so a fresh install never sees the
intermediate text and this migration no-ops there. Deployed installs reach
``_FINAL`` from either side: pre-0114 rows via 0114 → 0115, at-0114 rows via
0115 alone.

The guard is an exact match on the two known prior values rather than
``LIKE '%signed%'`` (0114's guard), so an operator who has reworded the
description themselves is never clobbered — including one whose wording happens
to contain "signed".

Downgrade restores 0114's wording, which is the canonical description for a
database at revision 0114. On a *fresh* install seeded straight to ``_FINAL``
that is technically a rewrite rather than a restore; re-running the upgrade
returns it to ``_FINAL``.
"""
from alembic import op
import sqlalchemy as sa


revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


# As seeded by 0066 before the audit fix.
_ORIGINAL_0066 = (
    "Generate signed ZIP compliance packs for release decisions. "
    "Tier 1 item 4."
)
# As set by 0114 — honest, but still contains the word "signed".
_INTERIM_0114 = (
    "Generate ZIP compliance packs for release decisions, sealed with a "
    "tamper-evident SHA-256 checksum chain (not cryptographically signed). "
    "Tier 1 item 4."
)
# Final wording. Must stay byte-identical to the string 0066 seeds.
_FINAL = (
    "Generate ZIP compliance packs for release decisions, sealed with a "
    "tamper-evident SHA-256 checksum chain (no HMAC, no PKI). "
    "Tier 1 item 4."
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE feature_flags SET description = :final, updated_at = now() "
            "WHERE key = 'release_compliance_pack' "
            "AND description IN (:original, :interim)"
        ).bindparams(final=_FINAL, original=_ORIGINAL_0066, interim=_INTERIM_0114)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE feature_flags SET description = :interim, updated_at = now() "
            "WHERE key = 'release_compliance_pack' "
            "AND description = :final"
        ).bindparams(interim=_INTERIM_0114, final=_FINAL)
    )
