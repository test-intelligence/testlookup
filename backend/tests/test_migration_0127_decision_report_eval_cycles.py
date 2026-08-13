from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0127_decision_report_eval_cycles.py"


def test_report_eval_cycle_migration_chains_from_0126_and_is_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0127"' in source
    assert 'down_revision = "0126"' in source
    assert '"decision_report_eval_cycles"' in source
    assert "uq_drec_cycle_key" in source
    assert "ck_drec_corpus_hash" in source
    assert "def downgrade()" in source
    assert "op.drop_table" in source
