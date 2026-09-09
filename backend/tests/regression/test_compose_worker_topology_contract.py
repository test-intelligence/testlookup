"""Every supported Compose topology must consume every routed Celery queue."""
from __future__ import annotations

from pathlib import Path

import pytest

from compose_topology_contract import (
    celery_worker_queues,
    live_ingest_shard_count,
    load_compose_topology,
    required_queues,
)


ROOT = Path(__file__).parents[3]

TOPOLOGIES = (
    pytest.param(("docker-compose.yml",), id="source-dev"),
    pytest.param(("docker-compose.release.yml",), id="release"),
    pytest.param(("docker-compose.dev-lite.yml",), id="dev-lite"),
    pytest.param(
        ("docker-compose.yml", "docker-compose.gcp-vm.yml"),
        id="gcp-base-plus-override",
    ),
    pytest.param(
        ("docker-compose.release.yml", "docker-compose.airgap.yml"),
        id="release-plus-airgap",
    ),
)


@pytest.mark.parametrize("compose_files", TOPOLOGIES)
def test_supported_compose_topology_consumes_every_required_queue(compose_files):
    topology = load_compose_topology(ROOT / name for name in compose_files)
    shard_count = live_ingest_shard_count(topology, environment={})
    subscriptions = celery_worker_queues(topology)
    consumed = set().union(*subscriptions.values()) if subscriptions else set()
    missing = required_queues(shard_count) - consumed

    assert subscriptions, f"{compose_files} has no Celery worker service"
    assert not missing, (
        f"{compose_files} leaves routed queues without a consumer: {sorted(missing)}; "
        f"workers={subscriptions}, LIVE_INGEST_SHARD_COUNT={shard_count}"
    )


@pytest.mark.parametrize("compose_files", TOPOLOGIES)
def test_supported_compose_topology_aligns_shards_with_effective_environment(
    compose_files,
):
    topology = load_compose_topology(ROOT / name for name in compose_files)
    shard_count = live_ingest_shard_count(topology, environment={})
    consumed = set().union(*celery_worker_queues(topology).values())
    expected_shards = {
        f"ingestion.shard.{index}" for index in range(shard_count)
    }
    actual_shards = {
        queue for queue in consumed if queue.startswith("ingestion.shard.")
    }

    assert actual_shards == expected_shards


@pytest.mark.parametrize(
    "compose_files",
    (
        pytest.param(("docker-compose.release.yml",), id="release"),
        pytest.param(
            ("docker-compose.yml", "docker-compose.gcp-vm.yml"),
            id="gcp-base-plus-override",
        ),
        pytest.param(
            ("docker-compose.release.yml", "docker-compose.airgap.yml"),
            id="release-plus-airgap",
        ),
    ),
)
def test_production_topologies_isolate_agent_children(compose_files):
    topology = load_compose_topology(ROOT / name for name in compose_files)
    subscriptions = celery_worker_queues(topology)

    assert subscriptions.get("worker-children") == {"agent_children"}
    assert all(
        "agent_children" not in queues
        for service, queues in subscriptions.items()
        if service != "worker-children"
    )


def test_gcp_staging_deploys_the_documented_effective_topology():
    workflow = (ROOT / ".github/workflows/deploy-staging.yml").read_text(
        encoding="utf-8"
    )
    command_prefix = (
        "docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml "
        "--profile async"
    )

    assert f"{command_prefix} pull" in workflow
    assert f"{command_prefix} up -d --remove-orphans" in workflow

    override = load_compose_topology((ROOT / "docker-compose.gcp-vm.yml",))
    services = override["services"]
    backend_image = (
        "gcr.io/${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set}/"
        "testlookup-backend:${IMAGE_TAG:?IMAGE_TAG must be set}"
    )
    for service in ("db-migrate", "backend", "worker", "worker-children", "beat", "seed-init"):
        assert services[service]["image"] == backend_image
    assert services["frontend"]["image"] == (
        "gcr.io/${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set}/"
        "testlookup-frontend:${IMAGE_TAG:?IMAGE_TAG must be set}"
    )
    assert services["mcp"]["image"] == (
        "gcr.io/${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set}/"
        "testlookup-mcp:${IMAGE_TAG:?IMAGE_TAG must be set}"
    )
    assert "testlookup-mcp:$IMAGE_TAG ./mcp" in workflow


def test_negative_shard_count_fails_closed(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  backend:\n    environment:\n"
        "      LIVE_INGEST_SHARD_COUNT: '-1'\n",
        encoding="utf-8",
    )
    topology = load_compose_topology((compose,))

    with pytest.raises(AssertionError, match="non-negative"):
        live_ingest_shard_count(topology, environment={})


def test_runtime_probe_task_is_registered_by_production_worker():
    from app.worker.celery_app import celery_app
    from scripts.verify_compose_queues import PROBE_TASK

    assert PROBE_TASK in celery_app.tasks
