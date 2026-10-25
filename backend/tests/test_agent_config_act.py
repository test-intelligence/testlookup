"""W15 §2.3 test 6: the investigator's Act allowlist (``extensions.investigator.act``).

D16: Act runs only the types ticked here, each one of ``ACT_ACTION_TYPES``.
An unknown or external type is refused on every PUT with one sentence (through
the route's existing validation mapping, FastAPI's 422); duplicates collapse;
a stored document without the block reads as ``[]``; a budget save keeps the
list; the fixer cannot carry it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.analytics_errors import validation_exception_handler
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.routers import agent_configs
from app.services import agent_config_service as svc
from app.services import agent_investigation_service as inv_svc

PROJECT_ID = uuid.UUID("5b0d8c3e-1f2a-4b6c-9d7e-8a1b2c3d4e5f")
SENTENCE = (
    "act.allowed_action_types: {x} is not an action type Act can execute; "
    "choose from quarantine.apply, failure.reassign, defect.promote"
)


def _investigator_doc(allowed):
    doc = svc.default_config("investigator").model_dump(mode="json")
    doc["mode"] = "act"
    doc["extensions"]["investigator"]["act"] = {"allowed_action_types": allowed}
    return doc


@pytest.mark.parametrize("bad", ["jira.file", "decision.acknowledge", "external.webhook", "QUARANTINE.APPLY"])
def test_a_type_act_cannot_execute_is_refused_with_the_sentence(bad):
    with pytest.raises(ValidationError) as exc:
        svc.AgentConfigV1.model_validate(_investigator_doc(["quarantine.apply", bad]))
    assert SENTENCE.format(x=bad) in str(exc.value)


def test_duplicates_collapse_and_the_list_is_sorted():
    config = svc.AgentConfigV1.model_validate(
        _investigator_doc(["quarantine.apply", "defect.promote", "quarantine.apply", "failure.reassign"])
    )
    assert config.extensions.investigator.act.allowed_action_types == [
        "defect.promote", "failure.reassign", "quarantine.apply",
    ]


def test_a_stored_document_without_the_block_reads_as_none_ticked():
    doc = svc.default_config("investigator").model_dump(mode="json")
    doc["extensions"]["investigator"].pop("act")
    config = svc.AgentConfigV1.model_validate(doc)
    assert config.extensions.investigator.act.allowed_action_types == []
    assert svc.default_config("investigator").extensions.investigator.act.allowed_action_types == []


def test_the_list_is_kept_when_the_mode_is_lowered():
    doc = _investigator_doc(["quarantine.apply"])
    doc["mode"] = "shadow"
    config = svc.AgentConfigV1.model_validate(doc)
    assert config.extensions.investigator.act.allowed_action_types == ["quarantine.apply"]


def test_the_list_holds_at_most_eight_entries():
    with pytest.raises(ValidationError):
        svc.InvestigatorActConfig.model_validate({"allowed_action_types": ["quarantine.apply"] * 9})


def test_the_fixer_cannot_carry_the_list():
    doc = svc.default_config("fixer").model_dump(mode="json")
    doc["extensions"]["investigator"] = {"act": {"allowed_action_types": ["quarantine.apply"]}}
    with pytest.raises(ValidationError) as exc:
        svc.AgentConfigV1.model_validate(doc)
    assert "fixer config cannot carry extensions.investigator" in str(exc.value)
    with pytest.raises(ValidationError):
        svc.FixerConfigExtension.model_validate(
            {**svc.default_config("fixer").extensions.fixer.model_dump(mode="json"),
             "act": {"allowed_action_types": []}}
        )


# ── the PUT route's existing validation mapping ──────────────────────────────
class _NoMembership:
    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: None)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent_configs.router)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True, is_synthetic=False)

    async def _db():
        yield _NoMembership()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    return app


@pytest.mark.asyncio
async def test_the_put_answers_422_with_the_sentence_and_writes_nothing():
    transport = httpx.ASGITransport(app=_app())
    with patch.object(svc, "put_config", new=AsyncMock()) as put_config:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.put(
                f"/api/v1/projects/{PROJECT_ID}/agent-configs/investigator",
                json=_investigator_doc(["quarantine.apply", "jira.file"]),
                headers={"If-Match": '"0"'},
            )
    assert response.status_code == 422
    assert SENTENCE.format(x="jira.file") in response.text
    put_config.assert_not_awaited()


# ── a budget save keeps the list ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_budget_save_through_upsert_policy_keeps_the_list():
    stored = _investigator_doc(["defect.promote", "quarantine.apply"])
    row = SimpleNamespace(
        config=stored, enabled=True, mode="act", config_version=3, updated_at=None, updated_by=None,
    )
    saved = {}

    async def _put(_db, _project_id, config, **_kwargs):
        saved["config"] = config
        return row

    with patch.object(inv_svc, "get_policy_row", new=AsyncMock(return_value=row)), \
            patch.object(svc, "put_config", new=_put):
        await inv_svc.upsert_policy(
            object(), PROJECT_ID, "investigator", enabled=True, mode="act",
            budgets={"max_runs_per_day": 4},
        )
    extension = saved["config"].extensions.investigator
    assert extension.budgets.max_runs_per_day == 4
    assert extension.act.allowed_action_types == ["defect.promote", "quarantine.apply"]
