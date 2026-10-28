"""Retention holds (W22a ACT-05).

* ``retention_holds``: one row per hold an operator places on a project, a
  release, a run or a compliance pack. ``entity_id`` is NULL for a project hold
  (the whole project) and set for every other type -- deliberately no FK (the
  entity is polymorphic, and it cannot be deleted while the hold lives).
  ``entity_label`` / ``placed_by_name`` / ``released_by_name`` are snapshots
  taken when the row is written, so a later rename does not rewrite the audit
  record. A hold is never deleted: releasing it stamps ``released_at`` with a
  required ``release_reason``. Its state (live / expired / released) is derived
  on read from ``released_at`` / ``expires_at`` / now and never stored.
* Four CHECKs: the entity-type vocabulary (``_ENTITY_TYPES``, the same literals
  as ``app.services.retention_vocab.HOLD_ENTITY_TYPES``, pinned by
  ``tests/test_migrations_0239_0240.py``), ``entity_id`` NULL iff a project
  hold, a trimmed reason of at least 3 characters, and a release reason on
  every released row.
* ``ix_retention_holds_live`` -- partial on the live rows (``released_at IS
  NULL``) -- serves every hold anti-join; ``ix_retention_holds_project_placed``
  serves the per-project list.

A NEW table, so its two indexes are built in the same transaction,
non-concurrently (the 0166+ rule exempts a table the migration created
itself, ``tests/regression/test_migration_index_builds_are_concurrent.py``).
No backfill. The downgrade drops the two indexes, then the table.

Revision ID: 0239
Revises: 0238
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0239"
down_revision = "0238"
branch_labels = None
depends_on = None

_ENTITY_TYPES = ("project", "release", "run", "compliance_pack")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "retention_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_label", sa.String(300), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("placed_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("placed_by_name", sa.String(200), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("released_by_name", sa.String(200), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(f"entity_type IN ({_in(_ENTITY_TYPES)})", name="ck_retention_holds_entity_type"),
        sa.CheckConstraint(
            "(entity_type = 'project' AND entity_id IS NULL) OR (entity_type <> 'project' AND entity_id IS NOT NULL)",
            name="ck_retention_holds_entity_id",
        ),
        sa.CheckConstraint("length(trim(reason)) >= 3", name="ck_retention_holds_reason"),
        sa.CheckConstraint("released_at IS NULL OR release_reason IS NOT NULL", name="ck_retention_holds_release_reason"),
    )
    op.create_index(
        "ix_retention_holds_live",
        "retention_holds",
        ["project_id", "entity_type", "entity_id"],
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index("ix_retention_holds_project_placed", "retention_holds", ["project_id", "placed_at"])


def downgrade() -> None:
    op.drop_index("ix_retention_holds_project_placed", table_name="retention_holds")
    op.drop_index("ix_retention_holds_live", table_name="retention_holds")
    op.drop_table("retention_holds")
