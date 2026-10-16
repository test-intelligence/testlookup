"""Java source roots for a project: ``projects.source_roots`` (W07 FAIL-08).

A JVM stack frame names a class and a file (``at com.acme.pay.CheckoutTest.pays
(CheckoutTest.java:42)``), never a repository path, so TestLookup could not
locate a Java failure for CODEOWNERS routing. A QA lead now lists where the
project's test sources live: ``[{"prefix": str, "root": str}, ...]`` in
priority order (``codeowners_service.validate_source_roots`` checks the shape).

A nullable JSON column: NULL and ``[]`` both mean "no roots" (today's behaviour,
Java unlocated). No index (it is read by primary key), no backfill (no project
has roots before this change).

The downgrade drops the column.

Revision ID: 0215
Revises: 0214
"""
import sqlalchemy as sa
from alembic import op

revision = "0215"
down_revision = "0214"
branch_labels = None
depends_on = None

TABLE = "projects"
COLUMN = "source_roots"


def upgrade() -> None:
    # Nullable, no default: a catalogue change, no table rewrite.
    op.add_column(TABLE, sa.Column(COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
