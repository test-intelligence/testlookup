"""Add tenant-bound immutable evidence artifact authority.

Revision ID: 0120
Revises: 0119
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "evidence_artifacts_test_case_id_fkey", "evidence_artifacts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "evidence_artifacts_test_case_id_fkey", "evidence_artifacts", "test_cases",
        ["test_case_id"], ["id"], ondelete="CASCADE",
    )
    op.add_column("evidence_artifacts", sa.Column(
        "project_id", postgresql.UUID(as_uuid=True), nullable=True
    ))
    op.add_column("evidence_artifacts", sa.Column(
        "producer_pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True
    ))
    op.add_column("evidence_artifacts", sa.Column(
        "schema_version", sa.Integer(), nullable=False, server_default="2"
    ))
    op.add_column("evidence_artifacts", sa.Column("source_version", sa.String(100)))
    op.add_column("evidence_artifacts", sa.Column("content_sha256", sa.String(64)))
    op.add_column("evidence_artifacts", sa.Column("content_size_bytes", sa.BigInteger()))
    op.add_column("evidence_artifacts", sa.Column("media_type", sa.String(100)))
    op.add_column("evidence_artifacts", sa.Column("sensitivity", sa.String(20)))
    op.add_column("evidence_artifacts", sa.Column("freshness", sa.String(20)))
    op.add_column("evidence_artifacts", sa.Column(
        "observed_at", sa.DateTime(timezone=True)
    ))
    op.add_column("evidence_artifacts", sa.Column(
        "integrity_status", sa.String(30), nullable=False,
        server_default="legacy_unverified",
    ))
    op.add_column("evidence_artifacts", sa.Column(
        "retention_class", sa.String(30), nullable=False, server_default="artifacts"
    ))
    op.add_column("evidence_artifacts", sa.Column("idempotency_key", sa.String(64)))
    op.execute(sa.text("""
        UPDATE evidence_artifacts ea
        SET project_id = tr.project_id
        FROM test_runs tr
        WHERE tr.id = ea.run_id AND ea.project_id IS NULL
    """))
    op.create_foreign_key(
        "fk_evidence_project", "evidence_artifacts", "projects",
        ["project_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_evidence_pipeline", "evidence_artifacts", "agent_pipeline_runs",
        ["producer_pipeline_run_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_evidence_project_run", "evidence_artifacts", ["project_id", "run_id"])
    op.create_index("ix_evidence_run_test", "evidence_artifacts", ["run_id", "test_case_id"])
    op.create_index("ix_evidence_pipeline", "evidence_artifacts", ["producer_pipeline_run_id"])
    op.create_index(
        "ux_evidence_idempotency", "evidence_artifacts", ["idempotency_key"], unique=True
    )
    op.create_check_constraint(
        "ck_evidence_content_sha256", "evidence_artifacts",
        "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_evidence_content_size_nonnegative", "evidence_artifacts",
        "content_size_bytes IS NULL OR content_size_bytes >= 0",
    )
    op.execute(sa.text("""
        CREATE FUNCTION validate_evidence_artifact_scope() RETURNS trigger AS $$
        BEGIN
          IF NEW.integrity_status = 'verified' THEN
            IF NEW.project_id IS NULL OR NEW.producer_pipeline_run_id IS NULL
               OR NEW.test_case_id IS NULL OR NEW.content_sha256 IS NULL
               OR NEW.idempotency_key IS NULL OR NEW.uri_or_ref IS NOT NULL THEN
              RAISE EXCEPTION 'verified evidence artifact is incomplete';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM test_runs
              WHERE id = NEW.run_id AND project_id = NEW.project_id
            ) THEN RAISE EXCEPTION 'evidence run/project mismatch'; END IF;
            IF NOT EXISTS (
              SELECT 1 FROM test_cases
              WHERE id = NEW.test_case_id AND test_run_id = NEW.run_id
            ) THEN RAISE EXCEPTION 'evidence test/run mismatch'; END IF;
            IF NOT EXISTS (
              SELECT 1 FROM agent_pipeline_runs
              WHERE id = NEW.producer_pipeline_run_id AND test_run_id = NEW.run_id
            ) THEN RAISE EXCEPTION 'evidence pipeline/run mismatch'; END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_validate_evidence_artifact_scope
        BEFORE INSERT OR UPDATE ON evidence_artifacts
        FOR EACH ROW EXECUTE FUNCTION validate_evidence_artifact_scope();
    """))
    op.execute(sa.text("""
        CREATE FUNCTION prevent_verified_evidence_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.integrity_status = 'verified' THEN
            RAISE EXCEPTION 'verified evidence artifacts are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_prevent_verified_evidence_mutation
        BEFORE UPDATE ON evidence_artifacts
        FOR EACH ROW EXECUTE FUNCTION prevent_verified_evidence_mutation();
    """))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_verified_evidence_mutation ON evidence_artifacts")
    op.execute("DROP FUNCTION IF EXISTS prevent_verified_evidence_mutation")
    op.execute("DROP TRIGGER IF EXISTS trg_validate_evidence_artifact_scope ON evidence_artifacts")
    op.execute("DROP FUNCTION IF EXISTS validate_evidence_artifact_scope")
    op.drop_constraint("ck_evidence_content_size_nonnegative", "evidence_artifacts", type_="check")
    op.drop_constraint("ck_evidence_content_sha256", "evidence_artifacts", type_="check")
    op.drop_index("ux_evidence_idempotency", table_name="evidence_artifacts")
    op.drop_index("ix_evidence_pipeline", table_name="evidence_artifacts")
    op.drop_index("ix_evidence_run_test", table_name="evidence_artifacts")
    op.drop_index("ix_evidence_project_run", table_name="evidence_artifacts")
    op.drop_constraint("fk_evidence_pipeline", "evidence_artifacts", type_="foreignkey")
    op.drop_constraint("fk_evidence_project", "evidence_artifacts", type_="foreignkey")
    op.drop_constraint(
        "evidence_artifacts_test_case_id_fkey", "evidence_artifacts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "evidence_artifacts_test_case_id_fkey", "evidence_artifacts", "test_cases",
        ["test_case_id"], ["id"], ondelete="SET NULL",
    )
    for column in (
        "idempotency_key", "retention_class", "integrity_status", "observed_at",
        "freshness", "sensitivity", "media_type", "content_size_bytes",
        "content_sha256", "source_version", "schema_version",
        "producer_pipeline_run_id", "project_id",
    ):
        op.drop_column("evidence_artifacts", column)
