from pathlib import Path

from app.models.postgres import (
    AgentChildDispatchOutbox,
    AgentInvestigation,
    AgentPipelineRun,
    AgentStageResult,
    FailureCluster,
)


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/0121_agentic_child_runtime_schema.py"
)


def test_migration_0121_chains_from_0120_and_is_reversible():
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0121"' in source
    assert 'down_revision = "0120"' in source
    for marker in (
        "uq_failure_clusters_pipeline_cluster",
        "fk_agent_pipeline_parent_pipeline",
        "ux_agent_stage_pipeline_task_key",
        "ux_agent_stage_pipeline_idempotency",
        "uq_agent_investigations_active_run_scope",
        "uq_agent_investigations_active_failure_cluster",
        "ck_agent_investigation_scope_consistency",
        "agent_child_dispatch_outbox",
        "ix_agent_child_dispatch_status_next_attempt",
    ):
        assert marker in source

    assert "def downgrade() -> None:" in source
    assert "resolve duplicate historical rows before upgrade" in source
    assert source.index('op.drop_table("agent_child_dispatch_outbox")') < source.index(
        'op.drop_column("agent_investigations", column)'
    )
    assert 'ondelete="SET NULL"' in source
    assert 'ondelete="CASCADE"' in source


def test_agent_pipeline_and_stage_orm_match_runtime_columns():
    pipeline_columns = AgentPipelineRun.__table__.columns
    for name in ("parent_pipeline_run_id", "parent_task_id", "spawn_depth"):
        assert name in pipeline_columns

    stage_columns = AgentStageResult.__table__.columns
    for name in (
        "task_key",
        "capability_id",
        "parent_task_key",
        "failure_cluster_id",
        "attempt",
        "selected",
        "required",
        "dependencies",
        "allocated_budget",
        "stop_reason",
        "idempotency_key",
        "lease_expires_at",
    ):
        assert name in stage_columns

    stage_indexes = {index.name: index for index in AgentStageResult.__table__.indexes}
    assert stage_indexes["ux_agent_stage_pipeline_task_key"].unique is True
    assert stage_indexes["ux_agent_stage_pipeline_idempotency"].unique is True


def test_investigation_scope_and_cluster_identity_orm_match_migration():
    columns = AgentInvestigation.__table__.columns
    for name in (
        "scope_type",
        "failure_cluster_id",
        "cluster_scope_sha256",
        "cluster_member_test_ids",
        "parent_pipeline_run_id",
        "parent_task_id",
        "spawn_lineage_id",
        "spawn_depth",
        "spawn_key",
        "selection_reason",
    ):
        assert name in columns

    investigation_indexes = {
        index.name: index for index in AgentInvestigation.__table__.indexes
    }
    assert investigation_indexes["uq_agent_investigations_active_run_scope"].unique
    assert investigation_indexes[
        "uq_agent_investigations_active_failure_cluster"
    ].unique
    assert investigation_indexes["ux_agent_investigations_spawn_key"].unique

    cluster_constraints = {
        constraint.name for constraint in FailureCluster.__table__.constraints
    }
    assert "uq_failure_clusters_pipeline_cluster" in cluster_constraints


def test_child_dispatch_outbox_orm_has_durable_retry_identity():
    columns = AgentChildDispatchOutbox.__table__.columns
    for name in (
        "spawn_key",
        "investigation_id",
        "parent_pipeline_run_id",
        "project_id",
        "run_id",
        "status",
        "attempts",
        "next_attempt_at",
        "sent_at",
        "created_at",
        "updated_at",
        "last_error",
    ):
        assert name in columns

    assert columns.spawn_key.unique is True
    assert columns.investigation_id.unique is True
    indexes = {index.name for index in AgentChildDispatchOutbox.__table__.indexes}
    assert "ix_agent_child_dispatch_status_next_attempt" in indexes
