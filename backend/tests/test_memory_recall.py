"""Memory recall in the reasoning path (Agentic plan AI-F3).

Pins the acceptance criteria:
  * a fingerprint with a prior HUMAN CORRECTION must surface that correction
    in the recall/tool output (cited with its date, marked authoritative);
  * semantic recall is project-scoped (tenant isolation — the project_id the
    caller supplies is the one the ChromaDB/agent-memory layer is queried
    with; SQL sub-fetchers filter on TestRun.project_id);
  * the tool completes well under the 500 ms latency budget against mocked
    stores, never raises into the ReAct loop, and returns bounded,
    JSON-safe strings;
  * ReAct wiring: recall_similar_failures is registered as the 6th tool.

All DB/ChromaDB calls are mocked — no live services required.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import memory_recall as mr  # noqa: E402

PROJECT_A = uuid.uuid4()
PROJECT_B = uuid.uuid4()
FP = "fp-checkout-123"


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDB:
    """Captures executed statements; returns queued results in order."""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _FakeResult()


def _correction(category="INFRASTRUCTURE"):
    return {
        "corrected_category": category,
        "corrected_root_cause": "Pod eviction during rollout, not a product bug",
        "feedback_id": "fb-1",
        "corrected_at": "2026-06-14T10:00:00+00:00",
    }


# ── (a) Human corrections MUST surface (plan acceptance criterion) ───────────


@pytest.mark.asyncio
async def test_prior_human_correction_surfaces_in_recall_output():
    db = _FakeDB([_FakeResult(), _FakeResult(scalar=None)])
    with patch(
        "app.services.analysis_corrections.get_corrections_for_fingerprints",
        new=AsyncMock(return_value={FP: _correction()}),
    ) as get_corr, patch(
        "app.services.agent_memory_service.recall_similar",
        new=AsyncMock(return_value=[]),
    ):
        recall = await mr.recall_failure_history(
            db, PROJECT_A, test_fingerprint=FP, error_text="TimeoutError",
        )

    assert recall["has_history"] is True
    assert recall["human_corrections"] == [_correction()]
    # Project-scoped: the corrections source received THIS project's id.
    assert get_corr.await_args.args[1] == PROJECT_A

    report = mr.render_recall_report(recall)
    assert "2026-06-14: human corrected to INFRASTRUCTURE" in report
    assert "HUMAN CORRECTION — authoritative" in report


@pytest.mark.asyncio
async def test_prior_analyses_are_cited_with_category_confidence_date():
    rows = [
        SimpleNamespace(
            failure_category="FLAKY",
            confidence_score=72,
            root_cause_summary="Async timing race in checkout polling",
            created_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        ),
    ]
    db = _FakeDB([_FakeResult(rows=rows), _FakeResult(scalar=None)])
    with patch(
        "app.services.analysis_corrections.get_corrections_for_fingerprints",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.services.agent_memory_service.recall_similar",
        new=AsyncMock(return_value=[]),
    ):
        recall = await mr.recall_failure_history(db, PROJECT_A, test_fingerprint=FP)

    report = mr.render_recall_report(recall)
    assert "2026-06-20: prior analysis → FLAKY (confidence 72)" in report
    # The prior-analyses SQL is project-scoped (tenant isolation).
    joined_sql = " ".join(str(s) for s in db.statements)
    assert "test_runs.project_id" in joined_sql
    assert "test_fingerprint" in joined_sql


@pytest.mark.asyncio
async def test_quarantine_history_one_liner():
    quarantine_row = SimpleNamespace(
        status="QUARANTINED",
        flip_rate=0.42,
        detected_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        pass_count=11,
        fail_count=8,
    )
    db = _FakeDB([_FakeResult(), _FakeResult(scalar=quarantine_row)])
    with patch(
        "app.services.analysis_corrections.get_corrections_for_fingerprints",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.services.agent_memory_service.recall_similar",
        new=AsyncMock(return_value=[]),
    ):
        recall = await mr.recall_failure_history(db, PROJECT_A, test_fingerprint=FP)

    report = mr.render_recall_report(recall)
    assert "Quarantine history: QUARANTINED since 2026-06-01, flip rate 42%." in report


# ── (c) Semantic recall is project-scoped (cross-project leakage) ────────────


@pytest.mark.asyncio
async def test_semantic_recall_uses_the_callers_project_only():
    calls: list[uuid.UUID] = []

    async def _recall_similar(db, project_id, signature, entity_type=None, limit=10):
        calls.append(project_id)
        entry = SimpleNamespace(
            failure_category="PRODUCT_BUG",
            root_cause_summary=f"memory of project {project_id}",
            error_signature="sig",
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            entity_id="tc-9",
        )
        return [{"memory": entry, "similarity": 0.91}]

    db = _FakeDB([])
    with patch("app.services.agent_memory_service.recall_similar", new=_recall_similar):
        recall_a = await mr.recall_failure_history(db, PROJECT_A, error_text="boom")
        recall_b = await mr.recall_failure_history(db, PROJECT_B, error_text="boom")

    assert calls == [PROJECT_A, PROJECT_B]
    assert f"memory of project {PROJECT_A}" in mr.render_recall_report(recall_a)
    assert f"memory of project {PROJECT_B}" not in mr.render_recall_report(recall_a)
    assert f"memory of project {PROJECT_B}" in mr.render_recall_report(recall_b)


# ── Graceful degradation: no history / broken stores ─────────────────────────


@pytest.mark.asyncio
async def test_no_history_is_graceful():
    db = _FakeDB([_FakeResult(), _FakeResult(scalar=None)])
    with patch(
        "app.services.analysis_corrections.get_corrections_for_fingerprints",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.services.agent_memory_service.recall_similar",
        new=AsyncMock(return_value=[]),
    ):
        recall = await mr.recall_failure_history(
            db, PROJECT_A, test_fingerprint=FP, error_text="fresh error",
        )
    assert recall["has_history"] is False
    assert mr.NO_HISTORY_LINE in mr.render_recall_report(recall)


@pytest.mark.asyncio
async def test_recall_never_raises_when_every_store_is_broken():
    class _ExplodingDB:
        async def execute(self, stmt):
            raise RuntimeError("db down")

    with patch(
        "app.services.analysis_corrections.get_corrections_for_fingerprints",
        new=AsyncMock(side_effect=RuntimeError("corrections down")),
    ), patch(
        "app.services.agent_memory_service.recall_similar",
        new=AsyncMock(side_effect=RuntimeError("chroma down")),
    ):
        recall = await mr.recall_failure_history(
            _ExplodingDB(), PROJECT_A, test_fingerprint=FP, error_text="x",
        )
    assert recall["has_history"] is False
    assert mr.NO_HISTORY_LINE in mr.render_recall_report(recall)


def test_report_is_bounded():
    recall = {
        "test_name": "t" * 500,
        "human_corrections": [
            {
                "corrected_category": "PRODUCT_BUG",
                "corrected_root_cause": "y" * 5000,
                "corrected_at": "2026-01-01T00:00:00Z",
            }
        ] * 20,
        "prior_analyses": [],
        "similar_failures": [],
        "quarantine": None,
        "has_history": True,
    }
    report = mr.render_recall_report(recall)
    assert len(report) <= mr.MAX_REPORT_CHARS


# ── The LangChain tool: wiring, scoping, latency, never-raises ───────────────


def _tool():
    from app.tools.recall_memory import recall_similar_failures

    return recall_similar_failures


class _FakeSessionCM:
    def __init__(self, db=None):
        self._db = db or _FakeDB([])

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_tool_without_context_degrades_to_unavailable():
    from app.tools import recall_memory as rm

    rm._RECALL_CONTEXT.set(None)
    out = await _tool().ainvoke({"query": "anything"})
    assert isinstance(out, str)
    assert "Memory recall unavailable" in out
    assert "project-scoped" in out


@pytest.mark.asyncio
async def test_tool_surfaces_human_correction_and_is_project_scoped():
    from app.tools import recall_memory as rm

    seen: dict = {}

    async def _fake_recall(db, project_id, **kwargs):
        seen["project_id"] = project_id
        seen.update(kwargs)
        return {
            "test_name": "checkout_test",
            "has_history": True,
            "human_corrections": [_correction()],
            "prior_analyses": [],
            "similar_failures": [],
            "quarantine": None,
        }

    token = rm.set_recall_context(
        project_id=str(PROJECT_A),
        test_fingerprint=FP,
        test_name="checkout_test",
        error_message="TimeoutError: pod evicted",
    )
    try:
        with patch("app.db.postgres.AsyncSessionLocal", lambda: _FakeSessionCM()), \
             patch("app.services.memory_recall.recall_failure_history", new=_fake_recall):
            out = await _tool().ainvoke({"query": ""})
    finally:
        rm.reset_recall_context(token)

    assert "2026-06-14: human corrected to INFRASTRUCTURE" in out
    assert seen["project_id"] == PROJECT_A
    assert seen["test_fingerprint"] == FP
    # Error text comes from the server-side context, not the LLM's input.
    assert seen["error_text"] == "TimeoutError: pod evicted"


@pytest.mark.asyncio
async def test_tool_never_raises_into_the_loop():
    from app.tools import recall_memory as rm

    token = rm.set_recall_context(project_id=str(PROJECT_A), test_fingerprint=FP)
    try:
        with patch("app.db.postgres.AsyncSessionLocal", side_effect=RuntimeError("no db")):
            out = await _tool().ainvoke({"query": "x"})
    finally:
        rm.reset_recall_context(token)
    assert isinstance(out, str)
    assert "Memory recall unavailable" in out


@pytest.mark.asyncio
async def test_tool_latency_budget_under_500ms_with_mocked_stores():
    from app.tools import recall_memory as rm

    async def _fake_recall(db, project_id, **kwargs):
        return {
            "test_name": "t",
            "has_history": True,
            "human_corrections": [_correction()],
            "prior_analyses": [],
            "similar_failures": [],
            "quarantine": None,
        }

    token = rm.set_recall_context(project_id=str(PROJECT_A), test_fingerprint=FP)
    try:
        with patch("app.db.postgres.AsyncSessionLocal", lambda: _FakeSessionCM()), \
             patch("app.services.memory_recall.recall_failure_history", new=_fake_recall):
            start = time.perf_counter()
            out = await asyncio.wait_for(_tool().ainvoke({"query": ""}), timeout=0.5)
            elapsed = time.perf_counter() - start
    finally:
        rm.reset_recall_context(token)
    assert elapsed < 0.5
    assert isinstance(out, str) and out


def test_react_agent_registers_recall_tool():
    pytest.importorskip("langchain_core")
    from app.services.agent import _get_tools

    tools = _get_tools()
    names = [t.name for t in tools]
    assert len(tools) == 6
    assert "recall_similar_failures" in names
    # The v2 system prompt teaches the loop to use it.
    from app.services.agent import SYSTEM_PROMPT

    assert "recall_similar_failures" in SYSTEM_PROMPT


def test_recall_context_set_reset_roundtrip():
    from app.tools import recall_memory as rm

    token = rm.set_recall_context(project_id="p", test_fingerprint="f")
    assert rm._RECALL_CONTEXT.get()["project_id"] == "p"
    rm.reset_recall_context(token)
    assert rm._RECALL_CONTEXT.get() is None


# ── Chat integration (conversation.py retrieval step) ────────────────────────


@pytest.mark.asyncio
async def test_chat_fingerprint_recall_matches_test_name_in_query():
    pytest.importorskip("langchain_core")
    from app.agents.conversation import ConversationAgent

    rows = [
        SimpleNamespace(test_name="checkout_payment_test", test_fingerprint=FP),
        SimpleNamespace(test_name="other_test_name", test_fingerprint="fp-other"),
    ]

    async def _fake_recall(db, project_id, **kwargs):
        assert kwargs["test_fingerprint"] == FP
        return {
            "test_name": "checkout_payment_test",
            "has_history": True,
            "human_corrections": [_correction()],
            "prior_analyses": [],
            "similar_failures": [],
            "quarantine": None,
        }

    agent = ConversationAgent()
    with patch(
        "app.agents.conversation.AsyncSessionLocal",
        lambda: _FakeSessionCM(_FakeDB([_FakeResult(rows=rows)])),
    ), patch("app.services.memory_recall.recall_failure_history", new=_fake_recall):
        out = await agent._fetch_fingerprint_recall(
            "why did checkout_payment_test fail again?", str(PROJECT_A),
        )
    assert "human corrected to INFRASTRUCTURE" in out


@pytest.mark.asyncio
async def test_chat_fingerprint_recall_requires_project_scope():
    pytest.importorskip("langchain_core")
    from app.agents.conversation import ConversationAgent

    agent = ConversationAgent()
    assert await agent._fetch_fingerprint_recall("why did x fail", None) == ""


@pytest.mark.asyncio
async def test_chat_fingerprint_recall_never_raises():
    pytest.importorskip("langchain_core")
    from app.agents.conversation import ConversationAgent

    agent = ConversationAgent()
    with patch(
        "app.agents.conversation.AsyncSessionLocal",
        side_effect=RuntimeError("db down"),
    ):
        out = await agent._fetch_fingerprint_recall("why did x fail", str(PROJECT_A))
    assert out == ""
