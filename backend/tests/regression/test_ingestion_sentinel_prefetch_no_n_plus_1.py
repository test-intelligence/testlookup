"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-0830).

The MinIO/sentinel ingest path (`process_sentinel`) upserted parsed cases with
`for case_data in parsed_cases: await _upsert_test_case(db, case_data, run)` —
no prefetch — so `_upsert_test_case` issued one SELECT per case (N+1: a 1000-test
upload = 1000 extra round trips). The fix prefetches existing rows in one query
and passes `existing`/`fingerprint` through, mirroring
`ingestion_pipeline.ingest_test_results`.

These tests pin the contract the prefetch relies on: `_upsert_test_case` skips
its SELECT when the caller has completed a prefetch, including a known miss,
and only falls back to a per-row SELECT when no prefetch occurred.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

# app.services.ingestion imports testng_parser → defusedxml (a CI dep that may
# be absent locally). Skip the module rather than error-collect when it's gone.
pytest.importorskip("defusedxml")


def _case():
    return {
        "test_name": "t1", "class_name": "C", "status": "passed",
        "duration_ms": 12, "error_message": None,
    }


@pytest.mark.asyncio
async def test_upsert_with_prefetched_existing_issues_no_select():
    """Prefetch path: passing `existing` must NOT trigger a per-row SELECT."""
    from app.services.ingestion import _upsert_test_case

    run = SimpleNamespace(id=uuid.uuid4())
    existing = SimpleNamespace(
        id=uuid.uuid4(), status=None, duration_ms=None, error_message=None,
        test_fingerprint="fp", test_name="t1",
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
        add=MagicMock(),
        flush=AsyncMock(),
    )
    # The history-dedup probe (one SELECT on TestCaseHistory, added after this
    # pin was first written) runs on the existing path; stub it to "no prior
    # history row" so a fresh one is inserted.
    tc = await _upsert_test_case(db, _case(), run, existing=existing, fingerprint="fp")

    # The prefetch eliminates the per-case TestCase SELECT — the only remaining
    # execute is the single history-dedup probe (not a per-row lookup + history).
    assert db.execute.await_count == 1
    assert tc is existing             # updated the prefetched row in place


@pytest.mark.asyncio
async def test_upsert_without_existing_falls_back_to_one_select():
    """Legacy path: no `existing` → exactly one per-row SELECT (what the
    prefetch eliminates by doing a single batched query upstream)."""
    from app.services.ingestion import _upsert_test_case

    run = SimpleNamespace(id=uuid.uuid4())
    found = SimpleNamespace(
        id=uuid.uuid4(), status=None, duration_ms=None, error_message=None,
        test_fingerprint="fp", test_name="t1",
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: found)
        ),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    await _upsert_test_case(db, _case(), run)  # no existing/fingerprint

    # Legacy path issues the inline per-case TestCase SELECT (what the prefetch
    # avoids) plus the history-dedup probe = two executes.
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_upsert_with_prefetched_miss_issues_no_select():
    """A known prefetch miss inserts directly instead of querying again."""
    from app.services.ingestion import _upsert_test_case

    run = SimpleNamespace(id=uuid.uuid4())
    db = SimpleNamespace(
        execute=AsyncMock(),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    await _upsert_test_case(
        db,
        _case(),
        run,
        existing=None,
        fingerprint="fp",
        existing_was_prefetched=True,
    )

    assert db.execute.await_count == 0


def test_sentinel_path_uses_bounded_prefetch_and_marks_misses_known():
    """The MinIO path must share the bounded prefetch contract."""
    import inspect
    from app.services import ingestion

    source = inspect.getsource(ingestion.process_sentinel)
    assert "await _prefetch_test_cases" in source
    assert "existing_was_prefetched=True" in source
