"""H07 release proof using the real rendered Compose services and Redis."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from compose_topology_contract import (
    celery_worker_queues,
    live_ingest_shard_count,
    load_compose_topology,
    required_queues,
)

ROOT = Path(__file__).parents[3]
EVIDENCE_DIR = ROOT / "backend" / ".tmp" / "h07-compose-queue"
TOPOLOGIES = (
    ("source-dev", ("docker-compose.yml",)),
    ("release", ("docker-compose.release.yml",)),
    ("dev-lite", ("docker-compose.dev-lite.yml",)),
    ("gcp-vm", ("docker-compose.yml", "docker-compose.gcp-vm.yml")),
    ("airgap", ("docker-compose.release.yml", "docker-compose.airgap.yml")),
)


def _proof_requested() -> bool:
    return os.getenv("TESTLOOKUP_RUN_COMPOSE_QUEUE_PROOF", "").lower() == "true"


def _run(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=600,
    )


@pytest.fixture(scope="session")
def h07_backend_image() -> str:
    if not _proof_requested():
        pytest.skip("H07 Compose queue proof not selected")
    source_sha = os.environ.get("GITHUB_SHA", "local")
    image = f"testlookup-h07-backend:{source_sha[:12]}"
    _run(["docker", "build", "--target", "production", "-t", image, "."],
         cwd=ROOT / "backend")
    return image


def _compose_base(project: str, compose_files: tuple[str, ...], override: Path) -> list[str]:
    command = ["docker", "compose", "--project-name", project, "--profile", "async"]
    for name in compose_files:
        command.extend(("-f", str(ROOT / name)))
    command.extend(("-f", str(override)))
    return command


def _write_override(
    path: Path, *, image: str, env_file_services: set[str]
) -> None:
    evidence = EVIDENCE_DIR.resolve().as_posix()
    document = f"""services:
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    ports: !reset []
  worker:
    image: {image}
    build: !reset null
    volumes: !override
      - {evidence}:/evidence
    env_file: !reset []
    depends_on: !reset {{}}
    environment:
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
      REDIS_URL: redis://redis:6379/0
      APP_SECRET_KEY: h07-compose-secret
      JWT_SECRET_KEY: h07-compose-jwt
      AI_OFFLINE_MODE: "true"
  worker-children:
    image: {image}
    build: !reset null
    volumes: !override
      - {evidence}:/evidence
    env_file: !reset []
    depends_on: !reset {{}}
    environment:
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
      REDIS_URL: redis://redis:6379/0
      APP_SECRET_KEY: h07-compose-secret
      JWT_SECRET_KEY: h07-compose-jwt
      AI_OFFLINE_MODE: "true"
"""
    for service in sorted(env_file_services - {"worker", "worker-children"}):
        document += f"  {service}:\n    env_file: !reset []\n"
    path.write_text(document, encoding="utf-8")


def test_queue_proof_override_resets_every_declared_env_file(tmp_path: Path) -> None:
    override = tmp_path / "override.yml"
    _write_override(
        override,
        image="h07:test",
        env_file_services={"backend", "beat", "worker", "worker-children"},
    )
    document = override.read_text(encoding="utf-8")

    assert "  backend:\n    env_file: !reset []\n" in document
    assert "  beat:\n    env_file: !reset []\n" in document
    assert document.count("env_file: !reset []") == 4


@pytest.mark.integration
@pytest.mark.skipif(not _proof_requested(), reason="H07 Compose queue proof not selected")
@pytest.mark.parametrize("topology_name,compose_files", TOPOLOGIES)
def test_supported_compose_topology_delivers_one_task_per_queue(
    topology_name: str, compose_files: tuple[str, ...], h07_backend_image: str,
) -> None:
    source_sha = os.environ.get("GITHUB_SHA", "local")
    project = f"h07-{re.sub('[^a-z0-9-]', '-', topology_name.lower())}"
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    override = EVIDENCE_DIR / f"{topology_name}-override.yml"
    topology = load_compose_topology(ROOT / name for name in compose_files)
    env_file_services = {
        str(name)
        for name, service in topology.get("services", {}).items()
        if service.get("env_file")
    }
    _write_override(
        override, image=h07_backend_image, env_file_services=env_file_services
    )
    compose = _compose_base(project, compose_files, override)
    shard_count = live_ingest_shard_count(topology, environment={})
    expected_queues = required_queues(shard_count)
    assert set().union(*celery_worker_queues(topology).values()) == expected_queues

    compose_environment = {
        **os.environ,
        "POSTGRES_PASSWORD": "h07-compose-postgres",
        "MONGO_PASSWORD": "h07-compose-mongo",
        "MINIO_ACCESS_KEY": "h07-compose-minio",
        "MINIO_SECRET_KEY": "h07-compose-minio-secret",
        "FLOWER_PASSWORD": "h07-compose-flower",
        "APP_SECRET_KEY": "h07-compose-secret",
        "JWT_SECRET_KEY": "h07-compose-jwt",
        "TESTLOOKUP_IMAGE": "testlookup-h07",
        "TESTLOOKUP_VERSION": source_sha[:12],
        "TESTLOOKUP_REGISTRY": "testlookup-h07",
        "GCP_PROJECT_ID": "h07-contract",
        "IMAGE_TAG": source_sha[:12],
    }
    try:
        rendered = subprocess.run(
            [*compose, "config", "--format", "json"], cwd=ROOT,
            env=compose_environment, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        document = json.loads(rendered.stdout)
        assert {"redis", "worker", "worker-children"} <= set(document["services"])
        assert document["services"]["redis"]["image"] == "redis:7-alpine"
        assert document["services"]["worker"]["image"] == h07_backend_image
        assert document["services"]["worker-children"]["image"] == h07_backend_image

        subprocess.run(
            [*compose, "up", "-d", "--no-build", "--no-deps", "redis", "worker",
             "worker-children"], cwd=ROOT, env=compose_environment, check=True,
            timeout=120,
        )
        subprocess.run(
            [*compose, "run", "--rm", "--no-deps", "--user",
             f"{os.getuid()}:{os.getgid()}", "worker", "python",
             "scripts/verify_compose_queues.py", "--topology", topology_name,
             "--shard-count", str(shard_count), "--broker-url",
             "redis://redis:6379/0", "--result-url", "redis://redis:6379/1",
             "--timeout", "90", "--evidence", f"/evidence/{topology_name}.json",
             "--source-sha", source_sha], cwd=ROOT, env=compose_environment,
            check=True, timeout=120,
        )
        evidence = json.loads(
            (EVIDENCE_DIR / f"{topology_name}.json").read_text(encoding="utf-8")
        )
        assert evidence["source_sha"] == source_sha
        assert evidence["result"] == "passed"
        assert {row["queue"] for row in evidence["queues"]} == expected_queues
        assert all(row["workers"] for row in evidence["queues"])
    finally:
        try:
            logs = subprocess.run(
                [*compose, "logs", "--no-color", "redis", "worker", "worker-children"],
                cwd=ROOT, env=compose_environment, check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            )
            (EVIDENCE_DIR / f"{topology_name}.log").write_text(
                logs.stdout, encoding="utf-8"
            )
        finally:
            subprocess.run(
                [*compose, "down", "-v", "--remove-orphans", "--timeout", "10"],
                cwd=ROOT, env=compose_environment, check=False, timeout=120,
            )
