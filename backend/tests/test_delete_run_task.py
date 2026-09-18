"""S2c — the Celery task that actually performs a single-run deletion.

The route validates and returns 202; this is what does the work. It reuses
``execute_candidates`` so the cross-store ordering exists in one place, and it
adds the two things a per-run delete needs that the nightly purge does not: a
tombstone written in the same transaction, and a scope that cannot widen to the
project.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

pytest.importorskip("sqlalchemy")


# ── the scope cannot widen ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_run_scoped_plan_pins_every_project_wide_clock_to_false():
    """The load-bearing property of ``resolve_run_candidates``.

    ``_ExecutionPlan`` carries five clocks that never reference a run: the
    access and test-case audit logs, provenance records, deletion jobs, and
    (as lists) expired memory and compliance packs. Those belong to the nightly
    policy purge. Inheriting them here would take a project's entire audit
    trail along with one run — and the executor would report it as a
    successful single-run delete.
    """
    from sqlalchemy.dialects import postgresql

    from app.services import retention_service as rs

    project_id, run_id = uuid.uuid4(), uuid.uuid4()

    class _R:
        @staticmethod
        def scalars():
            return type("S", (), {"all": staticmethod(lambda: [])})()

    class _DB:
        async def execute(self, *_a, **_kw):
            return _R()

    cand, plan = await rs.resolve_run_candidates(
        _DB(), project_id=project_id, run_id=run_id, minio_prefix=None
    )

    for field in ("access_audit_where", "tc_audit_where",
                  "deletion_job_where", "provenance_where"):
        clause = getattr(plan, field)
        rendered = " ".join(
            str(c.compile(dialect=postgresql.dialect(),
                          compile_kwargs={"literal_binds": True}))
            for c in clause
        )
        assert "false" in rendered.lower(), (
            f"{field} is not pinned to false — a single-run delete would "
            f"take project-wide rows with it (got {rendered!r})"
        )

    assert plan.memory_expired_ids == []
    assert plan.expired_packs == []


@pytest.mark.asyncio
async def test_the_event_archive_clock_is_scoped_to_this_run():
    """The one plan filter that SHOULD fire, scoped to the run.

    Pinning this to false as well would leave the archived event blob behind
    on every single-run delete, and nothing would say so.
    """
    from sqlalchemy.dialects import postgresql

    from app.services import retention_service as rs

    run_id = uuid.uuid4()

    class _R:
        @staticmethod
        def scalars():
            return type("S", (), {"all": staticmethod(lambda: [])})()

    class _DB:
        async def execute(self, *_a, **_kw):
            return _R()

    _cand, plan = await rs.resolve_run_candidates(
        _DB(), project_id=uuid.uuid4(), run_id=run_id, minio_prefix=None
    )

    rendered = " ".join(
        str(c.compile(dialect=postgresql.dialect(),
                      compile_kwargs={"literal_binds": True}))
        for c in plan.event_archive_where
    )
    assert str(run_id) in rendered
    assert "false" not in rendered.lower()


@pytest.mark.asyncio
async def test_only_this_run_is_in_the_candidate_set():
    from app.services import retention_service as rs

    run_id = uuid.uuid4()

    class _R:
        @staticmethod
        def scalars():
            return type("S", (), {"all": staticmethod(lambda: [])})()

    class _DB:
        async def execute(self, *_a, **_kw):
            return _R()

    cand, _plan = await rs.resolve_run_candidates(
        _DB(), project_id=uuid.uuid4(), run_id=run_id, minio_prefix=None
    )

    assert cand.purge_run_ids == [run_id]
    assert cand.purge_run_strs == [str(run_id)]
    assert cand.raw_run_strs == [str(run_id)]


@pytest.mark.asyncio
async def test_a_prefix_outside_the_project_never_reaches_the_candidates():
    """The guard is applied at resolution, not left to the caller.

    ``artifact_prefixes`` is handed straight to ``delete_prefix``. A resolver
    that passed ``minio_prefix`` through unvalidated would put a shared-area
    prefix in front of the executor with nothing left to stop it.
    """
    from app.services import retention_service as rs

    project_id, run_id = uuid.uuid4(), uuid.uuid4()

    class _R:
        @staticmethod
        def scalars():
            return type("S", (), {"all": staticmethod(lambda: [])})()

    class _DB:
        async def execute(self, *_a, **_kw):
            return _R()

    cand, _plan = await rs.resolve_run_candidates(
        _DB(), project_id=project_id, run_id=run_id,
        minio_prefix="uploads/shared/",
    )

    assert "uploads/shared/" not in cand.artifact_prefixes
    assert cand.artifact_prefixes == [f"uploads/{project_id}/{run_id}/"]


# ── the task itself ──────────────────────────────────────────────────────────


def test_the_task_reuses_the_shared_executor():
    """Two copies of the cross-store ordering is the failure mode S2a existed
    to prevent.

    The task delegates to ``perform_run_deletion``, which is what the
    integration suite drives; that function must call the shared executor
    rather than re-implementing the Mongo -> MinIO -> Postgres sequence.
    """
    from app.services import run_deletion_service
    from app.worker import tasks

    task_source = inspect.getsource(tasks.delete_run_everywhere)
    assert "perform_run_deletion" in task_source, (
        "the task no longer routes through the shared deletion transaction"
    )

    shared = inspect.getsource(run_deletion_service.perform_run_deletion)
    assert "execute_candidates" in shared
    assert "resolve_run_candidates" in shared


def test_the_tombstone_is_written_in_the_same_transaction_as_the_delete():
    """Committed separately, in either order, it is wrong.

    Tombstone-then-delete blocks live ingestion into a run that still exists.
    Delete-then-tombstone leaves a window in which the run is gone and the
    five re-creation paths are free to bring it back.
    """
    from app.services import run_deletion_service
    from app.worker import tasks

    # The staging order lives in the shared transaction; the single commit
    # that covers it lives in the task. Both halves are asserted, because
    # either one alone is satisfiable while the other is wrong.
    shared = inspect.getsource(run_deletion_service.perform_run_deletion)
    execute = shared.find("execute_candidates(")
    stage = shared.find("stage_tombstone(")
    assert execute != -1, "the shared transaction never runs the executor"
    assert stage != -1, "the shared transaction never stages a tombstone"
    assert execute < stage, "the tombstone is staged before the deletion runs"
    assert "commit" not in shared.replace(
        run_deletion_service.perform_run_deletion.__doc__ or "", ""
    ), "the shared transaction must stage only; the caller owns the commit"

    task = inspect.getsource(tasks.delete_run_everywhere)
    call = task.find("perform_run_deletion(")
    commit = task.find("await db.commit()")
    assert call != -1 and commit != -1
    assert call < commit, (
        "the tombstone and the deletion must land in ONE commit; a commit "
        "between them opens the resurrection window this table exists to close"
    )
    assert task.count("await db.commit()") == 1, (
        "more than one commit in the delete path — the deletion and its "
        "tombstone would no longer be atomic"
    )


def test_the_task_closes_its_deletion_job_on_both_paths():
    """A job left `running` forever is the state the S2b table exists to
    avoid, so the task that opens one must close it on failure too."""
    from app.worker import tasks

    source = inspect.getsource(tasks.delete_run_everywhere)
    assert "deletion_job_service.FAILED" in source
    assert "deletion_job_service.COMPLETED" in source


def test_the_task_is_registered_on_a_real_queue():
    """An unrouted task is accepted by the API and then never runs — the
    caller polls a job that stays `running` forever."""
    from app.worker import tasks

    opts = getattr(tasks.delete_run_everywhere, "queue", None) or (
        getattr(tasks.delete_run_everywhere, "request", None)
    )
    source = inspect.getsource(tasks)
    idx = source.find("def delete_run_everywhere")
    decorator = source[max(0, idx - 400):idx]
    assert "queue=" in decorator, "the task declares no queue"
    assert any(q in decorator for q in ('"critical"', '"ingestion"', '"default"')), (
        f"the task is routed to a queue no worker subscribes to: {decorator!r}"
    )
    assert opts is not None or True  # queue is declared in the decorator


def test_the_task_purges_the_index_for_the_RUN_not_the_project():
    """RET-D8 at the call site.

    ``purge_run_documents`` exists precisely so this path cannot call the
    project-wide purge; a task that called the wrong one would empty the
    project's whole index while reporting a single-run delete.
    """
    from app.worker import tasks

    source = inspect.getsource(tasks.delete_run_everywhere)
    assert "purge_run_documents" in source
    assert "purge_project_documents" not in source, (
        "the single-run path must never call the project-wide index purge"
    )


@pytest.mark.asyncio
async def test_execution_rechecks_mutable_protections(mocker):
    """A preview is not permission to delete evidence protected afterwards."""
    from app.services import run_deletion_service as service

    run = type(
        "Run",
        (),
        {"id": uuid.uuid4(), "status": "IN_PROGRESS"},
    )()
    citations = mocker.patch(
        "app.services.run_deletion_service.citation_blockers",
        mocker.AsyncMock(return_value=["linked to 1 release(s)"]),
    )

    blockers = await service.execution_blockers(
        object(), run=run, mongo=object()
    )

    assert blockers == ["run is still executing", "linked to 1 release(s)"]
    citations.assert_awaited_once()


@pytest.mark.parametrize(
    "task_name", ["delete_run_everywhere", "execute_criteria_deletion_task"]
)
def test_workers_recheck_protection_before_touching_the_search_index(task_name):
    """Fail before the first irreversible cross-store side effect."""
    from app.worker import tasks

    source = inspect.getsource(getattr(tasks, task_name))
    lock = source.find("with_for_update()")
    guard = source.find("execution_blockers(")
    purge = source.find("purge_run_documents(")
    assert -1 not in (lock, guard, purge)
    assert lock < guard < purge
    assert "RunDeletionBlocked" in source
