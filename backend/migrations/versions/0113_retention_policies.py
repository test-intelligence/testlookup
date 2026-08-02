"""project_retention_policies + provenance retention re-anchoring

Revision ID: 0113
Revises: 0112
Create Date: 2026-08-02

PMF US-11.4 — per-project retention policies with a scheduled cross-store
purge (Postgres / Mongo / MinIO).

1. New ``project_retention_policies`` table — one row per project; a
   missing row resolves to the code defaults in
   ``retention_service.EffectiveRetentionPolicy`` (disabled; raw events
   90 d, runs 365 d, artifacts 180 d, audit 2555 d). Server defaults
   exactly match the ORM/Python defaults (the #433 drift lesson).

2. ``ai_provenance_records`` re-anchored as AUDIT-class data:

   * ``run_id`` FK flipped ``CASCADE`` → ``SET NULL`` — provenance must
     OUTLIVE the run it describes; deleting a run on the runs clock now
     detaches the pointer instead of destroying the audit trail.
   * New nullable ``project_id`` FK (CASCADE with the project itself),
     backfilled from ``test_runs`` via ``run_id`` — the purge deletes
     provenance on the AUDIT clock scoped by this column.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Per-project retention policy rows ─────────────────────────────
    op.create_table(
        "project_retention_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "raw_events_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("90"),
        ),
        sa.Column(
            "runs_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("365"),
        ),
        sa.Column(
            "artifacts_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("180"),
        ),
        sa.Column(
            "audit_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2555"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("project_id", name="uq_project_retention_policies_project"),
    )
    op.create_index(
        "ix_project_retention_policies_project",
        "project_retention_policies",
        ["project_id"],
        unique=True,
    )

    # ── 2. ai_provenance_records → audit-class retention ─────────────────
    # run_id: CASCADE → SET NULL (column was already nullable in 0026).
    op.drop_constraint(
        "ai_provenance_records_run_id_fkey",
        "ai_provenance_records",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "ai_provenance_records_run_id_fkey",
        "ai_provenance_records",
        "test_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # project_id scope column + backfill from the (still intact) run link.
    op.add_column(
        "ai_provenance_records",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "ai_provenance_records_project_id_fkey",
        "ai_provenance_records",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(
        "UPDATE ai_provenance_records AS apr "
        "SET project_id = tr.project_id "
        "FROM test_runs AS tr "
        "WHERE apr.run_id = tr.id AND apr.project_id IS NULL"
    )
    op.create_index(
        "ix_provenance_project", "ai_provenance_records", ["project_id"],
    )


def downgrade() -> None:
    # Provenance: drop the project scope and restore the CASCADE run FK.
    op.drop_index("ix_provenance_project", table_name="ai_provenance_records")
    op.drop_constraint(
        "ai_provenance_records_project_id_fkey",
        "ai_provenance_records",
        type_="foreignkey",
    )
    op.drop_column("ai_provenance_records", "project_id")
    op.drop_constraint(
        "ai_provenance_records_run_id_fkey",
        "ai_provenance_records",
        type_="foreignkey",
    )
    # Rows whose run was purged while the SET NULL FK was live keep
    # run_id NULL — restoring CASCADE is safe for them (nothing to cascade).
    op.create_foreign_key(
        "ai_provenance_records_run_id_fkey",
        "ai_provenance_records",
        "test_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index(
        "ix_project_retention_policies_project",
        table_name="project_retention_policies",
    )
    op.drop_table("project_retention_policies")
