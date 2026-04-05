"""Deep investigation tables: failure_clusters, deep_findings, release_decisions, contract_violations

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── failure_clusters ─────────────────────────────────────────
    op.create_table(
        "failure_clusters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pipeline_run_id", UUID(as_uuid=True), sa.ForeignKey("agent_pipeline_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cluster_id", sa.String(20), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("representative_error", sa.Text, nullable=True),
        sa.Column("member_test_ids", JSONB, nullable=True),
        sa.Column("size", sa.Integer, nullable=False, server_default="1"),
        sa.Column("cohesion_score", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_failure_clusters_run", "failure_clusters", ["test_run_id"])

    # ── deep_findings ─────────────────────────────────────────────
    op.create_table(
        "deep_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", sa.String(20), nullable=False),
        sa.Column("root_cause", sa.Text, nullable=True),
        sa.Column("failure_category", sa.String(30), nullable=True),
        sa.Column("confidence_score", sa.Integer, nullable=True),
        sa.Column("causal_chain", JSONB, nullable=True),
        sa.Column("evidence", JSONB, nullable=True),
        sa.Column("affected_services", JSONB, nullable=True),
        sa.Column("contract_violations", JSONB, nullable=True),
        sa.Column("log_evidence", JSONB, nullable=True),
        sa.Column("recommended_actions", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_deep_findings_run", "deep_findings", ["test_run_id"])

    # ── release_decisions ─────────────────────────────────────────
    op.create_table(
        "release_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("recommendation", sa.String(20), nullable=False),
        sa.Column("risk_score", sa.Integer, nullable=False, server_default="50"),
        sa.Column("blocking_issues", JSONB, nullable=True),
        sa.Column("conditions_for_go", JSONB, nullable=True),
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("human_override", sa.Text, nullable=True),
        sa.Column("overridden_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), onupdate=sa.text("now()")),
    )

    # ── contract_violations ───────────────────────────────────────
    op.create_table(
        "contract_violations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=True),
        sa.Column("violation_type", sa.String(50), nullable=False),
        sa.Column("field_path", sa.String(500), nullable=True),
        sa.Column("expected", sa.String(500), nullable=True),
        sa.Column("actual", sa.String(500), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="'warning'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_contract_violations_run", "contract_violations", ["test_run_id"])
    op.create_index("ix_contract_violations_tc", "contract_violations", ["test_case_id"])


def downgrade() -> None:
    op.drop_table("contract_violations")
    op.drop_table("release_decisions")
    op.drop_table("deep_findings")
    op.drop_table("failure_clusters")
