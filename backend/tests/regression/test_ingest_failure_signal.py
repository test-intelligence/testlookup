"""Regression: a live/file ingest where every row fails must signal loudly.

Bug (2026-05-30 multi-pass review, `live-stream-ingestion` audit, Major):
``ingestion_pipeline.ingest_test_results`` swallowed each per-row upsert
failure with a WARNING and returned ``count``. A run where ALL rows failed
returned ``count=0`` — indistinguishable from an empty payload — and still
flowed to ``finalize_run`` and got marked "complete". The only trace was N
scattered per-row warnings; nothing said "this run ingested nothing".

Fix: emit an ERROR (``ingest_test_results_all_failed``) when every row of a
non-empty payload failed, and a WARNING (``ingest_test_results_partial_failure``)
on partial failure. The ``int`` return is preserved (callers unchanged).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ingest_test_results pulls in the ingestion module → testng_parser → defusedxml
# (a declared dependency). Skip cleanly where the optional dep isn't installed
# locally; it's present in CI/Docker.
pytest.importorskip("defusedxml")


def _fake_db_no_existing_rows():
    """db.execute(...) → .scalars().all() == [] (no prefetched TestCase rows)."""
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=exec_result)
    savepoint = AsyncMock()
    savepoint.__aenter__ = AsyncMock(return_value=savepoint)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(side_effect=lambda: savepoint)
    return db


@pytest.mark.asyncio
async def test_all_rows_failed_logs_error_signal():
    from app.services import ingestion_pipeline as ip

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    # suite_name present → skips the project-default lookup (one less db call).
    results = [
        {"test_name": "t1", "class_name": "C", "status": "passed", "suite_name": "S"},
        {"test_name": "t2", "class_name": "C", "status": "failed", "suite_name": "S"},
    ]

    fake_logger = MagicMock()
    with (
        patch.object(
            ip, "_upsert_test_case",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch.object(ip, "logger", fake_logger),
    ):
        count = await ip.ingest_test_results(_fake_db_no_existing_rows(), run, results)

    assert count == 0
    error_events = [c.args[0] for c in fake_logger.error.call_args_list if c.args]
    assert "ingest_test_results_all_failed" in error_events, (
        "All-rows-failed ingest no longer emits its ERROR signal — a run that "
        "ingested nothing is once again indistinguishable from an empty payload."
    )


@pytest.mark.asyncio
async def test_partial_failure_logs_warning_signal():
    from app.services import ingestion_pipeline as ip

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    results = [
        {"test_name": "t1", "class_name": "C", "status": "passed", "suite_name": "S"},
        {"test_name": "t2", "class_name": "C", "status": "failed", "suite_name": "S"},
    ]

    fake_logger = MagicMock()
    # First row upserts, second raises → partial failure.
    with (
        patch.object(
            ip, "_upsert_test_case",
            new=AsyncMock(side_effect=[None, RuntimeError("boom")]),
        ),
        patch.object(ip, "logger", fake_logger),
    ):
        count = await ip.ingest_test_results(_fake_db_no_existing_rows(), run, results)

    assert count == 1
    warn_events = [c.args[0] for c in fake_logger.warning.call_args_list if c.args]
    assert "ingest_test_results_partial_failure" in warn_events
