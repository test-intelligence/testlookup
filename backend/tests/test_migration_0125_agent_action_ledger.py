from pathlib import Path

from app.models.postgres import AgentActionLedger


MIGRATION = Path(__file__).resolve().parents[1] / "migrations/versions/0125_agent_action_ledger.py"


def test_migration_0125_chains_and_contains_action_authority_contract():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0125"' in source
    assert 'down_revision = "0124"' in source
    for marker in (
        "agent_action_ledger",
        "request_sha256",
        "request_payload",
        "approval_required",
        "idempotency_key",
        "ck_agent_action_status",
        "ck_agent_action_request_hash",
        "def downgrade()",
    ):
        assert marker in source


def test_action_ledger_orm_matches_migration_surface():
    columns = AgentActionLedger.__table__.columns
    for name in (
        "project_id", "action_type", "target_type", "status", "request_sha256",
        "request_payload", "result_payload", "rollback_payload", "error_code",
    ):
        assert name in columns


def test_action_dispatch_outbox_migration_is_next_and_recoverable():
    path = MIGRATION.parent / "0126_agent_action_dispatch_outbox.py"
    source = path.read_text(encoding="utf-8")
    assert 'revision = "0126"' in source
    assert 'down_revision = "0125"' in source
    assert "agent_action_dispatch_outbox" in source
    assert "lease_expires_at" in source
    assert "uq_agent_action_outbox_action" in source
