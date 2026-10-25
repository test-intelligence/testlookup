"""The reviewed agent-action adapter registry (W15 AGT-03, contract §2.3 test 5; owner decision D16).

Adding an adapter fails here until a person lists it in ``REVIEWED_ADAPTERS``:
the first code path where AI output causes a mutation (risk R-1) grows only by
review. D16 scope: three internal, reversible adapters plus the no-effect
acknowledge; never an external one.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.models.postgres import UserRole
from app.services.agent_action_adapters import agent_action_registry  # noqa: F401  (registers the four)
from app.services.agent_action_adapters.adapter_base import get_adapter, register, registered_types
from app.services.agent_action_types import ACT_ACTION_TYPES, ACTION_TYPES, resolve_action_type

#: REVIEWED. Each entry was read and accepted under D16 (W15, 2026-10-25).
REVIEWED_ADAPTERS = ("decision.acknowledge", "defect.promote", "failure.reassign", "quarantine.apply")
#: The prefix of each effectful type's IDOR cases in ``test_agent_action_adapters.py``.
IDOR_TEST_PREFIX = {
    "quarantine.apply": "test_quarantine_idor_",
    "failure.reassign": "test_reassign_idor_",
    "defect.promote": "test_defect_idor_",
}
APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_the_registered_adapters_are_exactly_the_reviewed_allowlist():
    assert tuple(sorted(registered_types())) == REVIEWED_ADAPTERS


def test_every_act_type_and_the_acknowledge_have_an_adapter():
    assert set(ACT_ACTION_TYPES) | {"decision.acknowledge"} == set(registered_types())
    # The legacy decision-report type resolves to the acknowledge adapter (§0.22).
    legacy = get_adapter("decision_report_action")
    assert legacy is not None and legacy.action_type == "decision.acknowledge"
    assert get_adapter("jira_ticket_creation") is None


@pytest.mark.parametrize("action_type", REVIEWED_ADAPTERS)
def test_every_registered_adapter_is_typed_internal_and_qa_lead_only(action_type):
    spec = ACTION_TYPES[action_type]
    adapter = get_adapter(action_type)
    assert adapter is not None and adapter.action_type == action_type
    assert spec.payload_model.model_config.get("extra") == "forbid"
    assert spec.required_role == UserRole.QA_LEAD
    assert spec.side_effect in ("none", "internal")
    assert isinstance(adapter.commits_effect, bool)
    for method in ("preflight", "execute", "rollback"):
        assert inspect.iscoroutinefunction(getattr(adapter, method)), method
    assert resolve_action_type(action_type) is spec


def test_only_the_quarantine_adapter_declares_a_committing_effect():
    committing = {t for t in registered_types() if get_adapter(t).commits_effect}  # type: ignore[union-attr]
    assert committing == {"quarantine.apply"}


@pytest.mark.parametrize("action_type", sorted(IDOR_TEST_PREFIX))
def test_every_effectful_adapter_has_a_project_filter_test(action_type):
    from tests import test_agent_action_adapters as adapter_tests

    names = [n for n, _ in inspect.getmembers(adapter_tests, inspect.iscoroutinefunction)]
    idor = [n for n in names if n.startswith(IDOR_TEST_PREFIX[action_type])]
    assert idor, f"{action_type}: no {IDOR_TEST_PREFIX[action_type]}* case in test_agent_action_adapters.py"
    assert ACTION_TYPES[action_type].side_effect != "none"


def test_register_refuses_an_external_adapter():
    external = [t for t, s in ACTION_TYPES.items() if s.side_effect == "external"]
    assert external == [], "D16: no external action type until a separate security review"

    class _Duplicate:
        action_type = "quarantine.apply"
        commits_effect = False

    with pytest.raises(ValueError):
        register(_Duplicate())  # type: ignore[arg-type]


def _calls(name: str) -> list[tuple[Path, ast.Call]]:
    found: list[tuple[Path, ast.Call]] = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called == name:
                    found.append((path, node))
    return found


def test_no_ai_row_is_ever_proposed_without_a_person_s_approval():
    """``create_action_proposal(approval_required=False)`` never meets a ``pipeline_run_id``."""
    calls = _calls("create_action_proposal")
    assert calls, "the scan found no create_action_proposal caller at all"
    offenders = []
    for path, call in calls:
        keywords = {k.arg: k.value for k in call.keywords if k.arg}
        pipeline = keywords.get("pipeline_run_id")
        if pipeline is None or (isinstance(pipeline, ast.Constant) and pipeline.value is None):
            continue
        approval = keywords.get("approval_required")
        if approval is None:
            continue                      # the default is True
        if not (isinstance(approval, ast.Constant) and approval.value is True):
            offenders.append(f"{path.relative_to(APP_DIR)}:{call.lineno}")
    assert offenders == [], "an AI-originated proposal must keep approval_required=True: " + ", ".join(offenders)
