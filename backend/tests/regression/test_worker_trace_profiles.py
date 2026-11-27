"""Opt-in rendered worker/Beat roles retain private mutual-TLS transport."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("profile", ["prod-telemetry", "k3s-telemetry", "openshift-telemetry"])
def test_rendered_consumer_and_standalone_beat_trace_roles(profile):
    kustomize = shutil.which("kustomize")
    if kustomize is None:
        pytest.skip("Kustomize renderer is unavailable")
    rendered = subprocess.run([kustomize, "build", str(ROOT / "k8s/overlays" / profile)],
                              capture_output=True, text=True, timeout=40, check=True)
    documents = list(yaml.safe_load_all(rendered.stdout))
    workloads = {item["metadata"]["name"]: item for item in documents if item.get("kind") == "Deployment"}
    expected = {
        **{name:"worker" for name in ("testlookup-worker-critical", "testlookup-worker-ingestion",
                                      "testlookup-worker-ai", "testlookup-worker-default")},
        "testlookup-worker-children":"worker-children", "testlookup-beat":"beat",
    }
    for name, role in expected.items():
        spec = workloads[name]["spec"]["template"]["spec"]
        container, = spec["containers"]
        env = {entry["name"]:entry.get("value") for entry in container.get("env", [])}
        assert env["OTEL_ENABLED"] == "true"
        assert env["OTEL_LOGS_ROLE"] == role
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://testlookup-otel.testlookup-telemetry.svc:4318"
        assert env["OTEL_EXPORTER_OTLP_CERTIFICATE"] == "/var/run/testlookup-otel/ca.crt"
        assert env["OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE"] == "/var/run/testlookup-otel/tls.crt"
        assert env["OTEL_EXPORTER_OTLP_CLIENT_KEY"] == "/var/run/testlookup-otel/tls.key"
        identity, = [volume for volume in spec["volumes"] if volume["name"] == "telemetry-client-identity"]
        assert identity["secret"]["secretName"] == ("testlookup-otel-beat-client" if role == "beat"
                                                     else "testlookup-otel-worker-client")
    policy, = [item for item in documents if item.get("kind") == "NetworkPolicy"
               and item["metadata"]["name"] == "allow-telemetry-client"]
    assert "scheduler" in policy["spec"]["podSelector"]["matchExpressions"][0]["values"]
    for item in documents:
        if item.get("kind") == "HorizontalPodAutoscaler":
            target = item["spec"]["scaleTargetRef"]["name"]
            if target.startswith("testlookup-worker-"):
                assert target in expected
