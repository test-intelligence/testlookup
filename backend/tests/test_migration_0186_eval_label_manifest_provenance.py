from __future__ import annotations

from pathlib import Path

from app.models.postgres import AIFeedback, ReviewRequest


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0186_eval_label_manifest_provenance.py"
    ).read_text(encoding="utf-8")


def test_eval_label_manifest_migration_is_linear_and_reversible() -> None:
    source = _source()
    assert 'revision = "0186"' in source
    assert 'down_revision = "0185"' in source
    assert source.count('op.add_column(') == 2
    assert source.count('op.drop_column(') == 2
    assert source.count('op.create_check_constraint(') == 2
    assert source.count('op.drop_constraint(') == 2
    assert "op.create_index" not in source


def test_feedback_and_review_models_expose_manifest_provenance() -> None:
    assert AIFeedback.__table__.columns["eval_manifest_checksum"].type.length == 64
    assert ReviewRequest.__table__.columns["eval_manifest_checksum"].type.length == 64
