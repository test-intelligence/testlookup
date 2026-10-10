"""Give each analysed test case the category its analysis concluded.

``POST /analyze`` wrote a failure's category to both ``ai_analysis`` and
``test_cases``; the pipeline's batch upsert wrote only ``ai_analysis``. Failure
Analysis, the trend/chart rows, digests, failure groups and My Failures read
``test_cases.failure_category``, so every pipeline-analysed failure read
"Unknown" there while Run Intelligence showed its real category (E2E
2026-10-10: 69 such analyses on the homelab, none on their test cases). The
pipeline now mirrors it (``services/failure_category_sync``); this repairs the
rows it already wrote.

Only unset or UNKNOWN test-case categories are filled, so a label a person set
(a correction, the bulk Classify) is never overwritten. Idempotent.

Revision ID: 0200
Revises: 0199
"""
from alembic import op

revision = "0200"
down_revision = "0199"
branch_labels = None
depends_on = None

SQL = """
UPDATE test_cases AS tc
SET failure_category = a.failure_category
FROM ai_analysis AS a
WHERE a.test_case_id = tc.id
  AND a.failure_category IN (
      'PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY'
  )
  AND (tc.failure_category IS NULL OR tc.failure_category = 'UNKNOWN')
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    # Intentionally a no-op: the copied categories are the analyses' own
    # conclusions, and nothing records which rows were unset before.
    pass
