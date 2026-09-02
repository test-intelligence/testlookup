"""S5 — preview freezes, execute replays. What each refuses, and why.

The freeze is the load-bearing idea. Everything here either proves the frozen
set is what runs, or proves a request that should never reach the executor
does not.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.routers.retention import (  # noqa: E402
    execute_criteria_deletion,
    preview_criteria_deletion,
)
from app.services import deletion_job_service as jobs  # noqa: E402
from app.services.deletion_criteria import RetentionCriteria  # noqa: E402


class _Result:
    def __init__(self, value=None, rows=None):
        self._value, self._rows = value, rows or []

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return type("S", (), {"all": staticmethod(lambda: list(self._rows))})()

    def all(self):
        return list(self._rows)


class _DB:
    """Answers each execute from a queue, in issue order."""

    def __init__(self, *results):
        self._queue = list(results)
        self.added: list = []
        self.commits = 0

    async def execute(self, *_a, **_kw):
        return self._queue.pop(0) if self._queue else _Result()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


# ── execute takes a job id, never criteria ───────────────────────────────────


def test_execute_accepts_a_job_id_and_not_a_criteria_body():
    """The signature IS the guarantee.

    Accepting criteria here would re-resolve them at execute time, which is
    precisely the bug the freeze exists to prevent: the set executed would not
    be the set an ADMIN reviewed.
    """
    from app.models.schemas import DeletionExecuteRequest

    fields = set(DeletionExecuteRequest.model_fields)
    assert fields == {"job_id", "confirmation_name"}, (
        f"execute takes {fields}; a criteria field here re-opens the drift "
        "the freeze closes"
    )

    source = inspect.getsource(execute_criteria_deletion)
    assert "resolve_criteria_candidates" not in source, (
        "execute re-resolves the criteria instead of replaying the frozen set"
    )


def test_preview_is_the_only_producer_of_a_frozen_set():
    source = inspect.getsource(preview_criteria_deletion)
    assert "stage_frozen_candidate_set" in source
    assert "resolve_criteria_candidates" in source


# ── preview ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_foreign_run_ids_fail_the_whole_request(mocker):
    """403 for the request, not a filtered subset.

    Deleting only the ids that happened to be theirs acts on a request the
    caller got wrong and tells them nothing. The refusal also must not name
    which id was foreign — that would confirm it exists elsewhere.
    """
    mocker.patch(
        "app.services.deletion_criteria.foreign_run_ids",
        mocker.AsyncMock(return_value=[uuid.uuid4()]),
    )
    frozen = mocker.patch(
        "app.services.deletion_job_service.stage_frozen_candidate_set",
        mocker.Mock(return_value=uuid.uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        await preview_criteria_deletion(
            project_id=uuid.uuid4(),
            criteria=RetentionCriteria(run_ids=[uuid.uuid4()]),
            db=_DB(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            _=SimpleNamespace(),
        )

    assert exc.value.status_code == 403
    detail = str(exc.value.detail)
    assert "do not belong" in detail
    frozen.assert_not_called()


@pytest.mark.asyncio
async def test_a_blocked_run_is_reported_and_excluded_from_the_frozen_set(mocker):
    """Blockers surface at PREVIEW, not mid-execution.

    An ADMIN authorises a count. If cited runs were discovered later, the
    number that went would not be the number approved.
    """
    project_id = uuid.uuid4()
    ok_run, cited_run = uuid.uuid4(), uuid.uuid4()

    mocker.patch(
        "app.services.deletion_criteria.foreign_run_ids",
        mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.deletion_criteria.resolve_criteria_candidates",
        mocker.AsyncMock(return_value=[ok_run, cited_run]),
    )
    mocker.patch("app.db.mongo.get_mongo_db", mocker.AsyncMock(return_value={}))
    mocker.patch(
        "app.services.run_deletion_service.citation_blockers",
        mocker.AsyncMock(side_effect=[[], ["cited by 1 compliance pack(s)"]]),
    )
    frozen = mocker.patch(
        "app.services.deletion_job_service.stage_frozen_candidate_set",
        mocker.Mock(return_value=uuid.uuid4()),
    )

    from app.models.postgres import LaunchStatus

    rows = _Result(rows=[(ok_run, LaunchStatus.PASSED, None),
                         (cited_run, LaunchStatus.PASSED, None)])

    response = await preview_criteria_deletion(
        project_id=project_id,
        criteria=RetentionCriteria(statuses=["PASSED"]),
        db=_DB(rows),
        current_user=SimpleNamespace(id=uuid.uuid4()),
        _=SimpleNamespace(),
    )

    assert response.run_count == 1
    assert response.run_ids == [ok_run]
    assert len(response.blocked) == 1
    assert response.blocked[0]["run_id"] == str(cited_run)
    # The frozen set must hold only what can actually be deleted.
    assert frozen.call_args.kwargs["run_ids"] == [ok_run]


@pytest.mark.asyncio
async def test_an_in_flight_run_is_blocked_at_preview(mocker):
    from app.models.postgres import LaunchStatus

    run_id = uuid.uuid4()
    mocker.patch(
        "app.services.deletion_criteria.foreign_run_ids",
        mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.deletion_criteria.resolve_criteria_candidates",
        mocker.AsyncMock(return_value=[run_id]),
    )
    mocker.patch("app.db.mongo.get_mongo_db", mocker.AsyncMock(return_value={}))
    mocker.patch(
        "app.services.run_deletion_service.citation_blockers",
        mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.deletion_job_service.stage_frozen_candidate_set",
        mocker.Mock(return_value=uuid.uuid4()),
    )

    response = await preview_criteria_deletion(
        project_id=uuid.uuid4(),
        criteria=RetentionCriteria(statuses=["IN_PROGRESS"]),
        db=_DB(_Result(rows=[(run_id, LaunchStatus.IN_PROGRESS, None)])),
        current_user=SimpleNamespace(id=uuid.uuid4()),
        _=SimpleNamespace(),
    )

    assert response.run_count == 0
    assert "executing" in response.blocked[0]["reasons"][0]


@pytest.mark.asyncio
async def test_a_prefix_outside_the_project_is_reported_at_preview(mocker):
    run_id, project_id = uuid.uuid4(), uuid.uuid4()
    from app.models.postgres import LaunchStatus

    mocker.patch(
        "app.services.deletion_criteria.foreign_run_ids",
        mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.deletion_criteria.resolve_criteria_candidates",
        mocker.AsyncMock(return_value=[run_id]),
    )
    mocker.patch("app.db.mongo.get_mongo_db", mocker.AsyncMock(return_value={}))
    mocker.patch(
        "app.services.run_deletion_service.citation_blockers",
        mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.deletion_job_service.stage_frozen_candidate_set",
        mocker.Mock(return_value=uuid.uuid4()),
    )

    response = await preview_criteria_deletion(
        project_id=project_id,
        criteria=RetentionCriteria(statuses=["PASSED"]),
        db=_DB(_Result(rows=[(run_id, LaunchStatus.PASSED, "uploads/shared/")])),
        current_user=SimpleNamespace(id=uuid.uuid4()),
        _=SimpleNamespace(),
    )

    assert response.refused_prefixes == ["uploads/shared/"]
    assert response.run_count == 1, (
        "a refused prefix must not silently drop the run — the Postgres and "
        "Mongo rows still go, and the operator is told what did not"
    )


@pytest.mark.asyncio
async def test_a_preview_that_fails_later_leaves_no_executable_job(mocker):
    """Staging, not a private session — and that is a behavioural difference.

    ``open_job``/``close_job`` take their own sessions because they record
    whether a deletion FAILED, and a status written on the failing transaction
    dies with it. The freeze records no outcome: giving it a private session
    would mean a preview that errored after the write still left a job an
    operator could execute.
    """
    import inspect as _inspect

    source = _inspect.getsource(jobs.stage_frozen_candidate_set)
    assert "AsyncSessionLocal" not in source, (
        "the freeze must stage on the caller's session; its own session would "
        "survive a failure that abandoned the preview"
    )
    assert "commit" not in source.replace(
        jobs.stage_frozen_candidate_set.__doc__ or "", ""
    ), "the router owns the commit"

    router_source = _inspect.getsource(preview_criteria_deletion)
    staged = router_source.find("stage_frozen_candidate_set(")
    committed = router_source.find("await db.commit()")
    assert staged != -1 and committed != -1
    assert staged < committed, "the job is staged, then committed by the route"


# ── the frozen set's own guards ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_foreign_job_is_404_and_indistinguishable_from_a_missing_one(mocker):
    """``job_id`` is not a guarded path param, so this check is the defence."""
    mine = uuid.uuid4()
    outcomes = []
    for job in (None, SimpleNamespace(project_id=uuid.uuid4())):
        mocker.patch(
            "app.services.deletion_job_service.get_job",
            mocker.AsyncMock(return_value=job),
        )
        with pytest.raises(jobs.FrozenSetRejected) as exc:
            await jobs.claim_frozen_set(
                _DB(), job_id=uuid.uuid4(), project_id=mine
            )
        outcomes.append((exc.value.status_code, exc.value.detail))

    assert outcomes[0] == outcomes[1] == (404, "Deletion job not found")


@pytest.mark.asyncio
async def test_a_job_that_is_not_previewed_is_refused(mocker):
    """What stops a double-submitted form deleting twice."""
    project_id = uuid.uuid4()
    for status in (jobs.RUNNING, jobs.COMPLETED, jobs.FAILED, jobs.PARTIAL):
        mocker.patch(
            "app.services.deletion_job_service.get_job",
            mocker.AsyncMock(
                return_value=SimpleNamespace(
                    project_id=project_id,
                    status=status,
                    resolved_run_ids=[str(uuid.uuid4())],
                    candidate_hash="x",
                )
            ),
        )
        with pytest.raises(jobs.FrozenSetRejected) as exc:
            await jobs.claim_frozen_set(
                _DB(), job_id=uuid.uuid4(), project_id=project_id
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_hash_drift_is_refused(mocker):
    """The row was edited underneath the preview.

    The set is no longer the one authorised, so re-preview rather than execute
    something nobody reviewed.
    """
    project_id = uuid.uuid4()
    mocker.patch(
        "app.services.deletion_job_service.get_job",
        mocker.AsyncMock(
            return_value=SimpleNamespace(
                project_id=project_id,
                status=jobs.PREVIEWED,
                resolved_run_ids=[str(uuid.uuid4())],
                candidate_hash="not-the-real-hash",
            )
        ),
    )

    with pytest.raises(jobs.FrozenSetRejected) as exc:
        await jobs.claim_frozen_set(
            _DB(), job_id=uuid.uuid4(), project_id=project_id
        )

    assert exc.value.status_code == 409
    assert "hash" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_a_matching_hash_is_accepted(mocker):
    """The other half — a check that refused everything would ship a preview
    that can never be executed."""
    project_id = uuid.uuid4()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    mocker.patch(
        "app.services.deletion_job_service.get_job",
        mocker.AsyncMock(
            return_value=SimpleNamespace(
                project_id=project_id,
                status=jobs.PREVIEWED,
                resolved_run_ids=ids,
                candidate_hash=jobs.candidate_hash(ids),
            )
        ),
    )

    out = await jobs.claim_frozen_set(
        _DB(), job_id=uuid.uuid4(), project_id=project_id
    )

    assert [str(r) for r in out] == ids


@pytest.mark.asyncio
async def test_an_empty_frozen_set_is_refused(mocker):
    """Executing nothing would report a successful deletion of zero runs."""
    project_id = uuid.uuid4()
    mocker.patch(
        "app.services.deletion_job_service.get_job",
        mocker.AsyncMock(
            return_value=SimpleNamespace(
                project_id=project_id,
                status=jobs.PREVIEWED,
                resolved_run_ids=[],
                candidate_hash=jobs.candidate_hash([]),
            )
        ),
    )

    with pytest.raises(jobs.FrozenSetRejected) as exc:
        await jobs.claim_frozen_set(
            _DB(), job_id=uuid.uuid4(), project_id=project_id
        )

    assert exc.value.status_code == 409


# ── the outcome vocabulary ───────────────────────────────────────────────────


def test_partial_is_produced_when_some_runs_survive():
    """The state S2b declared and nothing wrote.

    A criteria job is one-shot — nothing retries it — so "some of them went"
    is a final answer, and calling it either success or failure is a lie.
    """
    assert jobs.outcome_status(requested=5, deleted=5) == jobs.COMPLETED
    assert jobs.outcome_status(requested=5, deleted=3) == jobs.PARTIAL
    assert jobs.outcome_status(requested=5, deleted=0) == jobs.FAILED
    assert jobs.outcome_status(requested=0, deleted=0) == jobs.COMPLETED


def test_the_task_does_not_retry():
    """A retry would replay deletions already done and count them as failures,
    turning a partial success into a reported disaster."""
    from app.worker import tasks

    source = inspect.getsource(tasks)
    idx = source.find("def execute_criteria_deletion_task")
    decorator = source[max(0, idx - 400):idx]
    assert "max_retries=0" in decorator


def test_the_task_reports_the_outcome_rather_than_assuming_success():
    from app.worker import tasks

    source = inspect.getsource(tasks.execute_criteria_deletion_task)
    assert "outcome_status" in source, (
        "the task hardcodes a final status instead of deriving it from how "
        "many runs actually went"
    )
    assert "perform_run_deletion" in source, (
        "the criteria path must share the single-run deletion transaction, "
        "not re-implement the cross-store ordering"
    )
