"""W11 REL-07: the bulk create (the PRD draft's Create) and the draft route's order.

Over a statement-scripted session and the REAL router + ``release_service``:

* the statements, in order: the release by id, the target row ``FOR UPDATE``,
  the release's phases; then ONE flush of every row and the
  ``release.phases_generated`` event staged on the same session BEFORE the
  commit; the metric counts the rows under the request's ``source``;
* the refusals: a name twice in the batch (409, case-insensitive), a release
  that has a phase (409), a type outside ``PHASE_TYPES`` (422), an end
  before its start (422), 31 phases (Pydantic's 422);
* the draft route hands the request's connection back
  (``release_read_connection``) BEFORE it reads the AI config or builds the
  model, so no connection is held while the model runs.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from prometheus_client import REGISTRY
from pydantic import ValidationError

from app.core.release_planning import (
    PHASE_TYPE_SENTENCE,
    PHASE_WINDOW_SENTENCE,
    TARGET_HAS_PHASES,
)
from app.models.postgres import ProjectActivityEvent, Release, ReleasePhase, UserRole
from app.models.schemas import ReleasePhasesBulkRequest
from app.routers import releases as rel_router

PROJECT = uuid.uuid4()


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    def __init__(self, answers):
        self.answers = list(answers)
        self.sql: list[str] = []
        self.added: list = []
        self.log: list[str] = []
        self.commits = 0
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt.compile()))
        self.log.append("execute")
        if not self.answers:
            raise AssertionError(f"unexpected statement: {stmt}")
        return _Result(self.answers.pop(0))

    def add(self, obj):
        self.added.append(obj)
        self.log.append(f"add:{type(obj).__name__}")

    def add_all(self, objs):
        for obj in objs:
            self.add(obj)

    async def flush(self):
        self.log.append("flush")
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.log.append("commit")
        self.commits += 1

    async def refresh(self, obj):
        return None

    async def close(self):
        self.log.append("close")


def _release() -> Release:
    return Release(id=uuid.uuid4(), project_id=PROJECT, name="2026.13", status="planning")


def _user():
    return SimpleNamespace(id=uuid.uuid4(), username="lead", full_name="Lea Lead",
                           role=UserRole.QA_LEAD, is_service_account=False)


def _body(*phases, source="rules", model_name=None) -> ReleasePhasesBulkRequest:
    return ReleasePhasesBulkRequest(phases=list(phases), source=source, model_name=model_name)


async def _bulk(db, release, body):
    return await rel_router.create_release_phases_bulk(
        release_id=str(release.id), body=body, db=db, current_user=_user(), __=_user(),
    )


def _metric(source: str) -> float:
    return REGISTRY.get_sample_value(
        "testlookup_release_phases_created_total", {"source": source}
    ) or 0.0


async def test_the_statements_the_rows_and_the_event_in_order():
    release = _release()
    db = _Session([release, None, None])
    before = _metric("rules")

    out = await _bulk(db, release, _body(
        {"name": " Smoke testing ", "phase_type": "qa_testing",
         "planned_start": "2026-11-03T00:00:00Z", "planned_end": "2026-11-05T00:00:00Z"},
        {"name": "UAT", "phase_type": "uat", "description": "Exit criteria: sign-off"},
        {"name": "Production cutover", "phase_type": "production", "exit_criteria": {"thresholds": {}}},
    ))

    # release by id; the lock; the phases read.
    assert len(db.sql) == 3
    assert "FROM releases" in db.sql[0]
    assert "FOR UPDATE" in db.sql[1] and "releases" in db.sql[1]
    assert "FROM release_phases" in db.sql[2]
    rows = [o for o in db.added if isinstance(o, ReleasePhase)]
    assert [r.name for r in rows] == ["Smoke testing", "UAT", "Production cutover"]
    assert [r.order_index for r in rows] == [0, 1, 2]
    assert {r.status for r in rows} == {"pending"}
    assert rows[0].planned_start == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert rows[2].exit_criteria == {"thresholds": {}}
    # ONE flush for every row, then the event, then the commit.
    assert db.log.count("flush") == 1
    assert db.log.index("flush") > db.log.index("add:ReleasePhase")
    event = next(o for o in db.added if isinstance(o, ProjectActivityEvent))
    assert event.event_type == "release.phases_generated"
    assert event.context["count"] == 3 and event.context["source"] == "rules"
    assert "3 phases created (rules)" in event.summary
    assert db.log.index("add:ProjectActivityEvent") < db.log.index("commit")
    assert out["created"] == 3
    assert _metric("rules") == before + 3


async def test_an_unknown_source_is_recorded_as_manual_and_the_model_only_for_llm():
    release = _release()
    db = _Session([release, None, None])
    await _bulk(db, release, _body({"name": "Smoke"}, source="spreadsheet", model_name="x"))
    event = next(o for o in db.added if isinstance(o, ProjectActivityEvent))
    assert event.context["source"] == "manual"
    assert event.context["model_name"] is None

    db = _Session([release, None, None])
    await _bulk(db, release, _body({"name": "Smoke"}, source="llm", model_name="qwen2.5:7b"))
    event = next(o for o in db.added if isinstance(o, ProjectActivityEvent))
    assert (event.context["source"], event.context["model_name"]) == ("llm", "qwen2.5:7b")


async def test_a_name_twice_in_the_batch_is_a_409():
    release = _release()
    db = _Session([release, None, None])
    with pytest.raises(HTTPException) as exc:
        await _bulk(db, release, _body({"name": "Smoke"}, {"name": "UAT"}, {"name": " smoke "}))
    assert exc.value.status_code == 409
    assert exc.value.detail == "Phase 'smoke' appears twice in this request"
    assert not db.added and db.commits == 0


async def test_the_sentence_names_the_phase_as_written():
    release = _release()
    db = _Session([release, None, None])
    with pytest.raises(HTTPException) as exc:
        await _bulk(db, release, _body({"name": "Smoke"}, {"name": "Smoke"}))
    assert exc.value.detail == "Phase 'Smoke' appears twice in this request"


async def test_a_release_with_a_phase_is_a_409():
    release = _release()
    db = _Session([release, None, uuid.uuid4()])
    with pytest.raises(HTTPException) as exc:
        await _bulk(db, release, _body({"name": "Smoke"}))
    assert (exc.value.status_code, exc.value.detail) == (409, TARGET_HAS_PHASES)
    assert not db.added


async def test_a_type_outside_the_vocabulary_is_a_422():
    release = _release()
    db = _Session([release, None, None])
    with pytest.raises(HTTPException) as exc:
        await _bulk(db, release, _body({"name": "Smoke", "phase_type": "testing"}))
    assert (exc.value.status_code, exc.value.detail) == (422, PHASE_TYPE_SENTENCE)


async def test_an_end_before_its_start_is_a_422():
    release = _release()
    db = _Session([release, None, None])
    with pytest.raises(HTTPException) as exc:
        await _bulk(db, release, _body({
            "name": "Smoke", "planned_start": "2026-11-05", "planned_end": "2026-11-03",
        }))
    assert (exc.value.status_code, exc.value.detail) == (422, PHASE_WINDOW_SENTENCE)


def test_31_phases_or_none_are_refused_by_the_schema():
    with pytest.raises(ValidationError):
        ReleasePhasesBulkRequest(phases=[{"name": f"P{i}"} for i in range(31)])
    with pytest.raises(ValidationError):
        ReleasePhasesBulkRequest(phases=[])
    with pytest.raises(ValidationError):
        ReleasePhasesBulkRequest(phases=[{"name": "P", "status": "completed"}])   # extra key


async def test_the_draft_route_hands_its_connection_back_before_the_config_and_the_model(monkeypatch):
    calls: list[str] = []
    release = _release()
    db = _Session([release])

    async def released(session):
        calls.append("release_read_connection")
        return True

    async def config(*_a, **_k):
        calls.append("get_effective_ai_config")
        return {"analysis_mode": "auto", "offline_mode": False, "model": "qwen2.5:7b"}

    async def get_llm(*_a, **_k):
        calls.append("get_llm")
        raise RuntimeError("no model on this box")

    from app.services import release_phase_generator as gen

    monkeypatch.setattr(rel_router, "release_read_connection", released)
    monkeypatch.setattr(rel_router, "get_effective_ai_config", config)
    monkeypatch.setattr(gen, "get_llm", get_llm)

    out = await rel_router.draft_release_phases(
        release_id=str(release.id), text="## Smoke testing\n\n## UAT\n", file=None,
        db=db, current_user=_user(), __=_user(),
    )

    assert calls == ["release_read_connection", "get_effective_ai_config", "get_llm"]
    assert len(db.sql) == 1   # the release, then nothing more on the request's session
    assert out["release_id"] == release.id
    assert out["source"] == "rules" and out["fallback_reason"] == "llm_unavailable"
    assert [p["name"] for p in out["phases"]] == ["Smoke testing", "UAT"]


def test_the_three_routes_need_a_qa_lead():
    for handler in (rel_router.clone_release_phases, rel_router.draft_release_phases,
                    rel_router.create_release_phases_bulk):
        params = inspect.signature(handler).parameters
        assert params["__"].default.dependency.__qualname__.startswith("require_release_access")
        assert params["current_user"].default.dependency.__qualname__.startswith("require_role")
