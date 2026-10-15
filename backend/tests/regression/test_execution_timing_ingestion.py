"""EXEC-01: ingestion writes each test's start / finish / phase split / worker.

Every parser now puts what its report says about WHEN a test ran on the result
dict (``report_timings.CASE_TIMING_KEYS``); these tests pin the transport from
there to the ``test_cases`` columns (migration 0202):

* ``ingestion._upsert_test_case`` writes the seven columns on insert AND on the
  update path, bounded for their columns, and all NULL (``timing_source`` too)
  for a result that carries no timing;
* the MinIO sentinel path (``process_sentinel``) parses its TestNG/JUnit files
  through the timing twin and keeps the report's suite timing for finalize;
* the upload worker dispatch (``_parse_file_to_results_with_timings``) returns
  the same result dicts as the plain dispatch plus the ``ReportTimings``, and
  an archive merges its files' timings.

The real-Postgres round trip is ``tests/integration/test_test_case_execution_timing_postgres.py``.
"""
from __future__ import annotations

import base64
import io
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import TestCase as _TestCase  # noqa: E402  (aliased so pytest doesn't collect it)
from app.services import ingestion  # noqa: E402
from app.services.testng_parser import parse_testng_xml  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "timing"
SAMPLES = Path(__file__).resolve().parents[3] / "samples"
COLUMNS = ("started_at", "finished_at", "setup_ms", "call_ms", "teardown_ms", "worker_id", "timing_source")


def _at(second: int, ms: int = 0) -> datetime:
    return datetime(2026, 10, 14, 10, 0, second, ms * 1000, tzinfo=timezone.utc)


class _FakeDB:
    """Just enough session for ``_upsert_test_case``: no prior row, no history."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        return result


def _columns(row) -> dict:
    return {column: getattr(row, column) for column in COLUMNS}


async def _insert(case: dict):
    db = _FakeDB()
    run = SimpleNamespace(id=uuid.uuid4())
    row = await ingestion._upsert_test_case(db, case, run, existing=None, fingerprint="fp")
    assert isinstance(row, _TestCase)
    return row, db, run


@pytest.mark.asyncio
async def test_a_testng_case_writes_its_start_finish_and_phases():
    cases = {c["test_name"]: c for c in parse_testng_xml((FIXTURES / "testng-results-config.xml").read_text("utf-8"), "r")}
    # The step snapshot is another write (and needs a project); timing is the subject here.
    row, _, _ = await _insert({**cases["addsItem"], "steps": []})
    assert _columns(row) == {
        "started_at": _at(1), "finished_at": _at(3), "setup_ms": 120, "call_ms": 1600,
        "teardown_ms": 80, "worker_id": None, "timing_source": "report",
    }
    assert row.duration_ms == 1600  # unchanged: the method's own time


@pytest.mark.asyncio
async def test_a_case_without_timing_writes_null_timing_and_claims_no_source():
    """JUnit: "not reported", never a zero or a "now"."""
    (case, *_rest) = parse_testng_xml((SAMPLES / "junit" / "auth-suite.xml").read_text("utf-8"), "r")
    row, _, _ = await _insert(case)
    assert _columns(row) == dict.fromkeys(COLUMNS)
    assert row.duration_ms == 1234


@pytest.mark.asyncio
async def test_json_transport_timing_text_is_parsed_and_bounded():
    row, _, _ = await _insert({
        "test_name": "t", "status": "passed", "duration_ms": 5,
        "started_at": "2026-10-14T12:00:01.250+02:00", "finished_at": "2026-10-14T10:00:03Z",
        "setup_ms": -1, "call_ms": "1600", "teardown_ms": 10**9, "worker_id": "  gw3  ",
        "timing_source": "derived",
    })
    assert _columns(row) == {
        "started_at": _at(1, 250), "finished_at": _at(3), "setup_ms": None, "call_ms": 1600,
        "teardown_ms": None, "worker_id": "gw3", "timing_source": "derived",
    }


@pytest.mark.asyncio
async def test_the_update_path_rewrites_timing_like_it_rewrites_duration():
    db = _FakeDB()
    run = SimpleNamespace(id=uuid.uuid4())
    timed = {"test_name": "t", "status": "failed", "duration_ms": 100,
             "started_at": _at(1), "finished_at": _at(2), "worker_id": "1"}
    row = await ingestion._upsert_test_case(db, timed, run, existing=None, fingerprint="fp")
    assert (row.started_at, row.worker_id, row.timing_source) == (_at(1), "1", "report")

    retry = {**timed, "status": "passed", "started_at": _at(5), "finished_at": _at(6), "worker_id": "2"}
    again = await ingestion._upsert_test_case(db, retry, run, existing=row, fingerprint="fp")
    assert again is row
    assert (row.started_at, row.finished_at, row.worker_id) == (_at(5), _at(6), "2")

    # A corrected re-ingest that no longer reports timing does not keep the old one.
    plain = {"test_name": "t", "status": "passed", "duration_ms": 90}
    await ingestion._upsert_test_case(db, plain, run, existing=row, fingerprint="fp")
    assert _columns(row) == dict.fromkeys(COLUMNS)


@pytest.mark.asyncio
async def test_sanitizing_the_case_does_not_redact_its_datetime_start():
    """The sanitizer turns non-JSON values into a redaction marker; timing is read
    from the case itself, so a parser's ``datetime`` survives."""
    row, _, _ = await _insert({"test_name": "t", "status": "passed", "started_at": _at(1)})
    assert row.started_at == _at(1)


