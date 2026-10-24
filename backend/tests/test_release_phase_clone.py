"""W11 REL-05: copy a release's phases into a release that has none.

Over a statement-scripted session and the REAL ``release_service`` +
router handler:

1. the copy: four dated phases shifted to ``shift_to`` (+63 days), all
   Pending, ``actual_*`` cleared, ``order_index`` 0..3 in the source's order,
   ``exit_criteria`` equal but a deep copy; the target's cutoff start as the
   anchor when no ``shift_to``; neither and a dated source -> 422; an undated
   source copies undated with ``shifted_by_days`` None;
2. the body id: a source of ANOTHER project is 404 ``Release not found`` with
   NO phase read issued (the source statement carries the target's
   ``project_id``); the target itself -> 422; a source without phases -> 422;
   a target that has a phase -> 409 and nothing added;
3. the router stages ``release.phases_cloned`` on the SAME session before the
   commit (a failing commit leaves nothing committed), the metric's
   ``source="clone"`` sample grows by the number copied, and a QA engineer is
   refused by the route's own role dependency.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from prometheus_client import REGISTRY

from app.core.release_planning import (
    NO_SHIFT_ANCHOR,
    RELEASE_NOT_FOUND,
    SOURCE_HAS_NO_PHASES,
    SOURCE_IS_TARGET,
    TARGET_HAS_PHASES,
)
from app.models.postgres import ProjectActivityEvent, Release, ReleasePhase, UserRole
from app.models.schemas import ReleasePhasesCloneRequest
from app.routers import releases as rel_router

PROJECT = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._value or []))


class _Session:
    """Answers each statement from ``answers`` in order (a value, or a callable
    of the statement); records the SQL of every statement and every staged row,
    in one ordered log."""

    def __init__(self, answers, *, fail_commit: bool = False):
        self.answers = list(answers)
        self.sql: list[str] = []
        self.added: list = []
        self.log: list[str] = []
        self.commits = 0
        self.fail_commit = fail_commit
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, stmt, *a, **k):
        compiled = stmt.compile()
        self.sql.append(str(compiled))
        self.log.append("execute")
        if not self.answers:
            raise AssertionError(f"unexpected statement: {compiled}")
        answer = self.answers.pop(0)
        return _Result(answer(stmt) if callable(answer) else answer)

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
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits += 1

    async def refresh(self, obj):
        return None


def _release(name="2026.12", *, project=PROJECT, cutoff=None) -> Release:
    return Release(id=uuid.uuid4(), project_id=project, name=name, status="planning",
                   cutoff_start_at=cutoff)


def _source_phases(release: Release, *, dated=True) -> list[ReleasePhase]:
    windows = [(1, 5), (6, 10), (11, 15), (16, 20)]
    names = ["Smoke", "Regression", "UAT", "Production cutover"]
    types = ["qa_testing", "qa_testing", "uat", "production"]
    phases = []
    for i, ((a, b), name, kind) in enumerate(zip(windows, names, types)):
        phases.append(ReleasePhase(
            id=uuid.uuid4(), release_id=release.id, name=name, phase_type=kind,
            status="completed", description=f"{name} of 2026.10", order_index=i,
            planned_start=_utc(2026, 9, a) if dated else None,
            planned_end=_utc(2026, 9, b) if dated else None,
            actual_start=_utc(2026, 9, a), actual_end=_utc(2026, 9, b),
            exit_criteria={"thresholds": {"pass_rate_min": 95 - i}} if i < 2 else None,
            notes="signed off" if i == 3 else None,
        ))
    return phases


def _source_lookup(source: Release):
    """The source statement as a database would answer it: by id AND, when the
    statement says so, by the target's project."""

    def answer(stmt):
        compiled = stmt.compile()
        params = compiled.params
        if "releases.project_id" in str(compiled):
            wanted = [v for k, v in params.items() if k.startswith("project_id")]
            if wanted and wanted[0] != source.project_id:
                return None
        return source

    return answer


