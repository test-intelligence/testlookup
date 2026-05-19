"""Tests for the P2-1 prefetch refactor in ``ingestion_pipeline``.

Pins the contract: ``ingest_test_results`` issues ONE bulk SELECT to
load every existing TestCase row for the run keyed by fingerprint,
then per-row ``_upsert_test_case`` uses the cached match instead of
its own SELECT. For a 1000-test run this collapses ~1000 round-trips
into 1.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return self._rows


class _ExecResult:
    def __init__(self, *, scalars=None, scalar=None):
        self._scalars = scalars or []
        self._scalar = scalar
    def scalars(self):
        return _ScalarsResult(self._scalars)
    def scalar_one_or_none(self):
        return self._scalar
    def scalar(self):
        return self._scalar


@pytest.mark.asyncio
async def test_ingest_test_results_prefetches_existing_rows_in_one_select(monkeypatch):
    """The prefetch issues exactly ONE SELECT regardless of row count,
    and per-row upsert never issues its own SELECT when the cache is
    populated."""
    from app.services import ingestion_pipeline as pipeline

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    results = [
        {"test_name": f"test_{i}", "class_name": "S", "status": "passed", "suite_name": "smoke"}
        for i in range(50)
    ]

    # Track execute() calls — we expect exactly one SELECT TestCase
    # against the run id.
    execute_calls: list = []

    async def _fake_execute(stmt):
        execute_calls.append(stmt)
        # First call is the bulk prefetch SELECT — return zero existing
        # rows so every input row takes the INSERT path.
        return _ExecResult(scalars=[])

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=_fake_execute),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    # Stub out the per-row upsert so we don't touch SQLAlchemy ORM
    # internals — count invocations + verify it received the cache.
    upsert_calls: list = []

    async def _fake_upsert(db_arg, case_data, run_arg, *, existing, fingerprint):
        upsert_calls.append(
            (case_data.get("test_name"), existing, fingerprint)
        )

    monkeypatch.setattr(pipeline, "_upsert_test_case", _fake_upsert)

    count = await pipeline.ingest_test_results(db, run, results)

    assert count == 50
    # Exactly one SELECT (the bulk prefetch — no per-row queries).
    assert db.execute.await_count == 1
    # Every upsert call received existing=None (because the prefetch
    # returned []) and a non-empty fingerprint.
    assert len(upsert_calls) == 50
    for name, existing, fp in upsert_calls:
        assert existing is None
        assert fp  # non-empty string


@pytest.mark.asyncio
async def test_ingest_test_results_passes_cached_existing_row_to_upsert(monkeypatch):
    """When the prefetch returns a row matching a fingerprint, that
    row is handed to ``_upsert_test_case`` so it can skip the SELECT."""
    from app.services import ingestion_pipeline as pipeline
    from app.services.ingestion import make_test_fingerprint

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    fp_existing = make_test_fingerprint("test_a", "SuiteA")
    fp_new = make_test_fingerprint("test_b", "SuiteA")

    existing_row = SimpleNamespace(id=uuid.uuid4(), test_fingerprint=fp_existing)

    async def _fake_execute(stmt):
        # Single prefetch call returns one existing match.
        return _ExecResult(scalars=[existing_row])

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=_fake_execute),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    handed: dict[str, object] = {}

    async def _fake_upsert(db_arg, case_data, run_arg, *, existing, fingerprint):
        handed[case_data["test_name"]] = (existing, fingerprint)

    monkeypatch.setattr(pipeline, "_upsert_test_case", _fake_upsert)

    await pipeline.ingest_test_results(
        db,
        run,
        [
            {"test_name": "test_a", "class_name": "SuiteA", "status": "passed", "suite_name": "smoke"},
            {"test_name": "test_b", "class_name": "SuiteA", "status": "failed", "suite_name": "smoke"},
        ],
    )

    a_existing, a_fp = handed["test_a"]
    b_existing, b_fp = handed["test_b"]
    # test_a's fingerprint matched → row supplied to upsert.
    assert a_existing is existing_row
    assert a_fp == fp_existing
    # test_b's fingerprint didn't match → upsert gets None.
    assert b_existing is None
    assert b_fp == fp_new


@pytest.mark.asyncio
async def test_ingest_test_results_handles_empty_input_without_extra_select(monkeypatch):
    """No results → no prefetch SELECT. (The default-suite lookup may
    still fire if any row needs it — empty input means no row needs it.)"""
    from app.services import ingestion_pipeline as pipeline

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = SimpleNamespace(
        execute=AsyncMock(),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    async def _never(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("upsert should not be called on empty input")

    monkeypatch.setattr(pipeline, "_upsert_test_case", _never)

    count = await pipeline.ingest_test_results(db, run, [])

    assert count == 0
    # No prefetch SELECT issued for empty input.
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_upsert_test_case_skips_select_when_existing_supplied(monkeypatch):
    """Direct unit test on ``_upsert_test_case``: when the caller
    supplies ``existing`` and ``fingerprint``, the function does NOT
    issue its own SELECT — the prefetch supplied the answer."""
    from app.services import ingestion

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    existing_tc = SimpleNamespace(
        id=uuid.uuid4(),
        status="PASSED",
        duration_ms=100,
        error_message=None,
        test_fingerprint="fp-x",
    )

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=AssertionError(
            "no SELECT must fire when existing+fingerprint are supplied"
        )),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    # The function appends a TestCaseHistory row at the end; the model
    # has default uuid PKs, no FK validation in the unit test.
    monkeypatch.setattr(
        "app.services.privacy_service.sanitize_for_persistence",
        lambda s: s,
        raising=False,
    )

    result = await ingestion._upsert_test_case(
        db,
        {"test_name": "test_x", "class_name": "C", "status": "failed", "duration_ms": 200, "error_message": "boom"},
        run,
        existing=existing_tc,
        fingerprint="fp-x",
    )

    # Existing row was mutated in place — no INSERT path.
    assert result is existing_tc
    assert existing_tc.duration_ms == 200
    assert existing_tc.error_message == "boom"
    db.execute.assert_not_awaited()  # the SELECT was skipped
