"""Add durable parent/child agent runtime scheduling fields.

Revision ID: 0121
Revises: 0120
"""
import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql


revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


_ACTIVE_STATUSES_SQL = "status IN ('queued', 'running', 'synthesizing')"


def upgrade() -> None:
    # Fail with an actionable message before the unique constraint build. The
    # migration intentionally does not pick a winner or delete historical AI
    # evidence.
    if not context.is_offline_mode():
        bind = op.get_bind()
        duplicates = bind.execute(sa.text(
            "SELECT pipeline_run_id, cluster_id, count(*) AS duplicate_count "
            "FROM failure_clusters WHERE pipeline_run_id IS NOT NULL "
            "GROUP BY pipeline_run_id, cluster_id HAVING count(*) > 1 LIMIT 1"
        )).first()
        if duplicates is not None:
            raise RuntimeError(
                "0121 requires unique failure_clusters(pipeline_run_id, cluster_id); "
                "resolve duplicate historical rows before upgrade"
            )
    op.create_unique_constraint(
        "uq_failure_clusters_pipeline_cluster",
        "failure_clusters",
        ["pipeline_run_id", "cluster_id"],
    )

    op.add_column(
        "agent_pipeline_runs",
        sa.Column("parent_pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_pipeline_runs",
        sa.Column("parent_task_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_pipeline_runs",
        sa.Column("spawn_depth", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_agent_pipeline_parent_pipeline",
        "agent_pipeline_runs",
        "agent_pipeline_runs",
        ["parent_pipeline_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_agent_pipeline_spawn_depth",
        "agent_pipeline_runs",
        "spawn_depth >= 0 AND spawn_depth <= 8",
    )

    op.add_column("agent_stage_results", sa.Column("task_key", sa.String(length=255)))
    op.add_column("agent_stage_results", sa.Column("capability_id", sa.String(length=120)))
    op.add_column("agent_stage_results", sa.Column("parent_task_key", sa.String(length=255)))
    op.add_column(
        "agent_stage_results",
        sa.Column("failure_cluster_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column("selected", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column("required", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "agent_stage_results",
        sa.Column(
            "dependencies", sa.JSON(), nullable=True
        ),
    )
    op.add_column("agent_stage_results", sa.Column("allocated_budget", sa.JSON()))
    op.add_column("agent_stage_results", sa.Column("stop_reason", sa.String(length=100)))
    op.add_column("agent_stage_results", sa.Column("idempotency_key", sa.String(length=64)))
    op.add_column(
        "agent_stage_results",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_foreign_key(
        "fk_agent_stage_failure_cluster",
        "agent_stage_results",
        "failure_clusters",
        ["failure_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_agent_stage_attempt_positive", "agent_stage_results", "attempt >= 1"
    )
    op.create_check_constraint(
        "ck_agent_stage_idempotency_sha256",
        "agent_stage_results",
        "idempotency_key IS NULL OR idempotency_key ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        "ux_agent_stage_pipeline_task_key",
        "agent_stage_results",
        ["pipeline_run_id", "task_key"],
        unique=True,
        postgresql_where=sa.text("task_key IS NOT NULL"),
    )
    op.create_index(
        "ux_agent_stage_pipeline_idempotency",
        "agent_stage_results",
        ["pipeline_run_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.drop_index(
        "uq_agent_investigations_one_active_per_run",
        table_name="agent_investigations",
    )
    op.add_column(
        "agent_investigations",
        sa.Column("scope_type", sa.String(length=20), nullable=False, server_default="run"),
    )
    op.add_column(
        "agent_investigations",
        sa.Column("failure_cluster_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "agent_investigations", sa.Column("cluster_scope_sha256", sa.String(length=64))
    )
    op.add_column(
        "agent_investigations",
        sa.Column(
            "cluster_member_test_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "agent_investigations",
        sa.Column("parent_pipeline_run_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "agent_investigations", sa.Column("parent_task_id", sa.String(length=255))
    )
    op.add_column(
        "agent_investigations",
        sa.Column("spawn_lineage_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "agent_investigations",
        sa.Column("spawn_depth", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_investigations", sa.Column("spawn_key", sa.String(length=64))
    )
    op.add_column("agent_investigations", sa.Column("selection_reason", sa.Text()))
    op.create_foreign_key(
        "fk_agent_investigation_failure_cluster",
        "agent_investigations",
        "failure_clusters",
        ["failure_cluster_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_investigation_parent_pipeline",
        "agent_investigations",
        "agent_pipeline_runs",
        ["parent_pipeline_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_agent_investigation_scope_type",
        "agent_investigations",
        "scope_type IN ('run', 'failure_cluster')",
    )
    op.create_check_constraint(
        "ck_agent_investigation_spawn_depth",
        "agent_investigations",
        "spawn_depth >= 0 AND spawn_depth <= 8",
    )
    op.create_check_constraint(
        "ck_agent_investigation_cluster_scope_sha256",
        "agent_investigations",
        "cluster_scope_sha256 IS NULL OR cluster_scope_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_agent_investigation_spawn_key_sha256",
        "agent_investigations",
        "spawn_key IS NULL OR spawn_key ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_agent_investigation_cluster_members_array",
        "agent_investigations",
        "jsonb_typeof(cluster_member_test_ids) = 'array'",
    )
    op.create_check_constraint(
        "ck_agent_investigation_scope_consistency",
        "agent_investigations",
        "(scope_type = 'run' AND failure_cluster_id IS NULL "
        "AND cluster_scope_sha256 IS NULL AND parent_pipeline_run_id IS NULL "
        "AND parent_task_id IS NULL AND spawn_lineage_id IS NULL "
        "AND spawn_key IS NULL AND spawn_depth = 0 "
        "AND jsonb_array_length(cluster_member_test_ids) = 0) "
        "OR (scope_type = 'failure_cluster' AND failure_cluster_id IS NOT NULL "
        "AND cluster_scope_sha256 IS NOT NULL AND parent_pipeline_run_id IS NOT NULL "
        "AND parent_task_id IS NOT NULL AND length(parent_task_id) > 0 "
        "AND spawn_lineage_id IS NOT NULL AND spawn_key IS NOT NULL "
        "AND spawn_depth >= 1 AND jsonb_array_length(cluster_member_test_ids) > 0)",
    )
    op.create_index(
        "uq_agent_investigations_active_run_scope",
        "agent_investigations",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope_type = 'run' AND " + _ACTIVE_STATUSES_SQL
        ),
    )
    op.create_index(
        "uq_agent_investigations_active_failure_cluster",
        "agent_investigations",
        ["parent_pipeline_run_id", "failure_cluster_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope_type = 'failure_cluster' AND " + _ACTIVE_STATUSES_SQL
        ),
    )
    op.create_index(
        "ux_agent_investigations_spawn_key",
        "agent_investigations",
        ["spawn_key"],
        unique=True,
        postgresql_where=sa.text("spawn_key IS NOT NULL"),
    )

    op.create_table(
        "agent_child_dispatch_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("spawn_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_investigations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "parent_pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint("spawn_key ~ '^[0-9a-f]{64}$'", name="ck_agent_child_outbox_spawn_key_sha256"),
        sa.CheckConstraint("attempts >= 0", name="ck_agent_child_outbox_attempts_nonnegative"),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')",
            name="ck_agent_child_outbox_status",
        ),
    )
    op.create_index(
        "ix_agent_child_dispatch_status_next_attempt",
        "agent_child_dispatch_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_child_dispatch_status_next_attempt",
        table_name="agent_child_dispatch_outbox",
    )
    op.drop_table("agent_child_dispatch_outbox")

    op.drop_index("ux_agent_investigations_spawn_key", table_name="agent_investigations")
    op.drop_index(
        "uq_agent_investigations_active_failure_cluster",
        table_name="agent_investigations",
    )
    op.drop_index(
        "uq_agent_investigations_active_run_scope",
        table_name="agent_investigations",
    )
    for constraint in (
        "ck_agent_investigation_scope_consistency",
        "ck_agent_investigation_cluster_members_array",
        "ck_agent_investigation_spawn_key_sha256",
        "ck_agent_investigation_cluster_scope_sha256",
        "ck_agent_investigation_spawn_depth",
        "ck_agent_investigation_scope_type",
    ):
        op.drop_constraint(constraint, "agent_investigations", type_="check")
    op.drop_constraint(
        "fk_agent_investigation_parent_pipeline",
        "agent_investigations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_investigation_failure_cluster",
        "agent_investigations",
        type_="foreignkey",
    )
    for column in (
        "selection_reason",
        "spawn_key",
        "spawn_depth",
        "spawn_lineage_id",
        "parent_task_id",
        "parent_pipeline_run_id",
        "cluster_member_test_ids",
        "cluster_scope_sha256",
        "failure_cluster_id",
        "scope_type",
    ):
        op.drop_column("agent_investigations", column)
    op.create_index(
        "uq_agent_investigations_one_active_per_run",
        "agent_investigations",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_STATUSES_SQL),
    )

    op.drop_index(
        "ux_agent_stage_pipeline_idempotency", table_name="agent_stage_results"
    )
    op.drop_index("ux_agent_stage_pipeline_task_key", table_name="agent_stage_results")
    op.drop_constraint(
        "ck_agent_stage_idempotency_sha256", "agent_stage_results", type_="check"
    )
    op.drop_constraint(
        "ck_agent_stage_attempt_positive", "agent_stage_results", type_="check"
    )
    op.drop_constraint(
        "fk_agent_stage_failure_cluster", "agent_stage_results", type_="foreignkey"
    )
    for column in (
        "lease_expires_at",
        "idempotency_key",
        "stop_reason",
        "allocated_budget",
        "dependencies",
        "required",
        "selected",
        "attempt",
        "failure_cluster_id",
        "parent_task_key",
        "capability_id",
        "task_key",
    ):
        op.drop_column("agent_stage_results", column)

    op.drop_constraint(
        "ck_agent_pipeline_spawn_depth", "agent_pipeline_runs", type_="check"
    )
    op.drop_constraint(
        "fk_agent_pipeline_parent_pipeline",
        "agent_pipeline_runs",
        type_="foreignkey",
    )
    for column in ("spawn_depth", "parent_task_id", "parent_pipeline_run_id"):
        op.drop_column("agent_pipeline_runs", column)

    op.drop_constraint(
        "uq_failure_clusters_pipeline_cluster", "failure_clusters", type_="unique"
    )