def _user(role=UserRole.QA_LEAD):
    return SimpleNamespace(id=uuid.uuid4(), username="lead", full_name="Lea Lead", role=role,
                           is_service_account=False)


async def _clone(db, target, source_id, shift_to=None):
    return await rel_router.clone_release_phases(
        release_id=str(target.id),
        body=ReleasePhasesCloneRequest(from_release_id=source_id, shift_to=shift_to),
        db=db, current_user=_user(), __=_user(),
    )


# ── 1. The copy ──────────────────────────────────────────────────────────────


async def test_four_dated_phases_move_to_shift_to_all_pending():
    target, source = _release(), _release("2026.10")
    phases = _source_phases(source)
    db = _Session([target, _source_lookup(source), phases, None, None])

    body = await _clone(db, target, source.id, shift_to=_utc(2026, 11, 3))

    rows = [o for o in db.added if isinstance(o, ReleasePhase)]
    assert [r.name for r in rows] == ["Smoke", "Regression", "UAT", "Production cutover"]
    assert [r.order_index for r in rows] == [0, 1, 2, 3]
    assert {r.status for r in rows} == {"pending"}
    assert all(r.actual_start is None and r.actual_end is None for r in rows)
    assert all(r.release_id == target.id for r in rows)
    for new, old in zip(rows, phases):
        assert new.planned_start == old.planned_start + timedelta(days=63)
        assert new.planned_end == old.planned_end + timedelta(days=63)
        assert new.phase_type == old.phase_type
        assert new.description == old.description and new.notes == old.notes
        assert new.exit_criteria == old.exit_criteria
        if old.exit_criteria is not None:
            assert new.exit_criteria is not old.exit_criteria   # a deep copy
            assert new.exit_criteria["thresholds"] is not old.exit_criteria["thresholds"]
    assert rows[0].planned_start == _utc(2026, 11, 3)
    assert body["created"] == 4
    assert body["shifted_by_days"] == 63
    assert body["source_release_id"] == source.id
    assert [p["name"] for p in body["phases"]] == ["Smoke", "Regression", "UAT", "Production cutover"]


async def test_without_shift_to_the_target_cutoff_start_is_the_anchor():
    target, source = _release(cutoff=_utc(2026, 12, 1)), _release("2026.10")
    db = _Session([target, _source_lookup(source), _source_phases(source), None, None])

    body = await _clone(db, target, source.id)

    rows = [o for o in db.added if isinstance(o, ReleasePhase)]
    assert rows[0].planned_start == _utc(2026, 12, 1)
    assert body["shifted_by_days"] == 91


async def test_no_anchor_and_a_dated_source_is_a_422_never_an_unshifted_copy():
    target, source = _release(), _release("2026.10")
    db = _Session([target, _source_lookup(source), _source_phases(source)])

    with pytest.raises(HTTPException) as exc:
        await _clone(db, target, source.id)

    assert exc.value.status_code == 422
    assert exc.value.detail == NO_SHIFT_ANCHOR
    assert not db.added and db.commits == 0


async def test_an_undated_source_copies_undated():
    target, source = _release(), _release("2026.10")
    db = _Session([target, _source_lookup(source), _source_phases(source, dated=False), None, None])

    body = await _clone(db, target, source.id)

    rows = [o for o in db.added if isinstance(o, ReleasePhase)]
    assert len(rows) == 4
    assert all(r.planned_start is None and r.planned_end is None for r in rows)
    assert body["shifted_by_days"] is None


# ── 2. The body id and the refusals ──────────────────────────────────────────


async def test_a_source_of_another_project_is_404_and_no_phase_is_read():
    target, foreign = _release(), _release("2026.10", project=OTHER_PROJECT)
    db = _Session([target, _source_lookup(foreign)])

    with pytest.raises(HTTPException) as exc:
        await _clone(db, target, foreign.id)

    assert exc.value.status_code == 404
    assert exc.value.detail == RELEASE_NOT_FOUND
    # Two statements: the target, then the source by id AND the target's project.
    assert len(db.sql) == 2
    assert "releases.project_id" in db.sql[1]
    assert not any("release_phases" in sql for sql in db.sql)
    assert db.commits == 0


