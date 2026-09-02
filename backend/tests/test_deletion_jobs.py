"""S2b — operational records of deletions actually running.

The purge already writes a never-purged record to ``settings_audit_log``, but
that row is written *after* the purge commits. It cannot say a destructive job
is running now, and it cannot exist at all for one that failed — which is
exactly the half an operator needs.

These tests pin the two properties that make the record trustworthy: it is
written on its own session, and it never invents a number it did not measure.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from app.services import deletion_job_service as svc


# ── the fingerprint ──────────────────────────────────────────────────────────


def test_candidate_hash_is_order_independent():
    """Two resolutions of the same runs must agree.

    Criteria deletion compares this between preview and execute; if query plan
    order changed the hash, every execute would 409 on a set that had not moved.
    """
    a, b, c = (uuid.uuid4() for _ in range(3))
    assert svc.candidate_hash([a, b, c]) == svc.candidate_hash([c, a, b])


def test_candidate_hash_changes_when_the_set_does():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert svc.candidate_hash([a]) != svc.candidate_hash([a, b])


def test_candidate_hash_accepts_strings_and_uuids_alike():
    """The resolver yields UUIDs; the stored column yields strings. If those
    hashed differently, a frozen set would never match itself on replay."""
    a = uuid.uuid4()
    assert svc.candidate_hash([a]) == svc.candidate_hash([str(a)])


# ── own-session writes ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_writes_open_their_own_session(mocker):
    """The load-bearing design decision.

    A ``failed`` written on the caller's session is erased by the rollback that
    produced the failure, so a same-session writer can only ever record
    success — precisely the wrong half. Both functions must open their own.

    Asserted by watching ``AsyncSessionLocal`` actually get CALLED. Grepping
    the source for the name was the first version of this test, and it passed
    against a mutant that used the caller's session — because the name still
    appeared on the function's own import line.
    """
    for fn, kwargs in (
        (svc.open_job, {"project_id": uuid.uuid4(), "job_kind": svc.KIND_SCHEDULED}),
        (svc.close_job, {"status": svc.COMPLETED}),
    ):
        db = mocker.AsyncMock()
        session_cm = mocker.MagicMock()
        session_cm.__aenter__ = mocker.AsyncMock(return_value=db)
        session_cm.__aexit__ = mocker.AsyncMock(return_value=False)
        db.add = mocker.Mock()  # sync in SQLAlchemy
        factory = mocker.patch(
            "app.db.postgres.AsyncSessionLocal", return_value=session_cm
        )

        if fn is svc.close_job:
            await fn(uuid.uuid4(), **kwargs)
        else:
            await fn(**kwargs)

        factory.assert_called_once_with()
        db.commit.assert_awaited_once()


def test_neither_status_writer_accepts_a_caller_session():
    """No ``db`` parameter, so no caller can hand one in by mistake."""
    for fn in (svc.open_job, svc.close_job):
        params = inspect.signature(fn).parameters
        assert "db" not in params, (
            f"{fn.__name__} takes a session; a status written on the caller's "
            "transaction dies with that transaction's rollback"
        )


@pytest.mark.asyncio
async def test_open_job_returns_none_rather_than_raising(mocker):
    """Bookkeeping must never break the deletion it is recording.

    If this raised, a failure to write a status row would abort a purge that
    was otherwise fine — the record would have become more important than the
    work.
    """
    mocker.patch(
        "app.db.postgres.AsyncSessionLocal",
        side_effect=ConnectionError("db down"),
    )

    result = await svc.open_job(
        project_id=uuid.uuid4(), job_kind=svc.KIND_SCHEDULED
    )

    assert result is None


@pytest.mark.asyncio
async def test_close_job_on_a_missing_id_is_a_no_op(mocker):
    """``open_job`` can return None, so callers must not have to branch."""
    called = mocker.patch("app.db.postgres.AsyncSessionLocal")

    await svc.close_job(None, status=svc.COMPLETED)

    called.assert_not_called()


@pytest.mark.asyncio
async def test_an_unreachable_status_is_refused(mocker):
    """A typo'd status is a row no filter will ever match.

    Same silent-forever failure as a status literal that does not match its
    column's vocabulary.
    """
    called = mocker.patch("app.db.postgres.AsyncSessionLocal")

    await svc.close_job(uuid.uuid4(), status="complete")  # missing the 'd'

    called.assert_not_called()


# ── the vocabulary ───────────────────────────────────────────────────────────


def test_no_status_is_declared_that_nothing_can_produce():
    """``cancelled`` is deliberately absent.

    Nothing in this slice can cancel a job, and a state nothing writes is a
    filter option that returns nothing forever — the defect this epic
    catalogues elsewhere, not a placeholder for future work.
    """
    assert "cancelled" not in svc.REACHABLE_STATUSES
    assert "previewed" not in svc.REACHABLE_STATUSES, (
        "previewed belongs to criteria deletion's freeze; declaring it before "
        "anything writes it is the same defect"
    )


def test_the_model_documents_only_reachable_states():
    """The column comment and the service constant must not drift."""
    from app.models.postgres import DeletionJob

    doc = DeletionJob.__doc__ or ""
    for status in sorted(svc.REACHABLE_STATUSES):
        assert status in doc, f"{status} is reachable but undocumented"


# ── never invent a measurement ───────────────────────────────────────────────


def test_bytes_reclaimed_is_left_null_when_not_supplied():
    """NULL means "not measured". Defaulting it to 0 would report every purge
    as having reclaimed nothing, which is a different claim entirely."""
    from app.models.postgres import DeletionJob

    column = DeletionJob.__table__.c.bytes_reclaimed
    assert column.nullable is True
    assert column.default is None
    assert column.server_default is None


@pytest.mark.asyncio
async def test_close_job_only_sets_the_fields_it_was_given(mocker):
    """Passing no counts must not blank an existing value.

    Asserted on the UPDATE actually issued, not on the source text: a source
    grep would still pass if the conditionals were there but wrote ``None``.
    """
    db = mocker.AsyncMock()
    session_cm = mocker.MagicMock()
    session_cm.__aenter__ = mocker.AsyncMock(return_value=db)
    session_cm.__aexit__ = mocker.AsyncMock(return_value=False)
    db.add = mocker.Mock()  # sync in SQLAlchemy
    mocker.patch("app.db.postgres.AsyncSessionLocal", return_value=session_cm)

    await svc.close_job(uuid.uuid4(), status=svc.COMPLETED)

    statement = db.execute.await_args.args[0]
    written = set(statement.compile().params)
    for field in ("counts", "resolved_run_ids", "bytes_reclaimed", "error"):
        assert field not in written, (
            f"{field} was written despite not being supplied — a partial close "
            "would blank a value an earlier write had measured"
        )
    assert "status" in written and "finished_at" in written


@pytest.mark.asyncio
async def test_close_job_writes_the_fields_it_was_given(mocker):
    """The other half: the conditionals must not skip a supplied value.

    Without this, deleting every ``values[...]`` assignment still passes the
    test above.
    """
    db = mocker.AsyncMock()
    session_cm = mocker.MagicMock()
    session_cm.__aenter__ = mocker.AsyncMock(return_value=db)
    session_cm.__aexit__ = mocker.AsyncMock(return_value=False)
    db.add = mocker.Mock()  # sync in SQLAlchemy
    mocker.patch("app.db.postgres.AsyncSessionLocal", return_value=session_cm)

    run_id = str(uuid.uuid4())
    await svc.close_job(
        uuid.uuid4(),
        status=svc.PARTIAL,
        counts={"runs": 3},
        resolved_run_ids=[run_id],
        bytes_reclaimed=0,
        error="boom",
    )

    params = db.execute.await_args.args[0].compile().params
    assert params["counts"] == {"runs": 3}
    assert params["resolved_run_ids"] == [run_id]
    assert params["error"] == "boom"
    assert params["bytes_reclaimed"] == 0, (
        "0 is a measurement of nothing reclaimed and must survive the "
        "is-not-None guard; only an absent value stays NULL"
    )
    assert params["candidate_hash"] == svc.candidate_hash([run_id])


def test_the_model_documents_only_reachable_kinds():
    """Same rule as statuses: no kind is advertised that nothing produces."""
    import re

    from app.models.postgres import DeletionJob

    source = inspect.getsource(DeletionJob)
    comment = re.search(r"#: (.*?)job_kind", source, re.S)
    assert comment, "job_kind lost its vocabulary comment"
    declared = {
        v for k, v in vars(svc).items() if k.startswith("KIND_")
    }
    for stray in ("report", "reclamation", "purge"):
        assert stray not in declared
        assert stray not in comment.group(1), (
            f"{stray!r} is documented as a job kind but nothing writes it"
        )
    for kind in declared:
        assert kind in comment.group(1), f"{kind} is producible but undocumented"


# ── the scheduled purge actually writes one ──────────────────────────────────


def test_the_scheduled_purge_opens_and_closes_a_job():
    """A table with no writer is a table that is always empty.

    Wiring this into the existing nightly purge is what keeps the surface
    honest from day one rather than waiting for a slice that may not ship.
    """
    from app.worker import tasks

    source = inspect.getsource(tasks._retention_purge_sweep)
    assert "deletion_job_service.open_job" in source
    assert "deletion_job_service.close_job" in source


def test_the_job_is_opened_before_the_purge_runs():
    """Order matters: a job opened afterwards cannot record a crash mid-flight.

    That is the one thing the existing audit row already fails to do, and the
    reason this table exists.
    """
    from app.worker import tasks

    source = inspect.getsource(tasks._retention_purge_sweep)
    # Anchor on the CALL, not the name: this function's docstring mentions
    # ``retention_service.run_purge`` near the top, and matching that made the
    # assertion compare against prose rather than code.
    opened = source.find("deletion_job_service.open_job(")
    purged = source.find("await retention_service.run_purge(")

    assert opened != -1, "open_job call not found"
    assert purged != -1, "run_purge call not found — the test proved nothing"
    assert opened < purged, (
        "open_job must precede run_purge; opened afterwards it can only ever "
        "describe work that already survived"
    )


def test_a_failed_purge_is_recorded_as_failed():
    """The status must be derived from whether the purge errored."""
    from app.worker import tasks

    source = inspect.getsource(tasks._retention_purge_sweep)
    assert "deletion_job_service.FAILED" in source
    assert "deletion_job_service.COMPLETED" in source


# ── the route's own scoping guard ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetching_a_job_from_another_project_404s(mocker):
    """``job_id`` is NOT a guarded path param.

    ``require_project_access()`` only validates ``project_id``, so an ADMIN of
    project A who is also an ADMIN of nothing else still passes the guard and
    can then name any job id in the world. The handler's own ownership check is
    the entire defence — this is the IDOR shape that hid behind a passing
    ratchet nine times before.
    """
    from fastapi import HTTPException

    from app.routers.retention import get_deletion_job

    mine, theirs = uuid.uuid4(), uuid.uuid4()
    foreign_job = mocker.Mock(project_id=theirs, id=uuid.uuid4())
    mocker.patch.object(svc, "get_job", return_value=foreign_job)

    with pytest.raises(HTTPException) as exc:
        await get_deletion_job(
            project_id=mine,
            job_id=foreign_job.id,
            db=mocker.AsyncMock(),
            current_user=mocker.Mock(),
            _=mocker.Mock(),
        )

    assert exc.value.status_code == 404
    assert str(theirs) not in str(exc.value.detail), (
        "the 404 must not leak which project owns the job"
    )


@pytest.mark.asyncio
async def test_a_missing_job_and_a_foreign_job_are_indistinguishable(mocker):
    """Same status and same detail, or the 404 becomes an existence oracle."""
    from fastapi import HTTPException

    from app.routers.retention import get_deletion_job

    mine = uuid.uuid4()
    outcomes = []
    for job in (None, mocker.Mock(project_id=uuid.uuid4())):
        mocker.patch.object(svc, "get_job", return_value=job)
        with pytest.raises(HTTPException) as exc:
            await get_deletion_job(
                project_id=mine,
                job_id=uuid.uuid4(),
                db=mocker.AsyncMock(),
                current_user=mocker.Mock(),
                _=mocker.Mock(),
            )
        outcomes.append((exc.value.status_code, exc.value.detail))

    assert outcomes[0] == outcomes[1]


# ── the table is itself on the audit clock ───────────────────────────────────


def test_the_purge_deletes_terminal_deletion_jobs():
    """Without this, the operational log becomes a second never-purged table.

    An epic about unbounded storage growth must not ship a table that grows
    forever. The migration's docstring claimed this was already true before
    anything implemented it.
    """
    from app.services import retention_service

    source = inspect.getsource(retention_service)
    assert "delete(DeletionJob).where(*plan.deletion_job_where)" in source, (
        "deletion_jobs is written on every purge and never deleted"
    )


def test_a_hung_job_is_not_purged_on_the_clock():
    """A row still `running` past the audit window is the evidence of a crash.

    Deleting it on a clock erases exactly the record this table exists to keep,
    so the filter is restricted to terminal states.
    """
    assert svc.RUNNING not in svc.TERMINAL_STATUSES
    assert svc.QUEUED not in svc.TERMINAL_STATUSES
    assert svc.TERMINAL_STATUSES < svc.REACHABLE_STATUSES
    assert svc.TERMINAL_STATUSES == {svc.COMPLETED, svc.FAILED, svc.PARTIAL}


def test_the_purge_filter_scopes_by_project_and_terminal_status():
    """Compiled from the PRODUCTION predicate, not a copy of it.

    A test that rebuilt an equivalent WHERE clause would keep passing after
    the real one lost its project scope and began deleting another tenant's
    records.
    """
    import datetime as _dt
    import uuid as _uuid

    from sqlalchemy import delete as _delete
    from sqlalchemy.dialects import postgresql

    from app.models.postgres import DeletionJob
    from app.services import retention_service

    assert "deletion_job_where" in retention_service._ExecutionPlan.__dataclass_fields__

    pid = _uuid.uuid4()
    cutoff = _dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc)
    where = retention_service.deletion_job_purge_filter(pid, cutoff)
    sql = str(
        _delete(DeletionJob)
        .where(*where)
        .compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert str(pid) in sql, "the delete is not scoped to one project"
    assert "2020-01-01" in sql, "the delete is not scoped to the audit cutoff"
    for terminal in svc.TERMINAL_STATUSES:
        assert "'" + terminal + "'" in sql
    for live in (svc.RUNNING, svc.QUEUED):
        assert "'" + live + "'" not in sql, (
            live + " is in the delete's scope; a hung sweep would be erased"
        )


def test_the_module_docstring_lists_what_the_audit_clock_covers():
    """The header table is the first thing anyone reads about this service.

    It listed two tables while the code deleted four; a reader auditing
    retention coverage from it would have concluded deletion_jobs was exempt.
    """
    from app.services import retention_service

    doc = retention_service.__doc__ or ""
    lines = doc.splitlines()
    starts = [i for i, line in enumerate(lines) if "``audit_days``" in line]
    assert len(starts) == 1, "the audit_days row is missing or duplicated"
    # The row wraps: read to the next clock's row rather than one line, or the
    # assertion silently checks a fragment.
    end = next(
        (
            i for i in range(starts[0] + 1, len(lines))
            if lines[i].lstrip().startswith("``") and "_days``" in lines[i]
        ),
        len(lines),
    )
    joined = " ".join(lines[starts[0]:end])
    for table in ("access_audit_logs", "test_case_audit_logs",
                  "ai_provenance_records", "deletion_jobs"):
        assert table in joined, f"{table} is purged on the audit clock but undocumented"
