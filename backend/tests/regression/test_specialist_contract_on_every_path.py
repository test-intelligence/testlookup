"""Regression guard: a specialist that produced findings always says so.

The defect
----------
``_check_contract_evidence_support`` flags any agent whose output key is
populated but which emitted no entry in ``agent_contracts``::

    if has_output and not contract:
        missing_contracts.append(agent_name)

Each specialist node has several return paths, and only the full one built a
contract. ``log_intelligence_node``'s "no usable context" branch returned a
populated ``log_findings`` -- a status and a summary sentence -- with no
contract at all, and without marking itself skipped.

Measured on the deployment 2026-08-24: 8 of 8 deep runs failed
``contract_evidence_support`` with ``missing_contracts: ["log_intelligence"]``,
which fails the decision critic closed and takes every run to ``partial``. The
same hole sat in the flag-disabled branches of ``contract_validation`` and
``change_ownership``, latent only because those flags happened to be on.

An absent contract reads as a lost record, not as a negative result. A stage
that legitimately had nothing to look at has to say that in a contract --
``fallback_used`` plus a ``no_*`` reason is what makes the emptiness legible to
the verifier's ``no_evidence_ok`` branch.

What is guarded
---------------
* the no-context path emits a contract the real verifier accepts;
* so do the flag-disabled paths of all three flag-gated specialists;
* statically: no specialist node returns a populated output key without a
  contract -- the property, not the four instances, so a fifth path added later
  is covered too.
"""
from __future__ import annotations

import ast
import inspect

import pytest

pytest.importorskip("asyncpg")

from app.agents import workflow as wf  # noqa: E402
from app.services.agent_planner import _check_contract_evidence_support  # noqa: E402

# node -> (output key, the state flag that enables it)
SPECIALISTS = {
    "contract_validation_node": ("contract_findings", "contract_agent_enabled"),
    "log_intelligence_node": ("log_findings", "log_intelligence_enabled"),
    "regression_watchman_node": ("regression_classification", "regression_watchman_enabled"),
    "change_ownership_node": ("change_ownership_findings", "change_ownership_enabled"),
}


def _verify(delta: dict, output_key: str) -> dict:
    """Run the real verifier check over a state built from one node's delta."""
    return _check_contract_evidence_support({
        "agent_contracts": delta.get("agent_contracts") or {},
        output_key: delta.get(output_key),
    })


# ── The live failure ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_intelligence_without_context_still_contracts():
    """8/8 deep runs failed on exactly this path."""
    delta = await wf.log_intelligence_node({
        "log_intelligence_enabled": True,
        "pipeline_run_id": "pipe-1",
    })

    assert delta["log_findings"], "the node still reports findings"
    assert "log_intelligence" in delta["agent_contracts"], (
        "populated findings with no contract is what the verifier flags"
    )

    check = _verify(delta, "log_findings")
    assert check["status"] == "pass", check["details"]


# ── The same hole in the flag-disabled branches ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("node_name,output_key,agent_name", [
    ("log_intelligence_node", "log_findings", "log_intelligence"),
    ("contract_validation_node", "contract_findings", "contract_validation"),
    ("change_ownership_node", "change_ownership_findings", "change_ownership"),
])
async def test_a_disabled_specialist_still_contracts(node_name, output_key, agent_name):
    """Turning a flag off must not make the stage's record disappear."""
    delta = await getattr(wf, node_name)({"pipeline_run_id": "pipe-1"})

    assert delta[output_key], "the disabled node still reports findings"
    contract = (delta.get("agent_contracts") or {}).get(agent_name)
    assert contract, f"{agent_name} reported findings with no contract"
    assert contract.get("fallback_used") is True
    assert "no_" in str(contract.get("decision_reason"))

    check = _verify(delta, output_key)
    assert check["status"] == "pass", check["details"]


# ── The property, so a fifth path is covered too ─────────────────────────────


@pytest.mark.parametrize("node_name", sorted(SPECIALISTS))
def test_no_specialist_return_leaves_findings_uncontracted(node_name):
    """Static guard: populated output key => agent_contracts, on every path.

    Asserted over the AST rather than by exercising each branch, because the
    branches that bite are the ones nobody thought to call.
    """
    output_key = SPECIALISTS[node_name][0]
    tree = ast.parse(inspect.getsource(getattr(wf, node_name)))

    offenders = []
    for ret in [n for n in ast.walk(tree) if isinstance(n, ast.Return)]:
        if not isinstance(ret.value, ast.Dict):
            continue
        keys = {k.value for k in ret.value.keys if isinstance(k, ast.Constant)}
        if output_key not in keys or "agent_contracts" in keys:
            continue
        # An empty literal is falsy, so the verifier never asks for a contract.
        for k, v in zip(ret.value.keys, ret.value.values):
            if isinstance(k, ast.Constant) and k.value == output_key:
                if not (isinstance(v, ast.Dict) and not v.keys):
                    offenders.append(ret.lineno)

    assert not offenders, (
        f"{node_name} returns a populated {output_key!r} with no agent_contracts "
        f"at line(s) {offenders} — the verifier will read that as a missing record"
    )
