"""W13 TCL-03: library health on the server (``GET /api/v1/test-management/health``).

1. ``health_score`` / the components equal the page's OLD synthesis
   (``TestManagementPage.tsx``) applied to the same inputs; an empty library is
   not measured (never 0 %); an empty review queue scores 100.
2. The real router over a scripted session: the strategy gaps' order and
   conditions, the project's target, the 422 without a project, a non-member's
   403 before any health statement, and the flag off -> no requirement read.

Every time is built inside the test from one ``now``.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import deps
from app.core.deps import get_current_active_user, get_db
from app.core.test_library_vocab import (
    FLAKY_UNMEASURED_NO_SCORES,
    HEALTH_COMPONENTS,
    HEALTH_NEEDS_PROJECT_SENTENCE,
    LIBRARY_EMPTY_SENTENCE,
    STRATEGY_GAP_KINDS,
)
from app.models.postgres import UserRole
from app.routers import test_management_library as routes
from app.services import feature_flags
from app.services import test_library_health_service as health


def _js_round(value: float) -> int:
    return int(math.floor(value + 0.5))


def _old_page_formula(total, automated, stale, oldest_review_days, retired_in_open):
    """The deleted synthesis, line for line (TestManagementPage.tsx, W13 base)."""
    automated_pct = _js_round(automated / total * 100) if total > 0 else 0
    stale_score = max(0, 100 - (stale / total) * 400) if total > 0 else 100
    review_score = 100 if oldest_review_days == 0 else max(0, 100 - (oldest_review_days / 14) * 100)
    retired_score = max(0, 100 - (retired_in_open / total) * 400) if total > 0 else 100
    score = _js_round(automated_pct * 0.35 + stale_score * 0.20 + review_score * 0.20 + retired_score * 0.25)
    return {"automation": automated_pct, "stale_drafts": stale_score, "review_freshness": review_score,
            "retired_in_open_plans": retired_score}, score


def _facts(total, automated, stale, oldest, retired, *, review_total=1, target=60):
    return {"total": total, "automated": automated, "stale": stale, "review_total": review_total,
            "oldest_review_days": oldest, "retired_in_open": retired, "target": target}


@pytest.mark.parametrize(
    "row",
    [(34, 13, 2, 9, 1), (3, 1, 0, 0, 0), (50, 40, 0, 2, 0), (10, 0, 10, 30, 10), (7, 7, 0, 13, 0)],
)
def test_components_and_score_equal_the_old_page_formula(row):
    total, automated, stale, oldest, retired = row
    components = health.build_components(_facts(*row))
    old_values, old_score = _old_page_formula(*row)

    by_key = {c["key"]: c for c in components}
    assert [c["key"] for c in components] == list(HEALTH_COMPONENTS)
    assert _js_round(by_key["automation"]["value"]) == old_values["automation"]
    for key in ("stale_drafts", "review_freshness", "retired_in_open_plans"):
        assert by_key[key]["value"] == pytest.approx(old_values[key])
    assert health.health_score(components)[0] == old_score
    # every component carries BOTH numbers
    assert (by_key["automation"]["numerator"], by_key["automation"]["denominator"]) == (automated, total)
    assert (by_key["stale_drafts"]["numerator"], by_key["stale_drafts"]["denominator"]) == (stale, total)
    assert by_key["retired_in_open_plans"]["denominator"] == total
    assert by_key["review_freshness"]["numerator"] == oldest
    assert f"{automated} of {total} cases automated" in by_key["automation"]["detail"]


def test_the_example_row_reads_as_the_story_says():
    components = health.build_components(_facts(34, 13, 2, 9, 1))
    assert components[0]["detail"] == "13 of 34 cases automated (target 60 %)"
    assert health.health_score(components) == (58, "At risk")


def test_an_empty_library_is_not_measured_never_zero():
    components = health.build_components(_facts(0, 0, 0, None, 0, review_total=0))
    assert all(c["measured"] is False for c in components)
    assert all(c["value"] is None and c["numerator"] is None for c in components)
    assert all(c["detail"] == LIBRARY_EMPTY_SENTENCE for c in components)
    assert health.health_score(components) == (None, None)


def test_an_empty_review_queue_scores_100():
    components = health.build_components(_facts(5, 5, 0, None, 0, review_total=0))
    review = next(c for c in components if c["key"] == "review_freshness")
    assert review["value"] == 100.0
    assert health.health_score(components) == (100, "Healthy")


def test_the_score_renormalises_over_the_measured_components_only():
    components = [
        {"key": "a", "weight": 0.35, "measured": True, "value": 80.0},
        {"key": "b", "weight": 0.25, "measured": False, "value": None},
    ]
    assert health.health_score(components) == (80, "Needs attention")


# ── 2. the router over a scripted session ────────────────────────────────────


class _Result:
    def __init__(self, *, one=None, scalar=None, rows=()):
        self._one, self._scalar, self._rows = one, scalar, list(rows)

    def one(self):
        return self._one

    def scalar(self):
        return self._scalar

    def all(self):
        return list(self._rows)


class _Mapped(tuple):
    """A Row-like ``one()`` answer: positional AND ``_mapping``."""

    def __new__(cls, mapping):
        obj = super().__new__(cls, tuple(mapping.values()))
        obj._mapping = mapping
        return obj


class _Session:
    new = dirty = deleted = ()

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements: list = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        return self.answers.pop(0)


def _row(**kw):
    return SimpleNamespace(**kw)


def _answers(now, *, total=34, automated=13, stale=2, review_total=3, oldest_days=9, retired_in_open=1,
             flaky_scores=4, linked=6, flaky_total=2, no_owner_total=3, target=60):
    """One scripted answer per health statement, in the service's order."""
    oldest = now - timedelta(days=oldest_days, hours=1) if review_total else None
    answers = [
        _Result(one=_Mapped({
            "total": total, "active": 20, "retired": 4, "automated": automated, "review_total": review_total,
            "oldest_review": oldest, "stale": stale,
            "avg_created_epoch": (now - timedelta(days=40)).timestamp() if total else None,
            "older_than_180": 2 if total else 0,
        })),
        _Result(scalar=retired_in_open),
        _Result(rows=[_row(id=uuid.uuid4(), title="Sign in with SSO", status="review_requested", started=oldest,
                           full_name="Priya Shah", username="priya")] if review_total else []),
        _Result(one=(flaky_scores, linked)),
    ]
    if flaky_scores and linked:
        answers.append(_Result(rows=[
            _row(id=uuid.uuid4(), title="Checkout", status="active", score=0.72, confidence="high",
                 full_name=None, username="lee", total=flaky_total),
        ] if flaky_total else []))
    answers += [
        _Result(rows=[_row(id=uuid.uuid4(), title="Orphan", status="draft", updated_at=now - timedelta(days=3),
                           assignee_id=None, author_id=None, total=no_owner_total)] if no_owner_total else []),
        _Result(rows=[_row(name="API", total=2), _row(name="Checkout", total=2)]),
        _Result(scalar=target),
    ]
    return answers


