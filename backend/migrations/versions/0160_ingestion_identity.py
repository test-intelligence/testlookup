"""Add source-aware idempotency identity for reusable ingests."""
from alembic import op
import sqlalchemy as sa

revision = "0160"
down_revision = "0159"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column("ingestion_identity", sa.String(length=64), nullable=True),
    )
    # Replace the old unconditional build constraint. Source-aware rows
    # must be unique by their canonical identity, while legacy rows retain
    # the old build-label protection.
    op.drop_constraint("uq_test_run_build", "test_runs", type_="unique")
    op.create_index(
        "uq_test_runs_legacy_build",
        "test_runs",
        ["project_id", "build_number", "jenkins_job"],
        unique=True,
        postgresql_where=sa.text("ingestion_identity IS NULL"),
    )
    op.create_index(
        "uq_test_runs_project_ingestion_identity",
        "test_runs",
        ["project_id", "ingestion_identity"],
        unique=True,
        postgresql_where=sa.text("ingestion_identity IS NOT NULL"),
    )
    op.create_index(
        "ix_test_runs_ingestion_identity",
        "test_runs",
        ["ingestion_identity"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_runs_ingestion_identity", table_name="test_runs")
    op.drop_index("uq_test_runs_project_ingestion_identity", table_name="test_runs")
    op.drop_index("uq_test_runs_legacy_build", table_name="test_runs")
    op.drop_column("test_runs", "ingestion_identity")
    op.create_unique_constraint(
        "uq_test_run_build",
        "test_runs",
        ["project_id", "build_number", "jenkins_job"],
    )