async def test_an_unknown_source_is_the_same_404():
    target = _release()
    db = _Session([target, None])
    with pytest.raises(HTTPException) as exc:
        await _clone(db, target, uuid.uuid4())
    assert (exc.value.status_code, exc.value.detail) == (404, RELEASE_NOT_FOUND)


async def test_the_target_itself_is_a_422():
    target = _release()
    db = _Session([target, _source_lookup(target)])
    with pytest.raises(HTTPException) as exc:
        await _clone(db, target, target.id)
    assert (exc.value.status_code, exc.value.detail) == (422, SOURCE_IS_TARGET)


async def test_a_source_without_phases_is_a_422():
    target, source = _release(), _release("2026.10")
    db = _Session([target, _source_lookup(source), []])
    with pytest.raises(HTTPException) as exc:
        await _clone(db, target, source.id, shift_to=_utc(2026, 11, 3))
    assert (exc.value.status_code, exc.value.detail) == (422, SOURCE_HAS_NO_PHASES)


async def test_a_target_with_a_phase_is_a_409_and_nothing_is_added():
    target, source = _release(), _release("2026.10")
    db = _Session([target, _source_lookup(source), _source_phases(source), None, uuid.uuid4()])

    with pytest.raises(HTTPException) as exc:
        await _clone(db, target, source.id, shift_to=_utc(2026, 11, 3))

    assert (exc.value.status_code, exc.value.detail) == (409, TARGET_HAS_PHASES)
    assert not db.added and db.commits == 0
    # The lock is taken before the phases are read.
    assert "FOR UPDATE" in db.sql[3]
    assert "release_phases" in db.sql[4]


# ── 3. The event, the metric, the role ───────────────────────────────────────


def _metric(source: str) -> float:
    return REGISTRY.get_sample_value(
        "testlookup_release_phases_created_total", {"source": source}
    ) or 0.0


async def test_the_event_is_staged_on_the_request_session_before_the_commit():
    target, source = _release(), _release("2026.10")
    db = _Session([target, _source_lookup(source), _source_phases(source), None, None])
    before = _metric("clone")

    await _clone(db, target, source.id, shift_to=_utc(2026, 11, 3))

    events = [o for o in db.added if isinstance(o, ProjectActivityEvent)]
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "release.phases_cloned"
    assert event.context["count"] == 4
    assert event.context["source_release"] == "2026.10"
    assert event.context["shifted_by_days"] == 63
    assert "4 phases copied from 2026.10" in event.summary
    assert db.log.index("add:ProjectActivityEvent") < db.log.index("commit")
    assert db.commits == 1
    assert _metric("clone") == before + 4


async def test_a_failing_commit_counts_nothing():
    target, source = _release(), _release("2026.10")
    db = _Session([target, _source_lookup(source), _source_phases(source), None, None], fail_commit=True)
    before = _metric("clone")

    with pytest.raises(RuntimeError):
        await _clone(db, target, source.id, shift_to=_utc(2026, 11, 3))

    assert db.commits == 0
    assert _metric("clone") == before


async def test_a_qa_engineer_is_refused_by_the_route_dependency():
    dependency = inspect.signature(rel_router.clone_release_phases).parameters["current_user"].default.dependency
    engineer = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER)
    with pytest.raises(HTTPException) as exc:
        await dependency(current_user=engineer)
    assert exc.value.status_code == 403
    lead = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_LEAD)
    assert await dependency(current_user=lead) is lead


async def test_the_path_id_is_scoped_by_require_release_access():
    params = inspect.signature(rel_router.clone_release_phases).parameters
    assert params["__"].default.dependency.__qualname__.startswith("require_release_access")
