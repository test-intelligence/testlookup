"""TS-1: Suite membership traceability models.

Adds suite_memberships and suite_membership_events tables for tracking
which test cases belong to which suites, with full change history.

Revision ID: 0048
Revises: 0047
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suite_memberships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("suite_name", sa.String(500), nullable=False),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(1000), nullable=False),
        sa.Column("class_name", sa.String(500), nullable=True),
        sa.Column("managed_test_case_id", UUID(as_uuid=True), sa.ForeignKey("managed_test_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="execution"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("last_seen_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("first_seen_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("deleted_at_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_tag", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "suite_name", "test_fingerprint", name="uq_suite_membership"),
    )
    op.create_index("ix_sm_project_suite", "suite_memberships", ["project_id", "suite_name"])
    op.create_index("ix_sm_fingerprint", "suite_memberships", ["test_fingerprint"])
    op.create_index("ix_sm_status", "suite_memberships", ["status"])

    op.create_table(
        "suite_membership_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("suite_name", sa.String(500), nullable=False),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(1000), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("old_values", sa.JSON, nullable=True),
        sa.Column("new_values", sa.JSON, nullable=True),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_sme_project_suite", "suite_membership_events", ["project_id", "suite_name", "created_at"])
    op.create_index("ix_sme_run", "suite_membership_events", ["run_id"])


def downgrade() -> None:
    op.drop_table("suite_membership_events")
    op.drop_table("suite_memberships")
