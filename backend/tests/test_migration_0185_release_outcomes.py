from __future__ import annotations

from pathlib import Path


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0185_release_outcomes.py"
    ).read_text(encoding="utf-8")


def test_release_outcome_migration_is_linear_and_reversible() -> None:
    source = _source()
    assert 'revision = "0185"' in source
    assert 'down_revision = "0184"' in source
    assert 'TABLE = "release_outcomes"' in source
    assert "op.drop_table(TABLE)" in source
    assert "incident" in source and "rollback" in source
    assert source.count("sa.CheckConstraint(") == 2


def test_release_outcome_indexes_are_only_on_the_new_table() -> None:
    source = _source()
    assert source.count("op.create_index(") == 2
    assert '"ix_release_outcomes_release_marked"' in source
    assert '"ix_release_outcomes_project_marked"' in source
    assert "autocommit_block" not in source
