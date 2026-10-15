"""Per-project caps on kept AI history versions (AIH-06, owner decision D41).

``project_retention_policies`` gains ``ai_versions_per_run`` (versions kept per
run and per kind in Mongo ``ai_run_versions``) and ``ai_versions_per_test``
(rows kept per test result in ``ai_analysis_versions``). NULL means the code
defaults, 20 and 10 (``ai_history_core.DEFAULT_CAP_PER_RUN`` /
``DEFAULT_CAP_PER_TEST``), so an existing policy row needs no update and a
project without a row behaves the same. Each column is bounded 1-100 by a
CHECK. Pruning never removes version 1 or a version a published decision
report cites; that rule lives in code (``ai_history_core.plan_prune``).

``ADD COLUMN ... NULL`` without a default is a catalog change in PostgreSQL
11+: the table is not rewritten. Downgrade drops the constraints, then the
columns.

Revision ID: 0206
Revises: 0205
"""
from alembic import op
import sqlalchemy as sa

revision = "0206"
down_revision = "0205"
branch_labels = None
depends_on = None

TABLE = "project_retention_policies"
#: column -> CHECK constraint name
COLUMNS = {
    "ai_versions_per_run": "ck_retention_ai_versions_per_run",
    "ai_versions_per_test": "ck_retention_ai_versions_per_test",
}


def upgrade() -> None:
    for column, constraint in COLUMNS.items():
        op.add_column(TABLE, sa.Column(column, sa.Integer(), nullable=True))
        op.create_check_constraint(
            constraint, TABLE, f"{column} IS NULL OR {column} BETWEEN 1 AND 100"
        )


def downgrade() -> None:
    for column, constraint in reversed(list(COLUMNS.items())):
        op.drop_constraint(constraint, TABLE, type_="check")
        op.drop_column(TABLE, column)