def _app(session, user):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/test-management")

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return app


def _user(role=UserRole.QA_ENGINEER):
    return SimpleNamespace(id=uuid.uuid4(), role=role.value, is_active=True, username="tess", email="t@x.test")


@pytest.fixture
def flag(monkeypatch):
    state = {"on": False, "calls": []}

    async def _is_enabled(key, **kwargs):
        state["calls"].append((key, kwargs.get("project_id")))
        return state["on"]

    monkeypatch.setattr(feature_flags, "is_enabled", _is_enabled)
    return state


async def _get(session, user, params, monkeypatch, accessible):
    monkeypatch.setattr(deps, "get_accessible_project_ids", AsyncMock(return_value=accessible))
    async with AsyncClient(transport=ASGITransport(app=_app(session, user)), base_url="http://t") as client:
        return await client.get("/api/v1/test-management/health", params=params)


async def test_the_answer_its_strategy_gaps_order_and_lists(monkeypatch, flag):
    project = uuid.uuid4()
    now = datetime.now(timezone.utc)
    session = _Session(_answers(now))
    res = await _get(session, _user(), {"project_id": str(project)}, monkeypatch, {project})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["measured"] is True and body["total_cases"] == 34 and body["automated_cases"] == 13
    assert [g["kind"] for g in body["strategy_gaps"]] == [
        "retired_in_open_plans", "aging_reviews", "automation_below_target", "stale_drafts", "flaky_active",
        "no_owner",
    ]
    assert [g["kind"] for g in body["strategy_gaps"]] == [k for k in STRATEGY_GAP_KINDS]
    gaps = {g["kind"]: g for g in body["strategy_gaps"]}
    assert gaps["retired_in_open_plans"]["severity"] == "critical"
    assert gaps["retired_in_open_plans"]["pill"] == "1 in open plans"
    assert gaps["aging_reviews"]["detail"] == "oldest 9 days" and gaps["aging_reviews"]["pill"] == "3 pending"
    assert gaps["automation_below_target"]["detail"] == "38 % automated · target 60 %"
    assert gaps["automation_below_target"]["pill"] == "22 % gap" and gaps["automation_below_target"]["count"] == 22
    assert gaps["stale_drafts"]["pill"] == "2 stale"
    assert gaps["flaky_active"]["pill"] == "2 flaky" and gaps["no_owner"]["pill"] == "3 cases"
    # the lists: a count beside the page (a top-N length is not a count)
    assert body["review_queue"]["total"] == 3 and len(body["review_queue"]["items"]) == 1
    assert body["review_queue"]["oldest_age_days"] == 9
    assert body["review_queue"]["items"][0]["owner"] == "Priya Shah"
    assert body["flaky_active"]["total"] == 2 and body["flaky_active"]["items"][0]["owner"] == "lee"
    assert body["flaky_active"]["items"][0]["detail"] == "flaky score 0.72 (high confidence)"
    assert body["no_owner"]["items"][0]["detail"] == "unassigned"
    assert body["suite_names"] == ["API", "Checkout"] and body["suite_names_total"] == 2
    assert body["score"] == 58 and body["tag"] == "At risk"
    assert body["avg_age_days"] == pytest.approx(40.0, abs=0.1)
    # the flag off: no requirement statement and no requirements block
    assert body["requirements"] is None
    assert flag["calls"] == [("test_case_requirement_traceability", project)]
    assert len(session.statements) == 8
    assert not session.answers


