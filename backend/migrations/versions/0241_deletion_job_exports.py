"""Deletion-job export columns (W22b ACT-03, export before delete).

Twelve nullable columns, one FK and three CHECKs on the EXISTING
``deletion_jobs`` table (an operational table pruned on the audit clock):

* ``export_tier`` / ``export_target`` -- the archive a criteria deletion builds
  first (``_EXPORT_TIERS`` / ``_EXPORT_TARGETS``, the same literals as
  ``app.services.retention_vocab.EXPORT_TIERS`` / ``EXPORT_TARGETS``, pinned by
  ``tests/test_migrations_0241_0242.py``); both NULL = no export, and the pair
  CHECK keeps them NULL together.
* ``export_bytes``, ``export_manifest_sha256``, ``export_archive_sha256``,
  ``export_location`` (the storage key -- never returned to a client, never
  logged), ``export_verified_at`` (set only after the stored archive was
  re-read and verified; deletion requires it), ``export_meta`` (JSONB).
* ``collect_expires_at``, ``collected_at``, ``acknowledged_at``,
  ``acknowledged_by_id`` (FK ``users.id`` ON DELETE SET NULL) -- the
  download-and-confirm life of a ``collect`` archive.

``ADD COLUMN ... NULL`` without a default is metadata-only; the CHECKs scan
the table once (every existing row is NULL and passes). No index, so nothing
for the concurrent-build rule. The downgrade drops the three CHECKs, the FK,
then the twelve columns in reverse order.

Revision ID: 0241
Revises: 0240
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0241"
down_revision = "0240"
branch_labels = None
depends_on = None

_EXPORT_TIERS = ("summary", "evidence", "full")
_EXPORT_TARGETS = ("collect", "retain")

_COLUMNS = (
    ("export_tier", sa.String(10)),
    ("export_target", sa.String(10)),
    ("export_bytes", sa.BigInteger()),
    ("export_manifest_sha256", sa.String(64)),
    ("export_archive_sha256", sa.String(64)),
    ("export_location", sa.String(500)),
    ("export_verified_at", sa.DateTime(timezone=True)),
    ("export_meta", postgresql.JSONB()),
    ("collect_expires_at", sa.DateTime(timezone=True)),
    ("collected_at", sa.DateTime(timezone=True)),
    ("acknowledged_at", sa.DateTime(timezone=True)),
    ("acknowledged_by_id", postgresql.UUID(as_uuid=True)),
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("deletion_jobs", sa.Column(name, type_, nullable=True))
    op.create_foreign_key(
        "fk_deletion_jobs_acknowledged_by",
        "deletion_jobs",
        "users",
        ["acknowledged_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_deletion_jobs_export_tier",
        "deletion_jobs",
        f"export_tier IS NULL OR export_tier IN ({_in(_EXPORT_TIERS)})",
    )
    op.create_check_constraint(
        "ck_deletion_jobs_export_target",
        "deletion_jobs",
        f"export_target IS NULL OR export_target IN ({_in(_EXPORT_TARGETS)})",
    )
    op.create_check_constraint(
        "ck_deletion_jobs_export_pair",
        "deletion_jobs",
        "(export_tier IS NULL) = (export_target IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_deletion_jobs_export_pair", "deletion_jobs", type_="check")
    op.drop_constraint("ck_deletion_jobs_export_target", "deletion_jobs", type_="check")
    op.drop_constraint("ck_deletion_jobs_export_tier", "deletion_jobs", type_="check")
    op.drop_constraint("fk_deletion_jobs_acknowledged_by", "deletion_jobs", type_="foreignkey")
    for name, _type in reversed(_COLUMNS):
        op.drop_column("deletion_jobs", name)
