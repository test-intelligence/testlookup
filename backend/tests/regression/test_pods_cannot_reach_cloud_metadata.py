"""No NetworkPolicy lets a pod reach the cloud metadata service.

The backend and the workers had ``egress: [{}]``, which includes
169.254.169.254: on EC2, GCE and Azure VMs that address hands the node's cloud
credentials to any process that asks, so a compromised pod could act as the
node (OSS review 2026-11-12, L-8). Their egress is now every pod in the
cluster plus everything outside it except the metadata address (IPv4, and AWS's
IPv6 one).

Checked on the reference K3s install (kube-router) with a throwaway namespace:
with this shape and one extra address in ``except``, that address was refused
while a control pod without it connected, and SMTP to the internet stayed open.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
METADATA = {"0.0.0.0/0": "169.254.169.254/32", "::/0": "fd00:ec2::254/128"}


def _egress_policies():
    for path in sorted((ROOT / "k8s").rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(doc, dict) or doc.get("kind") != "NetworkPolicy":
                continue
            spec = doc.get("spec") or {}
            if "Egress" in (spec.get("policyTypes") or []):
                yield f"{path.relative_to(ROOT).as_posix()}:{doc['metadata']['name']}", spec


def test_no_egress_rule_allows_every_destination():
    """A rule without ``to`` (``{}``, or ports alone) reaches the metadata address."""
    policies = dict(_egress_policies())
    assert len(policies) >= 6, f"expected the base and component policies, found {len(policies)}"
    open_rules = sorted(name for name, spec in policies.items()
                        for rule in spec.get("egress") or [] if not (rule or {}).get("to"))
    assert not open_rules, f"these policies allow egress to every address: {open_rules}"


def test_every_world_wide_ip_block_excepts_the_metadata_address():
    missing = []
    for name, spec in _egress_policies():
        for rule in spec.get("egress") or []:
            for peer in rule.get("to") or []:
                block = peer.get("ipBlock") or {}
                needed = METADATA.get(block.get("cidr", ""))
                if needed and needed not in (block.get("except") or []):
                    missing.append(f"{name}: {block['cidr']} without {needed}")
    assert not missing, missing


def test_the_backend_and_workers_still_reach_the_cluster_and_the_internet():
    """They talk to data stores in other namespaces, LLM providers, SMTP and Jira."""
    policies = dict(_egress_policies())
    for wanted in ("k8s/base/networkpolicy.yaml:allow-backend", "k8s/base/networkpolicy.yaml:allow-workers"):
        peers = [peer for rule in policies[wanted]["egress"] for peer in rule.get("to") or []]
        assert {"namespaceSelector": {}} in peers, f"{wanted}: no egress to the cluster's pods"
        cidrs = {(peer.get("ipBlock") or {}).get("cidr") for peer in peers}
        assert {"0.0.0.0/0", "::/0"} <= cidrs, f"{wanted}: no egress outside the cluster ({cidrs})"
