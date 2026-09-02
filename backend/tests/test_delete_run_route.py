"""S2c — ``DELETE /api/v1/runs/{run_id}``: what it refuses, and how.

The handler is called directly rather than through the app. The role and
project guards are FastAPI dependencies, so a direct call cannot exercise them
— that is asserted separately against the signature, and the authorization
ratchet enforces ``require_run_access`` repo-wide.

What a direct call CAN prove is the part that is this endpoint's own: the four
guards, their order, and the fact that a refusal never enqueues the task.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.models.postgres import LaunchStatus  # noqa: E402
from app.routers.runs import delete_run  # noqa: E402


def _run(status=LaunchStatus.PASSED, prefix=None, project_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        status=status,
        minio_prefix=prefix,
    )


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _DB:
    def __init__(self, run):
        self._run = run

    async def execute(self, *_a, **_kw):
        return _Result(self._run)


def _body(confirm=True, reason="cleaning up a bad import"):
    return SimpleNamespace(confirm=confirm, reason=reason)


@pytest.fixture
def wired(mocker):
    """Patch the endpoint's collaborators and hand back the task spy."""
    mocker.patch("app.db.mongo.get_mongo_db", mocker.AsyncMock(return_value={}))
    mocker.patch(
        "app.services.run_tombstone_service.run_is_tombstoned",
        mocker.AsyncMock(return_value=False),
    )
    mocker.patch(
        "app.services.run_deletion_service.citation_blockers",
        mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.deletion_job_service.open_job",
        mocker.AsyncMock(return_value=uuid.uuid4()),
    )
    task = mocker.patch("app.worker.tasks.delete_run_everywhere")
    return task


async def _call(run, body=None, user=None):
    return await delete_run(
        run_id=run.id,
        body=body or _body(),
        db=_DB(run),
        current_user=user or SimpleNamespace(id=uuid.uuid4()),
        _=SimpleNamespace(),
    )


# ── the happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_deletable_run_is_accepted_with_a_job_to_poll(wired):
    """202 and a job id — not 204.

    A synchronous delete of a multi-GB prefix times out at the gateway, and
    because the store order is Mongo -> MinIO -> Postgres the caller would get
    a 504 with the artifacts gone and the run still listed.
    """
    run = _run()

    response = await _call(run)

    assert response.status == "accepted"
    assert response.run_id == run.id
    assert response.job_id is not None
    wired.delay.assert_called_once()


@pytest.mark.asyncio
async def test_the_task_is_handed_the_reason_and_the_requesting_user(wired):
    """Both end up on the tombstone, so "why is this run gone" has an answer."""
    run = _run()
    user = SimpleNamespace(id=uuid.uuid4())

    await _call(run, _body(reason="duplicate CI upload"), user=user)

    args = wired.delay.call_args.args
    assert str(run.id) in args
    assert "duplicate CI upload" in args
    assert str(user.id) in args


@pytest.mark.asyncio
async def test_a_job_that_could_not_be_opened_still_deletes(wired, mocker):
    """Bookkeeping must not veto the work.

    ``open_job`` returns None when its own write failed. The deletion still
    runs; the caller simply has no job to poll, and the response says so with
    a null rather than a fabricated id.
    """
    mocker.patch(
        "app.services.deletion_job_service.open_job",
        mocker.AsyncMock(return_value=None),
    )
    run = _run()

    response = await _call(run)

    assert response.job_id is None
    wired.delay.assert_called_once()


@pytest.mark.asyncio
async def test_a_refused_prefix_is_reported_on_the_202(wired):
    """The delete succeeds but leaves objects behind, and says which.

    Silence here reads as "everything went"; the operator would watch storage
    not fall and have nothing to go on.
    """
    run = _run(prefix="uploads/shared/")

    response = await _call(run)

    assert response.refused_prefixes == ["uploads/shared/"]


@pytest.mark.asyncio
async def test_an_in_scope_prefix_is_not_reported_as_refused(wired):
    """The other half — a guard that refused everything would report every
    delete as partial and train operators to ignore the field."""
    run = _run()
    run.minio_prefix = f"{run.project_id}/runs/build-9/"

    response = await _call(run)

    assert response.refused_prefixes == []


