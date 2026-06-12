"""
Architectural agent-contract ratchet (AIQ-P1).

Parallel to ``test_architectural_transaction_boundaries.py`` — this one
ratchets the **structured agent contract** discipline introduced in
phase AIQ-P1:

  * Every analytic agent must wrap its output through
    ``validate_agent_contract`` so consumers get audit-friendly contract
    metadata (confidence_score, evidence_count, decision_reason, …)
    stamped under ``agent_contracts`` without changing the agent's own
    output shape.

  * Infrastructure / orchestration agents that do not themselves emit a
    contracted analytic payload are explicitly allowlisted.

The tests are DB-free: they use ``ast`` static analysis plus pure
imports, so no fixtures or database sessions are required.

The tests fail if:

  1. A non-allowlisted agent module stops calling
     ``validate_agent_contract`` (regression).
  2. The required contract metadata fields disappear, or the count of
     contracted output models drops below the ratchet floor.
  3. ``validate_agent_contract`` stops stamping the required fields or
     starts raising on a normal payload.
"""
from __future__ import annotations

import ast
from pathlib import Path

# Infrastructure / orchestration agents that do not emit a contracted
# analytic payload of their own. Everything else under app/agents/ must
# call ``validate_agent_contract``.
INFRA_ALLOWLIST: frozenset[str] = frozenset({
    "__init__.py",
    "base.py",
    "state.py",
    "workflow.py",
    "conversation.py",
    "contract_agent.py",
    "defect_commander.py",
    "agent_planner.py",
})

AGENTS_DIR = Path(__file__).resolve().parents[1] / "app" / "agents"


def _agent_source_files() -> list[Path]:
    """Top-level .py files under backend/app/agents/."""
    return sorted(AGENTS_DIR.glob("*.py"))


def _calls_validate_agent_contract(source: Path) -> bool:
    """True if the module contains a call to ``validate_agent_contract``."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "validate_agent_contract":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "validate_agent_contract":
            return True
    return False


# ── The tests ───────────────────────────────────────────────────────────────


def test_every_analytic_agent_calls_validate_agent_contract() -> None:
    """Every non-allowlisted agent module must wrap its output through
    ``validate_agent_contract``. New analytic agents added without the
    contract wrap fail CI.
    """
    offenders: list[str] = []
    for source in _agent_source_files():
        if source.name in INFRA_ALLOWLIST:
            continue
        if not _calls_validate_agent_contract(source):
            offenders.append(source.name)

    assert not offenders, (
        "Analytic agents must call validate_agent_contract to stamp "
        "structured contract metadata onto their output:\n  "
        + "\n  ".join(offenders)
        + "\n\nFix: wrap the return payload with validate_agent_contract("
        "<Output>, payload, agent_name=..., ...), or add the module to "
        "INFRA_ALLOWLIST with a reason if it does not emit a contracted "
        "analytic payload."
    )


def test_contract_metadata_has_required_fields() -> None:
    """The contract metadata model must expose the required fields, and the
    number of contracted output models must not drop below the ratchet floor.
    """
    from app.models import agent_contracts as ac
    from app.models.agent_contracts import (
        AgentContractMetadata,
        ContractedAgentOutput,
    )

    required = {"confidence_score", "evidence_count", "decision_reason"}
    assert required <= set(AgentContractMetadata.model_fields), (
        "AgentContractMetadata is missing required contract fields: "
        f"{sorted(required - set(AgentContractMetadata.model_fields))}"
    )

    subclasses = [
        obj
        for obj in vars(ac).values()
        if isinstance(obj, type)
        and issubclass(obj, ContractedAgentOutput)
        and obj is not ContractedAgentOutput
    ]

    for subclass in subclasses:
        assert "contract" in subclass.model_fields, (
            f"{subclass.__name__} must carry the 'contract' field "
            "(inherit from ContractedAgentOutput)."
        )

    assert len(subclasses) >= 12, (
        f"Only {len(subclasses)} contracted output models found — the "
        "ratchet floor is 12. Removing a contracted output model regresses "
        "agent-contract coverage."
    )


def test_validator_stamps_required_fields_and_never_raises() -> None:
    """``validate_agent_contract`` must stamp the required fields onto the
    returned payload and must never raise on a normal payload, even when
    confidence is out of range.
    """
    from app.models.agent_contracts import (
        RunCompareAgentOutput,
        validate_agent_contract,
    )

    result = validate_agent_contract(
        RunCompareAgentOutput,
        {"status": "ready"},
        agent_name="run_compare",
        confidence=70,
        evidence_refs=[{"type": "x", "id": "y"}],
        decision_reason="t",
    )
    contract = result["agent_contracts"]["run_compare"]
    assert contract["confidence_score"] == 70
    assert contract["evidence_count"] == 1
    assert contract["decision_reason"] == "t"

    # Happy path is non-raising and confidence is clamped to [0, 100].
    clamped = validate_agent_contract(
        RunCompareAgentOutput,
        {"status": "ready"},
        agent_name="run_compare",
        confidence=150,
        evidence_refs=[],
        decision_reason="clamp",
    )
    assert clamped["agent_contracts"]["run_compare"]["confidence_score"] == 100
