"""On OpenShift only the stock data-store images run under the ``anyuid`` SCC.

``openshiftsetup/deploy-openshift-artifactory.sh`` granted ``anyuid`` to the
namespace's ``default`` ServiceAccount: the account of the frontend, MCP, the
backup jobs, the migration Job and of any pod later created in the namespace
without naming an account. Any of those could then run as root.

The rules:

* ``anyuid`` goes to one ServiceAccount, ``testlookup-anyuid``, used by exactly
  the pods whose stock image starts as root or with its own UID: PostgreSQL,
  MongoDB, Valkey, the object store, ChromaDB and Ollama.
* Every other pod runs with a fixed NON-ZERO UID, no added capability and no
  privilege escalation, which the ``nonroot-v2`` SCC admits; the script grants
  that one to ``testlookup-sa`` and ``default``. No pod asks for UID 0.
* ``GRANT_ANYUID`` stays on by default (the data stores do not start without
  it) and ``--no-anyuid`` / ``GRANT_ANYUID=false`` still skip the grant.

Rendered with kustomize and run against a stand-in for ``oc``; no cluster.
"""
from __future__ import annotations

import functools
import shutil
import subprocess

import pytest
import yaml

from tests.regression.openshift_deploy_harness import ROOT, SCRIPT, read, run_scenarios

pytestmark = pytest.mark.regression

OVERLAY = ROOT / "k8s" / "overlays" / "openshift-artifactory"
ANYUID_SA = "testlookup-anyuid"
# Stock third-party images: each starts as root (and drops to its own user) or
# runs as root by design (Ollama, k8s/base/ollama-deployment.yaml).
NEED_ANYUID = {
    "testlookup-postgres", "testlookup-mongo", "testlookup-redis",
    "testlookup-minio", "testlookup-chromadb", "testlookup-ollama",
}
NONROOT_ACCOUNTS = {None, "default", "testlookup-sa"}
# The deploy script's pull secret and the one the offline bundle's guide creates.
PULL_SECRETS = ("artifactory-pull", "local-registry")


@functools.lru_cache(maxsize=None)
def _rendered() -> tuple[dict, ...]:
    kustomize, kubectl = shutil.which("kustomize"), shutil.which("kubectl")
    if kustomize:
        command = [kustomize, "build", str(OVERLAY)]
    elif kubectl:
        command = [kubectl, "kustomize", str(OVERLAY)]
    else:
        pytest.skip("neither kustomize nor kubectl is installed (rendering only; no cluster access)")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0 and 'unknown command "kustomize"' in result.stderr:
        pytest.skip(f"{command[0]} has no kustomize command (a version-picking wrapper)")
    assert result.returncode == 0, result.stderr
    return tuple(d for d in yaml.safe_load_all(result.stdout) if d)


def _pods() -> dict[str, dict]:
    """Workload name -> pod spec, for every workload the overlay renders."""
    pods = {}
    for doc in _rendered():
        spec = doc.get("spec") or {}
        if doc["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        if doc["kind"] in ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"):
            pods[doc["metadata"]["name"]] = spec["template"]["spec"]
    return pods


def _containers(pod: dict) -> list[dict]:
    return [*(pod.get("initContainers") or []), *pod["containers"]]


def _service_accounts() -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in _rendered() if d["kind"] == "ServiceAccount"}


def test_exactly_the_stock_images_use_the_anyuid_account():
    pods = _pods()
    assert len(pods) >= 15, sorted(pods)
    using = {name for name, pod in pods.items() if pod.get("serviceAccountName") == ANYUID_SA}
    assert using == NEED_ANYUID


def test_the_anyuid_account_exists_and_can_pull():
    accounts = _service_accounts()
    assert ANYUID_SA in accounts, sorted(accounts)
    for name in (ANYUID_SA, "testlookup-sa"):
        # The deploy script and the offline-bundle procedure link their pull
        # secret to `default` only; a pod on another account pulls through its
        # own account's link.
        for secret in PULL_SECRETS:
            assert {"name": secret} in (accounts[name].get("imagePullSecrets") or []), (name, secret)


