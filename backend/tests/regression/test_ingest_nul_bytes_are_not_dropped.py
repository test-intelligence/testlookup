"""E2E pass 2026-10-10 (homelab): a result with a NUL byte was dropped.

PostgreSQL text and JSONB cannot hold U+0000 (SQLSTATE 22021). A JSON batch of
one FAILED test whose message carried ``\\x00`` plus two PASSED tests landed as
``2/2 passed, 100%, STOPPED`` with ``ingestion_rejection_reasons`` code 22021:
the failure itself was gone. Captured binary output in a stack trace is the
usual source. NULs are now stripped wherever results enter persistence: the
batch payload, ``ingest_test_results`` (before fingerprinting), the shared
upsert, and the transport sanitizer the live drainer uses.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.regression


def _has_nul(value) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_nul(v) for v in value)
    return False


def test_strip_nul_bytes_cleans_nested_strings_and_keys_only():
    from app.services.ingestion_sanitization import strip_nul_bytes

    raw = {"a\x00": "x\x00y", "n": 3, "l": ["p\x00", {"k": "v\x00"}], "none": None, "t": True}
    out = strip_nul_bytes(raw)
    assert out == {"a": "xy", "n": 3, "l": ["p", {"k": "v"}], "none": None, "t": True}
    assert _has_nul(raw)  # the input is not mutated


def test_the_transport_sanitizer_strips_them():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    out = sanitize_test_result_payload({
        "test_name": "t\x00", "status": "FAILED", "error_message": "boom \x00 here",
        "stack_trace": "line\x00", "suite_name": "s\x00",
    })
    assert not _has_nul(out)
    assert out["test_name"] == "t"
    assert "boom" in out["error_message"]


@pytest.mark.asyncio
async def test_ingest_test_results_hands_the_upsert_nul_free_rows_and_keeps_the_failure():
    from app.services import ingestion_pipeline

    seen: list[dict] = []

    async def _upsert(db, case_data, run, **kw):
        if _has_nul(case_data):
            raise RuntimeError("22021 invalid byte sequence")  # what PostgreSQL does
        seen.append(case_data)
        return SimpleNamespace(id=uuid.uuid4())

    class _Nested:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=_Nested())
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), framework="pytest")
    results = [
        {"test_name": "login \x00", "status": "FAILED", "suite_name": "api",
         "error_message": "Error: \x00 null byte", "stack_trace": "at \x00"},
        {"test_name": "a", "status": "PASSED", "suite_name": "api"},
        {"test_name": "b", "status": "PASSED", "suite_name": "api"},
    ]
    with patch.object(ingestion_pipeline, "_upsert_test_case", _upsert), \
            patch.object(ingestion_pipeline, "_prefetch_test_cases", AsyncMock(return_value={})):
        count = await ingestion_pipeline.ingest_test_results(db, run, results)

    assert count == 3
    assert run.ingestion_rejected_tests == 0
    assert run.ingestion_complete is True
    failed = [c for c in seen if c["status"] == "FAILED"]
    assert failed and failed[0]["test_name"] == "login " and "null byte" in failed[0]["error_message"]


def test_the_shared_upsert_and_the_batch_task_strip_them_too():
    """The upsert is reached by the MinIO-webhook path too, and a NUL in a
    run-level field (build number, branch) failed the run INSERT and lost the
    whole batch."""
    import inspect

    from app.services import ingestion
    from app.worker import tasks

    assert "case_data = strip_nul_bytes(case_data)" in inspect.getsource(ingestion._upsert_test_case)
    assert "payload = strip_nul_bytes(payload)" in inspect.getsource(tasks.ingest_uploaded_results)


def test_a_nul_in_a_run_level_field_is_stripped_at_the_schema():
    """Live (round 1): ``build_number="...\x00"`` made ``POST /ingest`` a 500,
    the run-identity lookup failing before anything was queued."""
    from app.models.schemas import IngestPayload, LiveSessionCreate

    p = IngestPayload.model_validate({
        "project_id": str(uuid.uuid4()), "build_number": "b-1\x00", "branch": "ma\x00in",
        "results": [{"test_name": "t\x00", "status": "FAILED", "error_message": "x\x00"}],
    })
    assert (p.build_number, p.branch) == ("b-1", "main")
    assert p.results[0].test_name == "t" and p.results[0].error_message == "x"
    s = LiveSessionCreate.model_validate({"project_id": "P\x00", "client_name": "c", "build_number": "\x00b"})
    assert (s.project_id, s.build_number) == ("P", "b")


def test_a_build_number_that_was_only_nul_is_still_refused():
    from pydantic import ValidationError

    from app.models.schemas import IngestPayload

    with pytest.raises(ValidationError):
        IngestPayload.model_validate({
            "project_id": str(uuid.uuid4()), "build_number": "\x00",
            "results": [{"test_name": "t", "status": "PASSED"}],
        })


def test_live_event_content_is_stripped_but_identifiers_are_refused_not_rewritten():
    """A batch id is an idempotency key: rewriting a NUL out of it would change
    which retry it matches, so the live schemas leave identifiers to their own
    control-character check (test_live_protocol_schema) and strip the rest."""
    from pydantic import ValidationError

    from app.models.schemas import LiveEventBatch

    ok = LiveEventBatch.model_validate({
        "session_id": "s-1", "run_id": "r-1", "batch_id": "b-1",
        "events": [{"event_type": "test_result", "test_name": "t\x00", "error_message": "e\x00"}],
    })
    assert ok.events[0].test_name == "t" and ok.events[0].error_message == "e"
    with pytest.raises(ValidationError):
        LiveEventBatch.model_validate({
            "session_id": "s-1", "run_id": "r-1", "batch_id": "b\x00-1",
            "events": [{"event_type": "test_result"}],
        })
