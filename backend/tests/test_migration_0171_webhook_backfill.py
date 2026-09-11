"""Re-audit N3: the webhook_deliveries.run_id backfill commits batch by batch.

0162 filled the column with one cross-table UPDATE inside the migration
transaction. That UPDATE moved to 0171, whose ``upgrade`` runs ``backfill``
inside ``autocommit_block``, so each batch commits and releases its row locks.
The behaviour on real PostgreSQL is in
tests/integration/test_webhook_delivery_backfill_postgres.py; these pin the two
structural halves the integration test cannot see: the autocommit block, and
0162 no longer running the UPDATE itself.
"""
from __future__ import annotations

import ast
import pathlib

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _source(name: str) -> str:
    return (VERSIONS / name).read_text(encoding="utf-8")


def test_0171_runs_the_backfill_inside_an_autocommit_block():
    tree = ast.parse(_source("0171_webhook_delivery_run_backfill.py"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    blocks = [
        node for node in ast.walk(upgrade)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "autocommit_block"
            for item in node.items
        )
    ]
    assert len(blocks) == 1, "0171 must backfill inside op.get_context().autocommit_block()"
    calls = [
        node for node in ast.walk(blocks[0])
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "backfill"
    ]
    assert len(calls) == 1, "the backfill must run inside the autocommit block"


def test_0162_no_longer_backfills_in_its_transaction():
    source = _source("0162_run_downstream_outbox.py")
    assert "UPDATE webhook_deliveries" not in source
    assert "0171" in source, "0162 should say where the backfill went"


def test_0171_follows_0170():
    source = _source("0171_webhook_delivery_run_backfill.py")
    assert 'revision = "0171"' in source and 'down_revision = "0170"' in source
