"""Regression guard: a specialist that produced findings always says so.

The defect
----------
``_check_contract_evidence_support`` flags any agent whose output key is
populated but which emitted no entry in ``agent_contracts``::

    if has_output and not contract:
        missing_contracts.append(agent_name)

Each specialist had several return paths, and only the full one built a
contract. ``log_intelligence``'s "no usable context" branch returned a
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
* statically: no specialist return path yields a populated output key without a
  contract -- the property, not the instances, so a path added later is covered.

Scope note (2026-08-24)
-----------------------
``contract_validation`` and ``log_intelligence`` moved out of the workflow node
and onto their agents when those agents became ``BaseAgent`` subclasses. The
static check follows them: it scans the node function **and** the agent methods
the node delegates to. Scanning only the node would have left it reading a
two-line router and reporting the property as held.
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents import workflow as wf  # noqa: E402
from app.agents.contract_agent import ContractAgent  # noqa: E402
from app.agents.log_intelligence_agent import LogIntelligenceAgent  # noqa: E402
from app.services.agent_planner import _check_contract_evidence_support  # noqa: E402

# node -> (output key, the state flag that enables it)
SPECIALISTS = {
    "contract_validation_node": ("contract_findings", "contract_agent_enabled"),
    "log_intelligence_node": ("log_findings", "log_intelligence_enabled"),
    "regression_watchman_node": ("regression_classification", "regression_watchman_enabled"),
    "change_ownership_node": ("change_ownership_findings", "change_ownership_enabled"),
}

# Every callable that can return one of those deltas. A node that delegates is
# only as guarded as the thing it delegates to.
DELEGATES = {
    "contract_validation_node": (ContractAgent.run, ContractAgent.disabled_delta),
    "log_intelligence_node": (LogIntelligenceAgent.run, LogIntelligenceAgent.disabled_delta),
}


@pytest.fixture
def quiet_lifecycle(monkeypatch):
    """Stub the stage lifecycle — it needs Postgres and Mongo, this test does not.

    Returned so a test can assert the stage still *recorded* itself; a contract
    with no stage record is the other half of the same lost-record problem.
    """
    running, done = AsyncMock(), AsyncMock()
    for agent in (ContractAgent, LogIntelligenceAgent):
        monkeypatch.setattr(agent, "mark_stage_running", running)
        monkeypatch.setattr(agent, "mark_stage_done", done)
        monkeypatch.setattr(agent, "log_decision", AsyncMock())
    return running, done


def _verify(delta: dict, output_key: str) -> dict:
    """Run the real verifier check over a state built from one delta."""
    return _check_contract_evidence_support({
        "agent_contracts": delta.get("agent_contracts") or {},
        output_key: delta.get(output_key),
    })


# ── The live failure ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_intelligence_without_context_still_contracts(quiet_lifecycle):
    """8/8 deep runs failed on exactly this path."""
    running, done = quiet_lifecycle
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

    # Having nothing to look at is a result, not an absence: the stage ran, so
    # it must appear in the timeline with a reason rather than vanish from it.
    assert running.await_count == 1, "the stage ran but never marked itself running"
    assert done.await_count == 1, "the stage finished but never marked itself done"
    assert done.await_args.kwargs["fallback_reason"] == "no_log_context"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("node_name", sorted(DELEGATES))
async def test_a_disabled_specialist_does_not_record_a_stage(node_name, quiet_lifecycle):
    """A skip is not a run: the flag-off branch must stay out of the timeline."""
    running, done = quiet_lifecycle
    delta = await getattr(wf, node_name)({"pipeline_run_id": "pipe-1"})

    assert delta["skipped_stages"], "the flag-off branch marks itself skipped"
    assert running.await_count == 0, "a stage that never executed marked itself running"
    assert done.await_count == 0, "a stage that never executed marked itself done"


# ── The property, so a path added later is covered too ───────────────────────


@pytest.mark.parametrize("node_name", sorted(SPECIALISTS))
def test_no_specialist_return_leaves_findings_uncontracted(node_name):
    """Static guard: populated output key => agent_contracts, on every path.

    Asserted over the AST rather than by exercising each branch, because the
    branches that bite are the ones nobody thought to call.
    """
    output_key = SPECIALISTS[node_name][0]
    sources = [getattr(wf, node_name), *DELEGATES.get(node_name, ())]

    offenders = []
    for source in sources:
        tree = ast.parse(inspect.getsource(source).lstrip())
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
                        offenders.append(f"{source.__qualname__}:{ret.lineno}")

    assert not offenders, (
        f"{node_name} returns a populated {output_key!r} with no agent_contracts "
        f"at {offenders} — the verifier will read that as a missing record"
    )


def test_the_static_guard_still_reaches_the_delegated_returns():
    """The guard above is only honest if it actually reads the agent methods.

    When the two specialists moved off the node, the node became a two-line
    router with no dict return at all — a guard scanning only the node would
    have gone quietly vacuous while still reporting green.
    """
    for node_name, (output_key, _flag) in SPECIALISTS.items():
        seen = 0
        for source in [getattr(wf, node_name), *DELEGATES.get(node_name, ())]:
            tree = ast.parse(inspect.getsource(source).lstrip())
            for ret in [n for n in ast.walk(tree) if isinstance(n, ast.Return)]:
                if isinstance(ret.value, ast.Dict):
                    keys = {k.value for k in ret.value.keys if isinstance(k, ast.Constant)}
                    if output_key in keys:
                        seen += 1
        assert seen >= 2, (
            f"{node_name}: the guard found only {seen} return(s) carrying "
            f"{output_key!r}; it has lost sight of the paths it is meant to check"
        )
