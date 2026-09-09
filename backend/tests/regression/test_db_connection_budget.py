"""M12: deployment topology must remain inside the PostgreSQL fleet budget."""

from __future__ import annotations

import copy
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "validate_db_connection_budget.py"
SPEC = importlib.util.spec_from_file_location("db_budget_validator", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def _documents() -> list[dict]:
    paths = [
        ROOT / "k8s" / "base" / "configmap.yaml",
        ROOT / "k8s" / "base" / "backend-deployment.yaml",
        ROOT / "k8s" / "base" / "worker-deployments.yaml",
        ROOT / "k8s" / "base" / "worker-children-deployment.yaml",
        ROOT / "k8s" / "base" / "hpa.yaml",
    ]
    documents = []
    for path in paths:
        documents.extend(doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc)
    hpas = {
        "testlookup-backend-hpa": 8,
        "testlookup-worker-critical-hpa": 6,
        "testlookup-worker-ingestion-hpa": 8,
        "testlookup-worker-ai-hpa": 6,
    }
    for document in documents:
        if document.get("kind") == "HorizontalPodAutoscaler":
            value = hpas.get(document["metadata"]["name"])
            if value is not None:
                document["spec"]["maxReplicas"] = value
    return documents


def test_production_rollout_preserves_operational_headroom() -> None:
    budgets, limits = validator.validate(_documents())
    by_name = {item.name: item.connections for item in budgets}
    assert by_name == {
        "testlookup-backend": 108,
        "testlookup-worker-ai": 28,
        "testlookup-worker-children": 4,
        "testlookup-worker-critical": 28,
        "testlookup-worker-default": 32,
        "testlookup-worker-ingestion": 72,
    }
    assert sum(by_name.values()) + limits["migration"] == 273
    assert limits == {
        "server_max": 400,
        "operational_reserve": 50,
        "superuser_reserved": 3,
        "reserved": 0,
        "migration": 1,
        "declared_required": 273,
    }

    gunicorn = (ROOT / "backend" / "gunicorn_conf.py").read_text(encoding="utf-8")
    assert "workers = 4" in gunicorn


def test_hpa_or_pool_growth_that_consumes_the_reserve_fails() -> None:
    documents = copy.deepcopy(_documents())
    for document in documents:
        if document.get("kind") == "Deployment" and document["metadata"]["name"] == "testlookup-backend":
            env = document["spec"]["template"]["spec"]["containers"][0]["env"]
            next(item for item in env if item["name"] == "PG_MAX_OVERFLOW")["value"] = "20"
    with pytest.raises(ValueError, match="fleet requires"):
        validator.validate(documents)


def test_worker_process_count_must_match_celery_concurrency() -> None:
    documents = copy.deepcopy(_documents())
    for document in documents:
        if document.get("kind") == "Deployment" and document["metadata"]["name"] == "testlookup-worker-ai":
            env = document["spec"]["template"]["spec"]["containers"][0]["env"]
            next(item for item in env if item["name"] == "PG_PROCESSES_PER_POD")["value"] = "1"
    with pytest.raises(ValueError, match="Celery concurrency=2"):
        validator.validate(documents)


def test_runtime_server_capacity_check_uses_measured_limits(monkeypatch) -> None:
    from app.core.config import settings
    from app.db.postgres import evaluate_server_connection_budget

    monkeypatch.setattr(settings, "PG_FLEET_MAX_CONNECTIONS", 400)
    monkeypatch.setattr(settings, "PG_FLEET_OPERATIONAL_RESERVE", 50)
    monkeypatch.setattr(settings, "PG_FLEET_REQUIRED_CONNECTIONS", 280)
    result = evaluate_server_connection_budget(
        server_max=400,
        superuser_reserved=3,
        reserved=0,
        role_limit=-1,
    )
    assert result["usable"] == 347

    with pytest.raises(RuntimeError, match="actual usable capacity is 247"):
        evaluate_server_connection_budget(
            server_max=300,
            superuser_reserved=3,
            reserved=0,
            role_limit=-1,
        )


def test_runtime_server_capacity_check_honors_role_limit(monkeypatch) -> None:
    from app.core.config import settings
    from app.db.postgres import evaluate_server_connection_budget

    monkeypatch.setattr(settings, "PG_FLEET_MAX_CONNECTIONS", 400)
    monkeypatch.setattr(settings, "PG_FLEET_OPERATIONAL_RESERVE", 50)
    monkeypatch.setattr(settings, "PG_FLEET_REQUIRED_CONNECTIONS", 280)
    result = evaluate_server_connection_budget(
        server_max=400,
        superuser_reserved=3,
        reserved=0,
        role_limit=300,
    )
    assert result["usable"] == 300
    with pytest.raises(RuntimeError, match="actual usable capacity is 270"):
        evaluate_server_connection_budget(
            server_max=400,
            superuser_reserved=3,
            reserved=0,
            role_limit=270,
        )


@pytest.mark.parametrize("compose_name", ["docker-compose.yml", "docker-compose.release.yml"])
def test_bundled_postgres_and_process_pools_are_explicit(compose_name: str) -> None:
    compose = yaml.safe_load((ROOT / compose_name).read_text(encoding="utf-8"))
    budgets, limits = validator.validate_compose(compose, {})
    expected = 14 if compose_name == "docker-compose.yml" else 23
    assert sum(item.connections for item in budgets) + limits["migration"] == expected
    assert limits["server_max"] == 400
    gate = compose["services"]["db-budget-check"]
    assert gate["command"] == "python -m app.db.compose_budget_gate"
    migration = compose["services"]["db-migrate"]
    assert migration["depends_on"]["db-budget-check"] == {
        "condition": "service_completed_successfully"
    }
    assert compose["services"]["backend"]["depends_on"]["db-migrate"] == {
        "condition": "service_completed_successfully"
    }
    for service_name in ("backend", "worker", "worker-children"):
        env = compose["services"][service_name]["environment"]
        joined = "\n".join(env)
        assert "PG_PROCESS_ROLE=" in joined
        assert "PG_POOL_SIZE=${PG_" in joined
        assert "PG_MAX_OVERFLOW=${PG_" in joined


def test_compose_concurrency_override_cannot_silently_exhaust_postgres() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.release.yml").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="requires 415 connections but declares 23"):
        validator.validate_compose(compose, {"CELERY_CONCURRENCY": "200"})


def test_compose_command_concurrency_cannot_drift_from_declared_processes() -> None:
    compose = copy.deepcopy(
        yaml.safe_load((ROOT / "docker-compose.release.yml").read_text(encoding="utf-8"))
    )
    compose["services"]["worker"]["command"] = compose["services"]["worker"][
        "command"
    ].replace("${CELERY_CONCURRENCY:-4}", "200")
    with pytest.raises(ValueError, match="Celery concurrency=200"):
        validator.validate_compose(compose, {})


def test_compose_cli_loads_project_env_overrides(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CELERY_CONCURRENCY=200\n", encoding="utf-8")
    process_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"CELERY_CONCURRENCY", "PG_COMPOSE_REQUIRED_CONNECTIONS"}
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--compose",
            str(ROOT / "docker-compose.release.yml"),
            "--env-file",
            str(env_file),
        ],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "requires 415 connections but declares 23" in result.stderr


