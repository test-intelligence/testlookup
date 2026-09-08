from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0162_run_downstream_outbox.py"


def test_migration_is_linear_and_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0162"' in source
    assert 'down_revision = "0161"' in source
    assert "run_downstream_outbox" in source
    assert "uq_run_downstream_operation_version" in source
    assert "lease_expires_at" in source
    assert "execution_attempts" in source
    assert "dispatch_failures" in source
    assert "published_at" in source
    assert "processing_started_at" in source
    assert "completed_at" in source
    assert "ix_run_downstream_outbox_due_project" in source
    assert "ix_webhook_delivery_dispatch_due" in source
    assert "uq_webhook_delivery_delivery_key" in source
    assert 'sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True)' in source
    assert "fk_webhook_deliveries_run_id" in source
    assert "ix_webhook_delivery_run_id" in source
    assert "SET run_id = run.id" in source
    assert "uq_notification_log_delivery_key" in source
    assert "def downgrade()" in source
