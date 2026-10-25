"""W15 §1.3: the adapter registry skeleton and the two shared execute-time checks.

* ``register`` refuses an unknown type, a duplicate and an external effect (D16);
  ``get_adapter`` resolves aliases and imports the registry module lazily — a
  registry that does not exist yet answers None (``action_executor_not_configured``),
  any OTHER import error is raised (a broken adapter never reads as "no executor").
* ``load_approver`` re-authorises the approver exactly as the PATCH's
  dependencies did: demoted / removed / deactivated / synthetic → refused, an
  instance ADMIN passes without a membership row.
* ``act_refusal`` reads the LIVE investigator config on the CALLER's session.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.postgres import UserRole
from app.services import agent_action_types as t
from app.services import agent_config_service as config_svc
from app.services.agent_action_adapters import adapter_base as base

PROJECT_ID = uuid.UUID("5b0d8c3e-1f2a-4b6c-9d7e-8a1b2c3d4e5f")
USER_ID = uuid.UUID("9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d")


class _Adapter:
    commits_effect = False

    def __init__(self, action_type: str) -> None:
        self.action_type = action_type

    async def preflight(self, db, *, action, payload, approver):
        return None

    async def execute(self, db, *, action, payload, approver):
        return base.ActionOutcome(result_payload={"ok": True})

    async def rollback(self, db, *, action, rollback_payload, actor):
        return base.ActionOutcome(result_payload={"undone": True})


@pytest.fixture
def fresh_registry(monkeypatch):
    registry: dict = {}
    monkeypatch.setattr(base, "_ADAPTERS", registry)
    monkeypatch.setattr(base, "_registry_loaded", True)
    return registry


# ── the registry ─────────────────────────────────────────────────────────────
def test_register_and_get_adapter_resolve_aliases(fresh_registry):
    ack = _Adapter("decision.acknowledge")
    base.register(ack)
    assert isinstance(ack, base.ActionAdapter)
    assert base.get_adapter("decision.acknowledge") is ack
    assert base.get_adapter("decision_report_action") is ack  # the legacy ledger type
    assert base.get_adapter("quarantine.apply") is None
    assert base.get_adapter("check_quarantine_status") is None
    assert base.registered_types() == ("decision.acknowledge",)


def test_register_refuses_an_unknown_type_and_a_duplicate(fresh_registry):
    with pytest.raises(ValueError, match="unknown agent action type"):
        base.register(_Adapter("jira.file"))
    with pytest.raises(ValueError, match="unknown agent action type"):
        base.register(_Adapter("decision_report_action"))  # an alias is not a catalogue type
    base.register(_Adapter("quarantine.apply"))
    with pytest.raises(ValueError, match="already registered"):
        base.register(_Adapter("quarantine.apply"))
    assert base.registered_types() == ("quarantine.apply",)


def test_register_refuses_an_external_effect(fresh_registry):
    external = replace(t.ACTION_TYPES["quarantine.apply"], action_type="webhook.post", side_effect="external")
    with patch.dict(t.ACTION_TYPES, {"webhook.post": external}):
        with pytest.raises(ValueError, match="external adapters are not allowed"):
            base.register(_Adapter("webhook.post"))
    assert fresh_registry == {}


def test_a_registry_that_has_not_landed_answers_none(monkeypatch, fresh_registry):
    monkeypatch.setattr(base, "_registry_loaded", False)
    monkeypatch.setattr(base, "REGISTRY_MODULE", "app.services.agent_action_adapters._not_landed_yet")
    assert base.get_adapter("quarantine.apply") is None
    assert base.registered_types() == ()
    assert base._registry_loaded is False  # asked again next time


def test_any_other_import_error_is_raised(monkeypatch, fresh_registry):
    monkeypatch.setattr(base, "_registry_loaded", False)

    def _broken(name):
        raise ModuleNotFoundError("No module named 'some_dependency'", name="some_dependency")

    monkeypatch.setattr(base.importlib, "import_module", _broken)
    with pytest.raises(ModuleNotFoundError):
        base.get_adapter("quarantine.apply")


def test_action_refused_carries_code_status_and_sentence():
    refused = base.ActionRefused("target_not_in_project")
    assert (refused.code, refused.status_code) == ("target_not_in_project", 409)
    assert refused.message == "The target does not belong to this project."
    assert base.ActionRefused("approver_not_authorized", status_code=403).status_code == 403
    assert base.ActionRefused("not_a_code").message == "not_a_code"


# ── load_approver ────────────────────────────────────────────────────────────
class _ScriptedSession:
    """Answers each ``execute`` with the next scripted ``scalar_one_or_none`` value."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements = []

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        value = self.answers.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: value)


def _user(role="QA_LEAD", *, active=True, synthetic=False):
    return SimpleNamespace(id=USER_ID, role=role, is_active=active, is_synthetic=synthetic, username="lena")


