"""Test case management tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_test_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("objective", sa.Text),
        sa.Column("preconditions", sa.Text),
        sa.Column("steps", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("expected_result", sa.Text),
        sa.Column("test_data", sa.Text),
        sa.Column("test_type", sa.String(50), server_default="functional"),
        sa.Column("priority", sa.String(20), server_default="medium"),
        sa.Column("severity", sa.String(20), server_default="major"),
        sa.Column("feature_area", sa.String(500)),
        sa.Column("tags", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("status", sa.String(30), server_default="draft", index=True),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_automated", sa.Boolean, server_default="false"),
        sa.Column("automation_status", sa.String(30), server_default="not_automated"),
        sa.Column("test_fingerprint", sa.String(64)),
        sa.Column("ai_generated", sa.Boolean, server_default="false"),
        sa.Column("ai_generation_prompt", sa.Text),
        sa.Column("ai_quality_score", sa.Integer),
        sa.Column("ai_review_notes", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("estimated_duration_minutes", sa.Integer),
        sa.Column("last_executed_at", sa.DateTime(timezone=True)),
        sa.Column("last_execution_status", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mtc_project_status", "managed_test_cases", ["project_id", "status"])
    op.create_index("ix_mtc_author", "managed_test_cases", ["author_id"])
    op.create_index("ix_mtc_fingerprint", "managed_test_cases", ["test_fingerprint"])

    op.create_table(
        "test_case_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("steps", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("expected_result", sa.Text),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("changed_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("change_summary", sa.String(500)),
        sa.Column("change_type", sa.String(30), server_default="updated"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tcv_test_case", "test_case_versions", ["test_case_id"])

    op.create_table(
        "test_case_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(30), server_default="pending"),
        sa.Column("ai_review_completed", sa.Boolean, server_default="false"),
        sa.Column("ai_quality_score", sa.Integer),
        sa.Column("ai_review_notes", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("ai_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("human_notes", sa.Text),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tcr_test_case", "test_case_reviews", ["test_case_id"])
    op.create_index("ix_tcr_reviewer", "test_case_reviews", ["reviewer_id"])

    op.create_table(
        "test_case_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("comment_type", sa.String(30), server_default="general"),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_case_comments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("step_number", sa.Integer),
        sa.Column("is_resolved", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tcc_test_case", "test_case_comments", ["test_case_id"])

    op.create_table(
        "test_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("objective", sa.Text),
        sa.Column("status", sa.String(30), server_default="draft"),
        sa.Column("planned_start_date", sa.DateTime(timezone=True)),
        sa.Column("planned_end_date", sa.DateTime(timezone=True)),
        sa.Column("actual_start_date", sa.DateTime(timezone=True)),
        sa.Column("actual_end_date", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_to_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ai_generated", sa.Boolean, server_default="false"),
        sa.Column("ai_generation_context", sa.Text),
        sa.Column("total_cases", sa.Integer, server_default="0"),
        sa.Column("executed_cases", sa.Integer, server_default="0"),
        sa.Column("passed_cases", sa.Integer, server_default="0"),
        sa.Column("failed_cases", sa.Integer, server_default="0"),
        sa.Column("blocked_cases", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tp_project", "test_plans", ["project_id"])
    op.create_index("ix_tp_status", "test_plans", ["status"])

    op.create_table(
        "test_plan_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_index", sa.Integer, server_default="0"),
        sa.Column("priority_override", sa.String(20)),
        sa.Column("execution_status", sa.String(30), server_default="not_run"),
        sa.Column("executed_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("execution_notes", sa.Text),
        sa.Column("actual_duration_minutes", sa.Integer),
        sa.Column("test_case_result_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("plan_id", "test_case_id", name="uq_plan_test_case"),
    )
    op.create_index("ix_tpi_plan", "test_plan_items", ["plan_id"])

    op.create_table(
        "test_strategies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("version_label", sa.String(50), server_default="v1.0"),
        sa.Column("status", sa.String(30), server_default="draft"),
        sa.Column("objective", sa.Text),
        sa.Column("scope", sa.Text),
        sa.Column("out_of_scope", sa.Text),
        sa.Column("test_approach", sa.Text),
        sa.Column("risk_assessment", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("test_types", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("entry_criteria", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("exit_criteria", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("environments", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("automation_approach", sa.Text),
        sa.Column("defect_management", sa.Text),
        sa.Column("ai_generated", sa.Boolean, server_default="true"),
        sa.Column("generation_context", sa.Text),
        sa.Column("ai_model_used", sa.String(100)),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ts_project", "test_strategies", ["project_id"])

    op.create_table(
        "test_case_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_name", sa.String(200)),
        sa.Column("old_values", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("new_values", postgresql.JSON(astext_type=sa.Text())),
        sa.Column("details", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    op.create_index("ix_tcal_entity", "test_case_audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_tcal_project_created", "test_case_audit_logs", ["project_id", "created_at"])
    op.create_index("ix_tcal_actor", "test_case_audit_logs", ["actor_id"])


def downgrade() -> None:
    op.drop_table("test_case_audit_logs")
    op.drop_table("test_strategies")
    op.drop_table("test_plan_items")
    op.drop_table("test_plans")
    op.drop_table("test_case_comments")
    op.drop_table("test_case_reviews")
    op.drop_table("test_case_versions")
    op.drop_table("managed_test_cases")
