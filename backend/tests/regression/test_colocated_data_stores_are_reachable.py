"""The in-namespace data stores admit their callers, on their own ports only.

The openshift-artifactory overlay runs Postgres, MongoDB, Redis and the object
store inside the testlookup namespace (its infra-*.yaml, copied from the
homelab overlay), on top of the base default-deny-all, but it never shipped the
ingress allow the homelab carried in netpol-homelab.yaml. Nothing could connect
to the stores: the workers' and beat's wait-for-redis init containers waited
forever, and the migration Job and the API could not reach Postgres.

Both overlays now include k8s/components/colocated-data-stores. The expected
callers were measured from the pods' live sockets on the K3s install
(2026-10-13): the backend and the workers use all four stores, beat only Redis,
and the MCP server none. The homelab's earlier allow also admitted the MCP
server and beat to every store on every port; the component narrows that.
"""
from __future__ import annotations

import re

import pytest
import yaml

from k8s_network_policy_contract import (
    OVERLAYS,
    REPO,
    admits,
    app_pods,
    overlay_files,
    policies_for,
    template_labels,
)

# Store Deployment -> the port its Service forwards to (k8s/base/services.yaml).
STORE_PORTS = {
    "testlookup-postgres": 5432,
    "testlookup-mongo": 27017,
    "testlookup-redis": 6379,
    "testlookup-minio": 9000,
}
MIGRATIONS = REPO / "scripts" / "run-k8s-migrations.sh"


def _colocated_stores(overlay: str) -> dict[str, dict[str, str]]:
    """Store Deployment -> pod labels, for the stores this overlay runs itself."""
    pods = template_labels(overlay_files(overlay))
    return {name: labels for name, labels in pods.items() if name in STORE_PORTS}


OVERLAYS_WITH_STORES = sorted(
    path.name for path in OVERLAYS.iterdir()
    if (path / "kustomization.yaml").exists() and _colocated_stores(path.name)
)


def _callers(store: str, pods: dict[str, dict[str, str]]) -> set[str]:
    expected = {
        name for name, labels in pods.items()
        if name == "testlookup-backend" or labels.get("app.kubernetes.io/component") == "worker"
    }
    if store == "testlookup-redis":
        expected |= {
            name for name, labels in pods.items()
            if labels.get("app.kubernetes.io/component") == "scheduler"
        }
    return expected


def _migration_job_pod_labels() -> dict[str, str]:
    """Pod labels of the Job scripts/run-k8s-migrations.sh creates (a heredoc)."""
    text = MIGRATIONS.read_text(encoding="utf-8")
    match = re.search(r'cat <<EOF \| "\$KCLI" create -f -\n(.*?)\nEOF\n', text, re.DOTALL)
    assert match, "the migration Job heredoc moved; update this test"
    job = yaml.safe_load(re.sub(r"\$\{[A-Za-z_]+\}", "x", match.group(1)))
    assert job["kind"] == "Job"
    return job["spec"]["template"]["metadata"]["labels"]


def test_the_discovery_finds_both_overlays_and_all_four_stores():
    """Everything below is parametrized over this; an empty scan passes vacuously."""
    assert {"homelab", "openshift-artifactory"} <= set(OVERLAYS_WITH_STORES), OVERLAYS_WITH_STORES
    for overlay in OVERLAYS_WITH_STORES:
        assert set(_colocated_stores(overlay)) == set(STORE_PORTS), overlay


@pytest.mark.parametrize("overlay", OVERLAYS_WITH_STORES)
def test_each_store_admits_exactly_its_callers(overlay: str):
    policies = policies_for(overlay)
    pods = app_pods()
    for store, labels in _colocated_stores(overlay).items():
        port = STORE_PORTS[store]
        admitted = {name for name, src in pods.items() if admits(policies, labels, src, port)}
        assert admitted == _callers(store, pods), (
            f"{overlay}: {store}:{port} admits {sorted(admitted)}, "
            f"expected {sorted(_callers(store, pods))}"
        )


@pytest.mark.parametrize("overlay", OVERLAYS_WITH_STORES)
def test_each_store_is_closed_on_other_ports(overlay: str):
    policies = policies_for(overlay)
    pods = app_pods()
    backend, beat = pods["testlookup-backend"], pods["testlookup-beat"]
    for store, labels in _colocated_stores(overlay).items():
        assert not admits(policies, labels, backend, 22), f"{overlay}: {store} open on port 22"
        if store != "testlookup-redis":
            port = STORE_PORTS[store]
            assert not admits(policies, labels, beat, port), f"{overlay}: beat reaches {store}"


@pytest.mark.parametrize("overlay", OVERLAYS_WITH_STORES)
def test_the_migration_job_reaches_postgres(overlay: str):
    """Both deploy scripts migrate through this Job before the rollout."""
    postgres = _colocated_stores(overlay)["testlookup-postgres"]
    assert admits(policies_for(overlay), postgres, _migration_job_pod_labels(), 5432)


def test_without_the_component_openshift_artifactory_reaches_nothing():
    """The regression itself, and proof the evaluator can say no: drop the
    component's policy and the base default-deny-all leaves every store shut."""
    policies = [p for p in policies_for("openshift-artifactory") if p["metadata"]["name"] != "allow-data-stores"]
    backend = app_pods()["testlookup-backend"]
    for store, labels in _colocated_stores("openshift-artifactory").items():
        assert not admits(policies, labels, backend, STORE_PORTS[store]), store