# ── MinIO sentinel path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_sentinel_parses_testng_with_its_timing(monkeypatch):
    from app.models.postgres import Project as ProjectModel
    from app.models.schemas import SentinelFile
    from app.services import cache_service

    project_id = uuid.uuid4()
    run_row = SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, build_number="b1",
        ingestion_attempted_tests=0, ingestion_rejected_tests=0,
        ingestion_complete=False, ingestion_rejection_reasons=None,
        ocp_pod_name=None, ocp_namespace=None, start_time=None,
    )
    project_row = ProjectModel(id=project_id, name="p", slug="p")

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, stmt, *_a, **_k):
            result = MagicMock()
            result.scalar_one_or_none.return_value = project_row if "projects" in str(stmt) else None
            result.scalars.return_value.all.return_value = []
            return result

        async def commit(self):
            return None

        async def rollback(self):
            return None

        def begin_nested(self):
            # The savepoint the sentinel path wraps the suite rows and the duration check in.
            return self

    xml = (FIXTURES / "testng-results-config.xml").read_bytes()

    async def _list(prefix):
        return [{"Key": f"{prefix}testng-results.xml"}] if prefix.endswith("testng/") else []

    storage = SimpleNamespace(list_objects=_list, get_object_content=AsyncMock(return_value=xml))
    upserted: list[dict] = []

    async def _capture(_db, case, _run, **_kwargs):
        upserted.append(case)
        return SimpleNamespace(test_fingerprint="fp")

    log = MagicMock()
    monkeypatch.setattr(ingestion, "logger", log)
    monkeypatch.setattr(ingestion, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(ingestion, "_upsert_test_run", AsyncMock(return_value=run_row))
    monkeypatch.setattr(ingestion, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(ingestion, "_prefetch_test_cases", AsyncMock(return_value={}))
    monkeypatch.setattr(ingestion, "_upsert_test_case", _capture)
    monkeypatch.setattr(ingestion, "_update_run_aggregates", AsyncMock())
    monkeypatch.setattr("app.services.run_downstream_outbox.stage_finalize_operations", AsyncMock())
    monkeypatch.setattr(cache_service, "invalidate_analytics_cache", AsyncMock())
    timings_seen: list[dict] = []

    async def _suite_rows(_db, run_id, report_timings):
        timings_seen.append(report_timings)

    monkeypatch.setattr("app.services.suite_execution_service.upsert_for_run", _suite_rows)
    duration_check = AsyncMock()
    monkeypatch.setattr("app.services.duration_regression_service.evaluate_duration_regressions", duration_check)

    await ingestion.process_sentinel(
        SentinelFile(project_id=str(project_id), build_number="b1", ocp_pod_name=None,
                     ocp_namespace=None, release_name=None),
        "prefix/",
    )

    by_name = {case["test_name"]: case for case in upserted}
    assert set(by_name) == {"addsItem", "appliesCoupon", "paysByCard", "paysByVoucher"}
    assert (by_name["addsItem"]["started_at"], by_name["addsItem"]["setup_ms"]) == (_at(1), 120)
    # The report's run and suite timing reach the suite rows (EXEC-02), once, and the duration check runs after them.
    # (This used to look for a "sentinel_report_timings" log line no version of process_sentinel ever wrote.)
    assert len(timings_seen) == 1
    assert len(timings_seen[0]["suites"]) == 1  # TestNG has suite clocks, no run clock: the run's wall clock is derived
    duration_check.assert_awaited_once()
    failed = [c.args[0] for c in log.warning.call_args_list if c.args and "sentinel_" in str(c.args[0])]
    assert failed == []


# ── Upload worker dispatch ─────────────────────────────────────────────────


@pytest.mark.parametrize("fmt, fixture", [
    ("testng", FIXTURES / "testng-results-config.xml"),
    ("junit", FIXTURES / "junit-jest-suites.xml"),
    ("nunit", FIXTURES / "nunit3-cart.xml"),
    ("trx", FIXTURES / "trx-cart.trx"),
    ("xunit", FIXTURES / "xunit-v2-cart.xml"),
])
def test_the_timing_dispatch_returns_the_plain_dispatchs_results_and_the_timings(fmt, fixture):
    from app.worker.tasks import _parse_file_to_results, _parse_file_to_results_with_timings

    content = fixture.read_text("utf-8")
    results, timings = _parse_file_to_results_with_timings(content, fmt, fixture.name, "run-1")
    assert results == _parse_file_to_results(content, fmt, fixture.name, "run-1")
    assert timings["suites"] or timings["run_wall_ms"] is not None


def test_a_lone_allure_result_file_has_no_report_timing():
    import json

    from app.services.report_timings import empty_timings
    from app.worker.tasks import _parse_file_to_results_with_timings

    content = json.dumps({"uuid": "a", "name": "T", "status": "passed", "start": 1, "stop": 2})
    results, timings = _parse_file_to_results_with_timings(content, "allure", "a-result.json", "run-1")
    assert len(results) == 1 and timings == empty_timings()


def _b64_zip(entries: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_an_archive_merges_its_files_timings():
    from app.worker.tasks import _parse_archive_to_results, _parse_file_to_results_with_timings

    archive = _b64_zip({
        "reports/TEST-jest.xml": (FIXTURES / "junit-jest-suites.xml").read_bytes(),
        "reports/TEST-auth.xml": (SAMPLES / "junit" / "auth-suite.xml").read_bytes(),
        "reports/not-a-report.txt": b"hello",
    })
    results, timings = _parse_file_to_results_with_timings(archive, "archive", "reports.zip", "run-1")
    assert len(results) == 9
    assert results == _parse_archive_to_results(archive, "reports.zip", "run-1")
    assert {s["name"]: s["wall_ms"] for s in timings["suites"]} == {
        "cart.test.js": 3002, "payment.test.js": 1204, "AuthSuite": 12456,
    }
    # One file states its run time, the other does not: a partial sum would mislead.
    assert timings["run_wall_ms"] is None


def test_the_upload_task_parses_through_the_timing_dispatch():
    import inspect

    from app.worker import tasks

    source = inspect.getsource(tasks.ingest_uploaded_file)
    assert "results, report_timings = _parse_file_to_results_with_timings(" in source
