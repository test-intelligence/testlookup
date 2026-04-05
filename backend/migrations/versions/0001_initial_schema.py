"""Initial schema — all tables.

Revision ID: 0001
Revises:
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pg extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), server_default="QA_ENGINEER"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("username"),
    )

    # projects
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("jira_project_key", sa.String(50)),
        sa.Column("splunk_index", sa.String(255)),
        sa.Column("ocp_namespace", sa.String(255)),
        sa.Column("jenkins_job_pattern", sa.String(500)),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("slug"),
    )

    # test_runs
    op.create_table(
        "test_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("build_number", sa.String(100), nullable=False),
        sa.Column("jenkins_job", sa.String(500)),
        sa.Column("trigger_source", sa.String(50)),
        sa.Column("branch", sa.String(255)),
        sa.Column("commit_hash", sa.String(64)),
        sa.Column("status", sa.String(20), server_default="IN_PROGRESS"),
        sa.Column("total_tests", sa.Integer, server_default="0"),
        sa.Column("passed_tests", sa.Integer, server_default="0"),
        sa.Column("failed_tests", sa.Integer, server_default="0"),
        sa.Column("skipped_tests", sa.Integer, server_default="0"),
        sa.Column("broken_tests", sa.Integer, server_default="0"),
        sa.Column("pass_rate", sa.Float),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("ocp_pod_name", sa.String(255)),
        sa.Column("ocp_node", sa.String(255)),
        sa.Column("ocp_namespace", sa.String(255)),
        sa.Column("ocp_metadata", postgresql.JSONB),
        sa.Column("minio_prefix", sa.String(1000)),
        sa.Column("start_time", sa.DateTime(timezone=True)),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "build_number", "jenkins_job", name="uq_test_run_build"),
    )
    op.create_index("ix_test_runs_project_status", "test_runs", ["project_id", "status"])
    op.create_index("ix_test_runs_created_at", "test_runs", ["created_at"])

    # test_cases
    op.create_table(
        "test_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(1000), nullable=False),
        sa.Column("full_name", sa.String(2000)),
        sa.Column("suite_name", sa.String(500)),
        sa.Column("class_name", sa.String(500)),
        sa.Column("package_name", sa.String(500)),
        sa.Column("status", sa.String(20), nullable=False, server_default="UNKNOWN"),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("severity", sa.String(20)),
        sa.Column("feature", sa.String(500)),
        sa.Column("story", sa.String(500)),
        sa.Column("epic", sa.String(500)),
        sa.Column("owner", sa.String(255)),
        sa.Column("tags", postgresql.JSONB),
        sa.Column("failure_category", sa.String(30)),
        sa.Column("error_message", sa.Text),
        sa.Column("minio_s3_prefix", sa.String(1000)),
        sa.Column("has_attachments", sa.Boolean, server_default="false"),
        sa.Column("search_vector", postgresql.TSVECTOR),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_test_cases_run_status", "test_cases", ["test_run_id", "status"])
    op.create_index("ix_test_cases_fingerprint", "test_cases", ["test_fingerprint"])
    op.create_index("ix_test_cases_search", "test_cases", ["search_vector"], postgresql_using="gin")

    # Auto-update search_vector trigger
    op.execute("""
        CREATE OR REPLACE FUNCTION update_test_case_search_vector()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english',
                COALESCE(NEW.test_name, '') || ' ' ||
                COALESCE(NEW.suite_name, '') || ' ' ||
                COALESCE(NEW.class_name, '') || ' ' ||
                COALESCE(NEW.error_message, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER test_cases_search_vector_update
        BEFORE INSERT OR UPDATE ON test_cases
        FOR EACH ROW EXECUTE FUNCTION update_test_case_search_vector();
    """)

    # test_case_history
    op.create_table(
        "test_case_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="CASCADE")),
        sa.Column("test_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE")),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("failure_category", sa.String(30)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_history_fingerprint_date", "test_case_history", ["test_fingerprint", "created_at"])

    # ai_analysis
    op.create_table(
        "ai_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), unique=True),
        sa.Column("root_cause_summary", sa.Text),
        sa.Column("failure_category", sa.String(30)),
        sa.Column("backend_error_found", sa.Boolean, server_default="false"),
        sa.Column("pod_issue_found", sa.Boolean, server_default="false"),
        sa.Column("is_flaky", sa.Boolean, server_default="false"),
        sa.Column("confidence_score", sa.Integer),
        sa.Column("recommended_actions", postgresql.JSONB),
        sa.Column("evidence_references", postgresql.JSONB),
        sa.Column("llm_provider", sa.String(50)),
        sa.Column("llm_model", sa.String(100)),
        sa.Column("requires_human_review", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # defects
    op.create_table(
        "defects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="CASCADE")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("jira_ticket_id", sa.String(50)),
        sa.Column("jira_ticket_url", sa.String(1000)),
        sa.Column("jira_status", sa.String(50)),
        sa.Column("ai_confidence_score", sa.Integer),
        sa.Column("failure_category", sa.String(30)),
        sa.Column("resolution_status", sa.String(50), server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )

    # quality_gates
    op.create_table(
        "quality_gates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("rules", postgresql.JSONB, server_default="[]"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # coverage_snapshots
    op.create_table(
        "coverage_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("total_suites", sa.Integer, server_default="0"),
        sa.Column("total_tests", sa.Integer, server_default="0"),
        sa.Column("automated_count", sa.Integer, server_default="0"),
        sa.Column("suite_coverage", postgresql.JSONB),
        sa.UniqueConstraint("project_id", "snapshot_date", name="uq_coverage_project_date"),
    )


def downgrade() -> None:
    op.drop_table("coverage_snapshots")
    op.drop_table("quality_gates")
    op.drop_table("defects")
    op.drop_table("ai_analysis")
    op.drop_table("test_case_history")
    op.execute("DROP TRIGGER IF EXISTS test_cases_search_vector_update ON test_cases")
    op.execute("DROP FUNCTION IF EXISTS update_test_case_search_vector")
    op.drop_table("test_cases")
    op.drop_table("test_runs")
    op.drop_table("projects")
    op.drop_table("users")
