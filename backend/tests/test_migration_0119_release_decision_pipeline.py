from pathlib import Path

from app.models.postgres import ReleaseDecision


def test_release_decision_model_exposes_pipeline_provenance():
    column = ReleaseDecision.__table__.c.pipeline_run_id
    assert column.nullable is True
    assert list(column.foreign_keys)[0].target_fullname == "agent_pipeline_runs.id"


def test_migration_0119_is_linear_and_reversible():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0119_release_decision_pipeline_provenance.py"
    )
    source = path.read_text(encoding="utf-8")
    assert 'revision = "0119"' in source
    assert 'down_revision = "0118"' in source
    assert 'op.add_column(' in source
    assert 'op.create_foreign_key(' in source
    assert 'op.create_index(' in source
    assert 'op.drop_index(' in source
    assert 'op.drop_constraint(' in source
    assert 'op.drop_column(' in source
