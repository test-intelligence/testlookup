"""W09 FLK-03: the four-signal score joined onto Flaky tests › Detected.

``flaky_score_brief.attach_flaky_scores`` over a fake session that holds
``flaky_score`` rows and test executions of TWO projects and answers each
statement the way Postgres would, honouring only the predicates the statement
actually carries (so a statement that forgot the project would read the other
project's rows, as it would in production):

* a scored row → ``"scored"``, the four components in ``COMPONENT_ORDER`` with
  the ROW's stored weights (a weight an old row lacks reads the default),
  ``computed_at`` ISO UTC;
* the same fingerprint scored in another project is ignored, and both
  statements carry ``project_id`` (fingerprints are unique per project only);
* no row: below the floor → ``"insufficient"`` with the sweep's count, at or
  above it → ``"pending"``; executions older than the window do not count;
* a failing statement leaves every ``flaky_score`` ``None`` (one warning) and
  the route answers 200; no entries → no statement;
* with ``release_id`` only the filtered entries are looked up.

The same rules over real Postgres: ``tests/integration/test_flaky_score_join_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import FlakyCoachEntry, FlakyCoachResponse, FlakyScoreBrief
from app.services import flaky_score_brief as brief_module
from app.services import flaky_score_service

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime.now(timezone.utc)
COMPUTED = datetime(2026, 10, 16, 2, 10, tzinfo=timezone.utc)


def _score_row(project_id, fingerprint, *, score=0.62, weights=None, components=None, observations=23,
               confidence="medium"):
    return SimpleNamespace(
        project_id=project_id,
        test_fingerprint=fingerprint,
        score=score,
        components=components if components is not None else {
            "result_volatility": 0.7, "retry_rate": 0.5, "environment_instability": 0.4, "duration_variance": 0.1,
        },
        weights=weights if weights is not None else {
            "result_volatility": 0.5, "retry_rate": 0.2, "environment_instability": 0.2, "duration_variance": 0.1,
        },
        observation_count=observations,
        confidence=confidence,
        window_days=30,
        computed_at=COMPUTED,
    )


def _entry(fingerprint: str) -> FlakyCoachEntry:
    return FlakyCoachEntry(test_fingerprint=fingerprint, test_name=f"test_{fingerprint}", failure_rate=0.4)


def _response(*fingerprints: str) -> FlakyCoachResponse:
    return FlakyCoachResponse(project_id=str(PROJECT), entries=[_entry(fp) for fp in fingerprints])


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Savepoint:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.savepoints += 1
        return self

    async def __aexit__(self, *exc):
        return False


def _params(stmt) -> dict:
    return stmt.compile().params


def _where_sql(stmt) -> str:
    return str(stmt.whereclause)


class _Session:
    """``flaky_score`` rows and executions ``(project_id, fingerprint, created_at)``."""

    def __init__(self, scores=(), executions=(), fail=False):
        self.scores = list(scores)
        self.executions = list(executions)
        self.fail = fail
        self.statements: list = []
        self.savepoints = 0

    def begin_nested(self):
        return _Savepoint(self)

    async def execute(self, stmt):
        self.statements.append(stmt)
        if self.fail:
            raise RuntimeError("connection reset")
        sql, where, params = str(stmt), _where_sql(stmt), _params(stmt)
        fingerprints = set(next(v for k, v in params.items() if k.startswith("test_fingerprint")))
        project = next((v for k, v in params.items() if k.startswith("project_id")), None)
        if "FROM flaky_score" in sql:
            scoped = "flaky_score.project_id" in where
            return _Rows([
                r for r in self.scores
                if r.test_fingerprint in fingerprints and (not scoped or r.project_id == project)
            ])
        if "FROM test_cases" in sql:
            scoped = "test_runs.project_id" in where
            since = next(v for k, v in params.items() if k.startswith("created_at"))
            counts: dict[str, int] = {}
            for pid, fp, created_at in self.executions:
                if fp in fingerprints and created_at >= since and (not scoped or pid == project):
                    counts[fp] = counts.get(fp, 0) + 1
            return _Rows(list(counts.items()))
        raise AssertionError(f"unexpected statement: {sql}")


def _brief(response: FlakyCoachResponse, fingerprint: str) -> FlakyScoreBrief | None:
    return next(e.flaky_score for e in response.entries if e.test_fingerprint == fingerprint)


def test_the_component_order_is_the_weights_order():
    assert brief_module.COMPONENT_ORDER == (
        "result_volatility", "retry_rate", "environment_instability", "duration_variance",
    )
    assert brief_module.STATUSES == ("scored", "insufficient", "pending")
    # The schema's default floor is the service's (the UI reads it, never hard-codes it).
    assert FlakyScoreBrief(status="pending").min_observations == flaky_score_service.MIN_OBSERVATIONS


@pytest.mark.asyncio
async def test_a_scored_row_carries_its_own_weights_in_order():
    db = _Session(scores=[_score_row(PROJECT, "fp-a")])
    response = await brief_module.attach_flaky_scores(db, PROJECT, _response("fp-a"))
    brief = _brief(response, "fp-a")
    assert brief is not None
    assert (brief.status, brief.score, brief.confidence, brief.observations) == ("scored", 0.62, "medium", 23)
    assert (brief.min_observations, brief.window_days) == (5, 30)
    assert brief.computed_at == "2026-10-16T02:10:00+00:00"
    assert [(c.name, c.value, c.weight) for c in brief.components] == [
        ("result_volatility", 0.7, 0.5),
        ("retry_rate", 0.5, 0.2),
        ("environment_instability", 0.4, 0.2),
        ("duration_variance", 0.1, 0.1),
    ]
    assert sum(c.weight for c in brief.components) == pytest.approx(1.0, abs=1e-9)
    assert len(db.statements) == 1, "every row scored: no count statement"
    assert db.savepoints == 1


@pytest.mark.asyncio
async def test_an_old_row_without_a_weight_reads_the_default():
    row = _score_row(PROJECT, "fp-a", weights={"result_volatility": 0.45, "retry_rate": 0.25},
                     components={"result_volatility": 0.9})
    response = await brief_module.attach_flaky_scores(_Session(scores=[row]), PROJECT, _response("fp-a"))
    brief = _brief(response, "fp-a")
    assert [(c.name, c.value, c.weight) for c in brief.components] == [
        ("result_volatility", 0.9, 0.45),
        ("retry_rate", 0.0, 0.25),
        ("environment_instability", 0.0, 0.20),
        ("duration_variance", 0.0, 0.10),
    ]


@pytest.mark.asyncio
async def test_both_statements_are_scoped_to_the_project():
    db = _Session(scores=[_score_row(PROJECT, "fp-a")], executions=[(PROJECT, "fp-b", NOW)])
    await brief_module.attach_flaky_scores(db, PROJECT, _response("fp-a", "fp-b"))
    scores, counts = db.statements
    assert "flaky_score.project_id = :project_id_1" in _where_sql(scores)
    assert _params(scores)["project_id_1"] == PROJECT
    assert "test_runs.project_id = :project_id_1" in _where_sql(counts)
    assert _params(counts)["project_id_1"] == PROJECT
    # The count asks only for the fingerprints without a row.
    assert _params(counts)["test_fingerprint_1"] == ["fp-b"]


@pytest.mark.asyncio
async def test_the_same_fingerprint_scored_in_another_project_is_ignored():
    db = _Session(
        scores=[_score_row(OTHER, "fp-shared", score=0.99)],
        executions=[(PROJECT, "fp-shared", NOW)] * 3 + [(OTHER, "fp-shared", NOW)] * 40,
    )
    response = await brief_module.attach_flaky_scores(db, PROJECT, _response("fp-shared"))
    brief = _brief(response, "fp-shared")
    assert brief is not None
    assert brief.status == "insufficient"
    assert brief.score is None
    assert brief.observations == 3


@pytest.mark.asyncio
async def test_below_the_floor_is_insufficient_and_at_it_pending():
    old = NOW - timedelta(days=45)
    db = _Session(executions=(
        [(PROJECT, "fp-thin", NOW)] * 3 + [(PROJECT, "fp-thin", old)] * 10
        + [(PROJECT, "fp-ready", NOW)] * 7
    ))
    response = await brief_module.attach_flaky_scores(db, PROJECT, _response("fp-thin", "fp-ready", "fp-never"))
    thin, ready, never = (_brief(response, fp) for fp in ("fp-thin", "fp-ready", "fp-never"))
    assert (thin.status, thin.observations, thin.min_observations, thin.score) == ("insufficient", 3, 5, None)
    assert thin.confidence == "none" and thin.components == [] and thin.computed_at is None
    assert (ready.status, ready.observations, ready.score) == ("pending", 7, None)
    assert (never.status, never.observations) == ("insufficient", 0)


@pytest.mark.asyncio
async def test_a_failing_statement_leaves_every_score_null(monkeypatch):
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(brief_module.logger, "warning", lambda event, **kw: warnings.append((event, kw)))
    response = await brief_module.attach_flaky_scores(_Session(fail=True), PROJECT, _response("fp-a", "fp-b"))
    assert [e.flaky_score for e in response.entries] == [None, None]
    assert warnings == [("flaky_score_attach_failed", {"project_id": str(PROJECT), "error_type": "RuntimeError"})]


@pytest.mark.asyncio
async def test_a_session_without_savepoints_is_swallowed_too(monkeypatch):
    monkeypatch.setattr(brief_module.logger, "warning", lambda *a, **k: None)
    response = await brief_module.attach_flaky_scores(object(), PROJECT, _response("fp-a"))
    assert response.entries[0].flaky_score is None


@pytest.mark.asyncio
async def test_no_entries_run_no_statement():
    db = _Session()
    response = _response()
    assert await brief_module.attach_flaky_scores(db, PROJECT, response) is response
    assert db.statements == [] and db.savepoints == 0


# ── Through the route ────────────────────────────────────────────────────────


class _RouteSession(_Session):
    """Also answers the route's own project lookup."""

    async def execute(self, stmt):
        if "FROM projects" in str(stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=PROJECT))
        return await super().execute(stmt)


