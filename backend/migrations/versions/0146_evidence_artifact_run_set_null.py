"""H1 — stop the run CASCADE from destroying protected evidence artifacts.

``retention_service`` deliberately spares evidence artifacts that a **published
decision report** still references: ``_published_report_artifact_ids`` filters
them out of the explicit ``delete(EvidenceArtifact)``, and fails closed so an
error protects everything rather than nothing.

Two steps later the purge runs ``delete(TestRun)``, and
``evidence_artifacts.run_id`` was ``ondelete="CASCADE", nullable=False`` — so
the CASCADE destroyed exactly what the filter had spared. With the shipped
defaults (``artifacts_days=180`` < ``runs_days=365``) that fires on every
project holding runs older than a year, silently, and the purge's
``evidence_artifact_rows`` count under-reports it because the count is taken
after the protective filter.

The same CASCADE also made the artifacts clock behave as
``min(artifacts_days, runs_days)``: an artifact younger than its own retention
window died anyway when its run aged out, which contradicts having two clocks.

All three run-owned parent links must use SET NULL. Deleting a run also deletes
its ``test_cases`` and ``agent_pipeline_runs``; leaving either artifact foreign
key on CASCADE would still destroy the row through that alternate path.
``retention_service`` stamps ``project_id`` first so a survivor stays scoped.

Verified artifacts also have immutable/scope-validation UPDATE triggers. The
foreign-key SET NULL actions fire those triggers, so this migration narrows
their exception to parent-link detachment only. Content and authority fields
remain immutable.

This migration follows the user-dismissal and feature-flag seed in 0145.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0146"
down_revision = "0145"
branch_labels = None
depends_on = None


_FK = "evidence_artifacts_run_id_fkey"
_TEST_CASE_FK = "evidence_artifacts_test_case_id_fkey"
_PIPELINE_FK = "fk_evidence_pipeline"


def _allow_parent_detachment() -> None:
    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION prevent_verified_evidence_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.integrity_status = 'verified' THEN
            IF NOT (
              (to_jsonb(NEW) - ARRAY['run_id', 'test_case_id', 'producer_pipeline_run_id'])
                =
              (to_jsonb(OLD) - ARRAY['run_id', 'test_case_id', 'producer_pipeline_run_id'])
              AND (
                NEW.run_id IS NOT DISTINCT FROM OLD.run_id
                OR (
                  OLD.run_id IS NOT NULL AND NEW.run_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM test_runs WHERE id = OLD.run_id)
                )
              )
              AND (
                NEW.test_case_id IS NOT DISTINCT FROM OLD.test_case_id
                OR (
                  OLD.test_case_id IS NOT NULL AND NEW.test_case_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM test_cases WHERE id = OLD.test_case_id
                  )
                )
              )
              AND (
                NEW.producer_pipeline_run_id
                  IS NOT DISTINCT FROM OLD.producer_pipeline_run_id
                OR (
                  OLD.producer_pipeline_run_id IS NOT NULL
                  AND NEW.producer_pipeline_run_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_pipeline_runs
                    WHERE id = OLD.producer_pipeline_run_id
                  )
                )
              )
              AND (
                NEW.run_id IS DISTINCT FROM OLD.run_id
                OR NEW.test_case_id IS DISTINCT FROM OLD.test_case_id
                OR NEW.producer_pipeline_run_id
                  IS DISTINCT FROM OLD.producer_pipeline_run_id
              )
            ) THEN
              RAISE EXCEPTION 'verified evidence artifacts are immutable';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    )
    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION validate_evidence_artifact_scope()
        RETURNS trigger AS $$
        BEGIN
          -- The preceding immutability trigger admits only a pure link detach
          -- after its parent has actually been deleted.
          IF TG_OP = 'UPDATE' AND OLD.integrity_status = 'verified'
             AND (
               NEW.run_id IS NULL
               OR NEW.test_case_id IS NULL
               OR NEW.producer_pipeline_run_id IS NULL
             ) THEN
            RETURN NEW;
          END IF;
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
    """)
    )


def _restore_strict_verified_triggers() -> None:
    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION prevent_verified_evidence_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.integrity_status = 'verified' THEN
            RAISE EXCEPTION 'verified evidence artifacts are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    )
    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION validate_evidence_artifact_scope()
        RETURNS trigger AS $$
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
    """)
    )


def upgrade() -> None:
    # Metadata-only; no table rewrite.
    op.alter_column(
        "evidence_artifacts",
        "run_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    _allow_parent_detachment()

    # Re-point every run-owned parent FK. Added NOT VALID first so the ACCESS
    # EXCLUSIVE lock is
    # held only for the catalog update rather than for a full scan of what can
    # be a large table; VALIDATE then takes the weaker SHARE UPDATE EXCLUSIVE.
    # Every existing row already satisfies the constraint (it was enforced
    # until a moment ago), so validation cannot fail — it just costs a read.
    constraints = (
        (_FK, "run_id", "test_runs"),
        (_TEST_CASE_FK, "test_case_id", "test_cases"),
        (_PIPELINE_FK, "producer_pipeline_run_id", "agent_pipeline_runs"),
    )
    for name, column, parent in constraints:
        op.drop_constraint(name, "evidence_artifacts", type_="foreignkey")
        op.execute(
            sa.text(
                f'ALTER TABLE evidence_artifacts ADD CONSTRAINT "{name}" '
                f"FOREIGN KEY ({column}) REFERENCES {parent}(id) "
                "ON DELETE SET NULL NOT VALID"
            )
        )
    for name, _, _ in constraints:
        op.execute(
            sa.text(f'ALTER TABLE evidence_artifacts VALIDATE CONSTRAINT "{name}"')
        )


def downgrade() -> None:
    # Rows whose run was purged while SET NULL was in force cannot satisfy the
    # restored NOT NULL. Deleting them would destroy the very evidence this
    # migration exists to preserve, so they are detached from the constraint
    # instead: the downgrade fails loudly if any exist, and the operator
    # decides. A downgrade that silently drops protected audit evidence would
    # be worse than one that refuses.
    conn = op.get_bind()
    orphaned = conn.execute(
        sa.text("SELECT count(*) FROM evidence_artifacts WHERE run_id IS NULL")
    ).scalar()
    if orphaned:
        raise RuntimeError(
            f"{orphaned} evidence_artifacts row(s) have run_id IS NULL — their "
            "runs were purged while SET NULL was in force. Restoring NOT NULL "
            "would require deleting them, which is the data loss 0146 fixed. "
            "Re-home or export them first, then re-run this downgrade."
        )

    op.drop_constraint(_FK, "evidence_artifacts", type_="foreignkey")
    op.drop_constraint(_TEST_CASE_FK, "evidence_artifacts", type_="foreignkey")
    op.drop_constraint(_PIPELINE_FK, "evidence_artifacts", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "evidence_artifacts",
        "test_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        _TEST_CASE_FK,
        "evidence_artifacts",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        _PIPELINE_FK,
        "evidence_artifacts",
        "agent_pipeline_runs",
        ["producer_pipeline_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "evidence_artifacts",
        "run_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    _restore_strict_verified_triggers()
