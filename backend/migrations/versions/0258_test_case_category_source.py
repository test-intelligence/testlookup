"""Who set a test case's failure category: a person or an analysis.

* ``test_cases.failure_category_source`` -- nullable ``VARCHAR(16)``,
  ``'human'`` or ``'analysis'``. Adding a nullable column with no default is
  metadata-only on PostgreSQL.

The pipeline copies an analysis's category onto its test case. It could not
tell a person's label from an earlier pipeline's, so it filled only an empty
column and the FIRST analysis won for good: a later Product Bug or a human
correction replayed by the pipeline never reached the Tests tab, which kept
reading Flaky (product findings D-85, D-150). With the source recorded the
pipeline replaces an analysis's label and leaves a person's alone.

Backfill: ``'human'`` on every test case with an INCORRECT rating that names a
category (a correction), which is a small set. Other rows stay NULL, which the
code reads as "set by an analysis"; there is no record of which rows the bulk
Classify wrote before this revision, so a full-table backfill would add nothing
but a long UPDATE.

The downgrade drops the column.

Revision ID: 0258
Revises: 0257
"""
from alembic import op
import sqlalchemy as sa

revision = "0258"
down_revision = "0257"
branch_labels = None
depends_on = None

TABLE = "test_cases"
COLUMN = "failure_category_source"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=16), nullable=True))
    op.execute(
        sa.text(
            "UPDATE test_cases SET failure_category_source = 'human' "
            "WHERE id IN ("
            "  SELECT DISTINCT test_case_id FROM ai_feedback "
            "  WHERE rating = 'incorrect' AND corrected_category IS NOT NULL"
            ")"
        )
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
