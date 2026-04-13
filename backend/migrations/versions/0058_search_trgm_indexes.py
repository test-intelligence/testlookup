"""trigram GIN indexes for test_cases text search

Revision ID: 0058
Revises: 0057
Create Date: 2026-04-13

Backs the ILIKE '%q%' search in ``services/search_service.py`` with trigram
GIN indexes so large tables don't scan sequentially. Requires the pg_trgm
extension, which ships with PostgreSQL and only needs one CREATE EXTENSION.
"""
from alembic import op

revision = "0058"
down_revision = "0057"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_cases_name_trgm "
        "ON test_cases USING gin (test_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_cases_suite_trgm "
        "ON test_cases USING gin (suite_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_cases_error_trgm "
        "ON test_cases USING gin (error_message gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_test_cases_error_trgm")
    op.execute("DROP INDEX IF EXISTS ix_test_cases_suite_trgm")
    op.execute("DROP INDEX IF EXISTS ix_test_cases_name_trgm")
    # Leave the pg_trgm extension installed — other features may depend on it.
