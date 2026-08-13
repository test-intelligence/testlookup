"""Regression guard for the homelab Redis broker durability contract."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
REDIS_MANIFEST = ROOT / "k8s" / "overlays" / "homelab" / "infra-redis.yaml"


def _resources() -> dict[str, dict]:
    return {
        item["kind"]: item
        for item in yaml.safe_load_all(REDIS_MANIFEST.read_text(encoding="utf-8"))
        if item
    }


def test_homelab_redis_has_durable_broker_storage_and_safe_eviction():
    resources = _resources()
    deployment = resources["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    command = container["command"]

    assert "--appendonly" in command
    assert command[command.index("--appendonly") + 1] == "yes"
    assert command[command.index("--appendfsync") + 1] == "everysec"
    assert command[command.index("--maxmemory-policy") + 1] == "volatile-lru"
    assert {mount["mountPath"] for mount in container["volumeMounts"]} >= {"/data"}
    assert deployment["spec"]["template"]["spec"]["volumes"] == [
        {"name": "data", "persistentVolumeClaim": {"claimName": "redis-data"}}
    ]

    pvc = resources["PersistentVolumeClaim"]
    assert pvc["metadata"]["name"] == "redis-data"
    assert pvc["spec"]["storageClassName"] == "local-path"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "5Gi"
