"""The shipped AWS Terraform can enforce NetworkPolicy and locks down node metadata.

NetworkPolicy remains an opt-in switch because it cannot be turned on for a
running cluster without preparation:

* ``enable_network_policy`` sets ``enableNetworkPolicy = "true"`` on the
  ``vpc-cni`` addon. The AWS VPC CNI accepts every NetworkPolicy object and
  enforces none of them until that setting is on. Once it is on,
  ``default-deny-all`` really applies, so the policies must first admit the
  load balancer to the frontend and backend pods.
Node metadata hardening is mandatory. ``require_imdsv2`` remains as a
compatibility input fixed to true and starts nodes from a launch template that
requires IMDSv2 with a hop limit of 1, so a pod off the host network cannot ask
the instance metadata service for the node role's credentials. A node group
cannot adopt a launch template in place: applying the hardening replaces the
group.

The rules pinned here:

* The launch template is always present and never takes over what the node
  group sets.
* The root and EKS modules default metadata hardening to true and reject false.
* Every shipped environment explicitly keeps metadata hardening enabled.
* The optional NetworkPolicy switch changes only the VPC CNI setting.
* The two deployment guides say that the cluster must enforce NetworkPolicy,
  name the switch for EKS, AKS and GKE, and document mandatory node hardening.

Read from the files, not from a plan: nothing here needs AWS or Terraform.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
TERRAFORM = ROOT / "infra" / "aws" / "terraform"
EKS_MODULE = TERRAFORM / "modules" / "eks" / "main.tf"
CONFIGURATION_INPUTS = ("enable_network_policy", "require_imdsv2")
GUIDES = (
    ROOT / "docs" / "operations" / "deploy-kubernetes.md",
    ROOT / "docs" / "operations" / "deploy-cloud.md",
)


def _without_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _block(text: str, header: str) -> str:
    """The body of the first ``header { ... }`` block, braces matched."""
    found = re.search(header + r"\s*\{", text)
    assert found, f"no block matching {header!r} in {EKS_MODULE.name}"
    depth, start = 1, found.end()
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start:index]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _setting(body: str, name: str) -> str | None:
    found = re.search(rf"^\s*{name}\s*=\s*(.+?)\s*$", body, re.MULTILINE)
    return found.group(1) if found else None


@pytest.fixture(scope="module")
def module() -> str:
    return _without_comments(EKS_MODULE.read_text(encoding="utf-8"))


def _variables(path: Path) -> dict[str, str]:
    text = _without_comments(path.read_text(encoding="utf-8"))
    return {name: _block(text, rf'variable\s+"{name}"') for name in re.findall(r'variable\s+"(\w+)"', text)}


@pytest.mark.parametrize("where", ["modules/eks/variables.tf", "variables.tf"])
def test_network_policy_switch_is_off_by_default(where):
    declared = _variables(TERRAFORM / where)
    switch = declared["enable_network_policy"]
    assert _setting(switch, "type") == "bool"
    assert _setting(switch, "default") == "false", (
        "enable_network_policy must stay opt-in: turning it on changes a running cluster"
    )


@pytest.mark.parametrize("where", ["modules/eks/variables.tf", "variables.tf"])
def test_imdsv2_is_mandatory(where):
    declared = _variables(TERRAFORM / where)
    control = declared["require_imdsv2"]
    assert _setting(control, "type") == "bool"
    assert _setting(control, "default") == "true"
    validation = _block(control, r"validation")
    assert _setting(validation, "condition") == "var.require_imdsv2"


@pytest.mark.parametrize("switch", CONFIGURATION_INPUTS)
def test_the_root_module_passes_each_switch_to_the_eks_module(switch):
    root = _without_comments((TERRAFORM / "main.tf").read_text(encoding="utf-8"))
    assert _setting(_block(root, r'module\s+"eks"'), switch) == f"var.{switch}"


def test_the_switch_turns_network_policy_enforcement_on_for_vpc_cni_only(module):
    addon = _block(module, r'resource\s+"aws_eks_addon"\s+"this"')
    assert _setting(addon, "configuration_values") == (
        "lookup(local.addon_configuration_values, each.value, null)"
    ), "an addon that has no entry must get null, which leaves configuration_values unset"

    # On: one entry, for vpc-cni, with the setting as the string "true" (the
    # addon schema takes a string). Off: an empty map, so every lookup is null.
    assert re.search(
        r'addon_configuration_values\s*=\s*var\.enable_network_policy\s*\?\s*\{\s*'
        r'"vpc-cni"\s*=\s*jsonencode\(\{\s*enableNetworkPolicy\s*=\s*"true"\s*\}\)\s*'
        r'\}\s*:\s*\{\s*\}', module
    ), "addon_configuration_values is not: switch on -> vpc-cni enforcement only, switch off -> nothing"


def test_the_other_addons_are_still_installed(module):
    addon = _block(module, r'resource\s+"aws_eks_addon"\s+"this"')
    for_each = _setting(addon, "for_each") or ""
    for name in ("vpc-cni", "coredns", "kube-proxy", "aws-ebs-csi-driver"):
        assert f'"{name}"' in for_each, f"the {name} addon is no longer installed"


def test_nodes_require_imdsv2_with_a_hop_limit_of_one(module):
    template = _block(module, r'resource\s+"aws_launch_template"\s+"\w+"')
    assert _setting(template, "count") == "1", (
        "the hardened launch template must always exist"
    )
    options = _block(template, r"metadata_options")
    assert _setting(options, "http_tokens") == '"required"'
    assert _setting(options, "http_put_response_hop_limit") == "1"
    assert _setting(options, "http_endpoint") != '"disabled"', (
        "the kubelet and the VPC CNI on the node itself still need the metadata service"
    )


def test_the_node_group_uses_that_launch_template_and_keeps_its_settings(module):
    name = re.search(r'resource\s+"aws_launch_template"\s+"(\w+)"', module)
    assert name, "no launch template in the EKS module"
    group = _block(module, r'resource\s+"aws_eks_node_group"\s+"this"')
    # A dynamic block over the single counted resource retains the state address
    # used by clusters that had already enabled metadata hardening.
    reference = _block(group, r'dynamic\s+"launch_template"')
    assert _setting(reference, "for_each") == f"aws_launch_template.{name.group(1)}"
    content = _block(reference, r"content")
    assert _setting(content, "id") == "launch_template.value.id"
    assert _setting(content, "version") == "launch_template.value.latest_version"
    assert not re.search(r"^\s*launch_template\s*\{", group, re.MULTILINE), (
        "the node group has an unconditional launch_template block"
    )

    # What the module set before the launch template was added must still be
    # set on the node group: a launch template that took these over would
    # change the nodes without anyone asking.
    assert _setting(group, "instance_types") == "var.node_instance_types"
    assert _setting(group, "subnet_ids") == "var.private_subnet_ids"
    assert _setting(group, "node_role_arn") == "aws_iam_role.node.arn"
    scaling = _block(group, r"scaling_config")
    assert _setting(scaling, "desired_size") == "var.node_desired_size"
    assert _setting(scaling, "min_size") == "var.node_min_size"
    assert _setting(scaling, "max_size") == "var.node_max_size"

    template = _block(module, r'resource\s+"aws_launch_template"\s+"\w+"')
    for owned_by_the_node_group in ("instance_type", "image_id", "user_data", "iam_instance_profile"):
        assert _setting(template, owned_by_the_node_group) is None, (
            f"the launch template sets {owned_by_the_node_group}; the managed node group owns it"
        )
    assert "block_device_mappings" not in template, "the launch template changes the node disk"


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.name)
def test_the_guides_say_the_cluster_must_enforce_network_policy(guide):
    text = guide.read_text(encoding="utf-8")
    assert "NetworkPolicy must be enforced" in text
    # One switch per managed platform, by the name an operator would search for.
    for switch in ("enableNetworkPolicy", "--network-policy", "Dataplane V2"):
        assert switch in text, f"{guide.name} does not name the switch {switch!r}"


def test_all_shipped_environments_require_imdsv2():
    for path in sorted((TERRAFORM / "environments").glob("*.tfvars")):
        text = _without_comments(path.read_text(encoding="utf-8"))
        assert _setting(text, "require_imdsv2") == "true", (
            f"{path.name} disables node hardening"
        )


def test_the_cloud_guide_documents_mandatory_imdsv2():
    text = GUIDES[1].read_text(encoding="utf-8")
    for switch in CONFIGURATION_INPUTS:
        assert f"`{switch}`" in text, f"deploy-cloud.md does not name the Terraform variable {switch}"
    assert "cannot be set to `false`" in text
