"""The optional Compose profile isolates its privileged telemetry endpoints."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("base", ["docker-compose.yml", "docker-compose.release.yml"])
def test_compose_durable_and_authentication_overlays_are_compatible(base):
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose renderer is unavailable")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30)
    if version.returncode != 0:
        pytest.skip("Docker Compose renderer is unavailable")
    placeholders = {
        name: "fixture-only"
        for name in (
            "GF_SECURITY_ADMIN_PASSWORD", "POSTGRES_PASSWORD", "MONGO_PASSWORD",
            "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD", "FLOWER_PASSWORD", "REDIS_PASSWORD",
        )
    }
    paths = {
        name: "./fixture-only"
        for name in (
            "METRICS_API_TOKEN_SOURCE", "METRICS_PROMETHEUS_TOKEN_SOURCE",
            "ALERTING_PROMETHEUS_CONFIG_SOURCE", "ALERTMANAGER_INCIDENT_URL_SOURCE",
            "ALERTMANAGER_INCIDENT_TOKEN_SOURCE", "ALERTMANAGER_WATCHDOG_URL_SOURCE",
            "ALERTMANAGER_WATCHDOG_TOKEN_SOURCE",
        )
    }
    command = [
        "docker", "compose", "-f", base, "-f", "docker-compose.monitoring.yml",
        "-f", "docker-compose.telemetry.yml", "-f", "docker-compose.alerting.yml",
        "-f", "docker-compose.monitoring-auth.yml", "config", "--no-env-resolution",
        "--format", "json",
    ]
    rendered = subprocess.run(
        command, cwd=ROOT, env={**os.environ, **placeholders, **paths}, capture_output=True,
        text=True, timeout=45, check=True,
    )
    project = json.loads(rendered.stdout)
    services = project["services"]
    for name in ("otel-collector", "jaeger", "loki"):
        service = services[name]
        assert not service.get("ports")
        assert service["user"] == "10001:10001"
        assert service["read_only"]
    for name in ("otel-collector", "jaeger", "loki", "telemetry-volume-init"):
        for app in ("backend", "worker", "worker-children", "mcp"):
            assert name not in services[app].get("depends_on", {})
    assert set(services["jaeger"]["networks"]) == {"telemetry_storage"}
    assert set(services["loki"]["networks"]) == {"telemetry_storage"}
    assert set(services["otel-collector"]["networks"]) == {
        "telemetry_storage", "telemetry_ingress", "telemetry_metrics",
    }
    assert all(project["networks"][name]["internal"] for name in services["otel-collector"]["networks"])
    assert services["backend"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318"
    assert services["backend"]["environment"]["METRICS_TOKEN_FILE"] == "/run/secrets/testlookup-metrics-token"
    assert services["grafana"]["environment"]["GF_FEATURE_TOGGLES_ENABLE"] == "jaegerEnableGrpcEndpoint"
    assert services["telemetry-volume-init"]["network_mode"] == "none"
