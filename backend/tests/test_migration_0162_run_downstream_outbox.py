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
    assert "uq_notification_log_delivery_key" in source
    assert "def downgrade()" in source


def test_0162_leaves_the_run_id_backfill_to_0171():
    """Re-audit N3: 0162's cross-table UPDATE ran unbatched inside the
    migration transaction. The backfill moved to 0171, which commits batch by
    batch. So 0162's upgrade executes no UPDATE at all, and 0171's backfill
    statement fills webhook_deliveries.run_id from the run the payload names.
    Behaviour on real PostgreSQL: tests/integration/test_webhook_delivery_backfill_postgres.py.
    """
    import ast
    import importlib.util

    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    executed = [
        node.args[0].value
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    assert not any("UPDATE" in sql.upper() for sql in executed), executed

    path = MIGRATION.parent / "0171_webhook_delivery_run_backfill.py"
    spec = importlib.util.spec_from_file_location("m0171_contract", path)
    m0171 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m0171)
    assert (m0171.revision, m0171.down_revision) == ("0171", "0170")
    fill = " ".join(str(m0171._FILL).split())
    assert fill.startswith("UPDATE webhook_deliveries AS delivery SET run_id = run.id"), fill
    assert "delivery.run_id IS NULL" in fill  # idempotent: only unfilled rows
    assert callable(m0171.backfill)
