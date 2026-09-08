"""Persist normalized batch-ingestion completeness and rejection samples."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0161"
down_revision = "0160"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column("ingestion_attempted_tests", sa.Integer(), nullable=True),
    )
    op.add_column(
        "test_runs",
        sa.Column(
            "ingestion_rejected_tests",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "release_gate_decisions",
        sa.Column("ingestion_complete", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "release_gate_decisions",
        sa.Column("incomplete_runs", sa.JSON(), nullable=True),
    )
    op.add_column(
        "test_runs",
        sa.Column("ingestion_complete", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "test_runs",
        sa.Column(
            "ingestion_rejection_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("release_gate_decisions", "incomplete_runs")
    op.drop_column("release_gate_decisions", "ingestion_complete")
    op.drop_column("test_runs", "ingestion_rejection_reasons")
    op.drop_column("test_runs", "ingestion_complete")
    op.drop_column("test_runs", "ingestion_rejected_tests")
    op.drop_column("test_runs", "ingestion_attempted_tests")
