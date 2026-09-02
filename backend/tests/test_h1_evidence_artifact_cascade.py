"""H1 — schema and ordering guards that run without a database.

The behavioural proof lives in
``tests/integration/test_protected_artifact_survives_run_cascade.py``, which
needs real Postgres because no mocked session can observe a foreign-key
CASCADE. That test skips wherever a DSN is not configured — which is most CI
runs — so these guards exist to catch a regression there instead of silently
passing.

They assert the two things that made the defect possible: the foreign key's
delete rule, and the fact that the project-scope stamp has to happen BEFORE the
run delete rather than after it.
"""

from __future__ import annotations

import inspect

from app.models.postgres import EvidenceArtifact
from app.services import retention_service


def _ondelete(column: str) -> str | None:
    return next(iter(EvidenceArtifact.__table__.c[column].foreign_keys)).ondelete


def test_evidence_artifact_run_link_detaches_rather_than_cascades():
    """The whole defect in one assertion.

    While this was ``CASCADE``, the run delete destroyed exactly the artifacts
    ``_published_report_artifact_ids`` had deliberately spared — and the
    purge's own count could not see it, because the count is taken after the
    protective filter.
    """
    assert _ondelete("run_id") == "SET NULL", (
        "evidence_artifacts.run_id must SET NULL: with CASCADE the run delete "
        "silently undoes the purge's published-report protection"
    )


def test_every_run_owned_parent_link_detaches_rather_than_cascades():
    """A run deletion also cascades its test-case and pipeline parents."""
    assert _ondelete("run_id") == "SET NULL"
    assert _ondelete("test_case_id") == "SET NULL"
    assert _ondelete("producer_pipeline_run_id") == "SET NULL"


def test_the_run_link_is_nullable_so_the_row_can_outlive_its_run():
    """SET NULL on a NOT NULL column is a constraint violation, not a fix."""
    assert EvidenceArtifact.__table__.c.run_id.nullable is True


def test_project_scope_is_stamped_before_the_run_delete_not_after():
    """Order is the load-bearing part.

    Once ``run_id`` detaches, an artifact whose ``project_id`` is also NULL is
    unreachable by every project-scoped sweep — so the fix for one leak would
    have created another. Stamping after the delete would be too late, and
    would still pass a test that only checked the stamp exists.
    """
    source = inspect.getsource(retention_service.run_purge)

    stamp = source.find("update(EvidenceArtifact)")
    run_delete = source.find("delete(TestRun)")

    assert stamp != -1, "run_purge must stamp EvidenceArtifact.project_id"
    assert run_delete != -1, "could not locate the run delete — test proved nothing"
    assert stamp < run_delete, (
        "the project_id stamp must run BEFORE delete(TestRun); afterwards the "
        "run link is already NULL and the surviving rows are unscoped forever"
    )


def test_the_stamp_only_fills_missing_scope():
    """It must not overwrite a project_id a writer already set."""
    source = inspect.getsource(retention_service.run_purge)
    stamp_block = source.split("update(EvidenceArtifact)", 1)[1][:400]

    assert "project_id.is_(None)" in stamp_block, (
        "the stamp must be conditional on project_id being NULL; an "
        "unconditional update would rewrite scope the writer owns"
    )


def test_the_migration_repoints_the_constraint_rather_than_only_dropping_it():
    """A dropped-and-not-recreated FK would silently remove referential
    integrity — a worse outcome than the bug being fixed."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0146_evidence_artifact_run_set_null.py"
    )
    body = migration.read_text(encoding="utf-8")
    upgrade = body.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]

    assert "ON DELETE SET NULL" in upgrade
    assert '(_TEST_CASE_FK, "test_case_id", "test_cases")' in upgrade
    assert (
        '(_PIPELINE_FK, "producer_pipeline_run_id", "agent_pipeline_runs")' in upgrade
    )
    assert "VALIDATE CONSTRAINT" in upgrade, (
        "add the constraint NOT VALID then VALIDATE separately — a plain ADD "
        "CONSTRAINT holds ACCESS EXCLUSIVE for a full scan of a table that can "
        "be large"
    )


def test_verified_artifact_triggers_allow_only_parent_detachment():
    """SET NULL fires UPDATE triggers; the exception must stay narrow."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0146_evidence_artifact_run_set_null.py"
    )
    body = migration.read_text(encoding="utf-8")
    upgrade = body.split("def upgrade()", 1)[0]

    assert "prevent_verified_evidence_mutation" in upgrade
    assert "validate_evidence_artifact_scope" in upgrade
    assert "to_jsonb(NEW)" in upgrade
    assert "NEW.run_id IS NULL" in upgrade
    assert "NEW.test_case_id IS NULL" in upgrade
    assert "NEW.producer_pipeline_run_id IS NULL" in upgrade
    assert "WHERE id = OLD.run_id" in upgrade
    assert "WHERE id = OLD.test_case_id" in upgrade
    assert "WHERE id = OLD.producer_pipeline_run_id" in upgrade
    assert "verified evidence artifacts are immutable" in upgrade


def test_the_downgrade_refuses_rather_than_deleting_survivors():
    """Restoring NOT NULL would require deleting rows whose run was purged —
    the exact evidence this migration exists to preserve."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0146_evidence_artifact_run_set_null.py"
    )
    downgrade = migration.read_text(encoding="utf-8").split("def downgrade()", 1)[1]

    assert "run_id IS NULL" in downgrade
    assert "raise RuntimeError" in downgrade, (
        "a downgrade that silently drops protected audit evidence is worse "
        "than one that refuses and tells the operator"
    )
