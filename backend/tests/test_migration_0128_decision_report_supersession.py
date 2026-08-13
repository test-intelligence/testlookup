from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0128_decision_report_supersession.py"


def test_migration_is_single_head_and_has_idempotent_request_constraints():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0128"' in source
    assert 'down_revision = "0127"' in source
    assert '"decision_report_supersession_requests"' in source
    assert 'sa.UniqueConstraint("parent_pipeline_run_id", name="uq_drsr_parent_pipeline")' in source
    assert "status IN ('pending', 'processing', 'published', 'rejected', 'failed')" in source
    assert 'op.drop_table("decision_report_supersession_requests")' in source
