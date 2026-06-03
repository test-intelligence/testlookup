"""Regression pins for the test_case_ai_agent review (review/test-case-ai-agent).

Two issues were fixed:

1. [Minor] The direct test-management AI paths sent caller-supplied content
   (requirements, test-case bodies, project context) to ``get_llm()`` — which
   resolves to a HOSTED provider when ``AI_OFFLINE_MODE`` is off —
   UNREDACTED, unlike the RAG grounded path (which applies ``redact_prompt``).
   Each tool now redacts via ``_redact_for_llm`` before the LLM call.

2. [Major] The test-management AI endpoints (``/cases/ai-generate`` [+async],
   ``/cases/{id}/ai-review``, ``/cases/ai-coverage``, ``/strategies/ai-generate``
   [+async]) took a caller-supplied ``project_id`` (or bare ``case_id``) with
   only ``get_current_active_user`` — no project-access check. Any tenant could
   write test cases/strategies into, or read+LLM-egress another tenant's
   project. They now call ``resolve_project_scope`` (403 on a foreign project),
   matching the ``/plans/ai-create`` reference that already did this.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.regression


# ── Bug 1: redaction at the LLM boundary ────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_tool_redacts_secrets_before_llm():
    pytest.importorskip("langchain_core")
    from app.services import test_case_ai_agent as svc

    captured: dict[str, str] = {}

    class _Resp:
        content = '{"test_cases": []}'

    async def _fake_ainvoke(messages):
        captured["human"] = messages[-1].content
        return _Resp()

    fake_llm = SimpleNamespace(ainvoke=_fake_ainvoke)
    with patch.object(svc, "get_llm", AsyncMock(return_value=fake_llm)):
        await svc.generate_test_cases_tool.ainvoke(
            {"requirements": "Login flow for user admin@secret-corp.com on the portal."}
        )

    assert "admin@secret-corp.com" not in captured["human"]
    assert "[REDACTED]" in captured["human"]


def test_redact_helper_is_idempotent():
    pytest.importorskip("langchain_core")
    from app.services.test_case_ai_agent import _redact_for_llm

    once = _redact_for_llm("contact me@example.com now")
    twice = _redact_for_llm(once)
    assert "me@example.com" not in once
    assert once == twice  # re-redacting the RAG path's already-redacted text is a no-op


# ── Bug 2: tenant access enforced before any read/write/LLM egress ──────────────

@pytest.mark.asyncio
async def test_review_test_case_blocks_foreign_project_before_llm():
    """A denied case never imports/touches the LLM tool."""
    from app.services import test_management_ai_service as svc

    case = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = AsyncMock()
    user = SimpleNamespace(id=uuid.uuid4())

    with patch.object(svc, "get_test_case_or_404", AsyncMock(return_value=case)), \
         patch(
             "app.core.deps.resolve_project_scope",
             AsyncMock(side_effect=HTTPException(status_code=403)),
         ) as scope:
        with pytest.raises(HTTPException) as ei:
            await svc.review_test_case_with_ai(db, case.id, user)

    assert ei.value.status_code == 403
    scope.assert_awaited_once()
    # project_id passed to the guard is the FETCHED case's project, not a param
    assert scope.await_args.args[2] == str(case.project_id)


def _payload(project_id):
    return SimpleNamespace(project_id=project_id, requirements="do x", project_context="ctx")


@pytest.mark.asyncio
async def test_ai_generate_cases_router_enforces_access():
    from app.routers import test_management_ai as r

    payload = _payload(uuid.uuid4())
    gen = AsyncMock()
    with patch.object(r, "resolve_project_scope", AsyncMock(side_effect=HTTPException(403))), \
         patch.object(r, "generate_ai_cases", gen):
        with pytest.raises(HTTPException) as ei:
            await r.ai_generate_cases(payload, AsyncMock(), SimpleNamespace())
    assert ei.value.status_code == 403
    gen.assert_not_called()  # no work on a denied project


@pytest.mark.asyncio
async def test_ai_generate_cases_router_allows_member_and_passes_project():
    from app.routers import test_management_ai as r

    pid = uuid.uuid4()
    payload = _payload(pid)
    db = AsyncMock()
    scope = AsyncMock(return_value=(pid, None))
    with patch.object(r, "resolve_project_scope", scope), \
         patch.object(r, "generate_ai_cases", AsyncMock(return_value={"test_cases": []})) as gen:
        await r.ai_generate_cases(payload, db, SimpleNamespace())
    gen.assert_awaited_once()
    assert scope.await_args.args[2] == str(pid)


@pytest.mark.asyncio
async def test_ai_generate_cases_async_router_enforces_access():
    from app.routers import test_management_ai as r

    payload = _payload(uuid.uuid4())
    enq = AsyncMock()
    with patch.object(r, "resolve_project_scope", AsyncMock(side_effect=HTTPException(403))), \
         patch.object(r, "enqueue_ai_case_generation", enq):
        with pytest.raises(HTTPException) as ei:
            await r.ai_generate_cases_async(payload, AsyncMock(), SimpleNamespace())
    assert ei.value.status_code == 403
    enq.assert_not_called()  # never queued for a project the caller can't access


@pytest.mark.asyncio
async def test_ai_coverage_router_enforces_access():
    from app.routers import test_management_ai as r

    payload = _payload(uuid.uuid4())
    cov = AsyncMock()
    with patch.object(r, "resolve_project_scope", AsyncMock(side_effect=HTTPException(403))), \
         patch.object(r, "analyze_case_coverage", cov):
        with pytest.raises(HTTPException) as ei:
            await r.ai_coverage_analysis(payload, AsyncMock(), SimpleNamespace())
    assert ei.value.status_code == 403
    cov.assert_not_called()


@pytest.mark.asyncio
async def test_ai_generate_strategy_router_enforces_access():
    from app.routers import test_management_strategies as r

    payload = _payload(uuid.uuid4())
    gen = AsyncMock()
    with patch.object(r, "resolve_project_scope", AsyncMock(side_effect=HTTPException(403))), \
         patch.object(r, "generate_ai_strategy", gen):
        with pytest.raises(HTTPException) as ei:
            await r.ai_generate_strategy(payload, AsyncMock(), SimpleNamespace())
    assert ei.value.status_code == 403
    gen.assert_not_called()
