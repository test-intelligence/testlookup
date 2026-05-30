"""Tag every test run with its suite(s).

Revision ID: 0072
Revises: 0071
Create Date: 2026-05-10

Adds two columns to ``test_runs``:

* ``primary_suite_name`` — the dominant suite for the run (most test cases;
  alphabetical tiebreak). Indexed for filter/sort/display.
* ``suite_names`` — JSON list of every distinct suite the run touched.

Both are populated from ``test_cases.suite_name`` and backfilled for all
existing rows so the UI doesn't show '—' for historical runs.
"""
from alembic import op
import sqlalchemy as sa


revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column("primary_suite_name", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "test_runs",
        sa.Column("suite_names", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_test_runs_primary_suite_name",
        "test_runs",
        ["primary_suite_name"],
    )

    # Backfill — for every run, aggregate distinct non-empty test_cases.suite_name.
    # primary = most-frequent (alphabetical tiebreak), suite_names = sorted distinct.
    op.execute(
        """
        WITH suite_counts AS (
            SELECT
                test_run_id,
                suite_name,
                COUNT(*) AS n
            FROM test_cases
            WHERE suite_name IS NOT NULL
              AND suite_name <> ''
            GROUP BY test_run_id, suite_name
        ),
        ranked AS (
            SELECT
                test_run_id,
                suite_name,
                ROW_NUMBER() OVER (
                    PARTITION BY test_run_id
                    ORDER BY n DESC, suite_name ASC
                ) AS rnk
            FROM suite_counts
        ),
        per_run AS (
            SELECT
                sc.test_run_id,
                (SELECT suite_name FROM ranked r
                 WHERE r.test_run_id = sc.test_run_id AND r.rnk = 1) AS primary_suite,
                jsonb_agg(DISTINCT sc.suite_name ORDER BY sc.suite_name) AS suite_list
            FROM suite_counts sc
            GROUP BY sc.test_run_id
        )
        UPDATE test_runs tr
        SET primary_suite_name = pr.primary_suite,
            suite_names = pr.suite_list::json
        FROM per_run pr
        WHERE tr.id = pr.test_run_id;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_test_runs_primary_suite_name", table_name="test_runs")
    op.drop_column("test_runs", "suite_names")
    op.drop_column("test_runs", "primary_suite_name")
