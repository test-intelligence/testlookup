"""AI-5: ``POST /api/v1/projects/{project_id}/fix-outcomes``.

The MCP ``record_fix_outcome`` tool's endpoint — the loop-closer that turns a
coding agent's merged (or reverted) fix into an AI-F1 training signal.

Pins:

1. **Outcome → rating mapping** — ``fixed`` confirms the diagnosis
   (FeedbackRating.CORRECT, so the analysis category becomes a usable label
   via ``resolve_feedback_label``); ``not_fixed`` / ``reverted`` mark it
   suspect (INCORRECT with **no** corrected category — honestly unusable as a
   classification label, but real negative-rating signal).
2. **Provenance bucket** — ``source="fix_outcome"`` maps to ``human_indirect``
   in ``label_provenance`` (never ``human_direct``, never ``llm_pseudo``).
3. **Project scoping** — the analysis lookup joins through
   ``test_runs.project_id`` (fingerprints are not salted per project).
4. **404 contract** — a never-analysed fingerprint has nothing to grade.
5. **Router shape + guard** — POST on ``lookup_router`` (already in
   ``bootstrap.PROTECTED_ROUTERS``) carrying ``require_project_access``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.models.postgres import FeedbackRating  # noqa: E402
from app.routers import feedback as feedback_router  # noqa: E402
from app.services import feedback_service  # noqa: E402
from app.services.ml.label_provenance import (  # noqa: E402
    HUMAN_DIRECT,
    HUMAN_INDIRECT,
    provenance_for_feedback_source,
)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _CapturingDB:
    """Fake AsyncSession recording executed statements + added rows."""

    def __init__(self, row=None):
        self.row = row
        self.stmts = []
        self.added = []
        self.flushed = False
        self.committed = False

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _FakeResult(self.row)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True
        # Emulate flush applying the Python-side uuid default.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.committed = True


def _analysis_row(category="FLAKY"):
    return SimpleNamespace(
        id=uuid.uuid4(), test_case_id=uuid.uuid4(), failure_category=category,
    )


_USER = SimpleNamespace(id=uuid.uuid4())


# ── Provenance bucket pin ─────────────────────────────────────────────────────


def test_fix_outcome_source_is_human_indirect():
    assert provenance_for_feedback_source("fix_outcome") == HUMAN_INDIRECT
    assert provenance_for_feedback_source("fix_outcome") != HUMAN_DIRECT


# ── Service: outcome → rating + row shape ─────────────────────────────────────


@pytest.mark.asyncio
async def test_fixed_outcome_writes_correct_rating():
    db = _CapturingDB(row=_analysis_row("FLAKY"))
    out = await feedback_service.record_fix_outcome(
        db, uuid.uuid4(), "deadbeefdeadbeef", "fixed", "org/repo#42", None, _USER,
    )
    assert len(db.added) == 1
    fb = db.added[0]
    assert fb.rating == FeedbackRating.CORRECT
    assert fb.source == "fix_outcome"
    assert fb.corrected_category is None
    assert fb.user_id == _USER.id
    assert fb.exported is False
    assert out["outcome"] == "fixed"
    assert out["rating"] == "correct"
    assert out["label_provenance"] == "human_indirect"
    assert out["analysis_category"] == "FLAKY"
    # Flush happened so the returned feedback_id is a real uuid, not "None".
    assert db.flushed and out["feedback_id"] != "None"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["not_fixed", "reverted"])
async def test_negative_outcomes_write_incorrect_without_correction(outcome):
    """INCORRECT with no corrected_category — resolve_feedback_label treats it
    as unusable for classification, which is the honest behaviour: we know
    the diagnosis didn't lead to a working fix, not what the right label is."""
    db = _CapturingDB(row=_analysis_row())
    out = await feedback_service.record_fix_outcome(
        db, uuid.uuid4(), "fp", outcome, None, None, _USER,
    )
    fb = db.added[0]
    assert fb.rating == FeedbackRating.INCORRECT
    assert fb.corrected_category is None
    assert out["rating"] == "incorrect"


