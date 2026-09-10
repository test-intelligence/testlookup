"""One uploaded report may not carry more results than one JSON batch.

Re-audit M5. ``POST /api/v1/ingest`` caps a batch at 50,000 results in its
schema. ``POST /api/v1/ingest/file`` had no equivalent: its 50 MB limit bounds
bytes, not rows, and 50 MB of minimal JUnit elements is ~1.3M results. Parsing
that one file peaked at 1.3 GB with the real parser, past an ingestion worker
child's ~1 GB share, and every stage after parsing then ran once per row.

The cap is enforced by a cheap count before parsing -- with headroom, because
the count is an estimate -- and exactly after, on what was parsed.

Sizes below are literal numbers on purpose. Deriving them from
``PREPARSE_HEADROOM`` would let a mutated headroom resize its own test.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import uuid
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import MAX_RESULTS_PER_INGEST, Settings, settings
from app.models.schemas import IngestPayload
from app.services import upload_limits, upload_status
from app.services.upload_limits import PREPARSE_HEADROOM, TooManyResults
from tests.regression.test_parser_format_coverage import COVERAGE

pytestmark = pytest.mark.regression

CAP = 5  # every size below is written against this cap


@pytest.fixture
def cap(monkeypatch):
    monkeypatch.setattr(settings, "INGEST_MAX_RESULTS_PER_UPLOAD", CAP)
    return CAP


def _junit(n: int) -> str:
    cases = "".join(f'<testcase classname="c" name="t{i}"/>' for i in range(n))
    return f'<?xml version="1.0"?><testsuite name="s" tests="{n}">{cases}</testsuite>'


def _testng(real: int, config: int) -> str:
    methods = [
        f'<test-method status="PASS" name="t{i}" duration-ms="1"/>' for i in range(real)
    ] + [
        f'<test-method status="PASS" name="setUp{i}" is-config="true" duration-ms="1"/>'
        for i in range(config)
    ]
    return (
        '<?xml version="1.0"?><testng-results><suite name="s"><test name="t">'
        f'<class name="C">{"".join(methods)}</class></test></suite></testng-results>'
    )


def _zip(entries: dict[str, str]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Real report shapes the coverage fixtures do not have: a TRX that carries its
# <TestDefinitions> (what `dotnet test --logger trx` writes), and a Playwright
# report whose tests carry "expectedStatus".
TRX_WITH_DEFINITIONS = """<?xml version="1.0"?>
<TestRun id="r" name="run" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testId="id-1" testName="Adds" outcome="Passed" duration="00:00:00.1000000"/>
    <UnitTestResult testId="id-2" testName="Subtracts" outcome="Failed" duration="00:00:00.1000000">
      <Output><ErrorInfo><Message>boom</Message></ErrorInfo></Output>
    </UnitTestResult>
  </Results>
  <TestDefinitions>
    <UnitTest id="id-1" name="Adds"><TestMethod className="Calc.Tests" name="Adds"/></UnitTest>
    <UnitTest id="id-2" name="Subtracts"><TestMethod className="Calc.Tests" name="Subtracts"/></UnitTest>
  </TestDefinitions>
