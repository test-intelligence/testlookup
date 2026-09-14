from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "0183_workflow_definitions.py"


def test_workflow_definition_migration_is_linear_and_reversible() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0183"' in source
    assert 'down_revision = "0182"' in source
    assert 'op.create_table(\n        "workflow_definitions"' in source
    assert "uq_workflow_definitions_project_workflow_version" in source
    assert "ck_workflow_definitions_published_at" in source
    assert "ix_workflow_definitions_project_status" in source
    assert 'op.drop_table("workflow_definitions")' in source


def test_new_table_index_is_transactional() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "autocommit_block" not in source
    assert "postgresql_concurrently" not in source