def test_every_other_pod_is_admitted_without_anyuid():
    """What the nonroot-v2 SCC requires: a non-zero UID stated in the manifest,
    no privilege escalation, every capability dropped."""
    problems = []
    for name, pod in _pods().items():
        if name in NEED_ANYUID:
            continue
        if pod.get("serviceAccountName") not in NONROOT_ACCOUNTS:
            problems.append(f"{name}: account {pod.get('serviceAccountName')}")
        pod_context = pod.get("securityContext") or {}
        for container in _containers(pod):
            context = container.get("securityContext") or {}
            where = f"{name}/{container['name']}"
            uid = context.get("runAsUser", pod_context.get("runAsUser"))
            if not isinstance(uid, int) or uid == 0:
                problems.append(f"{where}: runAsUser is {uid!r}")
            if context.get("allowPrivilegeEscalation") is not False:
                problems.append(f"{where}: allowPrivilegeEscalation is not false")
            if context.get("privileged"):
                problems.append(f"{where}: privileged")
            capabilities = context.get("capabilities") or {}
            if capabilities.get("drop") != ["ALL"] or capabilities.get("add"):
                problems.append(f"{where}: capabilities {capabilities}")
    assert not problems, "these pods need more than nonroot-v2 gives:\n" + "\n".join(problems)


def test_no_pod_asks_for_uid_zero():
    asking = [f"{name}/{container['name']}" for name, pod in _pods().items()
              for container in _containers(pod)
              if (container.get("securityContext") or {}).get("runAsUser") == 0
              or (container.get("securityContext") or {}).get("runAsNonRoot") is False]
    assert not asking, asking


def test_the_frontend_still_serves_the_rendered_server_block():
    """Positive case for the frontend, which ran as root here until the image
    moved to uid 101: the ConfigMap is still what nginx loads, and the writable
    paths of the base manifest are still mounted."""
    pod = _pods()["testlookup-frontend"]
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["nginx-conf-d"] == {"name": "nginx-conf-d", "configMap": {"name": "frontend-nginx-conf"}}
    mounts = {mount["name"]: mount["mountPath"] for mount in pod["containers"][0]["volumeMounts"]}
    assert mounts["nginx-conf-d"] == "/etc/nginx/conf.d" and mounts["tmp"] == "/tmp"
    assert pod["containers"][0]["command"] == ["nginx", "-g", "daemon off;"]
    assert pod["containers"][0]["securityContext"]["runAsUser"] == 101


# ── The deploy script ──────────────────────────────────────────────────────

BODY = r"""
NO_ANYUID=false; GRANT_ANYUID=true; GRANT_NONROOT=true
scenario default
grant_sccs
GRANT_ANYUID=false
scenario anyuid-off
grant_sccs
GRANT_ANYUID=true; NO_ANYUID=true
scenario no-anyuid-flag
grant_sccs
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    work = tmp_path_factory.mktemp("openshift-scc")
    result = run_scenarios(work, BODY)
    assert result.returncode == 0, result.stderr
    return work


def _grants(directory) -> set[tuple[str, str]]:
    """(scc, service account) for every grant the script asked for."""
    grants = set()
    for call in read(directory / "argv.log").splitlines():
        words = call.split()
        if "add-scc-to-user" in words:
            grants.add((words[words.index("add-scc-to-user") + 1], words[words.index("-z") + 1]))
    return grants


def test_anyuid_goes_to_the_one_account_and_nonroot_to_the_rest(run):
    assert _grants(run / "default") == {
        ("anyuid", ANYUID_SA), ("nonroot-v2", "default"), ("nonroot-v2", "testlookup-sa"),
    }


@pytest.mark.parametrize("scenario", ["anyuid-off", "no-anyuid-flag"])
def test_a_hardened_cluster_can_still_skip_the_anyuid_grant(run, scenario):
    grants = _grants(run / scenario)
    assert not [grant for grant in grants if grant[0] == "anyuid"], grants
    assert ("nonroot-v2", "default") in grants


def test_the_default_account_is_never_given_anyuid():
    code = "\n".join(line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    assert "add-scc-to-user anyuid -z default" not in code
    assert "grant_scc anyuid default" not in code


def test_the_anyuid_grant_stays_on_by_default():
    """The data stores do not start under restricted-v2; turning the default
    off would break every install that does not pre-provision an SCC."""
    assert 'GRANT_ANYUID="${GRANT_ANYUID:-true}"' in SCRIPT.read_text(encoding="utf-8")
