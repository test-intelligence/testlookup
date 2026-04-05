"""Add evidence_artifacts and ai_provenance_records tables.

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Evidence Artifacts — reusable evidence items linked to runs/clusters ──
    op.create_table(
        "evidence_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", sa.String(20), nullable=True),
        sa.Column("test_case_id", UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("artifact_type", sa.String(50), nullable=False),     # stack_trace | log_anomaly | api_contract | metric | build_change | config_diff
        sa.Column("source_system", sa.String(100), nullable=False),    # splunk | mongodb | prometheus | github | ocp | chromadb
        sa.Column("uri_or_ref", sa.String(1000), nullable=True),       # link to source
        sa.Column("summary_excerpt", sa.Text(), nullable=True),         # short human-readable excerpt
        sa.Column("relevance_score", sa.Float(), nullable=True),        # 0-1 relevance to the conclusion
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_evidence_run_id", "evidence_artifacts", ["run_id"])
    op.create_index("ix_evidence_cluster", "evidence_artifacts", ["cluster_id"])

    # ── AI Provenance Records — tracks what model/method produced each conclusion ─
    op.create_table(
        "ai_provenance_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),       # run_summary | cluster_analysis | release_decision | defect_candidate
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),    # ID of the entity this provenance belongs to
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("model_name", sa.String(200), nullable=True),        # e.g. "qwen2.5:7b", "gpt-4o", "deterministic"
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),           # 0-100
        sa.Column("confidence_reason", sa.Text(), nullable=True),       # human-readable explanation
        sa.Column("evidence_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("sources_used", sa.JSON(), nullable=True),            # ["splunk", "stacktrace", "chromadb"]
        sa.Column("deterministic_checks_used", sa.JSON(), nullable=True),  # ["flaky_detection", "regression_classification"]
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_provenance_entity", "ai_provenance_records", ["entity_type", "entity_id"])
    op.create_index("ix_provenance_run", "ai_provenance_records", ["run_id"])


def downgrade() -> None:
    op.drop_table("ai_provenance_records")
    op.drop_table("evidence_artifacts")
