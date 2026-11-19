"""Rule: a phase id is only accepted together with the release it belongs to.

Two routes take a release id in the path and a phase id beside it:

* ``POST /releases/{release_id}/phases/{phase_id}/gate/evaluate``
* ``POST /releases/{release_id}/test-runs`` (``phase_id`` in the body)

The access guard on both checks the release in the path. The phase must then be
a phase OF that release. If it is not, the caller could name a phase of a
release in a project they cannot read: the gate route answered with that
phase's name and policy, and the link route stored the foreign phase id on a
run link in the caller's own project.

Both routes answer 404 "Phase not found" for a phase of another release, and
the same 404 for an id that does not exist, so the answer does not tell a
caller whether a phase id is real.

The fake session below answers the phase lookup the way the database would: it
returns a row only when EVERY condition in the statement's WHERE clause matches
it. A lookup by id alone therefore still finds a foreign phase, which is what
makes the refusal tests fail when the release condition is missing.
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.postgres import ReleaseTestRunLink
from app.routers import releases as releases_router
from app.services import release_phase_gate_service as gate
from app.services import release_service
from app.services.release_rollup_service import STATUSES, ReleaseRollup

pytestmark = pytest.mark.regression

PROJECT_A = uuid.uuid4()
PROJECT_B = uuid.uuid4()
RELEASE_A = uuid.uuid4()
RELEASE_B = uuid.uuid4()


def _phase(release_id, name, exit_criteria=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        release_id=release_id,
        name=name,
        exit_criteria=exit_criteria,
        order_index=0,
        project_id=None,
    )


PHASE_A = _phase(RELEASE_A, "QA")
PHASE_B = _phase(RELEASE_B, "UAT Sign-off", exit_criteria={"thresholds": {"go": 99}})


class _Result:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _matching_phase(stmt, phases):
    """The first phase that satisfies every bound condition of ``stmt``."""
    params = stmt.compile().params
    for phase in phases:
        if all(
            str(getattr(phase, re.sub(r"_\d+$", "", key))) == str(value)
            for key, value in params.items()
        ):
            return phase
    return None


class _Session:
    """Answers each read by the table it names; records what was written."""

    def __init__(self, *, run=None):
        self.phases = [PHASE_A, PHASE_B]
        self.run = run
        self.added: list = []
        self.commits = 0
        self.flush = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, stmt, *args, **kwargs):
        sql = " ".join(str(stmt).split())
        if "FROM release_phases" in sql:
            return _Result(_matching_phase(stmt, self.phases))
        if "FROM test_runs" in sql:
            return _Result(self.run)
        # The existing-link lookup, the primary-link demotion, the project read.
        return _Result(None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def gate_fakes(monkeypatch):
    """The rollup, the policy and the decision table, faked and observable."""
    seen = SimpleNamespace(rollups=[], recorded=[])

    async def _build(db, release_id, phase_id=None):
        seen.rollups.append((str(release_id), str(phase_id)))
        rollup = ReleaseRollup(latest_by_test={})
        rollup.status_counts = {s: 0 for s in STATUSES}
        return rollup

    async def _policy(db, project_id, **kwargs):
        return {"document": {}, "sources": {}, "layers": [], "effective_level": "hardcoded"}

    async def _current(db, release_id, *, phase_id=None):
        return None

    async def _record(db, release_id, verdict, **kwargs):
        seen.recorded.append({"release_id": str(release_id), "verdict": verdict, **kwargs})
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(gate.rollup_svc, "build_rollup", _build)
    monkeypatch.setattr(gate.policy_resolution, "resolve_effective_document", _policy)
    monkeypatch.setattr(gate.decisions, "current_decision", _current)
    monkeypatch.setattr(gate.decisions, "record_decision", _record)
    return seen


# ── Gate evaluate ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluating_a_phase_of_another_release_is_not_found(gate_fakes):
    with pytest.raises(HTTPException) as exc:
        await gate.evaluate_phase(_Session(), str(RELEASE_A), str(PHASE_B.id), record=True)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Phase not found"
    # Nothing of the foreign phase travels with the refusal.
    assert PHASE_B.name not in str(exc.value.detail)
    assert gate_fakes.rollups == [], "the foreign phase was rolled up against this release"
    assert gate_fakes.recorded == [], "a gate decision was recorded for a foreign phase"


@pytest.mark.asyncio
async def test_an_unknown_phase_answers_exactly_like_a_foreign_one(gate_fakes):
    """Same status and same text, so the route does not say which ids exist."""
    with pytest.raises(HTTPException) as unknown:
        await gate.evaluate_phase(_Session(), str(RELEASE_A), str(uuid.uuid4()), record=False)
    with pytest.raises(HTTPException) as foreign:
        await gate.evaluate_phase(_Session(), str(RELEASE_A), str(PHASE_B.id), record=False)

    assert (unknown.value.status_code, unknown.value.detail) == (404, "Phase not found")
    assert (foreign.value.status_code, foreign.value.detail) == (404, "Phase not found")


@pytest.mark.asyncio
async def test_a_phase_id_that_is_not_a_uuid_is_not_found(gate_fakes):
    with pytest.raises(HTTPException) as exc:
        await gate.evaluate_phase(_Session(), str(RELEASE_A), "not-a-uuid", record=False)

    assert (exc.value.status_code, exc.value.detail) == (404, "Phase not found")


@pytest.mark.asyncio
async def test_the_route_refuses_a_foreign_phase_and_commits_nothing(gate_fakes):
    db = _Session()
    user = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await releases_router.evaluate_release_phase_gate(
            release_id=str(RELEASE_A), phase_id=str(PHASE_B.id), record=True,
            db=db, current_user=user, __=user,
        )

    assert exc.value.status_code == 404
    assert db.commits == 0
    assert gate_fakes.recorded == []


@pytest.mark.asyncio
async def test_evaluating_a_releases_own_phase_still_answers_and_records(gate_fakes):
    author = uuid.uuid4()

    result = await gate.evaluate_phase(
        _Session(), str(RELEASE_A), str(PHASE_A.id), record=True, created_by_id=author
    )

    assert result["phase_id"] == str(PHASE_A.id)
    assert result["phase_name"] == "QA"
    assert result["verdict"] == "NOT_EVALUATED"  # no runs: the evidence floor
    assert result["recorded"] is True
    assert "policy" in result
    assert len(gate_fakes.recorded) == 1
    assert str(gate_fakes.recorded[0]["phase_id"]) == str(PHASE_A.id)
    assert gate_fakes.recorded[0]["release_id"] == str(RELEASE_A)
    assert gate_fakes.recorded[0]["created_by_id"] == author


@pytest.mark.asyncio
async def test_internal_callers_may_pass_uuid_objects(gate_fakes):
    """Phase enforcement and the all-phases read hand over ``phase.id`` itself."""
    result = await gate.evaluate_phase(_Session(), RELEASE_A, PHASE_A.id, record=False)

    assert result["phase_name"] == "QA"
    assert result["recorded"] is False


# ── Link a run ───────────────────────────────────────────────────────────────


@pytest.fixture
def link_fakes(monkeypatch):
    release = SimpleNamespace(id=RELEASE_A, project_id=PROJECT_A, name="2026.11")
    run = SimpleNamespace(id=uuid.uuid4(), project_id=PROJECT_A, build_number="42")
    monkeypatch.setattr(release_service, "get_release_or_404", AsyncMock(return_value=release))
    monkeypatch.setattr(release_service, "sync_primary_release", AsyncMock())
    return SimpleNamespace(release=release, run=run, db=_Session(run=run))


def _links(db):
    return [obj for obj in db.added if isinstance(obj, ReleaseTestRunLink)]


@pytest.mark.asyncio
async def test_linking_a_run_with_a_phase_of_another_release_is_not_found(link_fakes):
    body = SimpleNamespace(test_run_id=str(link_fakes.run.id), phase_id=str(PHASE_B.id))

    with pytest.raises(HTTPException) as exc:
        await release_service.link_test_run(link_fakes.db, str(RELEASE_A), body)

    assert (exc.value.status_code, exc.value.detail) == (404, "Phase not found")
    assert _links(link_fakes.db) == [], "a link carrying a foreign phase id was staged"
    link_fakes.db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_linking_a_run_with_an_unknown_phase_is_not_found(link_fakes):
    body = SimpleNamespace(test_run_id=str(link_fakes.run.id), phase_id=str(uuid.uuid4()))

    with pytest.raises(HTTPException) as exc:
        await release_service.link_test_run(link_fakes.db, str(RELEASE_A), body)

    assert (exc.value.status_code, exc.value.detail) == (404, "Phase not found")
    assert _links(link_fakes.db) == []


@pytest.mark.asyncio
async def test_linking_a_run_with_the_releases_own_phase_stores_that_phase(link_fakes):
    body = SimpleNamespace(test_run_id=str(link_fakes.run.id), phase_id=str(PHASE_A.id))

    link, is_new = await release_service.link_test_run(link_fakes.db, str(RELEASE_A), body)

    assert is_new is True
    assert link.phase_id == PHASE_A.id
    assert link.release_id == RELEASE_A
    assert _links(link_fakes.db) == [link]


@pytest.mark.asyncio
async def test_linking_a_run_without_a_phase_still_works(link_fakes):
    body = SimpleNamespace(test_run_id=str(link_fakes.run.id), phase_id=None)

    link, is_new = await release_service.link_test_run(link_fakes.db, str(RELEASE_A), body)

    assert is_new is True
    assert link.phase_id is None
