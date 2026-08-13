from pathlib import Path

from app.models.postgres import ChatSession

MIGRATION = Path(__file__).resolve().parents[1] / "migrations/versions/0122_chat_report_binding.py"


def test_migration_0122_chains_and_binds_report_fields():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0122"' in source
    assert 'down_revision = "0121"' in source
    for marker in (
        "active_test_run_id",
        "active_report_id",
        "active_report_version",
        "fk_chat_sessions_active_test_run",
        "ck_chat_sessions_active_report_version_positive",
        "ix_chat_sessions_active_report",
    ):
        assert marker in source
    assert 'ondelete="SET NULL"' in source
    assert "def downgrade() -> None:" in source


def test_chat_session_orm_matches_report_binding_columns():
    columns = ChatSession.__table__.columns
    assert "active_test_run_id" in columns
    assert "active_report_id" in columns
    assert "active_report_version" in columns