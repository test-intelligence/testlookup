"""Fix ORM/DB drift: add project_id index on project_llm_usage + align JSON→JSONB

Revision ID: 0070
Revises: 0069
Create Date: 2026-04-14

Follow-up cleanup for code review findings on the Tier 0-2 work:

1. Add the missing ``ix_llm_usage_project`` index on
   ``project_llm_usage(project_id)``. The per-project budget lookup in
   ``services/llm_cost_budget.get_current_usage`` filters by project and
   was table-scanning at scale.

2. No schema change for the JSONB columns — the underlying Postgres type
   is already JSONB (set by migrations 0062/0065/0066/0068). The ORM
   declarations in ``app/models/postgres.py`` were corrected in the same
   changeset so reads/writes go through the JSONB adapter, unlocking
   operators and GIN-index friendly queries without touching data.
"""
from alembic import op


revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_llm_usage_project",
        "project_llm_usage",
        ["project_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_project", table_name="project_llm_usage")
