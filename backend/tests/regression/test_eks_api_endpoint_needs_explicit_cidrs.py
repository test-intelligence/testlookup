"""The EKS API endpoint is never open to the internet by default.

``infra/aws/terraform`` set ``endpoint_public_access = true`` with
``public_access_cidrs`` defaulting to ``["0.0.0.0/0"]``, and the prod tfvars
left the CIDR line commented out, so a default apply published the cluster API
to every address (OSS review 2026-11-12, L-8). The variable now has no default:
Terraform asks for it unless the tfvars file sets it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

TERRAFORM = Path(__file__).resolve().parents[3] / "infra" / "aws" / "terraform"


def _variable_block(name: str) -> str:
    text = (TERRAFORM / "variables.tf").read_text(encoding="utf-8")
    start = text.index(f'variable "{name}" {{')
    depth = 0
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0 and text[index] == "}":
            return text[start:index + 1]
    raise AssertionError(f"unterminated variable block: {name}")


def test_public_access_cidrs_has_no_default():
    block = _variable_block("public_access_cidrs")
    assert not re.search(r"^\s*default\s*=", block, re.MULTILINE), block
    assert "cidrhost" in block, "expected a validation that each entry is a CIDR block"


def test_no_environment_opens_the_endpoint_to_everyone():
    for tfvars in sorted((TERRAFORM / "environments").glob("*.tfvars")):
        live = [line for line in tfvars.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")]
        assert not [line for line in live if "0.0.0.0/0" in line], tfvars.name
