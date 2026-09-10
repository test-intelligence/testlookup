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
import re
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


# ── Namespace prefixes (QA of the M5 review) ─────────────────────────────
#
# The TRX parser matches elements by local name, so a report that writes
# ``<t:UnitTestResult>`` was parsed in full -- 2,000 results in QA's probe --
# and counted as 0. Whatever the names look like, the count may exceed what a
# parser keeps but never fall short of it.

TRX_NAMESPACE = "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"
_ELEMENT_NAME = re.compile(r"<(/?)([A-Za-z_][\w.-]*)")
_XML_FORMATS = set(upload_limits._RESULT_MARKERS) - set(upload_limits._JSON_FORMATS)
_XML_SAMPLES = [sample for sample in SAMPLES if sample[0] in _XML_FORMATS]
# Prefixes the XML parser accepts: a plain one, one with a digit, a dot and a
# dash, and a non-ASCII one. A marker that allows only some lets the rest by.
_PREFIXES = ["t", "v1.x-y", chr(0x442) + chr(0x435)]


def _prefixed(xml: str, uri: str, prefix: str) -> str:
    """``xml`` with every element named ``prefix:name``, the prefix bound to ``uri`` on its root."""
    root = _ELEMENT_NAME.search(xml).group(2)
    renamed = _ELEMENT_NAME.sub(lambda tag: f"<{tag.group(1)}{prefix}:{tag.group(2)}", xml)
    return renamed.replace(f"<{prefix}:{root}", f'<{prefix}:{root} xmlns:{prefix}="{uri}"', 1)


def _default_namespaced(xml: str, uri: str) -> str:
    """``xml`` with its root, and so every unprefixed element, in namespace ``uri``."""
    if ' xmlns="' in xml:
        return xml  # TRX already is: that is how MSTest writes it
    root = _ELEMENT_NAME.search(xml).group(2)
    return xml.replace(f"<{root}", f'<{root} xmlns="{uri}"', 1)


def _namespaced_samples():
    for fmt, fixture, filename in _XML_SAMPLES:
        uri = TRX_NAMESPACE if fmt == "trx" else "urn:example:results"
        for index, prefix in enumerate(_PREFIXES):
            yield pytest.param(
                fmt, _prefixed(fixture, uri, prefix), filename,
                id=f"{fmt}-{filename}-prefix{index}",
            )
        yield pytest.param(
            fmt, _default_namespaced(fixture, uri), filename, id=f"{fmt}-{filename}-default"
        )


def test_every_xml_format_has_a_sample_to_rewrite():
    """A new XML format needs a sample here, or its prefixed spelling goes unchecked."""
    assert {fmt for fmt, _fixture, _name in _XML_SAMPLES} == _XML_FORMATS


@pytest.mark.parametrize("fmt,report,filename", list(_namespaced_samples()))
def test_a_namespaced_report_counts_at_least_what_it_parses_to(fmt, report, filename):
    """TRX's parser reads a prefixed report in full, and the others read it as
    nothing: the count has to follow each one."""
    from app.worker.tasks import _parse_file_to_results

    parsed = len(_parse_file_to_results(report, fmt, filename, "run-1"))
    counted = upload_limits.estimated_results(report, fmt)
    assert counted >= parsed, f"{fmt}: {counted} markers for {parsed} parsed results"


@pytest.mark.parametrize("prefix", _PREFIXES, ids=["plain", "dotted", "non-ascii"])
def test_a_prefixed_trx_report_is_parsed_and_counted_like_a_plain_one(prefix):
    """Keeps the check above from passing vacuously: the TRX parser does read
    a prefixed report, result for result."""
    from app.worker.tasks import _parse_file_to_results

    trx_samples = [(fixture, name) for fmt, fixture, name in _XML_SAMPLES if fmt == "trx"]
    assert trx_samples
    for fixture, filename in trx_samples:
        report = _prefixed(fixture, TRX_NAMESPACE, prefix)
        plain = len(_parse_file_to_results(fixture, "trx", filename, "run-1"))
        assert plain > 0
        assert len(_parse_file_to_results(report, "trx", filename, "run-1")) == plain
        assert upload_limits.estimated_results(report, "trx") == upload_limits.estimated_results(
            fixture, "trx"
        ), f"a {prefix!r} prefix changed the TRX count"


def test_a_prefixed_trx_report_far_over_the_cap_is_refused_unparsed(cap):
    """QA's probe in miniature: 21 prefixed results against a cap of 5."""
    from app.worker.tasks import _parse_file_to_results

    rows = "".join(f'<t:UnitTestResult testName="t{i}" outcome="Passed"/>' for i in range(21))
    report = (
        f'<?xml version="1.0"?><t:TestRun xmlns:t="{TRX_NAMESPACE}">'
        f"<t:Results>{rows}</t:Results></t:TestRun>"
    )
    with patch(
        "app.services.trx_parser.parse_trx_xml",
        side_effect=AssertionError("the report was parsed"),
    ):
        with pytest.raises(TooManyResults):
            _parse_file_to_results(report, "trx", "results.trx", "run-1")
