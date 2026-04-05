"""Add performance indexes for high-volume queries.

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-24
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── test_cases ─────────────────────────────────────────────────────────
    # High-frequency filter: status lookups per run (ingestion + anomaly agent)
    op.create_index("ix_test_cases_run_status", "test_cases", ["test_run_id", "status"], if_not_exists=True)
    # Fingerprint lookups for flakiness detection
    op.create_index("ix_test_cases_fingerprint", "test_cases", ["test_fingerprint"], if_not_exists=True)
    # Duration-sorted queries for performance regression detection
    op.create_index("ix_test_cases_run_duration", "test_cases", ["test_run_id", "duration_ms"], if_not_exists=True)

    # ── test_runs ───────────────────────────────────────────────────────────
    # Project + start_time for ordered run history (most common dashboard query)
    op.create_index("ix_test_runs_project_start", "test_runs", ["project_id", "start_time"], if_not_exists=True)
    # Build number lookups for idempotent ingestion upserts
    op.create_index("ix_test_runs_project_build", "test_runs", ["project_id", "build_number"], if_not_exists=True)

    # ── test_case_history ───────────────────────────────────────────────────
    # Baseline lookups for anomaly detection (fingerprint + run ordering)
    op.create_index("ix_history_fingerprint_run", "test_case_history", ["test_fingerprint", "test_run_id"], if_not_exists=True)

    # ── ai_analysis ─────────────────────────────────────────────────────────
    # Run-level AI analysis lookups
    op.create_index("ix_ai_analyses_run", "ai_analysis", ["test_case_id"], if_not_exists=True)

    # ── agent_pipeline_runs ─────────────────────────────────────────────────
    # Ordered pipeline list per run
    op.create_index("ix_pipeline_runs_test_run", "agent_pipeline_runs", ["test_run_id"], if_not_exists=True)
    # Status filtering (running pipelines for live dashboard)
    op.create_index("ix_pipeline_runs_status", "agent_pipeline_runs", ["status"], if_not_exists=True)

    # ── agent_stage_results ─────────────────────────────────────────────────
    # Stage lookups per pipeline
    op.create_index("ix_stage_results_pipeline", "agent_stage_results", ["pipeline_run_id"], if_not_exists=True)

    # ── chat_messages ────────────────────────────────────────────────────────
    # Message history per session ordered by creation time
    op.create_index("ix_chat_messages_session_created", "chat_messages", ["session_id", "created_at"], if_not_exists=True)

    # ── defects ──────────────────────────────────────────────────────────────
    # Project-level defect lookups for triage agent
    op.create_index("ix_defects_project", "defects", ["project_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_defects_project", table_name="defects")
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_index("ix_stage_results_pipeline", table_name="agent_stage_results")
    op.drop_index("ix_pipeline_runs_status", table_name="agent_pipeline_runs")
    op.drop_index("ix_pipeline_runs_test_run", table_name="agent_pipeline_runs")
    op.drop_index("ix_ai_analyses_run", table_name="ai_analysis")
    op.drop_index("ix_history_fingerprint_run", table_name="test_case_history")
    op.drop_index("ix_test_runs_project_build", table_name="test_runs")
    op.drop_index("ix_test_runs_project_start", table_name="test_runs")
    op.drop_index("ix_test_cases_run_duration", table_name="test_cases")
    op.drop_index("ix_test_cases_fingerprint", table_name="test_cases")
    op.drop_index("ix_test_cases_run_status", table_name="test_cases")
