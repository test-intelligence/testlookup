from pathlib import Path

from app.models.postgres import EvidenceArtifact


def test_migration_adds_scope_integrity_retention_and_immutability():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/0120_evidence_artifact_authority.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "project_id", "producer_pipeline_run_id", "content_sha256",
        "content_size_bytes", "sensitivity", "freshness", "integrity_status",
        "retention_class", "idempotency_key", "validate_evidence_artifact_scope",
        "prevent_verified_evidence_mutation",
    ):
        assert marker in migration
    assert 'down_revision = "0119"' in migration
    assert "''verified''" not in migration
    assert "~ ''^[0-9a-f]{64}$''" not in migration
    assert "$$ LANGUAGE plpgsql;\n        CREATE TRIGGER" not in migration


def test_orm_matches_authoritative_artifact_columns():
    columns = EvidenceArtifact.__table__.columns
    for name in (
        "project_id", "producer_pipeline_run_id", "content_sha256",
        "content_size_bytes", "sensitivity", "freshness", "integrity_status",
        "retention_class", "idempotency_key",
    ):
        assert name in columns
