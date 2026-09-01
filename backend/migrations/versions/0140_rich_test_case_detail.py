"""Add source provenance and explicit step presence to executed test cases.

The columns are additive and nullable/defaulted so existing report rows and
older producers remain readable. Source identifiers are strings because
Allure and other frameworks use opaque identifiers, not always UUIDs.
"""
from alembic import op
import sqlalchemy as sa


revision = "0140"
down_revision = "0139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("source_uuid", sa.String(length=255), nullable=True))
    op.add_column("test_cases", sa.Column("source_history_id", sa.String(length=255), nullable=True))
    op.add_column("test_cases", sa.Column("source_test_case_id", sa.String(length=255), nullable=True))
    op.add_column("test_cases", sa.Column("parser_format", sa.String(length=100), nullable=True))
    op.add_column("test_cases", sa.Column("parser_version", sa.String(length=100), nullable=True))
    op.add_column(
        "test_cases",
        sa.Column("steps_present", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Existing nullable step_count cannot distinguish missing from empty; both
    # are explicitly represented as false rather than guessed as present.
    op.execute(
        "UPDATE test_cases SET steps_present = "
        "CASE WHEN step_count IS NOT NULL AND step_count > 0 THEN true ELSE false END"
    )
    op.alter_column("test_cases", "steps_present", server_default=None)


def downgrade() -> None:
    op.drop_column("test_cases", "steps_present")
    op.drop_column("test_cases", "parser_version")
    op.drop_column("test_cases", "parser_format")
    op.drop_column("test_cases", "source_test_case_id")
    op.drop_column("test_cases", "source_history_id")
    op.drop_column("test_cases", "source_uuid")
