"""Store optional authored parameter definitions for rich test details."""

from alembic import op
import sqlalchemy as sa


revision = "0143"
down_revision = "0142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("managed_test_cases", sa.Column("parameters", sa.JSON(), nullable=True))
    op.add_column("test_case_versions", sa.Column("parameters", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_case_versions", "parameters")
    op.drop_column("managed_test_cases", "parameters")
