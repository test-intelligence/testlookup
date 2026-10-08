"""Date the dev seed's test cases at their run, not at the moment the seed ran.

``scripts/seed_dev_data.py`` created every ``TestCase`` without ``created_at``,
so the column defaulted to the insert time: thirty days of seeded results, all
dated the minute the seed ran. Every analytics window on
``test_cases.created_at`` (top failing, failure categories, ...) then counted
all thirty days as "the last 7 days", while the flaky list, windowed on
``test_case_history.created_at`` (the run's start), did not. One /failures row
read "7" failures over "failed 3 of 7 executions" (the UX redesign's browser
E2E pass, 2026-10-08; every seeded install, the homelab demo included). The
seed now dates each test case at its run's start; this repairs the rows it
already wrote.

Only the dev seed's own rows: projects carrying its marker, runs on its
``<slug>-regression-pipeline`` job (the viz seed's runs already carry their
time), and only rows dated after their run ended. Idempotent.

Revision ID: 0199
Revises: 0198
"""
from alembic import op

revision = "0199"
down_revision = "0198"
branch_labels = None
depends_on = None

SQL = """
UPDATE test_cases AS tc
SET created_at = COALESCE(tr.start_time, tr.created_at)
FROM test_runs AS tr
JOIN projects AS p ON p.id = tr.project_id
WHERE tc.test_run_id = tr.id
  AND p.description LIKE '%seed_dev_data_v1%'
  AND tr.jenkins_job = REPLACE(p.slug, '-', '_') || '-regression-pipeline'
  AND tc.created_at > COALESCE(tr.end_time, tr.start_time, tr.created_at)
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    # Intentionally a no-op: there is no reason to re-date seeded rows to the
    # minute the seed ran.
    pass
