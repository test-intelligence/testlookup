"""Unit tests for ``services.test_suite_service`` (Phase 2 of the TestSuite
+ CanonicalTestCase feature).

The DB-bound paths are exercised with ``AsyncMock`` sessions plus
``FakeExecuteResult`` (see ``tests/conftest.py``) — same approach as
``tests/services/test_run_compare_service.py`` and the rest of the
non-integration service suite.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import test_suite_service as svc
from tests.conftest import FakeExecuteResult


# ── default_suite_name_for ────────────────────────────────────────────────


def test_default_suite_name_format():
    assert svc.default_suite_name_for("Acme") == "Default Suite (Acme)"
    assert svc.default_suite_name_for("acme corp") == "Default Suite (acme corp)"


# ── get_or_create_default_suite ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_default_suite_returns_existing():
    project = SimpleNamespace(id=uuid.uuid4(), name="Acme")
    existing_suite = SimpleNamespace(
        id=uuid.uuid4(), project_id=project.id, name="Default Suite (Acme)", is_default=True
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=existing_suite))

    result = await svc.get_or_create_default_suite(db, project)

    assert result is existing_suite
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_default_suite_creates_when_missing():
    project = SimpleNamespace(id=uuid.uuid4(), name="Acme")

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=None))
    db.flush = AsyncMock()

    result = await svc.get_or_create_default_suite(db, project)

    assert result.is_default is True
    assert result.name == "Default Suite (Acme)"
    assert result.project_id == project.id
    db.add.assert_called_once()
    db.flush.assert_awaited_once()


# ── get_or_create_suite_by_name ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_suite_by_name_returns_existing():
    project_id = uuid.uuid4()
    existing = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Regression")

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=existing))

    result = await svc.get_or_create_suite_by_name(db, project_id, "Regression")
    assert result is existing
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_suite_by_name_creates_new():
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=None))
    db.flush = AsyncMock()

    result = await svc.get_or_create_suite_by_name(db, project_id, "Smoke")
    assert result.name == "Smoke"
    assert result.is_default is False
    db.add.assert_called_once()


# ── sync_canonical_test_cases ───────────────────────────────────────────


def _make_test_case(fingerprint: str, suite_name: str | None, test_name: str = "test_x"):
    tc = SimpleNamespace(
        test_fingerprint=fingerprint,
        suite_name=suite_name,
        test_name=test_name,
        class_name="com.example.MyTest",
        canonical_test_case_id=None,
    )
    return tc


@pytest.mark.asyncio
async def test_sync_canonical_no_cases_returns_zero_counts():
    """The empty-cases path with NO primary_suite_name fallback returns
    the zero-count summary unchanged. Live-stream-gap branch is exercised
    in ``test_sync_canonical_creates_test_suite_from_primary_suite_name``."""
    empty_result = FakeExecuteResult()
    empty_result.scalars = lambda: SimpleNamespace(all=lambda: [])
    # Second execute call (TestRun lookup) returns scalar None — no primary_suite_name
    # to fall back on, so the fallback branch is a no-op.
    no_run_result = FakeExecuteResult(scalar_value=None)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[empty_result, no_run_result])

    result = await svc.sync_canonical_test_cases(db, uuid.uuid4(), uuid.uuid4())
    assert result == {"added": 0, "updated": 0, "linked": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_sync_canonical_creates_test_suite_from_primary_suite_name():
    """Regression for live-stream-gap missing suites (CLAUDE.md pitfall #15).

    When ``test_cases`` is empty for a run BUT ``test_runs.primary_suite_name``
    is populated (typical state when the SDK heartbeat updates run aggregates
    but the per-event Redis buffer was evicted before persist), the finalize
    step must still materialise a ``TestSuite`` row so /suites and
    /test-management surface the suite. Without this, the run is "invisible
    catalog-wise" until a manual reaper runs.
    """
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    empty_cases = FakeExecuteResult()
    empty_cases.scalars = lambda: SimpleNamespace(all=lambda: [])

    # The TestRun row carries the primary_suite_name set by stream_service
    # during live ingest.
    test_run = SimpleNamespace(
        id=run_id,
        primary_suite_name="Realistic TestNG client examples",
    )
    run_lookup = FakeExecuteResult(scalar_value=test_run)

    # get_or_create_suite_by_name (called by the new fallback) does a
    # SELECT TestSuite first; return None so the create branch fires.
    no_existing_suite = FakeExecuteResult(scalar_value=None)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[empty_cases, run_lookup, no_existing_suite])
    # Stamp ids on flush to mimic SQLAlchemy default uuid generation, so
    # the seeded suite owner path (no-op since the project has no
    # default_qa_lead_user_id) doesn't bomb on a None id.
    def _stamp(*_a, **_k):
        for call in db.add.call_args_list:
            obj = call.args[0]
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
    db.flush = AsyncMock(side_effect=_stamp)

    result = await svc.sync_canonical_test_cases(db, project_id, run_id)

    # The fallback added the TestSuite via db.add(...) — pin that we
    # actually created the row, not just early-returned.
    added_args = [c.args[0] for c in db.add.call_args_list]
    suite_names = [
        getattr(o, "name", None) for o in added_args
        if hasattr(o, "name")
    ]
    assert "Realistic TestNG client examples" in suite_names
    # And the count map reflects the addition for observability.
    assert result["added"] == 1


@pytest.mark.asyncio
async def test_sync_canonical_adds_new_fingerprints(monkeypatch):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    suite = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Regression")

    cases = [
        _make_test_case("fp1", "Regression", "test_a"),
        _make_test_case("fp2", "Regression", "test_b"),
    ]

    # Three execute() calls happen in order:
    #   1) SELECT TestCase WHERE test_run_id == run_id
    #   2) SELECT TestSuite WHERE project_id == ... AND name IN (...)
    #   3) SELECT CanonicalTestCase WHERE project_id == ... AND fingerprint IN (...)
    cases_result = FakeExecuteResult()
    cases_result.scalars = lambda: SimpleNamespace(all=lambda: cases)

    suites_result = FakeExecuteResult()
    suites_result.scalars = lambda: SimpleNamespace(all=lambda: [suite])

    canonical_result = FakeExecuteResult()
    canonical_result.scalars = lambda: SimpleNamespace(all=lambda: [])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cases_result, suites_result, canonical_result])

    # Real SQLAlchemy populates ``id`` from ``default=uuid.uuid4`` at flush
    # time. The AsyncMock flush is a no-op, so we simulate that here by
    # stamping an id on whatever was just added(). Without this, the link
    # counter under test stays at zero because ``canonical.id`` is None.
    def _stamp_ids(*_args, **_kwargs):
        for call in db.add.call_args_list:
            obj = call.args[0]
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=_stamp_ids)

    result = await svc.sync_canonical_test_cases(db, project_id, run_id)

    assert result["added"] == 2
    assert result["updated"] == 0
    assert result["linked"] == 2
    assert db.add.call_count == 2
    for tc in cases:
        assert tc.canonical_test_case_id is not None


@pytest.mark.asyncio
async def test_sync_canonical_updates_existing_and_restores_deleted():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    suite = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Regression")
    existing_canonical = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        test_suite_id=suite.id,
        test_fingerprint="fp1",
        test_name="old_name",
        class_name="OldClass",
        status="deleted",
        deleted_at_run_id=uuid.uuid4(),
        last_seen_run_id=uuid.uuid4(),
    )
    case = _make_test_case("fp1", "Regression", test_name="new_name")
    case.class_name = "NewClass"

    cases_result = FakeExecuteResult()
    cases_result.scalars = lambda: SimpleNamespace(all=lambda: [case])
    suites_result = FakeExecuteResult()
    suites_result.scalars = lambda: SimpleNamespace(all=lambda: [suite])
    canonical_result = FakeExecuteResult()
    canonical_result.scalars = lambda: SimpleNamespace(all=lambda: [existing_canonical])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cases_result, suites_result, canonical_result])

    result = await svc.sync_canonical_test_cases(db, project_id, run_id)

    assert result["updated"] == 1
    assert result["added"] == 0
    assert result["linked"] == 1
    # Restored.
    assert existing_canonical.status == "active"
    assert existing_canonical.deleted_at_run_id is None
    assert existing_canonical.last_seen_run_id == run_id
    # Name + class refreshed from the payload.
    assert existing_canonical.test_name == "new_name"
    assert existing_canonical.class_name == "NewClass"
    # Run TestCase linked.
    assert case.canonical_test_case_id == existing_canonical.id


@pytest.mark.asyncio
async def test_sync_canonical_skips_cases_with_no_fingerprint():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    case = _make_test_case("", "Regression")  # empty fingerprint

    cases_result = FakeExecuteResult()
    cases_result.scalars = lambda: SimpleNamespace(all=lambda: [case])
    suites_result = FakeExecuteResult()
    suites_result.scalars = lambda: SimpleNamespace(all=lambda: [
        SimpleNamespace(id=uuid.uuid4(), name="Regression")
    ])
    canonical_result = FakeExecuteResult()
    canonical_result.scalars = lambda: SimpleNamespace(all=lambda: [])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cases_result, suites_result, canonical_result])

    result = await svc.sync_canonical_test_cases(db, project_id, run_id)
    assert result["skipped"] == 1
    assert result["added"] == 0


# ── list_test_suites backfill probe (regression for /suites?project_id 500) ───


@pytest.mark.asyncio
async def test_list_test_suites_backfill_does_not_rollback_on_probe_failure():
    """Regression: when the backfill probe raises (e.g. asyncpg IN-binding
    error from missing ``expanding=True``), the service must NOT call
    ``db.rollback()`` on the injected session. The handler's ``get_db``
    dependency owns the transaction lifecycle; a service-side rollback
    aborts every other DB op in the request, producing a downstream 500
    that masks the real (degraded-but-correct) behaviour.

    The fix: catch the probe exception, log a warning, set
    ``missing_rows = []``, and continue. ``await db.rollback()`` is
    NEVER called from inside ``list_test_suites``.

    Reference incident 2026-05-18 — /suites?project_id=<uuid> returned
    500 because the original code did:

        WHERE tr.project_id IN :pids   # without expanding=True
        ...except Exception: await db.rollback()

    The IN binding raised; rollback aborted the request; commit-time
    failed; 500 with no traceback in logs.
    """
    project_id = uuid.uuid4()

    db = AsyncMock()
    # First call (selecting TestSuite rows) returns empty so the
    # backfill probe is the next thing to fire.
    empty_suites = FakeExecuteResult()
    empty_suites.scalars = lambda: SimpleNamespace(all=lambda: [])

    # Make the backfill probe raise — simulates the original asyncpg
    # IN-binding failure or any other DB error during the backfill.
    db.execute = AsyncMock(side_effect=[empty_suites, RuntimeError("boom")])
    db.rollback = AsyncMock()

    # Must NOT raise to the caller, AND must NOT call db.rollback().
    result = await svc.list_test_suites(db, [project_id])

    assert result == []
    db.rollback.assert_not_awaited()


def test_list_test_suites_uses_expanding_bind_for_in_clause():
    """Pin the canonical SQLAlchemy pattern for `IN :pids` bindings.

    Source-inspection test: opening the test_suite_service.py source
    and confirming the backfill query uses ``bindparam("pids",
    expanding=True)`` rather than the previous broken
    ``bindparams(pids=tuple(project_ids))``. The latter fails when
    asyncpg can't expand a tuple at the SQL layer for the IN clause.
    """
    from pathlib import Path
    source = Path(svc.__file__).read_text(encoding="utf-8")
    assert 'bindparam("pids", expanding=True)' in source, (
        "list_test_suites backfill must use bindparam('pids', expanding=True). "
        "The non-expanding form 500s /suites?project_id=<uuid>."
    )
    # And the broken pattern must NOT be present anywhere.
    assert 'bindparams(pids=tuple(' not in source, (
        "Found the legacy non-expanding bind pattern — replace with "
        "bindparam('pids', expanding=True)."
    )


# ── reconcile_canonical_deletions ───────────────────────────────────────────


def _canonical(*, fingerprint: str, status: str = "active") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        test_fingerprint=fingerprint,
        status=status,
        deleted_at_run_id=None,
        last_seen_run_id=None,
        test_name="test_x",
        class_name="C",
    )


@pytest.mark.asyncio
async def test_reconcile_deletion_short_circuits_when_window_is_zero():
    """``CANONICAL_DELETION_WINDOW_RUNS=0`` is the Phase-2b cutover knob —
    the reconciler must be a hard no-op so canonical vs legacy
    suite_memberships row-count comparisons stay clean."""
    project_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock()  # must NOT be called

    result = await svc.reconcile_canonical_deletions(
        db, project_id, window_runs=0
    )
    assert result == {"deleted": 0, "unchanged": 0, "window_size": 0}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_deletion_noop_when_project_has_no_runs():
    """An empty project must not have its catalog wiped on the very first
    reconciler tick after migration — without recent runs there is nothing
    to compare against and the safe answer is 'do nothing'."""
    project_id = uuid.uuid4()
    no_runs = FakeExecuteResult()
    no_runs.all = lambda: []  # zero recent runs

    db = AsyncMock()
    db.execute = AsyncMock(return_value=no_runs)

    result = await svc.reconcile_canonical_deletions(
        db, project_id, window_runs=5
    )
    assert result["deleted"] == 0
    assert result["window_size"] == 0
    # Only the runs lookup was issued — no fingerprint scan, no canonical sweep.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_reconcile_deletion_marks_stale_active_canonicals_only():
    """The reconciler's core contract: an active canonical whose
    fingerprint is missing from every run in the window is marked
    ``deleted``; one that's present in the window is left alone; rows
    with non-active statuses (``deleted``, ``needs_review``) are never
    touched (state machine ownership belongs to other paths)."""
    project_id = uuid.uuid4()
    recent_run_ids = [uuid.uuid4() for _ in range(3)]

    # Three active canonicals: stale, present, and previously-deleted-but-active-now
    # would be a contradiction so we use stale + present + needs_review.
    stale = _canonical(fingerprint="stale-fp")
    present = _canonical(fingerprint="present-fp")
    # needs_review must NOT be touched even if absent — only "active" is in scope.
    needs_review = _canonical(fingerprint="absent-but-not-active", status="needs_review")

    runs_result = FakeExecuteResult()
    runs_result.all = lambda: [(rid,) for rid in recent_run_ids]
    fps_result = FakeExecuteResult()
    fps_result.all = lambda: [("present-fp",)]
    actives_result = FakeExecuteResult()
    actives_result.scalars = lambda: SimpleNamespace(all=lambda: [stale, present])
    # needs_review never appears in the sweep because the WHERE clause filters by status=='active'.

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[runs_result, fps_result, actives_result])

    result = await svc.reconcile_canonical_deletions(
        db, project_id, window_runs=3
    )

    assert result == {"deleted": 1, "unchanged": 1, "window_size": 3}
    assert stale.status == "deleted"
    # deleted_at points at the MOST RECENT run in the window (the run after
    # which we noticed the absence), not any older one.
    assert stale.deleted_at_run_id == recent_run_ids[0]
    # Present + needs_review stay put.
    assert present.status == "active"
    assert present.deleted_at_run_id is None
    assert needs_review.status == "needs_review"


@pytest.mark.asyncio
async def test_reconcile_deletion_idempotent_on_rerun():
    """Running the reconciler twice in a row over the same DB state must
    NOT keep deleting things — the second run should report 0 deleted."""
    project_id = uuid.uuid4()
    recent_run_ids = [uuid.uuid4() for _ in range(5)]

    # First pass: stale → deleted, present → unchanged.
    stale = _canonical(fingerprint="stale-fp")
    present = _canonical(fingerprint="present-fp")

    runs_result_1 = FakeExecuteResult()
    runs_result_1.all = lambda: [(rid,) for rid in recent_run_ids]
    fps_result_1 = FakeExecuteResult()
    fps_result_1.all = lambda: [("present-fp",)]
    actives_result_1 = FakeExecuteResult()
    actives_result_1.scalars = lambda: SimpleNamespace(all=lambda: [stale, present])

    # Second pass: stale is now status='deleted' so the WHERE filter excludes
    # it from the actives sweep — only ``present`` is scanned.
    runs_result_2 = FakeExecuteResult()
    runs_result_2.all = lambda: [(rid,) for rid in recent_run_ids]
    fps_result_2 = FakeExecuteResult()
    fps_result_2.all = lambda: [("present-fp",)]
    actives_result_2 = FakeExecuteResult()
    actives_result_2.scalars = lambda: SimpleNamespace(all=lambda: [present])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        runs_result_1, fps_result_1, actives_result_1,
        runs_result_2, fps_result_2, actives_result_2,
    ])

    first = await svc.reconcile_canonical_deletions(db, project_id, window_runs=5)
    second = await svc.reconcile_canonical_deletions(db, project_id, window_runs=5)

    assert first["deleted"] == 1
    assert second["deleted"] == 0
    assert second["unchanged"] == 1


@pytest.mark.asyncio
async def test_reconcile_deletion_window_size_capped_by_actual_runs():
    """Asking for a 5-run window when the project has 2 runs: window_size
    must report the actual number considered, not the requested cap."""
    project_id = uuid.uuid4()
    only_two = [uuid.uuid4(), uuid.uuid4()]

    runs_result = FakeExecuteResult()
    runs_result.all = lambda: [(rid,) for rid in only_two]
    fps_result = FakeExecuteResult()
    fps_result.all = lambda: [("fp-a",)]
    actives_result = FakeExecuteResult()
    actives_result.scalars = lambda: SimpleNamespace(all=lambda: [])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[runs_result, fps_result, actives_result])

    result = await svc.reconcile_canonical_deletions(
        db, project_id, window_runs=5
    )
    assert result["window_size"] == 2


@pytest.mark.asyncio
async def test_reconcile_deletion_filters_null_fingerprints():
    """Test cases without a fingerprint can't match anything; the
    sweep must not let a NULL fingerprint sneak into the seen set
    and accidentally "rescue" a stale canonical that also has NULL
    (which shouldn't exist, but defence-in-depth)."""
    project_id = uuid.uuid4()
    stale = _canonical(fingerprint="stale-fp")

    runs_result = FakeExecuteResult()
    runs_result.all = lambda: [(uuid.uuid4(),)]
    # Seen fingerprints include a None + an unrelated fp.
    fps_result = FakeExecuteResult()
    fps_result.all = lambda: [(None,), ("other-fp",)]
    actives_result = FakeExecuteResult()
    actives_result.scalars = lambda: SimpleNamespace(all=lambda: [stale])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[runs_result, fps_result, actives_result])

    result = await svc.reconcile_canonical_deletions(
        db, project_id, window_runs=1
    )
    assert result["deleted"] == 1
    assert stale.status == "deleted"


# ── bulk_link_canonicals_to_suite ────────────────────────────────────────────


def _movable_canonical(*, project_id, suite_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        test_suite_id=suite_id or uuid.uuid4(),
        test_fingerprint=f"fp-{uuid.uuid4().hex[:6]}",
        status="active",
    )


@pytest.mark.asyncio
async def test_bulk_link_empty_list_is_noop():
    """Empty input must NOT issue any DB call — saves a round trip on
    accidental UI submit-with-zero-selected."""
    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)

    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    result = await svc.bulk_link_canonicals_to_suite(db, target, [])
    assert result == {"moved": 0, "skipped_already_in_target": 0, "missing_ids": []}
    db.execute.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_link_moves_within_same_project():
    """Happy path: every id resolves, every canonical belongs to the
    target suite's project, none are already in the target suite."""
    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    c1 = _movable_canonical(project_id=project_id)
    c2 = _movable_canonical(project_id=project_id)

    found_result = FakeExecuteResult()
    found_result.scalars = lambda: SimpleNamespace(all=lambda: [c1, c2])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=found_result)
    db.flush = AsyncMock()

    result = await svc.bulk_link_canonicals_to_suite(db, target, [c1.id, c2.id])

    assert result == {
        "moved": 2,
        "skipped_already_in_target": 0,
        "missing_ids": [],
    }
    assert c1.test_suite_id == target.id
    assert c2.test_suite_id == target.id
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_bulk_link_skips_canonicals_already_in_target():
    """An id whose canonical already lives in the target suite is a
    no-op for that row — count it under ``skipped_already_in_target``
    so the UI can show "2 already there, 3 moved"."""
    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    already_there = _movable_canonical(project_id=project_id, suite_id=target.id)
    moving = _movable_canonical(project_id=project_id)

    found_result = FakeExecuteResult()
    found_result.scalars = lambda: SimpleNamespace(all=lambda: [already_there, moving])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=found_result)
    db.flush = AsyncMock()

    result = await svc.bulk_link_canonicals_to_suite(
        db, target, [already_there.id, moving.id]
    )

    assert result["moved"] == 1
    assert result["skipped_already_in_target"] == 1
    assert moving.test_suite_id == target.id
    # The flush is gated on moved>0 — verify it still fired since one row moved.
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_bulk_link_no_flush_when_nothing_moves():
    """If every supplied id is already in the target suite, no write
    happens — saves a no-op flush round trip."""
    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    c1 = _movable_canonical(project_id=project_id, suite_id=target.id)

    found_result = FakeExecuteResult()
    found_result.scalars = lambda: SimpleNamespace(all=lambda: [c1])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=found_result)
    db.flush = AsyncMock()

    result = await svc.bulk_link_canonicals_to_suite(db, target, [c1.id])

    assert result["moved"] == 0
    assert result["skipped_already_in_target"] == 1
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_link_surfaces_missing_ids_without_failing():
    """A stale UI selection that includes ids deleted in flight must
    NOT 4xx — the resolvable ones still move, and the missing ones
    come back in the response so the UI can drop them."""
    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    resolves = _movable_canonical(project_id=project_id)
    deleted_id = uuid.uuid4()

    found_result = FakeExecuteResult()
    found_result.scalars = lambda: SimpleNamespace(all=lambda: [resolves])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=found_result)
    db.flush = AsyncMock()

    result = await svc.bulk_link_canonicals_to_suite(
        db, target, [resolves.id, deleted_id]
    )

    assert result["moved"] == 1
    assert result["missing_ids"] == [deleted_id]