async def _load(db, *, user_id=USER_ID, project_id=PROJECT_ID, required_role=UserRole.QA_LEAD):
    return await base.load_approver(db, user_id=user_id, project_id=project_id, required_role=required_role)


@pytest.mark.asyncio
async def test_a_qa_lead_member_of_the_project_passes():
    user = _user()
    db = _ScriptedSession(user, uuid.uuid4())
    assert await _load(db, user_id=str(USER_ID), project_id=str(PROJECT_ID)) is user
    assert len(db.statements) == 2
    compiled = str(db.statements[1].compile(compile_kwargs={"literal_binds": True}))
    assert "project_members" in compiled and PROJECT_ID.hex in compiled.replace("-", "")


@pytest.mark.asyncio
async def test_an_instance_admin_passes_without_a_membership_row():
    admin = _user("ADMIN")
    db = _ScriptedSession(admin)
    assert await _load(db) is admin
    assert len(db.statements) == 1


@pytest.mark.parametrize("case,answers", [
    ("demoted", (_user("QA_ENGINEER"),)),
    ("removed from the project", (_user(), None)),
    ("deactivated", (_user(active=False),)),
    ("synthetic", (_user(synthetic=True),)),
    ("deleted", (None,)),
    ("unknown role", (_user("SUPERUSER"),)),
])
@pytest.mark.asyncio
async def test_an_approver_no_longer_authorised_is_refused(case, answers):
    with pytest.raises(base.ActionRefused) as exc:
        await _load(_ScriptedSession(*answers))
    assert (exc.value.code, exc.value.status_code) == ("approver_not_authorized", 403), case


@pytest.mark.parametrize("user_id,project_id", [(None, PROJECT_ID), ("not-a-uuid", PROJECT_ID), (USER_ID, None)])
@pytest.mark.asyncio
async def test_a_missing_id_is_refused_without_a_statement(user_id, project_id):
    db = _ScriptedSession()
    with pytest.raises(base.ActionRefused):
        await _load(db, user_id=user_id, project_id=project_id)
    assert db.statements == []


# ── act_refusal ──────────────────────────────────────────────────────────────
def _resolved(mode, allowed):
    doc = config_svc.default_config("investigator").model_dump(mode="json")
    doc["mode"] = mode
    doc["extensions"]["investigator"]["act"] = {"allowed_action_types": allowed}
    return SimpleNamespace(config=config_svc.AgentConfigV1.model_validate(doc))


async def _act(db, action_type, *, mode="act", allowed=("quarantine.apply",), proposer="investigator"):
    ai_config = {"offline_mode": True}
    resolver = AsyncMock(return_value=_resolved(mode, list(allowed)))
    with patch.object(base, "resolve_for_project", new=resolver), \
            patch.object(base, "get_effective_ai_config", new=AsyncMock(return_value=ai_config)) as ai:
        code = await base.act_refusal(
            db, project_id=PROJECT_ID, spec=t.ACTION_TYPES[action_type], proposing_agent_id=proposer,
        )
    return code, resolver, ai


@pytest.mark.asyncio
async def test_act_on_and_the_type_ticked_runs():
    db = object()
    code, resolver, ai = await _act(db, "quarantine.apply")
    assert code is None
    resolver.assert_awaited_once_with(db, PROJECT_ID, "investigator", global_ai_config={"offline_mode": True})
    ai.assert_awaited_once_with(db=db)  # the caller's session, never a second one


@pytest.mark.parametrize("mode", ["shadow", "suggest"])
@pytest.mark.asyncio
async def test_act_off_is_refused(mode):
    code, _, _ = await _act(object(), "quarantine.apply", mode=mode)
    assert code == "act_mode_required"


@pytest.mark.asyncio
async def test_a_type_not_ticked_is_refused():
    code, _, _ = await _act(object(), "defect.promote", allowed=("quarantine.apply", "failure.reassign"))
    assert code == "action_type_not_allowed"
    code, _, _ = await _act(object(), "failure.reassign", allowed=())
    assert code == "action_type_not_allowed"


@pytest.mark.asyncio
async def test_a_proposer_outside_the_spec_is_refused_before_any_read():
    code, resolver, _ = await _act(object(), "quarantine.apply", proposer="agent.decision_report.v1")
    assert code == "proposer_not_permitted"
    resolver.assert_not_awaited()
    code, _, _ = await _act(object(), "quarantine.apply", proposer=None)
    assert code == "proposer_not_permitted"


@pytest.mark.asyncio
async def test_acknowledge_needs_no_act_and_reads_nothing():
    code, resolver, ai = await _act(object(), "decision.acknowledge", mode="shadow", allowed=())
    assert code is None
    resolver.assert_not_awaited()
    ai.assert_not_awaited()
