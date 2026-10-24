"""A semantic reindex job names the embedding generation it builds (W12 SRCH-02).

Two nullable columns on ``semantic_reindex_jobs`` and one backfill:

* ``collection`` (``VARCHAR(63)``, Chroma's own name limit): the physical
  ChromaDB collection the job writes. ``NULL`` only on a row an older build
  wrote before this column existed.
* ``embedder_key`` (``VARCHAR(200)``): the embedder key of the generation the
  job builds -- ``default`` (Chroma's bundled MiniLM), ``ollama:<model>`` or
  ``openai:<model>``.

Every existing row is a rebuild of the LEGACY generation of
``test_case_search`` (scope ``global`` or ``project:<id>``: no other kind of
job existed), so the backfill writes ``embedder_key = 'default'`` and
``collection = 'test_case_search'`` on each. A build of a NEW generation
reuses the same keyset job under ``scope_key = 'generation:<tag>'`` and sets
both columns when it claims the row. No index (the table holds one row per
scope) and no constraint change (``state_version = 1`` holds: a generation
build is the same keyset state).

The downgrade drops the two columns in reverse order. A ``generation:<tag>``
row a build left behind stays a valid row for the older code, which never
selects that scope.

Revision ID: 0226
Revises: 0225
"""
from alembic import op
import sqlalchemy as sa

revision = "0226"
down_revision = "0225"
branch_labels = None
depends_on = None

TABLE = "semantic_reindex_jobs"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("collection", sa.String(63), nullable=True))
    op.add_column(TABLE, sa.Column("embedder_key", sa.String(200), nullable=True))
    op.execute(
        "UPDATE semantic_reindex_jobs SET embedder_key = 'default', "
        "collection = 'test_case_search' WHERE embedder_key IS NULL"
    )


def downgrade() -> None:
    op.drop_column(TABLE, "embedder_key")
    op.drop_column(TABLE, "collection")
