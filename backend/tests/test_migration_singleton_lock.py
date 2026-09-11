"""Deployment ratchets for singleton-safe Alembic execution."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / "backend/migrations/env.py"
DOCKERFILE = ROOT / "backend/Dockerfile"
COMPOSE_PATHS = (
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.release.yml",
    ROOT / "docker-compose.dev-lite.yml",
)


LOCK_MODULE = ROOT / "backend/app/db/migration_lock.py"


def test_online_migrations_hold_a_session_lock_around_the_whole_upgrade():
    """N24: a transaction-scoped lock dies at the first autocommit_block().

    The behaviour is proven on real PostgreSQL by
    tests/integration/test_migration_singleton_lock_postgres.py; this pins
    the wiring so env.py cannot drift back to the xact lock.
    """
    source = ENV.read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" not in source
    assert source.count("async with migration_singleton_lock(") == 1

    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_async_migrations"
    )
    lock_block = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and getattr(item.context_expr.func, "id", None) == "migration_singleton_lock"
            for item in node.items
        )
    )
    body = "\n".join(ast.get_source_segment(source, stmt) or "" for stmt in lock_block.body)
    assert "_migrate()" in body

    lock = LOCK_MODULE.read_text(encoding="utf-8")
    # Polls: a waiter blocked in pg_advisory_lock holds a snapshot that the
    # holder's CONCURRENTLY build waits on -- an undetected deadlock.
    assert 'text("SELECT pg_try_advisory_lock(:lock_id)")' in lock
    assert 'text("SELECT pg_advisory_lock(' not in lock
    assert 'text("SELECT pg_advisory_xact_lock' not in lock
    assert 'isolation_level="AUTOCOMMIT"' in lock
    assert "ALEMBIC_ADVISORY_LOCK_ID = 6075990748104101441" in lock


def test_backend_container_entrypoints_do_not_run_schema_migrations():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    commands = [
        line
        for line in dockerfile.splitlines()
        if line.startswith("CMD ")
        and (
            "uvicorn app.main:app" in line
            or "gunicorn -c gunicorn_conf.py app.main:app" in line
        )
    ]
    assert len(commands) == 2
    assert all("alembic" not in command for command in commands)


def _environment(service: dict[str, object]) -> dict[str, str]:
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    return {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in raw
        if isinstance(item, str) and "=" in item
    }


@pytest.mark.parametrize("compose_path", COMPOSE_PATHS, ids=lambda path: path.name)
def test_compose_gates_api_on_one_bounded_migration(compose_path: Path):
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    migration = services["db-migrate"]
    backend = services["backend"]

    assert migration["command"] == ["alembic", "upgrade", "head"]
    assert migration["restart"] == "no"
    assert backend["depends_on"]["db-migrate"] == {
        "condition": "service_completed_successfully"
    }
    assert "alembic" not in str(backend.get("command", ""))
    if compose_path.name == "docker-compose.release.yml":
        assert "gunicorn -c gunicorn_conf.py app.main:app" in backend["command"]

    environment = _environment(migration)
    assert environment["PG_PROCESS_ROLE"] == "operation"
    assert environment["PG_PROCESSES_PER_POD"] == "1"
    assert environment["PG_POOL_SIZE"] == "1"
    assert environment["PG_MAX_OVERFLOW"] == "0"
    assert environment["POSTGRES_HOST"] == "postgres"
    assert environment["DATABASE_URL"].startswith("postgresql+asyncpg://")
    assert migration.get("image") == backend.get("image")
    assert migration.get("build") == backend.get("build")

    assert migration["depends_on"]["postgres"] == {
        "condition": "service_healthy"
    }
    if "db-budget-check" in services:
        assert migration["depends_on"]["db-budget-check"] == {
            "condition": "service_completed_successfully"
        }


def test_restore_and_upgrade_use_the_one_shot_migration_service():
    for relative in ("scripts/ops/upgrade.sh", "scripts/ops/restore.sh"):
        script = (ROOT / relative).read_text(encoding="utf-8")
        canonical = "compose up --force-recreate --abort-on-container-exit --exit-code-from db-migrate db-migrate"
        assert script.count(canonical) == 1
        assert "compose run --rm -T db-migrate" not in script
        assert "backend alembic upgrade head" not in script


def test_kubernetes_backend_has_a_deployment_owned_runtime_command():
    deployment = yaml.safe_load((ROOT / "k8s/base/backend-deployment.yaml").read_text(encoding="utf-8"))
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    command = " ".join(container["command"] + container["args"])
    assert "gunicorn -c gunicorn_conf.py app.main:app" in command
    assert "alembic" not in command


def test_gcp_vm_migration_uses_the_same_production_build_stage_as_backend():
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    overlay = yaml.safe_load((ROOT / "docker-compose.gcp-vm.yml").read_text(encoding="utf-8"))
    assert overlay["services"]["backend"]["build"]["target"] == "production"
    assert overlay["services"]["db-migrate"]["build"]["target"] == "production"
    assert base["services"]["backend"]["build"]["context"] == base["services"]["db-migrate"]["build"]["context"]