@pytest.fixture
def route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_health

    state = SimpleNamespace(db=_RouteSession(), coach=AsyncMock())
    admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True, is_service_account=False,
                            full_name="Ada", username="ada", email="ada@example.test")

    async def _db():
        yield state.db

    monkeypatch.setattr(test_health, "get_flaky_coach", state.coach)
    app = FastAPI()
    app.include_router(test_health.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    state.client = TestClient(app, raise_server_exceptions=False)
    return state


def test_the_route_answers_200_when_the_join_fails(route, monkeypatch):
    monkeypatch.setattr(brief_module.logger, "warning", lambda *a, **k: None)
    route.coach.return_value = _response("fp-a")

    async def broken(stmt):
        if "FROM projects" in str(stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=PROJECT))
        raise RuntimeError("relation flaky_score does not exist")

    route.db.execute = broken
    response = route.client.get(f"/api/v1/projects/{PROJECT}/flaky-coach")
    assert response.status_code == 200, response.text
    [entry] = response.json()["entries"]
    assert entry["test_fingerprint"] == "fp-a" and entry["flaky_score"] is None


def test_the_route_attaches_scores_to_the_listed_entries(route):
    route.db.scores = [_score_row(PROJECT, "fp-a")]
    route.coach.return_value = _response("fp-a")
    response = route.client.get(f"/api/v1/projects/{PROJECT}/flaky-coach")
    assert response.status_code == 200, response.text
    [entry] = response.json()["entries"]
    assert entry["flaky_score"]["status"] == "scored"
    assert entry["flaky_score"]["score"] == 0.62
    assert [c["name"] for c in entry["flaky_score"]["components"]] == list(brief_module.COMPONENT_ORDER)


def test_with_a_release_only_the_filtered_entries_are_looked_up(route, monkeypatch):
    from app.routers import test_health

    release = str(uuid.uuid4())
    monkeypatch.setattr(test_health, "resolve_release_query_scope", AsyncMock(return_value=release))
    # The coach service has already filtered to the release's tests: fp-b only.
    route.coach.return_value = _response("fp-b")
    response = route.client.get(f"/api/v1/projects/{PROJECT}/flaky-coach", params={"release_id": release})
    assert response.status_code == 200, response.text
    assert route.coach.await_args.kwargs["release_id"] == release
    scores = route.db.statements[0]
    assert _params(scores)["test_fingerprint_1"] == ["fp-b"]
