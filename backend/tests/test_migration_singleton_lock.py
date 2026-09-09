"""Deployment ratchets for singleton-safe Alembic execution."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / "backend/migrations/env.py"
DOCKERFILE = ROOT / "backend/Dockerfile"


def test_online_migrations_take_a_transaction_scoped_postgres_advisory_lock():
    source = ENV.read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in source
    assert "pg_advisory_lock(" not in source
    assert 'connection.dialect.name == "postgresql"' in source
    assert "_ALEMBIC_ADVISORY_LOCK_ID" in source

    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "do_run_migrations"
    )
    transaction = next(
        node
        for node in function.body
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "begin_transaction"
            for item in node.items
        )
    )
    transaction_source = ast.get_source_segment(source, transaction) or ""
    assert transaction_source.index("pg_advisory_xact_lock") < (
        transaction_source.index("run_migrations")
    )


def test_every_backend_container_entrypoint_remains_lock_protected():
    """Both dev and production images run Alembic; env.py serializes both."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    commands = [
        line
        for line in dockerfile.splitlines()
        if line.startswith("CMD ")
        and ("uvicorn app.main:app" in line or "gunicorn -c gunicorn_conf.py app.main:app" in line)
    ]
    assert len(commands) == 2
    assert all("alembic upgrade head" in command for command in commands)
    assert "pg_advisory_xact_lock" in ENV.read_text(encoding="utf-8")
