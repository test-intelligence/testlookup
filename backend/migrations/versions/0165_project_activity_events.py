"""Project activity ledger (epic ACT).

Creates ``project_activity_events``: one append-only, project-scoped feed of
everything that happens inside a project, plus the keyset indexes the feed and
each of its filters read through.

The four compliance audit tables are untouched. This table is a derived read
surface that links back to them via ``source_table`` / ``source_id``.

Revision ID: 0165
Revises: 0164
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0165"
down_revision = "0164"
branch_labels = None
depends_on = None


# Duplicated from ``app/services/activity/events.py`` and from the model's
# CheckConstraints on purpose — a migration must not import application code,
# which can change under it. Parity is held by
# ``tests/test_activity_events.py::test_vocabularies_agree_everywhere``.
_CATEGORIES = (
    "runs", "analysis", "release", "quality", "configuration",
    "membership", "test_management", "integration", "agent", "system",
)
_ACTOR_TYPES = ("user", "api_key", "service_account", "system", "agent")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ``?q=`` runs a trigram ILIKE over ``summary``. The extension is already
    # present on instances that went through the search work, but a fresh
    # database has not necessarily seen it, so create it idempotently rather
    # than assuming.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "project_activity_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_name", sa.String(200), nullable=True),
        sa.Column("actor_ref", sa.String(120), nullable=True),
        sa.Column("entity_type", sa.String(40), nullable=False),
        # TEXT, not UUID: live-stream sessions address runs by slug.
        sa.Column("entity_id", sa.String(120), nullable=False),
        sa.Column("entity_label", sa.String(300), nullable=True),
        sa.Column("target_type", sa.String(40), nullable=True),
        sa.Column("target_id", sa.String(120), nullable=True),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("source_table", sa.String(40), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("group_key", sa.String(120), nullable=True),
        sa.CheckConstraint(
            f"category IN ({_in_list(_CATEGORIES)})", name="ck_pae_category"
        ),
        sa.CheckConstraint(
            f"actor_type IN ({_in_list(_ACTOR_TYPES)})", name="ck_pae_actor_type"
        ),
    )

    # ── Keyset indexes ──────────────────────────────────────────────────────
    # Every index ends on (occurred_at DESC, id DESC) or a prefix of it, because
    # the feed pages by keyset on exactly that tuple. Declared through raw SQL
    # rather than op.create_index so the DESC ordering is actually applied —
    # op.create_index would build them ASC and the planner would still sort.
    op.execute(
        "CREATE INDEX ix_pae_project_time ON project_activity_events "
        "(project_id, occurred_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_pae_project_category_time ON project_activity_events "
        "(project_id, category, occurred_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_pae_project_event_type ON project_activity_events "
        "(project_id, event_type, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_pae_project_entity ON project_activity_events "
        "(project_id, entity_type, entity_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_pae_project_actor ON project_activity_events "
        "(project_id, actor_id, occurred_at DESC)"
    )
    # Partial: most rows have no release, so this stays small.
    op.execute(
        "CREATE INDEX ix_pae_project_release ON project_activity_events "
        "(project_id, release_id, occurred_at DESC) WHERE release_id IS NOT NULL"
    )
    # Duplicate suppression looks up (project_id, group_key) within a 60s
    # window; bulk bursts group on it for rendering.
    op.execute(
        "CREATE INDEX ix_pae_group_key ON project_activity_events "
        "(project_id, group_key)"
    )
    # Trigram index backing ``?q=``. GIN over gin_trgm_ops makes an unanchored
    # ILIKE indexable; without it a text filter degrades to a full scan of the
    # project's history.
    op.execute(
        "CREATE INDEX ix_pae_summary_trgm ON project_activity_events "
        "USING gin (summary gin_trgm_ops)"
    )


def downgrade() -> None:
    # Indexes go with the table; drop it explicitly so a partial upgrade that
    # created indexes but failed later still rolls back cleanly.
    op.execute("DROP INDEX IF EXISTS ix_pae_summary_trgm")
    op.execute("DROP INDEX IF EXISTS ix_pae_group_key")
    op.execute("DROP INDEX IF EXISTS ix_pae_project_release")
    op.execute("DROP INDEX IF EXISTS ix_pae_project_actor")
    op.execute("DROP INDEX IF EXISTS ix_pae_project_entity")
    op.execute("DROP INDEX IF EXISTS ix_pae_project_event_type")
    op.execute("DROP INDEX IF EXISTS ix_pae_project_category_time")
    op.execute("DROP INDEX IF EXISTS ix_pae_project_time")
    op.drop_table("project_activity_events")
    # pg_trgm is deliberately NOT dropped: the search path uses it too, and
    # dropping a shared extension on a downgrade would break an unrelated
    # feature.
