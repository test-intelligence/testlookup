"""The aws-eks overlay lets the load balancer reach every pod it targets.

Security review 2026-11-20, TL-05. The EKS overlay uses an ALB with
``target-type: ip``, which sends straight to pod addresses. The base
NetworkPolicy objects admit an in-cluster ingress controller by namespace, and
the overlay added the load balancer's address range to ``allow-mcp`` only.
So with NetworkPolicy enforcement on (the VPC CNI's ``enableNetworkPolicy``),
``default-deny-all`` refused the ALB at the frontend and backend and the
application stopped answering. That is why the Terraform switch was left off,
and with it off nothing isolates the data stores from other workloads.

The rule: for each of the three Services the ALB targets, the overlay adds an
``ipBlock`` source to the first ingress rule of that Service's policy, and that
rule names only the Service's own port. Nothing else is opened.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "k8s" / "overlays" / "aws-eks" / "kustomization.yaml"
BASE_POLICIES = ROOT / "k8s" / "base" / "networkpolicy.yaml"
TERRAFORM = ROOT / "infra" / "aws" / "terraform"

#: Policy -> the one pod port the ALB targets behind it.
ALB_TARGETS = {"allow-frontend": 8080, "allow-backend": 8000, "allow-mcp": 8002}


def _policy_patches() -> dict[str, list[dict]]:
    overlay = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))
    found: dict[str, list[dict]] = {}
    for patch in overlay["patches"]:
        target = patch.get("target") or {}
        if target.get("kind") == "NetworkPolicy":
            found.setdefault(target["name"], []).extend(yaml.safe_load(patch["patch"]))
    return found


def _base_policies() -> dict[str, dict]:
    docs = yaml.safe_load_all(BASE_POLICIES.read_text(encoding="utf-8"))
    return {doc["metadata"]["name"]: doc for doc in docs if doc and doc.get("kind") == "NetworkPolicy"}


@pytest.mark.parametrize("policy", sorted(ALB_TARGETS))
def test_the_overlay_admits_the_load_balancer_to_each_service_it_targets(policy: str):
    operations = _policy_patches().get(policy)
    assert operations, f"the aws-eks overlay does not admit the ALB to {policy}"
    assert operations == [{
        "op": "add", "path": "/spec/ingress/0/from/-", "value": {"ipBlock": {"cidr": operations[0]["value"]["ipBlock"]["cidr"]}},
    }], f"{policy}: the ALB rule must only append one ipBlock source to the first ingress rule"


@pytest.mark.parametrize(("policy", "port"), sorted(ALB_TARGETS.items()))
def test_the_rule_it_extends_opens_only_that_services_port(policy: str, port: int):
    rule = _base_policies()[policy]["spec"]["ingress"][0]
    assert [p["port"] for p in rule["ports"]] == [port], (
        f"{policy}: its first ingress rule now covers other ports, and the overlay "
        "would open them to the ALB's address range as well"
    )


def test_no_other_policy_is_opened_to_an_address_range():
    assert set(_policy_patches()) == set(ALB_TARGETS), (
        "the overlay patches a NetworkPolicy the ALB does not target (a data store?)"
    )


def test_terraform_reports_the_subnets_to_narrow_the_rule_to():
    outputs = (TERRAFORM / "outputs.tf").read_text(encoding="utf-8")
    network = (TERRAFORM / "modules" / "network" / "outputs.tf").read_text(encoding="utf-8")
    assert 'output "public_subnet_cidrs"' in outputs and "module.network.public_subnet_cidrs" in outputs
    assert 'output "public_subnet_cidrs"' in network and "aws_subnet.public[*].cidr_block" in network