def test_compose_startup_gate_uses_the_same_override_values() -> None:
    from app.db.compose_budget_gate import calculate

    safe = {
        "PG_API_PROCESSES_PER_POD": "4",
        "PG_API_POOL_SIZE": "2",
        "PG_API_MAX_OVERFLOW": "1",
        "CELERY_CONCURRENCY": "4",
        "CELERY_CHILDREN_CONCURRENCY": "1",
        "PG_WORKER_POOL_SIZE": "1",
        "PG_WORKER_MAX_OVERFLOW": "1",
        "PG_FLEET_MAX_CONNECTIONS": "400",
        "PG_FLEET_OPERATIONAL_RESERVE": "50",
        "PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS": "3",
        "PG_FLEET_RESERVED_CONNECTIONS": "0",
        "PG_FLEET_MIGRATION_CONNECTIONS": "1",
        "PG_FLEET_REQUIRED_CONNECTIONS": "23",
    }
    assert calculate(safe)["required"] == 23
    unsafe = {**safe, "CELERY_CONCURRENCY": "200"}
    with pytest.raises(RuntimeError, match="requires 415 connections but declares 23"):
        calculate(unsafe)


@pytest.mark.parametrize(
    "manifest",
    [
        "k8s/overlays/homelab/infra-postgres.yaml",
        "k8s/overlays/openshift-artifactory/infra-postgres.yaml",
    ],
)
def test_bundled_kubernetes_postgres_uses_declared_server_cap(manifest: str) -> None:
    documents = [
        document
        for document in yaml.safe_load_all((ROOT / manifest).read_text(encoding="utf-8"))
        if document
    ]
    postgres = next(document for document in documents if document.get("kind") == "Deployment")
    container = postgres["spec"]["template"]["spec"]["containers"][0]
    assert container["args"] == ["-c", "max_connections=400"]