</TestRun>
"""
PLAYWRIGHT_WITH_EXPECTED_STATUS = json.dumps({
    "config": {"version": "1.47.0"},
    "suites": [{
        "title": "a.spec.ts", "file": "a.spec.ts", "suites": [],
        "specs": [{
            "title": "adds", "file": "a.spec.ts", "line": 3,
            "tests": [{
                "expectedStatus": "passed", "projectName": "chromium", "status": "expected",
                "results": [{"status": "passed", "duration": 5, "retry": 0}],
            }],
        }],
    }],
})
SAMPLES = COVERAGE + [
    ("trx", TRX_WITH_DEFINITIONS, "results.trx"),
    ("playwright", PLAYWRIGHT_WITH_EXPECTED_STATUS, "pw-results.json"),
]


# ── One number for both routes ───────────────────────────────────────────


def test_the_upload_cap_is_the_json_batch_cap():
    field = IngestPayload.model_fields["results"]
    json_cap = next(m.max_length for m in field.metadata if hasattr(m, "max_length"))
    assert json_cap == MAX_RESULTS_PER_INGEST
    assert Settings.model_fields["INGEST_MAX_RESULTS_PER_UPLOAD"].default == json_cap, (
        "an uploaded file may carry more results than a JSON batch"
    )


# ── Before parsing: the worst case never reaches the parser ─────────────


def test_a_report_far_over_the_cap_is_refused_unparsed(cap):
    from app.worker.tasks import _parse_file_to_results

    oversized = _junit(21)  # past 4x the cap of 5: far enough over to refuse unparsed
    with patch(
        "app.services.testng_parser.parse_testng_xml",
        side_effect=AssertionError("the report was parsed"),
    ):
        with pytest.raises(TooManyResults) as exc:
            _parse_file_to_results(oversized, "junit", "big.xml", "run-1")
    assert "INGEST_MAX_RESULTS_PER_UPLOAD" in exc.value.message


def test_the_estimate_never_refuses_what_the_exact_check_accepts(cap):
    """TestNG reports config methods as ``<test-method>`` too, and the count sees them.

    Five real tests and fifteen @BeforeMethod-style config methods: twenty
    markers for five results. Without headroom the estimate alone would refuse
    a report that is exactly at the cap.
    """
    from app.worker.tasks import _parse_file_to_results

    report = _testng(real=5, config=15)
    assert upload_limits.estimated_results(report, "testng") == 20

    results = _parse_file_to_results(report, "testng", "testng-results.xml", "run-1")
    assert len(results) == 5
    upload_limits.enforce_result_limit(len(results))  # at the cap: accepted


@pytest.mark.parametrize(
    "fmt,fixture,filename", SAMPLES, ids=[f"{fmt}-{name}" for fmt, _, name in SAMPLES]
)
def test_each_formats_count_tracks_its_real_results(fmt, fixture, filename):
    """A marker that matched nothing would silently switch the guard off for
    that format; one that matched far too much would refuse good reports."""
    from app.worker.tasks import _parse_file_to_results

    parsed = len(_parse_file_to_results(fixture, fmt, filename, "run-1"))
    counted = upload_limits.estimated_results(fixture, fmt)
    assert parsed > 0
    assert parsed <= counted <= parsed * PREPARSE_HEADROOM, (
        f"{fmt}: {counted} markers for {parsed} parsed results"
    )


def test_the_allure_count_tracks_its_real_results():
    from app.worker.tasks import _parse_file_to_results

    report = json.dumps([
        {"uuid": f"u{i}", "name": f"t{i}", "fullName": f"C.t{i}", "status": "passed"}
        for i in range(3)
    ])
    parsed = len(_parse_file_to_results(report, "allure", "results.json", "run-1"))
    counted = upload_limits.estimated_results(report, "allure")
    assert parsed > 0
    assert parsed <= counted <= parsed * PREPARSE_HEADROOM


# ── Archives are capped as a whole ───────────────────────────────────────


def test_an_archive_is_capped_as_a_whole(cap):
    """Entries each under the cap, together over it: refused, not merged."""
    from app.worker.tasks import _parse_archive_to_results

    archive = _zip({f"TEST-{i}.xml": _junit(2) for i in range(3)})  # 6 results
    with pytest.raises(TooManyResults):
        _parse_archive_to_results(archive, "reports.zip", "run-1")


def test_an_oversized_entry_is_not_skipped_as_a_bad_one(cap):
    """The archive loop skips entries that fail to parse. Skipping this one
    would drop its results and report the rest as the whole upload."""
    from app.worker.tasks import _parse_archive_to_results

    archive = _zip({"TEST-small.xml": _junit(1), "TEST-huge.xml": _junit(21)})
    with pytest.raises(TooManyResults):
        _parse_archive_to_results(archive, "reports.zip", "run-1")


def test_an_archive_within_the_cap_still_parses(cap):
    from app.worker.tasks import _parse_archive_to_results

    archive = _zip({"TEST-a.xml": _junit(2), "TEST-b.xml": _junit(3)})
    assert len(_parse_archive_to_results(archive, "reports.zip", "run-1")) == 5


# ── The upload task: refused whole, with a reason ────────────────────────


def _rows(n: int) -> list[dict]:
    return [{"test_name": f"t{i}", "status": "passed"} for i in range(n)]


def _run_upload(parsed_results: list[dict]) -> SimpleNamespace:
    """Drive the real upload task with everything around parsing stubbed."""
    from app.services import ingestion_pipeline
    from app.worker import tasks

    run = SimpleNamespace(
        id=uuid.uuid4(),
        ingestion_attempted_tests=len(parsed_results),
        ingestion_rejected_tests=0,
        ingestion_complete=True,
        ingestion_rejection_reasons=[],
    )
    statuses: list[dict] = []
    failures = MagicMock()

    @asynccontextmanager
    async def _session():
        yield AsyncMock()

    async def _status(_task_id, *, state, **kwargs):
        statuses.append({"state": state, **kwargs})

    create = AsyncMock(return_value=run)
    ingest = AsyncMock(return_value=len(parsed_results))
    with (
        patch.object(tasks, "_run_async", side_effect=asyncio.run),
        patch.object(tasks, "_archive_raw_upload", new=AsyncMock(return_value=None)),
        patch.object(tasks, "_parse_file_to_results", return_value=parsed_results),
        patch.object(ingestion_pipeline, "create_run_from_payload", new=create),
        patch.object(ingestion_pipeline, "ingest_test_results", new=ingest),
        patch.object(ingestion_pipeline, "finalize_run", new=AsyncMock()),
        patch("app.db.postgres.AsyncSessionLocal", _session),
        patch("app.services.upload_status.set_status", side_effect=_status),
        patch("app.core.metrics.uploads_total", MagicMock()),
        patch("app.core.metrics.upload_failures_total", failures),
    ):
        tasks.ingest_uploaded_file.run(
            run_id=str(run.id),
            file_name="report.xml",
            file_format="junit",
            project_id=str(uuid.uuid4()),
            build_number="build-1",
            file_content="<testsuite />",
            run_ai=False,
        )
    return SimpleNamespace(statuses=statuses, create=create, ingest=ingest, failures=failures)


def test_an_upload_over_the_cap_fails_with_a_reason_and_ingests_nothing(cap):
    out = _run_upload(_rows(6))

    final = out.statuses[-1]
    assert final["state"] == upload_status.STATE_FAILED
    assert final["error"]["code"] == "too_many_results"
    assert "6" in final["error"]["message"] and "5" in final["error"]["message"]
    out.create.assert_not_awaited()
    out.ingest.assert_not_awaited()
    out.failures.labels.assert_called_with(code="too_many_results")


def test_an_upload_at_the_cap_is_ingested(cap):
    out = _run_upload(_rows(5))

    out.ingest.assert_awaited_once()
    assert all(s["state"] != upload_status.STATE_FAILED for s in out.statuses)


def test_zero_turns_the_cap_off(monkeypatch):
    monkeypatch.setattr(settings, "INGEST_MAX_RESULTS_PER_UPLOAD", 0)
    upload_limits.enforce_result_limit(10**9)
    upload_limits.refuse_before_parsing(_junit(50), "junit")


# ── Unicode-escaped keys (code review and QA of M5) ──────────────────────

_MARKER_KEYS = {
    "pytest": "nodeid",
    "cypress": "fullTitle",
    "playwright": "results",
    "cucumber": "steps",
    "allure": "uuid",
}
_ALLURE = json.dumps([
    {"uuid": f"u{i}", "name": f"t{i}", "fullName": f"C.t{i}", "status": "passed"}
    for i in range(3)
])
_JSON_SAMPLES = [sample for sample in SAMPLES if sample[0] in _MARKER_KEYS] + [
    ("allure", _ALLURE, "results.json"),
]


def _escaped(text: str, key: str) -> str:
    """``text`` with every letter of the quoted ``key`` written as a unicode escape."""
    escaped = "".join(chr(92) + "u" + format(ord(ch), "04x") for ch in key)
    return text.replace('"' + key + '"', '"' + escaped + '"')


def test_every_json_format_is_covered_by_the_escape_check():
    """A JSON format added later must be decoded too, or its keys can be escaped unseen."""
    assert {fmt for fmt, _fixture, _name in _JSON_SAMPLES} == set(_MARKER_KEYS)
    assert set(_MARKER_KEYS) == set(upload_limits._JSON_FORMATS)


@pytest.mark.parametrize(
    "fmt,fixture,filename", _JSON_SAMPLES, ids=[f"{fmt}-{name}" for fmt, _, name in _JSON_SAMPLES]
)
def test_escaped_keys_are_counted_like_plain_ones(fmt, fixture, filename):
    from app.worker.tasks import _parse_file_to_results

    escaped = _escaped(fixture, _MARKER_KEYS[fmt])
    assert escaped != fixture, f"{fmt}: the sample has no {_MARKER_KEYS[fmt]!r} key to escape"
    # The parser reads the escaped report exactly as the plain one, so this is
    # a real way past the count, not a malformed file.
    assert len(_parse_file_to_results(escaped, fmt, filename, "run-1")) == len(
        _parse_file_to_results(fixture, fmt, filename, "run-1")
    )
    assert upload_limits.estimated_results(escaped, fmt) == upload_limits.estimated_results(
        fixture, fmt
    ), f"{fmt}: escaping the {_MARKER_KEYS[fmt]!r} key changed the count"


def test_a_report_that_escapes_its_keys_is_still_refused_unparsed(cap):
    """QA's probe, in miniature: one escaped letter in every "nodeid"."""
    tests = [{"nodeid": f"t.py::test_{i}", "outcome": "passed"} for i in range(21)]
    report = json.dumps({"tests": tests}).replace(
        '"nodeid"', '"' + chr(92) + 'u006eodeid"'
    )
    with pytest.raises(TooManyResults):
        upload_limits.refuse_before_parsing(report, "pytest")
