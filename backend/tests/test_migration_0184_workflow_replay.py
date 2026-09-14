from __future__ import annotations

from pathlib import Path


def test_workflow_replay_migration_is_linear_reversible_and_keys_exactly() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0184_workflow_replay_corpus.py"
    )
    source = path.read_text(encoding="utf-8")

    assert 'revision = "0184"' in source
    assert 'down_revision = "0183"' in source
    assert 'TABLE = "workflow_replay_corpus"' in source
    assert '"agent_id",\n            "prompt_version",\n            "input_hash"' in source
    assert 'op.drop_table(TABLE)' in source
    assert source.count('op.drop_column("workflow_definitions"') == 8
    assert source.count("op.create_check_constraint(") == 4
    assert source.count("op.drop_constraint(") == 4


def test_new_table_index_is_transactional_and_no_existing_table_index_is_added() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0184_workflow_replay_corpus.py"
    )
    source = path.read_text(encoding="utf-8")

    assert source.count("op.create_index(") == 1
    assert '"ix_workflow_replay_project_run"' in source
    assert "autocommit_block" not in source
