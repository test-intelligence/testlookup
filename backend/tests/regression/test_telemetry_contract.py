"""Default required telemetry is independent of scrape discovery and traffic."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
MONITORING = ROOT / "infra/monitoring"
pytestmark = pytest.mark.skipif(not MONITORING.exists(), reason="infra absent")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def records(document: dict) -> list[dict]:
    return [rule for group in document["groups"] for rule in group["rules"] if "record" in rule]


def test_compose_contract_matches_both_scrape_auth_profiles():
    contract = records(load(MONITORING / "prometheus-rules/compose-telemetry-contract.yml"))
    expected = {(r["labels"]["job"], r["labels"]["telemetry_role"]) for r in contract}
    assert expected == {
        ("testlookup-backend", "backend"),
        ("testlookup-worker", "worker"),
        ("testlookup-worker", "worker-children"),
    }
    assert len(contract) == len(expected)
    assert all(r["expr"] == "vector(1)" for r in contract), "inventory must survive deleted scrape jobs"
    assert all(r["labels"]["telemetry_profile"] == "compose" for r in contract)
    main = load(MONITORING / "prometheus.yml")
    assert "rules/*.yml" in main["rule_files"]
    for source in ("backend-scrape.yml", "backend-scrape-authenticated.yml"):
        jobs = main["scrape_configs"] + load(MONITORING / source)["scrape_configs"]
        actual = {
            (job["job_name"], config["labels"]["telemetry_role"])
            for job in jobs for config in job["static_configs"]
            if config.get("labels", {}).get("telemetry_profile") == "compose"
        }
        assert expected == actual


def test_operator_contract_matches_rendered_worker_roles_and_target_labels():
    directory = ROOT / "k8s/monitoring"
    resource = load(directory / "telemetry-contract.yaml")
    contract = records(resource["spec"])
    deployments = []
    for source in ("backend-deployment.yaml", "worker-deployments.yaml", "worker-children-deployment.yaml"):
        deployments.extend(
            d for d in yaml.safe_load_all((ROOT / "k8s/base" / source).read_text(encoding="utf-8"))
            if d and d.get("kind") == "Deployment"
            and d["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/component") in {"api", "worker"}
        )
    expected_roles = {d["spec"]["template"]["metadata"]["labels"]["app"].removeprefix("testlookup-") for d in deployments}
    assert len(contract) == len(expected_roles) == 6
    assert {r["labels"]["telemetry_role"] for r in contract} == expected_roles
    assert all(r["expr"] == "vector(1)" for r in contract)
    assert all(r["labels"]["namespace"] == resource["metadata"]["namespace"] == "testlookup" for r in contract)
    assert all(r["labels"]["telemetry_profile"] == "kubernetes" for r in contract)
    monitors = list(yaml.safe_load_all((directory / "podmonitors.yaml").read_text(encoding="utf-8")))
    for deployment in deployments:
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        monitor, = (m for m in monitors if all(labels.get(k) == v for k, v in m["spec"]["selector"]["matchLabels"].items()))
        output = {}
        for relabel in monitor["spec"]["podMetricsEndpoints"][0]["relabelings"]:
            sources = relabel.get("sourceLabels", [])
            value = ";".join(labels["app"] if s == "__meta_kubernetes_pod_label_app" else "" for s in sources)
            match = re.fullmatch(relabel.get("regex", "(.*)"), value)
            if match:
                output[relabel["targetLabel"]] = re.sub(r"\$(\d+)", lambda m: match.group(int(m[1])), relabel["replacement"])
        role = labels["app"].removeprefix("testlookup-")
        expected, = (r["labels"] for r in contract if r["labels"]["telemetry_role"] == role)
        assert output == {k: v for k, v in expected.items() if k != "namespace"}
    # A site's common labels apply to both PrometheusRules and both monitors.
    resources = load(directory / "kustomization.yaml")["resources"]
    assert "telemetry-contract.yaml" in resources
    assert resource["metadata"]["labels"] == load(directory / "prometheusrule.yaml")["metadata"]["labels"]
    assert monitors[0]["metadata"]["labels"].items() <= resource["metadata"]["labels"].items()


def test_required_collectors_are_an_explicit_unconditional_baseline():
    from app.services.metric_collection import COLLECTORS

    document = load(MONITORING / "prometheus-rules/testlookup-alerts.yml")
    collector_rule, = (r for r in records(document) if r["record"] == "testlookup:telemetry_required_collector")
    baseline = set(re.findall(r'"collector", "([a-z_]+)"', collector_rule["expr"]))
    assert baseline == {"queues", "agents", "downstream", "redis"}
    assert baseline <= set(COLLECTORS), "a required collector must be initialized by the application"
    # Later feature/profile-specific collectors require a deliberate contract
    # decision; they are not automatically mandatory in every installation.
    assert "up{job=\"testlookup-backend\"} == 1" in collector_rule["expr"]
