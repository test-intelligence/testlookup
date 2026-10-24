"""One row per search the /search page ran -- with NO query text (W12 SRCH-05).

One new table, its check and two indexes:

* ``search_query_events``: one row per page-1 search with words that the
  /search page ran with ``record=true``, written after the response. It holds
  NO query text and NO user id: ``q_hash`` is an HMAC-SHA256 keyed from the
  deployment's ``APP_SECRET_KEY`` over the normalised query (lower-cased,
  whitespace collapsed), so it counts repeats but a database reader cannot
  dictionary-reverse it; beside it only the query's length, its word and
  filter counts, the scope, the mode asked for and the mode that answered,
  the fallback reason, the result count (and whether it is exact) and the
  latency. ``project_id`` is NULL for a search in All Projects; deleting a
  project deletes its rows (``ON DELETE CASCADE``).
* ``ck_search_query_events_nonnegative``: every count and the latency are
  ``>= 0``.
* ``ix_search_query_events_project_created`` (``project_id, created_at DESC``)
  serves a project's usage window; ``ix_search_query_events_created``
  (``created_at``) serves the deployment-wide window and the nightly purge.
  Both are built on the table this migration creates (empty), so plain
  builds: the concurrent-index rule is for existing tables.

Rows are kept 30 days (a FIXED window, not the audit clock): the nightly
``purge_search_usage_events`` task deletes older rows deployment-wide, and a
project's retention purge deletes that project's older rows.

The downgrade drops the two indexes and the table, in reverse order.

Revision ID: 0227
Revises: 0226
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0227"
down_revision = "0226"
branch_labels = None
depends_on = None

TABLE = "search_query_events"
PROJECT_INDEX = "ix_search_query_events_project_created"
CREATED_INDEX = "ix_search_query_events_created"
CHECK_NAME = "ck_search_query_events_nonnegative"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("q_hash", sa.String(64), nullable=False),
        sa.Column("q_length", sa.SmallInteger(), nullable=False),
        sa.Column("word_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("filter_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("entity_scope", sa.String(20), nullable=False),
        sa.Column("mode_requested", sa.String(10), nullable=False),
        sa.Column("mode_used", sa.String(10), nullable=False),
        sa.Column("fallback_reason", sa.String(30), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("counts_exact", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "q_length >= 0 AND word_count >= 0 AND filter_count >= 0 AND result_count >= 0 "
            "AND latency_ms >= 0",
            name=CHECK_NAME,
        ),
    )
    # On the table created just above (empty): plain builds are instant.
    op.create_index(PROJECT_INDEX, TABLE, ["project_id", sa.text("created_at DESC")])
    op.create_index(CREATED_INDEX, TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_index(CREATED_INDEX, table_name=TABLE)
    op.drop_index(PROJECT_INDEX, table_name=TABLE)
    op.drop_table(TABLE)