@pytest.mark.asyncio
async def test_comment_folds_outcome_reference_and_free_text():
    db = _CapturingDB(row=_analysis_row())
    await feedback_service.record_fix_outcome(
        db, uuid.uuid4(), "fp", "reverted", "abc1234", "regressed checkout", _USER,
    )
    comment = db.added[0].comment
    assert comment == "[fix_outcome:reverted] ref=abc1234 regressed checkout"


def test_compose_comment_without_optionals():
    assert feedback_service.compose_fix_outcome_comment("fixed") == "[fix_outcome:fixed]"


@pytest.mark.asyncio
async def test_unknown_outcome_is_422():
    db = _CapturingDB(row=_analysis_row())
    with pytest.raises(HTTPException) as exc:
        await feedback_service.record_fix_outcome(
            db, uuid.uuid4(), "fp", "merged", None, None, _USER,
        )
    assert exc.value.status_code == 422
    assert db.added == []


@pytest.mark.asyncio
async def test_never_analysed_fingerprint_is_404():
    db = _CapturingDB(row=None)
    with pytest.raises(HTTPException) as exc:
        await feedback_service.record_fix_outcome(
            db, uuid.uuid4(), "fp", "fixed", None, None, _USER,
        )
    assert exc.value.status_code == 404
    assert db.added == []


@pytest.mark.asyncio
async def test_lookup_is_project_scoped_latest_first():
    """Tenant-bleed discipline: fingerprints are shared across projects, so
    the join through test_runs.project_id is load-bearing."""
    db = _CapturingDB(row=_analysis_row())
    await feedback_service.record_fix_outcome(
        db, uuid.uuid4(), "fp", "fixed", None, None, _USER,
    )
    sql = str(db.stmts[0])
    assert "test_runs.project_id" in sql, sql
    assert "test_cases.test_fingerprint" in sql, sql
    assert "ORDER BY ai_analysis.created_at DESC" in sql, sql
    assert "LIMIT" in sql.upper(), sql


# ── Router shape + guard + registration ──────────────────────────────────────


def _walk_dep_names(dependant) -> list[str]:
    out: list[str] = []
    stack = [dependant]
    while stack:
        dep = stack.pop()
        call = dep.call
        if call is not None:
            out.append(getattr(call, "__qualname__", None) or type(call).__name__)
        stack.extend(dep.dependencies)
    return out


def test_route_shape_and_project_access_guard():
    from fastapi.routing import APIRoute

    routes = [
        r for r in feedback_router.lookup_router.routes if isinstance(r, APIRoute)
    ]
    matches = [
        r for r in routes if r.path == "/api/v1/projects/{project_id}/fix-outcomes"
    ]
    assert matches, [r.path for r in routes]
    route = matches[0]
    assert route.methods == {"POST"}
    assert route.status_code == 201
    dep_names = _walk_dep_names(route.dependant)
    assert any("require_project_access" in n for n in dep_names), dep_names


def test_outcome_vocabulary_is_closed_at_the_schema():
    """The request model rejects out-of-vocab outcomes at validation time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        feedback_router.FixOutcomeRequest(fingerprint="fp", outcome="merged")
    ok = feedback_router.FixOutcomeRequest(fingerprint="fp", outcome="fixed")
    assert ok.outcome == "fixed"


def test_lookup_router_still_registered_as_protected():
    from app import bootstrap

    assert feedback_router.lookup_router in bootstrap.PROTECTED_ROUTERS


@pytest.mark.asyncio
async def test_endpoint_commits_after_service_stages():
    db = _CapturingDB(row=_analysis_row())
    body = feedback_router.FixOutcomeRequest(
        fingerprint="deadbeefdeadbeef", outcome="fixed", reference="org/repo#7",
    )
    result = await feedback_router.record_fix_outcome(
        project_id=uuid.uuid4(), body=body, db=db, current_user=_USER,
    )
    assert db.committed
    assert result["outcome"] == "fixed"
