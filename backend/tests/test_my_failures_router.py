"""Unit tests for the /api/v1/me/assigned-failures inbox router.

The router is read-only and user-scoped — the only authorisation predicate
is ``TestCase.assigned_to_user_id == current_user.id``. Tests focus on:

  * Filter parsing (the ``"all"`` sentinel maps to None, bad UUIDs degrade
    to "no filter" rather than 400).
  * The count endpoint short-circuits when total is zero.
  * The list endpoint truncates long error messages.

DB execution is fully mocked; this is a pure unit-test surface so we can
run it without a real Postgres.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# The list endpoint now calls ``runs_service.fetch_run_seq_map`` to
# decorate each row with its per-(project, suite) run number. The helper
# fires its own SELECTs which would otherwise drain the test's
# ``execute.side_effect`` list and trip StopAsyncIteration. Patch it to
# an empty dict for tests that don't care about the sequence value —
# dedicated coverage lives in ``tests/services/test_run_seq_map.py``.
class _StackedPatch:
    """Apply several context-manager patches as one ``with`` block."""

    def __init__(self, *patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)
        return False


def _patch_run_seq_map():
    """Patch at the source — the router imports the helpers lazily inside
    the function (``from app.services.runs_service import
    fetch_run_seq_map, first_failed_step_by_canonical``), so a patch on the
    router-side name wouldn't intercept the local-import lookup.

    Phase 5: the list endpoint also batch-resolves the first-failed-step name
    via ``first_failed_step_by_canonical``. Patch it to an empty map so tests
    that don't exercise step enrichment don't need a 4th ``execute``
    side_effect; dedicated coverage lives in ``tests/test_granular_reports.py``.
    """
    return _StackedPatch(
        patch(
            "app.services.runs_service.fetch_run_seq_map",
            AsyncMock(return_value={}),
        ),
        patch(
            "app.services.runs_service.first_failed_step_by_canonical",
            AsyncMock(return_value={}),
        ),
    )


def _count_result(value: int):
    res = MagicMock()
    res.scalar = MagicMock(return_value=value)
    return res


def _list_result(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


# ── _parse_project_id ──────────────────────────────────────────────────────


def test_parse_project_id_none_returns_none():
    from app.routers.my_failures import _parse_project_id
    assert _parse_project_id(None) is None
    assert _parse_project_id("") is None


def test_parse_project_id_all_sentinel_returns_none():
    """The frontend ``ALL_PROJECTS_ID = 'all'`` must never reach the DB
    as a UUID — the parser maps it to None (no filter)."""
    from app.routers.my_failures import _parse_project_id
    assert _parse_project_id("all") is None


def test_parse_project_id_valid_uuid_round_trips():
    from app.routers.my_failures import _parse_project_id
    pid = uuid.uuid4()
    assert _parse_project_id(str(pid)) == pid


def test_parse_project_id_invalid_falls_back_to_none():
    """A garbage value must NOT 400 — the inbox is implicitly self-scoped,
    so a stale URL silently degrades to 'no project filter'."""
    from app.routers.my_failures import _parse_project_id
    assert _parse_project_id("not-a-uuid") is None
    assert _parse_project_id("0000") is None


# ── list_my_assigned_failures ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_empty_when_count_is_zero():
    """No rows = no second query. Short-circuit and return the empty envelope."""
    from app.routers.my_failures import list_my_assigned_failures

    db = AsyncMock()
    # Only the count query fires before we short-circuit.
    db.execute = AsyncMock(return_value=_count_result(0))

    user = SimpleNamespace(id=uuid.uuid4())
    result = await list_my_assigned_failures(
        project_id=None, days=30, release_id=None, page=1, size=25, db=db, current_user=user,
    )
    assert result.total == 0
    assert result.items == []
    assert result.pages == 0
    assert result.unresolved_total == 0
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_list_hydrates_rows_and_builds_navigation_url():
    from app.routers.my_failures import list_my_assigned_failures

    case_id = uuid.uuid4()
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    row = SimpleNamespace(
        id=case_id,
        test_name="test_login",
        suite_name="Auth",
        class_name="com.example.AuthTests",
        status="FAILED",
        severity="major",
        failure_category="PRODUCT_BUG",
        error_message="boom",
        duration_ms=420,
        created_at=datetime.now(timezone.utc),
        test_run_id=run_id,
        canonical_test_case_id=uuid.uuid4(),
        build_number=2029,
        project_id=project_id,
        project_name="GoogleSearch",
        triage_status="PENDING_REVIEW",
        triage_notes=None,
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _count_result(1),
        _list_result([row]),
        # Third call is the per-test failure-count grouping query.
        _list_result([
            SimpleNamespace(
                project_id=row.project_id,
                suite_name=row.suite_name,
                class_name=row.class_name,
                test_name=row.test_name,
                n=1,
            ),
        ]),
    ])
    user = SimpleNamespace(id=uuid.uuid4())

    with _patch_run_seq_map():
        result = await list_my_assigned_failures(
            project_id=None, days=30, release_id=None, page=1, size=25, db=db, current_user=user,
        )
    assert result.total == 1
    assert len(result.items) == 1
    item = result.items[0]
    assert item.test_name == "test_login"
    assert item.build_number == "2029"
    assert item.project_name == "GoogleSearch"
    # navigation_url drives the row click handler — keep it stable.
    assert item.navigation_url == f"/runs/{run_id}/tests/{case_id}"
    assert item.failure_count == 1


@pytest.mark.asyncio
async def test_list_truncates_long_error_message():
    """Error messages can be huge — the inbox row should ship a preview only."""
    from app.routers.my_failures import list_my_assigned_failures

    long_err = "x" * 1000
    row = SimpleNamespace(
        id=uuid.uuid4(),
        test_name="t",
        suite_name="s",
        class_name=None,
        status="FAILED",
        severity=None,
        failure_category=None,
        error_message=long_err,
        duration_ms=None,
        created_at=datetime.now(timezone.utc),
        test_run_id=uuid.uuid4(),
        canonical_test_case_id=uuid.uuid4(),
        build_number=None,
        project_id=uuid.uuid4(),
        project_name="P",
        triage_status="PENDING_REVIEW",
        triage_notes=None,
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _count_result(1),
        _list_result([row]),
        _list_result([]),  # failure-count grouping — no aggregation rows OK
    ])
    user = SimpleNamespace(id=uuid.uuid4())

    with _patch_run_seq_map():
        result = await list_my_assigned_failures(
            project_id=None, days=30, release_id=None, page=1, size=25, db=db, current_user=user,
        )
    assert result.items[0].error_message is not None
    # 280-char cap with ellipsis = 280 chars total.
    assert len(result.items[0].error_message) == 280
    assert result.items[0].error_message.endswith("...")
    # No grouping row matched → default failure_count = 1.
    assert result.items[0].failure_count == 1


@pytest.mark.asyncio
async def test_list_attaches_per_test_failure_count_from_grouping_query():
    """Each row gets ``failure_count`` = count of same-test failures in the
    selected window so the inbox can surface repeat offenders.
    Grouping key is (project_id, suite_name, class_name, test_name)."""
    from app.routers.my_failures import list_my_assigned_failures

    project_id = uuid.uuid4()
    # Two rows for the same logical test (different runs) + one unrelated row.
    repeat_row_a = SimpleNamespace(
        id=uuid.uuid4(),
        test_name="test_flaky",
        suite_name="checkout-api",
        class_name="com.example.CheckoutTests",
        status="FAILED",
        severity="major",
        failure_category=None,
        error_message=None,
        duration_ms=None,
        created_at=datetime.now(timezone.utc),
        test_run_id=uuid.uuid4(),
        canonical_test_case_id=uuid.uuid4(),
        build_number=None,
        project_id=project_id,
        project_name="P",
        triage_status="PENDING_REVIEW",
        triage_notes=None,
    )
    repeat_row_b = SimpleNamespace(**{**repeat_row_a.__dict__, "id": uuid.uuid4(), "test_run_id": uuid.uuid4()})
    other_row = SimpleNamespace(**{**repeat_row_a.__dict__, "id": uuid.uuid4(), "test_name": "test_other"})

    grouping = [
        SimpleNamespace(
            project_id=project_id,
            suite_name="checkout-api",
            class_name="com.example.CheckoutTests",
            test_name="test_flaky",
            n=5,
        ),
        SimpleNamespace(
            project_id=project_id,
            suite_name="checkout-api",
            class_name="com.example.CheckoutTests",
            test_name="test_other",
            n=1,
        ),
    ]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _count_result(3),
        _list_result([repeat_row_a, repeat_row_b, other_row]),
        _list_result(grouping),
    ])
    user = SimpleNamespace(id=uuid.uuid4())

    with _patch_run_seq_map():
        result = await list_my_assigned_failures(
            project_id=None, days=7, release_id=None, page=1, size=25, db=db, current_user=user,
        )

    by_test = {item.test_name: item.failure_count for item in result.items}
    # Both occurrences of the repeating test share the same count.
    assert [item.failure_count for item in result.items if item.test_name == "test_flaky"] == [5, 5]
    assert by_test["test_other"] == 1


# ── count endpoint ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_endpoint_returns_scalar():
    from app.routers.my_failures import my_assigned_failures_count

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_count_result(7))
    user = SimpleNamespace(id=uuid.uuid4())

    result = await my_assigned_failures_count(
        project_id=None, days=30, release_id=None, db=db, current_user=user,
    )
    assert result == {"count": 7}


@pytest.mark.asyncio
async def test_count_endpoint_handles_null_scalar():
    """``COUNT(*)`` should always return a number — but defensive against
    asyncpg returning None on a malformed query path."""
    from app.routers.my_failures import my_assigned_failures_count

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_count_result(None))
    user = SimpleNamespace(id=uuid.uuid4())

    result = await my_assigned_failures_count(
        project_id="all", days=30, release_id=None, db=db, current_user=user,
    )
    assert result == {"count": 0}
