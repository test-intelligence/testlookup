"""Deployment and runbook contracts for ingestion shard-count changes."""

from pathlib import Path

import yaml

from app.worker.ingestion_routing import queues_for_count, shard_change_queue_union


ROOT = Path(__file__).parents[3]


def _celery_queues(command: str) -> tuple[str, ...]:
    value = command.split(" -Q ", 1)[1].split(" ", 1)[0]
    return tuple(value.split(","))


def test_compose_topologies_keep_producer_count_and_consumers_aligned():
    development = (ROOT / "docker-compose.yml").read_text()
    assert development.count("LIVE_INGEST_SHARD_COUNT=0") >= 2
    dev_command = next(
        line for line in development.splitlines()
        if "celery -A app.worker.celery_app worker" in line and " -Q " in line
    )
    assert set(queues_for_count(0)).issubset(_celery_queues(dev_command))

    release = (ROOT / "docker-compose.release.yml").read_text()
    assert release.count("LIVE_INGEST_SHARD_COUNT=8") >= 2
    release_command = next(
        line for line in release.splitlines()
        if "celery -A app.worker.celery_app worker" in line and " -Q " in line
    )
    assert set(queues_for_count(8)).issubset(_celery_queues(release_command))


def test_kubernetes_declares_count_and_matching_ingestion_queues():
    config = yaml.safe_load((ROOT / "k8s/base/configmap.yaml").read_text())
    count = int(config["data"]["LIVE_INGEST_SHARD_COUNT"])
    deployment = (ROOT / "k8s/base/worker-deployments.yaml").read_text()
    queue_arg = next(
        line.strip().removeprefix("- ")
        for line in deployment.splitlines()
        if line.strip().startswith("- ingestion,")
    )
    assert set(queue_arg.split(",")) == set(queues_for_count(count))


def test_union_and_operational_runbook_cover_safe_cutover_surfaces():
    assert shard_change_queue_union(8, 9) == queues_for_count(9)
    runbook = (ROOT / "docs/operations/ingestion-shard-count-change.md").read_text()
    required_evidence = (
        "active_queues",
        "run_downstream_outbox",
        "'persist_live_session'",
        "('waiting', 'pending', 'sending', 'published', 'processing')",
        "priority steps are `0,3,6,9`",
        "printf '\\006\\026'",
        "inspect active",
        "inspect reserved",
        "inspect scheduled",
        "HVALS unacked",
        "3600-second",
        "auto-recover-completed-live-runs",
        "relay-run-downstream-outbox",
        "delete the API HPA",
        "testlookup-ingestion-drain",
        '-e LIVE_INGEST_SHARD_COUNT="$OLD_COUNT" worker',
        "consumes ingestion queues only",
        "delete hpa testlookup-worker-ingestion-hpa",
        "Do not run `kubectl apply -k` during this barrier",
        "up -d --no-deps worker",
        "up -d --no-deps backend",
    )
    missing = [item for item in required_evidence if item not in runbook]
    assert not missing, f"shard-change runbook is missing: {missing}"

    # Pin the safety-critical ordering: ingress/beat stop, durable relay drain,
    # final publisher stop, broker drain, new fleet, then one-shot canary relay.
    first_beat_stop = runbook.index("stop backend beat")
    durable_gate = runbook.index("SELECT id, run_id")
    first_manual_relay = runbook.index("celery -A app.worker.celery_app call")
    default_stop = runbook.index("gracefully stop `testlookup-worker-default`")
    broker_gate = runbook.index("default Redis priority steps")
    new_fleet = runbook.index("apply -f /tmp/new-count-configmap.yaml")
    canary_relay = runbook.index("invoke exactly one `relay_run_downstream_outbox`")
    assert (
        first_beat_stop
        < durable_gate
        < first_manual_relay
        < default_stop
        < broker_gate
        < new_fleet
        < canary_relay
    )


def test_release_compose_combined_worker_requires_documented_drain_consumer():
    compose = yaml.safe_load((ROOT / "docker-compose.release.yml").read_text())
    assert {"backend", "worker", "beat"}.issubset(compose["services"])
    queues = _celery_queues(compose["services"]["worker"]["command"])
    assert {"default", "ingestion"}.issubset(queues)

    runbook = (ROOT / "docs/operations/ingestion-shard-count-change.md").read_text()
    start = runbook.index("--name testlookup-ingestion-drain")
    stop_combined = runbook.index("release Compose, stop the combined `worker`")
    remove_drain = runbook.index("docker rm testlookup-ingestion-drain")
    no_deps_worker = runbook.index("up -d --no-deps worker")
    no_deps_backend = runbook.index("up -d --no-deps backend")
    assert start < stop_combined < remove_drain < no_deps_worker < no_deps_backend
