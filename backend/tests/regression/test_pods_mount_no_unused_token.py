"""A pod that never calls the Kubernetes API gets no service-account token.

Kubernetes mounts the namespace's default service-account token into every pod
unless told not to. The frontend, the MCP server, Ollama and the bundled data
stores never call the API, so a compromise of any of them handed the attacker a
credential for nothing (OSS review 2026-11-12, L-8). Pods on ``testlookup-sa``
keep theirs: the backend and workers use it for Vault's Kubernetes auth
(``secret_providers/vault.py``) and to tell a cluster from Compose.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
# Service accounts whose token a workload really uses.
TOKEN_USERS = {"testlookup-sa"}


def _pod_specs():
    for path in sorted((ROOT / "k8s").rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(doc, dict) or doc.get("kind") not in WORKLOADS:
                continue
            spec = doc.get("spec") or {}
            if doc["kind"] == "CronJob":
                spec = (spec.get("jobTemplate") or {}).get("spec") or {}
            pod = (spec.get("template") or {}).get("spec") or {}
            if pod.get("containers"):  # a full workload, not a kustomize patch
                yield f"{path.relative_to(ROOT).as_posix()}:{doc['metadata']['name']}", pod


def test_pods_without_an_api_identity_mount_no_token():
    pods = dict(_pod_specs())
    assert len(pods) >= 15, f"expected the app, worker and data-store workloads, found {len(pods)}"
    mounting = sorted(name for name, pod in pods.items()
                      if pod.get("serviceAccountName") not in TOKEN_USERS
                      and pod.get("automountServiceAccountToken") is not False)
    assert not mounting, (
        "these pods get the default service-account token although nothing in them calls the "
        "Kubernetes API; set automountServiceAccountToken: false: " + ", ".join(mounting)
    )


def test_the_api_users_keep_their_token():
    """Vault's Kubernetes auth reads the token; turning it off breaks secret loading."""
    users = {name: pod for name, pod in _pod_specs() if pod.get("serviceAccountName") in TOKEN_USERS}
    assert any("testlookup-backend" in name for name in users), sorted(users)
    assert not [name for name, pod in users.items() if pod.get("automountServiceAccountToken") is False]