@pytest.mark.asyncio
async def test_bulk_link_rejects_entire_batch_on_cross_project_id():
    """Even ONE id from a different project must fail the WHOLE batch
    with 400 — partial moves would leak data across tenant boundaries."""
    from fastapi import HTTPException

    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_a)
    valid = _movable_canonical(project_id=project_a)
    cross = _movable_canonical(project_id=project_b)  # different project

    found_result = FakeExecuteResult()
    found_result.scalars = lambda: SimpleNamespace(all=lambda: [valid, cross])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=found_result)
    db.flush = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await svc.bulk_link_canonicals_to_suite(db, target, [valid.id, cross.id])

    assert exc_info.value.status_code == 400
    # Crucial: the valid one was NOT mutated before the raise.
    assert valid.test_suite_id != target.id
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_link_rejects_oversized_batch():
    """Hard cap on batch size — the schema-level validation catches
    most callers, but the service refuses defence-in-depth too."""
    from fastapi import HTTPException

    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    too_many = [uuid.uuid4() for _ in range(svc.BULK_LINK_MAX_IDS + 1)]

    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await svc.bulk_link_canonicals_to_suite(db, target, too_many)

    assert exc_info.value.status_code == 400
    # Refused before any DB read.
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_link_dedups_duplicate_ids_in_input():
    """The UI sometimes sends the same id twice (re-selection mishaps).
    The service must NOT double-count those — moved should reflect
    distinct canonicals."""
    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    c1 = _movable_canonical(project_id=project_id)

    found_result = FakeExecuteResult()
    found_result.scalars = lambda: SimpleNamespace(all=lambda: [c1])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=found_result)
    db.flush = AsyncMock()

    # Same id three times.
    result = await svc.bulk_link_canonicals_to_suite(db, target, [c1.id, c1.id, c1.id])

    assert result["moved"] == 1
    assert result["missing_ids"] == []
