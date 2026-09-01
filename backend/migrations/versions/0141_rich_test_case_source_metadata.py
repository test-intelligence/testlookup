"""Retain bounded source metadata for rich test-case detail responses."""

from alembic import op
import sqlalchemy as sa


revision = "0141"
down_revision = "0140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("source_parameters", sa.JSON(), nullable=True))
    op.add_column("test_cases", sa.Column("source_links", sa.JSON(), nullable=True))
    op.add_column("test_cases", sa.Column("source_labels", sa.JSON(), nullable=True))
    op.add_column("test_cases", sa.Column("source_extensions", sa.JSON(), nullable=True))
    op.add_column("test_cases", sa.Column("service_name", sa.String(length=255), nullable=True))
    op.add_column("test_cases", sa.Column("component_names", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_cases", "component_names")
    op.drop_column("test_cases", "service_name")
    op.drop_column("test_cases", "source_extensions")
    op.drop_column("test_cases", "source_labels")
    op.drop_column("test_cases", "source_links")
    op.drop_column("test_cases", "source_parameters")
