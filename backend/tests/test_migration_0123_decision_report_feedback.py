from pathlib import Path

from app.models.postgres import DecisionReportFeedback

MIGRATION = Path(__file__).resolve().parents[1] / "migrations/versions/0123_decision_report_feedback.py"


def test_migration_0123_chains_and_creates_bound_feedback_table():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0123"' in source
    assert 'down_revision = "0122"' in source
    for marker in (
        "decision_report_feedback",
        "report_evidence_sha256",
        "report_version",
        "uq_decision_report_feedback_user_idempotency",
        "ck_decision_report_feedback_report_version_positive",
        "ix_decision_report_feedback_report",
    ):
        assert marker in source
    assert "def downgrade() -> None:" in source


def test_report_feedback_orm_matches_authority_columns():
    columns = DecisionReportFeedback.__table__.columns
    for name in (
        "project_id",
        "test_run_id",
        "report_id",
        "report_version",
        "report_evidence_sha256",
        "claim_id",
        "correction_type",
        "evidence_refs",
        "idempotency_key",
    ):
        assert name in columns
