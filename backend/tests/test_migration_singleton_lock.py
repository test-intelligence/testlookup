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
