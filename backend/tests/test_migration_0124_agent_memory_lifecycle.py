from pathlib import Path

from app.models.postgres import AgentMemoryEntry


MIGRATION = Path(__file__).resolve().parents[1] / "migrations/versions/0124_agent_memory_lifecycle.py"


def test_migration_0124_chains_and_reverses_lifecycle_authority():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0124"' in source
    assert 'down_revision = "0123"' in source
    for marker in (
        "source_type",
        "trust_level",
        "lifecycle_status",
        "source_snapshot_id",
        "source_hash",
        "expires_at",
        "superseded_by_id",
        "ck_ame_lifecycle_status",
        "ck_ame_trust_level",
        "ix_ame_project_lifecycle",
    ):
        assert marker in source
    assert "def downgrade() -> None:" in source


def test_memory_orm_matches_lifecycle_columns():
    columns = AgentMemoryEntry.__table__.columns
    for name in (
        "source_type",
        "trust_level",
        "lifecycle_status",
        "source_snapshot_id",
        "source_hash",
        "expires_at",
        "superseded_by_id",
        "superseded_at",
    ):
        assert name in columns