# ── the refusals ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_missing_run_is_404(wired):
    with pytest.raises(HTTPException) as exc:
        await delete_run(
            run_id=uuid.uuid4(),
            body=_body(),
            db=_DB(None),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            _=SimpleNamespace(),
        )

    assert exc.value.status_code == 404
    wired.delay.assert_not_called()


@pytest.mark.asyncio
async def test_an_unconfirmed_delete_is_refused(wired):
    """``confirm`` is a second step, not ceremony: five stores, no undo."""
    with pytest.raises(HTTPException) as exc:
        await _call(_run(), _body(confirm=False))

    assert exc.value.status_code == 422
    wired.delay.assert_not_called()


@pytest.mark.asyncio
async def test_an_in_progress_run_is_409_and_is_not_enqueued(wired):
    """Deleting a running run races the drainer and the persist task."""
    with pytest.raises(HTTPException) as exc:
        await _call(_run(status=LaunchStatus.IN_PROGRESS))

    assert exc.value.status_code == 409
    assert "executing" in str(exc.value.detail).lower()
    wired.delay.assert_not_called()


@pytest.mark.asyncio
async def test_a_cited_run_is_409_and_names_every_citation(wired, mocker):
    """The operator should not clear one blocker and discover a second."""
    mocker.patch(
        "app.services.run_deletion_service.citation_blockers",
        mocker.AsyncMock(return_value=["cited by 1 compliance pack(s)",
                                       "linked to 2 release(s)"]),
    )

    with pytest.raises(HTTPException) as exc:
        await _call(_run())

    assert exc.value.status_code == 409
    assert len(exc.value.detail["blockers"]) == 2
    wired.delay.assert_not_called()


@pytest.mark.asyncio
async def test_no_refusal_path_enqueues_the_task(wired, mocker):
    """The property that matters across all of them at once.

    A guard that raises AFTER ``delay()`` refuses the caller while the deletion
    proceeds anyway — the worst outcome available, and the one a per-guard test
    would not catch.
    """
    cases = [
        (_run(status=LaunchStatus.IN_PROGRESS), _body(), None),
        (_run(), _body(confirm=False), None),
        (_run(), _body(), ["cited by something"]),
    ]
    for run, body, blockers in cases:
        mocker.patch(
            "app.services.run_deletion_service.citation_blockers",
            mocker.AsyncMock(return_value=blockers or []),
        )
        with pytest.raises(HTTPException):
            await _call(run, body)

    wired.delay.assert_not_called()


@pytest.mark.asyncio
async def test_a_resurrected_run_can_still_be_deleted(wired, mocker):
    """A tombstoned row that EXISTS means a resurrection got past the guards.

    The drainer's tombstone lookup fails open on a database error, so this is
    reachable. Refusing here would strand a dataless run that nothing could
    remove — precisely when the operator most needs the endpoint to work.
    """
    mocker.patch(
        "app.services.run_tombstone_service.run_is_tombstoned",
        mocker.AsyncMock(return_value=True),
    )

    response = await _call(_run())

    assert response.status == "accepted"
    wired.delay.assert_called_once()


# ── the guards FastAPI owns ──────────────────────────────────────────────────


def test_the_endpoint_declares_both_the_role_and_the_project_guard():
    """A direct call cannot exercise dependencies, so assert they are wired.

    ``require_run_access`` is what makes this cross-tenant safe: without it an
    ADMIN of any project could name any run id in the deployment.
    """
    source = inspect.getsource(delete_run)
    assert "require_role(UserRole.ADMIN)" in source, "not ADMIN-gated"
    assert "require_run_access()" in source, (
        "no project scoping — any ADMIN could delete any run in the deployment"
    )


def test_the_endpoint_returns_202_not_204():
    """The decorator, read directly. An `or True` clause slipped into the
    first version of this and made half of it unfalsifiable."""
    from app.routers import runs as runs_module

    module_source = inspect.getsource(runs_module)
    decorator_start = module_source.find('@router.delete("/{run_id}"')
    assert decorator_start != -1, "the DELETE route is gone"
    decorator = module_source[decorator_start:decorator_start + 200]
    assert "status_code=202" in decorator, (
        "a synchronous 204 would 504 at the gateway on a multi-GB prefix, "
        "with the artifacts already deleted and the run still listed"
    )
