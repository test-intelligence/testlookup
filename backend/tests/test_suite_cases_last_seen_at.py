"""W13 TCL-07: ``GET /api/v1/suites/{suite_id}/test-cases`` carries ``last_seen_at``.

The latest-run cell on ``/suites/:id`` showed only the run's id. Each canonical
row now carries the ``created_at`` of the run its ``last_seen_run_id`` names,
read by RUN id in ONE statement scoped to the suite's project — so the time
shows even when the per-run result is gone, and a run id naming another
project's run resolves to ``None`` (never a guessed date). The legacy fallback
rows (derived from per-run results) carry their own ``created_at``.

The session below answers each statement from an in-memory table by the
statement's OWN bound parameters, so the project filter is exercised, not
assumed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


class _TableSession:
    """``test_runs`` and ``test_cases`` as dicts; each SELECT filtered by its params."""

    def __init__(self, *, runs, results):
        self.runs = runs            # {run_id: (project_id, created_at)}
        self.results = results      # [(test_case_id, fingerprint, run_id)]
        self.statements: list[str] = []

    async def execute(self, stmt, *_a, **_k):
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        params = stmt.compile().params
        result = MagicMock()
        if "FROM test_runs" in sql:
            wanted = set(next(v for k, v in params.items() if k.startswith("id_")))
            # No project parameter = no project filter (what a regression would send).
            project = next((v for k, v in params.items() if k.startswith("project_id")), None)
            result.all.return_value = [
                (run_id, created_at)
                for run_id, (run_project, created_at) in self.runs.items()
                if run_id in wanted and (project is None or run_project == project)
            ]
        else:
            keys = {tuple(pair) for pair in next(iter(params.values()))}
            result.all.return_value = [row for row in self.results if (row[1], row[2]) in keys]
        return result


def _canonical(project_id, suite_id, *, fingerprint, run_id):
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, test_suite_id=suite_id, test_fingerprint=fingerprint,
        test_name=f"test {fingerprint}", class_name=None, status="active", source="execution",
        first_seen_run_id=None, last_seen_run_id=run_id, deleted_at_run_id=None,
        managed_test_case_id=None, retirement_confirmed_at=None, retirement_confirmed_by_id=None,
        retirement_reason=None, deleted_observed_at=None, review_tag=None, tags=None,
        created_at=now, updated_at=now,
    )


def _wire(monkeypatch, suite, *, canonicals, legacy=None):
    from app.routers import suites
    from app.services import test_suite_service as svc

    monkeypatch.setattr(suites, "_enforce_project_access", AsyncMock())
    monkeypatch.setattr(svc, "get_suite_or_404", AsyncMock(return_value=suite))
    monkeypatch.setattr(svc, "list_canonical_test_cases", AsyncMock(return_value=(canonicals, len(canonicals))))
    monkeypatch.setattr(svc, "list_legacy_suite_test_cases", AsyncMock(return_value=legacy or []))
    return suites


@pytest.mark.asyncio
async def test_each_canonical_carries_its_last_runs_time_within_the_suites_project(monkeypatch):
    from app.models.schemas import CanonicalTestCaseListResponse

    project_id, other_project = uuid.uuid4(), uuid.uuid4()
    suite = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Checkout API")
    run_a, run_b, foreign_run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    t_a = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    t_b = datetime(2026, 10, 21, 14, 5, tzinfo=timezone.utc)
    rows = [
        _canonical(project_id, suite.id, fingerprint="fp-a", run_id=run_a),
        # Its per-run result was garbage-collected: no deep link, but a time.
        _canonical(project_id, suite.id, fingerprint="fp-b", run_id=run_b),
        # A run id naming ANOTHER project's run: no date (the project filter).
        _canonical(project_id, suite.id, fingerprint="fp-c", run_id=foreign_run),
        _canonical(project_id, suite.id, fingerprint="fp-d", run_id=None),
    ]
    result_a = uuid.uuid4()
    db = _TableSession(
        runs={run_a: (project_id, t_a), run_b: (project_id, t_b),
              foreign_run: (other_project, datetime(2026, 10, 22, tzinfo=timezone.utc))},
        results=[(result_a, "fp-a", run_a), (uuid.uuid4(), "fp-c", foreign_run)],
    )
    suites = _wire(monkeypatch, suite, canonicals=rows)

    body = await suites.list_suite_test_cases(suite.id, db=db, current_user=SimpleNamespace())

    by_fp = {item["test_fingerprint"]: item for item in body["items"]}
    assert by_fp["fp-a"]["last_seen_at"] == t_a
    assert by_fp["fp-a"]["last_seen_test_case_id"] == result_a
    assert by_fp["fp-b"]["last_seen_at"] == t_b
    assert by_fp["fp-b"]["last_seen_test_case_id"] is None
    assert by_fp["fp-c"]["last_seen_at"] is None
    assert by_fp["fp-d"]["last_seen_at"] is None
    # ONE statement for the run times, scoped to the suite's project.
    run_reads = [sql for sql in db.statements if "FROM test_runs" in sql]
    assert len(run_reads) == 1
    assert "test_runs.project_id = :project_id_1" in run_reads[0]
    parsed = CanonicalTestCaseListResponse.model_validate(body)
    assert parsed.items[0].last_seen_at is not None


@pytest.mark.asyncio
async def test_no_run_ids_reads_no_run_times_and_legacy_rows_carry_their_created_at(monkeypatch):
    project_id = uuid.uuid4()
    suite = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Smoke")
    db = _TableSession(runs={}, results=[])
    suites = _wire(monkeypatch, suite, canonicals=[
        _canonical(project_id, suite.id, fingerprint="fp", run_id=None),
    ])
    body = await suites.list_suite_test_cases(suite.id, db=db, current_user=SimpleNamespace())
    assert body["items"][0]["last_seen_at"] is None
    assert not any("FROM test_runs" in sql for sql in db.statements)

    ran = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
    legacy = [{"id": uuid.uuid4(), "test_fingerprint": "fp", "last_seen_run_id": uuid.uuid4(),
               "created_at": ran, "updated_at": ran}]
    suites = _wire(monkeypatch, suite, canonicals=[], legacy=legacy)
    body = await suites.list_suite_test_cases(suite.id, db=_TableSession(runs={}, results=[]),
                                              current_user=SimpleNamespace())
    assert body["items"][0]["last_seen_at"] == ran
    assert body["total"] == 1