async def test_a_target_of_75_and_conditions_that_hold_no_gap(monkeypatch, flag):
    project = uuid.uuid4()
    now = datetime.now(timezone.utc)
    answers = _answers(now, automated=34, stale=0, review_total=2, oldest_days=6, retired_in_open=0,
                       flaky_total=0, no_owner_total=0, target=75)
    res = await _get(_Session(answers), _user(), {"project_id": str(project)}, monkeypatch, {project})
    assert res.json()["strategy_gaps"] == []          # 100 % automated, a 6-day review, nothing stale
    answers = _answers(now, automated=13, stale=0, review_total=0, retired_in_open=0, flaky_total=0,
                       no_owner_total=0, target=75)
    body = (await _get(_Session(answers), _user(), {"project_id": str(project)}, monkeypatch, {project})).json()
    assert [g["kind"] for g in body["strategy_gaps"]] == ["automation_below_target"]
    assert body["strategy_gaps"][0]["detail"] == "38 % automated · target 75 %"
    assert body["automation_target_pct"] == 75
    assert body["components"][0]["detail"] == "13 of 34 cases automated (target 75 %)"
    assert body["review_queue"]["oldest_age_days"] is None


async def test_no_flaky_scores_say_why_and_skip_the_list(monkeypatch, flag):
    project = uuid.uuid4()
    now = datetime.now(timezone.utc)
    session = _Session(_answers(now, flaky_scores=0))
    body = (await _get(session, _user(), {"project_id": str(project)}, monkeypatch, {project})).json()
    assert body["flaky_active"] == {"total": 0, "items": [], "measured": False, "reason": FLAKY_UNMEASURED_NO_SCORES}
    assert "flaky_active" not in [g["kind"] for g in body["strategy_gaps"]]
    assert len(session.statements) == 7               # the flaky list is not read


async def test_an_empty_library_is_pending_not_zero(monkeypatch, flag):
    project = uuid.uuid4()
    now = datetime.now(timezone.utc)
    answers = _answers(now, total=0, automated=0, stale=0, review_total=0, retired_in_open=0, flaky_scores=0,
                       no_owner_total=0)
    body = (await _get(_Session(answers), _user(), {"project_id": str(project)}, monkeypatch, {project})).json()
    assert body["measured"] is False and body["score"] is None and body["tag"] is None
    assert body["strategy_gaps"] == [] and body["avg_age_days"] is None and body["older_than_180_pct"] is None
    assert body["no_owner"]["measured"] is False and body["no_owner"]["reason"] == LIBRARY_EMPTY_SENTENCE


async def test_without_a_project_the_sentence_and_no_statement(monkeypatch, flag):
    session = _Session([])
    res = await _get(session, _user(), {}, monkeypatch, set())
    assert res.status_code == 422 and res.json()["detail"] == HEALTH_NEEDS_PROJECT_SENTENCE
    assert session.statements == [] and flag["calls"] == []


async def test_a_non_member_is_refused_before_any_health_statement(monkeypatch, flag):
    session = _Session([])
    res = await _get(session, _user(), {"project_id": str(uuid.uuid4())}, monkeypatch, {uuid.uuid4()})
    assert res.status_code == 403 and res.json()["detail"] == "You do not have access to this project"
    assert session.statements == [] and flag["calls"] == []


async def test_the_flag_on_adds_the_requirement_summary(monkeypatch, flag):
    flag["on"] = True
    project = uuid.uuid4()
    now = datetime.now(timezone.utc)
    answers = _answers(now) + [
        _Result(rows=[("REQ-1", "req-1", True, False, 2), ("REQ-2", "REQ-2", False, False, 1)]),   # links
        _Result(rows=[("[REQ-3]", 4)]),                                                          # batches
        _Result(rows=[("REQ-1",)]),                                                              # chunks
    ]
    session = _Session(answers)
    body = (await _get(session, _user(), {"project_id": str(project)}, monkeypatch, {project})).json()
    req = body["requirements"]
    assert (req["known"], req["verified"], req["gaps"], req["measured"]) == (3, 1, 2, True)
    assert [(g["normalized_key"], g["reason"]) for g in req["top_gaps"]] == [("REQ-3", "no_link"),
                                                                             ("REQ-2", "related_only")]
    assert len(session.statements) == 11


async def test_a_malformed_project_id_is_refused(monkeypatch, flag):
    res = await _get(_Session([]), _user(), {"project_id": "nope"}, monkeypatch, set())
    assert res.status_code == 422                       # FastAPI's uuid parse, before the handler
