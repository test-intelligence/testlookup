"""W24 ADM-05 — ``testlookup projects export`` / ``projects import`` over a mocked wire.

* export: the job is polled to the end and the archive is STREAMED to the file;
  a 409 ``export_running`` follows the running job; nothing is overwritten
  without ``--force``;
* import: oversize / empty / non-ZIP / missing files are refused locally with
  NO request; the deterministic ``client_token`` (file hash + name) makes a
  re-run create ONE project and ``--new-copy`` a second one; a 200 with a
  finished job reads "Already imported as …"; the report reads in the singular
  for 1; a structured 4xx prints its sentence and exits 1.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.commands import projects

PROJECT_ID = str(uuid.uuid4())
JOB_ID = str(uuid.uuid4())
EXPORT_PATH = f"/api/v1/projects/{PROJECT_ID}/export"
JOB_PATH = f"/api/v1/project-data-jobs/{JOB_ID}"
IMPORT_PATH = "/api/v1/projects/import"


def _zip_bytes(payload: bytes = b"{}") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", payload)
    return buf.getvalue()


def _job(status: str, **extra: Any) -> dict[str, Any]:
    return {"id": JOB_ID, "kind": extra.pop("kind", "export"), "status": status, **extra}


@pytest.fixture
def wire(monkeypatch):
    """Route every request through ``handler``; record what was sent."""
    seen: list[httpx.Request] = []
    state: dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return state["handler"](request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        client.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(transport_handler), **kw),
    )
    monkeypatch.setattr(client, "get_profile", lambda _name=None: {
        "url": "http://testserver", "auth_type": "api_key", "api_key": "tl_test_key",
    })

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(projects, "_sleep", no_sleep)

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
        state["handler"] = handler
        return seen

    return install


def _run(*argv: str) -> SimpleNamespace:
    """Invoke the CLI; ``output`` has its whitespace collapsed (the console wraps long lines)."""
    result = CliRunner().invoke(app, list(argv))
    return SimpleNamespace(exit_code=result.exit_code, output=" ".join(result.output.split()))


def _form_field(request: httpx.Request, name: str) -> str | None:
    match = re.search(rb'name="' + name.encode() + rb'"\r\n\r\n([^\r]*)\r\n', request.content)
    return match.group(1).decode() if match else None


# ── export ───────────────────────────────────────────────────────────────────


def _export_handler(
    archive: bytes, *, post: httpx.Response | None = None, counts: dict[str, int] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    polls = {"n": 0}
    finished_counts = counts if counts is not None else {"test_suites": 3, "managed_test_cases": 8, "releases": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        project = {"id": PROJECT_ID, "name": "Payment Service", "slug": "payment-service"}
        if path == "/api/v1/projects":
            return httpx.Response(200, json=[project])
        if path == f"/api/v1/projects/{PROJECT_ID}" and request.method == "GET":
            return httpx.Response(200, json=project)
        if path == EXPORT_PATH and request.method == "POST":
            return post or httpx.Response(202, json=_job("queued"))
        if path == JOB_PATH:
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(200, json=_job("running", progress={"done": 12, "total": 30}))
            return httpx.Response(200, json=_job(
                "succeeded", counts=finished_counts,
                bytes=len(archive), finished_at="2026-10-30T12:05:00+00:00",
            ))
        if path == f"{JOB_PATH}/download":
            return httpx.Response(200, content=archive, headers={"Content-Type": "application/zip"})
        return httpx.Response(404, json={"detail": "Not found"})

    return handler


def test_export_polls_to_the_end_and_streams_the_archive_to_the_file(wire, tmp_path):
    archive = _zip_bytes(b'{"format": "testlookup.project-export"}' * 50)
    seen = wire(_export_handler(archive))
    dest = tmp_path / "payments.zip"
    result = _run("projects", "export", "Payment Service", "-o", str(dest))
    assert result.exit_code == 0, result.output
    assert dest.read_bytes() == archive
    assert not (tmp_path / "payments.zip.part").exists()
    assert "Exported Payment Service: 12 rows in 3 tables" in result.output
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/projects"), ("POST", EXPORT_PATH), ("GET", JOB_PATH), ("GET", JOB_PATH),
        ("GET", f"{JOB_PATH}/download"),
    ]
    assert all(r.headers["x-api-key"] == "tl_test_key" for r in seen)


# W25a (W24 F-8): the job's ``counts`` carries every exported table, zeros included; the line counts only the
# tables that hold rows (as the import line and the Export card do), in the singular for 1.
@pytest.mark.parametrize(("counts", "line"), [
    ({"test_suites": 3, "managed_test_cases": 8, "releases": 0}, "Exported Payment Service: 11 rows in 2 tables ("),
    ({"test_suites": 0, "releases": 1}, "Exported Payment Service: 1 row in 1 table ("),
    ({"test_suites": 0, "releases": 0}, "Exported Payment Service: 0 rows in 0 tables ("),
])
def test_the_export_line_counts_only_tables_with_rows(wire, tmp_path, counts, line):
    archive = _zip_bytes()
    wire(_export_handler(archive, counts=counts))
    result = _run("projects", "export", "Payment Service", "-o", str(tmp_path / "payments.zip"))
    assert result.exit_code == 0, result.output
    assert line in result.output


def test_export_default_file_name_is_slug_and_utc_stamp(wire, tmp_path, monkeypatch):
    archive = _zip_bytes()
    wire(_export_handler(archive))
    monkeypatch.chdir(tmp_path)
    result = _run("projects", "export", PROJECT_ID.upper())   # a UUID in any case resolves by id
    assert result.exit_code == 0, result.output
    assert (tmp_path / "payment-service-settings-catalog-20261030-1205.zip").read_bytes() == archive


def test_a_running_export_is_followed_not_restarted(wire, tmp_path):
    archive = _zip_bytes()
    conflict = httpx.Response(409, json={"detail": {
        "code": "export_running", "message": "An export of this project is already running.", "job_id": JOB_ID}})
    seen = wire(_export_handler(archive, post=conflict))
    dest = tmp_path / "x.zip"
    result = _run("projects", "export", "payment-service", "-o", str(dest))
    assert result.exit_code == 0, result.output
    assert "An export of this project is already running." in result.output
    assert dest.read_bytes() == archive
    assert [r.method for r in seen].count("POST") == 1


def test_export_never_overwrites_without_force_and_sends_nothing(wire, tmp_path):
    dest = tmp_path / "x.zip"
    dest.write_bytes(b"keep me")
    seen = wire(_export_handler(_zip_bytes()))
    result = _run("projects", "export", PROJECT_ID, "-o", str(dest))
    assert result.exit_code == 1
    assert "already exists" in result.output and dest.read_bytes() == b"keep me"
    assert [r.method for r in seen] == ["GET"]      # the project lookup only; no export was started


def test_no_wait_prints_the_job_id(wire):
    wire(_export_handler(_zip_bytes()))
    result = _run("projects", "export", PROJECT_ID, "--no-wait")
    assert result.exit_code == 0, result.output
    assert JOB_ID in result.output


def test_a_failed_export_prints_its_sentence_and_exits_1(wire, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/projects/{PROJECT_ID}":
            return httpx.Response(200, json={"id": PROJECT_ID, "name": "Payment Service", "slug": "payment-service"})
        if request.method == "POST":
            return httpx.Response(202, json=_job("queued"))
        return httpx.Response(200, json=_job("failed", error={
            "code": "export_too_large", "message": "This project's export is larger than 50 MB, the import limit."}))

    wire(handler)
    result = _run("projects", "export", PROJECT_ID, "-o", str(tmp_path / "x.zip"))
    assert result.exit_code == 1
    assert "larger than 50 MB" in result.output and not (tmp_path / "x.zip").exists()


# ── import ───────────────────────────────────────────────────────────────────

NEW_PROJECT = str(uuid.uuid4())
_REPORT = {
    "project_id": NEW_PROJECT, "project_name": "Payment Service (imported)", "project_slug": "payment-service-imported",
    "inserted": {"projects": 1, "test_suites": 3, "managed_test_cases": 8, "webhook_subscriptions": 0},
    "skipped": [{"entity": "test_plan_items", "column": "test_case_id", "reason": "row_unresolved", "count": 1}],
    "cleared": [{"entity": "managed_test_cases", "column": "assignee_id", "reason": "assignment", "count": 3},
                {"entity": "managed_test_cases", "column": "author_id", "reason": "user_not_found", "count": 1}],
    "switched_off": {"webhook_subscriptions": 1, "run_cadences": 2},
    "signature": "valid", "source_project_name": "Payment Service", "exported_at": "2026-10-30T12:05:00+00:00",
}


def _import_handler(*, post_status: int = 202, post_job: dict[str, Any] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == IMPORT_PATH and request.method == "POST":
            return httpx.Response(post_status, json=post_job or _job("queued", kind="import"))
        if request.url.path == JOB_PATH:
            return httpx.Response(200, json=_job("succeeded", kind="import", import_report=_REPORT))
        return httpx.Response(404, json={"detail": "Not found"})

    return handler


@pytest.mark.parametrize(
    ("make", "sentence"),
    [
        (lambda p: p.write_bytes(b"PK\x03\x04" + b"x" * 64), projects.UPLOAD_TOO_LARGE_HISTORY),
        (lambda p: p.write_bytes(b"<xml/>"), projects.NOT_A_ZIP),
        (lambda p: p.write_bytes(b""), projects.UPLOAD_EMPTY),
        (lambda p: p.mkdir(), "Not a file"),
    ],
    ids=["over-2-gb", "not-zip-magic", "empty", "a-directory"],
)
def test_import_refuses_locally_with_no_request(wire, tmp_path, monkeypatch, make, sentence):
    # W30: over 50 MB is no longer a refusal (it goes up in parts); over 2 GB is — scaled down to 32 bytes here.
    monkeypatch.setattr(projects, "IMPORT_UPLOAD_MAX_BYTES", 16)
    monkeypatch.setattr(projects, "IMPORT_ARCHIVE_MAX_BYTES", 32)
    target = tmp_path / "export.zip"
    make(target)
    seen = wire(_import_handler())
    result = _run("projects", "import", str(target))
    assert result.exit_code == 1
    assert sentence in result.output
    assert seen == []


def test_the_token_is_deterministic_and_new_copy_breaks_it(wire, tmp_path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_zip_bytes())
    seen = wire(_import_handler())
    assert _run("projects", "import", str(archive)).exit_code == 0
    assert _run("projects", "import", str(archive)).exit_code == 0
    assert _run("projects", "import", str(archive), "--new-copy").exit_code == 0
    assert _run("projects", "import", str(archive), "--name", "Payments sandbox").exit_code == 0
    tokens = [_form_field(r, "client_token") for r in seen if r.method == "POST"]
    assert tokens[0] == tokens[1] == str(projects.import_client_token(archive, None))
    assert len({tokens[0], tokens[2], tokens[3]}) == 3
    assert all(uuid.UUID(t) for t in tokens)
    names = [_form_field(r, "name") for r in seen if r.method == "POST"]
    assert names == [None, None, None, "Payments sandbox"]
    assert all(b'name="file"; filename="export.zip"' in r.content for r in seen if r.method == "POST")


def test_an_import_prints_the_report_in_the_pages_sentences(wire, tmp_path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_zip_bytes())
    wire(_import_handler())
    result = _run("projects", "import", str(archive))
    assert result.exit_code == 0, result.output
    assert f"Created Payment Service (imported) ({NEW_PROJECT})." in result.output
    assert "Inserted 12 rows in 3 tables:" in result.output
    assert "test_plan_items.test_case_id: 1 row was skipped: it pointed at a row that is not in the export" in result.output
    assert "managed_test_cases.assignee_id: 3 assignments were cleared" in result.output
    assert "managed_test_cases.author_id: 1 value was cleared: the person does not exist here" in result.output
    assert "webhook_subscriptions: 1 row switched off — secret required" in result.output
    assert "run_cadences: 2 rows paused for 90 days" in result.output
    assert projects.INERT_NOTE in result.output


def test_a_repeated_import_reads_already_imported(wire, tmp_path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_zip_bytes())
    seen = wire(_import_handler(post_status=200, post_job=_job("succeeded", kind="import", import_report=_REPORT)))
    result = _run("projects", "import", str(archive))
    assert result.exit_code == 0, result.output
    assert f"Already imported as Payment Service (imported) ({NEW_PROJECT})." in result.output
    assert [r.method for r in seen] == ["POST"]          # nothing polled


def test_a_structured_refusal_prints_its_sentence_and_exits_1(wire, tmp_path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_zip_bytes())
    sentence = "A project called Payments already exists; choose another name."
    wire(lambda request: httpx.Response(409, json={"detail": {"code": "name_taken", "message": sentence}}))
    result = _run("projects", "import", str(archive), "--name", "Payments")
    assert result.exit_code == 1
    assert sentence in result.output
    assert "'code'" not in result.output and "name_taken" not in result.output


def test_a_failed_import_job_prints_its_sentence(wire, tmp_path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_zip_bytes())
    sentence = "The archive's signature is not valid; nothing was imported."

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json=_job("queued", kind="import"))
        return httpx.Response(200, json=_job("failed", kind="import", error={"code": "signature_invalid", "message": sentence}))

    wire(handler)
    result = _run("projects", "import", str(archive))
    assert result.exit_code == 1 and sentence in result.output


def test_report_lines_read_in_the_singular_for_one():
    one = projects.report_lines({"skipped": [{"entity": "t", "reason": "row_unresolved", "count": 1}],
                                 "cleared": [{"entity": "t", "column": "c", "reason": "assignment", "count": 1}],
                                 "switched_off": {"t": 1}})
    many = projects.report_lines({"skipped": [{"entity": "t", "reason": "row_unresolved", "count": 2}],
                                  "cleared": [{"entity": "t", "column": "c", "reason": "assignment", "count": 2}],
                                  "switched_off": {"t": 2}})
    assert one == ["t: 1 row was skipped: it pointed at a row that is not in the export",
                   "t.c: 1 assignment was cleared", "t: 1 row switched off"]
    assert many == ["t: 2 rows were skipped: it pointed at a row that is not in the export",
                    "t.c: 2 assignments were cleared", "t: 2 rows switched off"]


def test_a_reset_review_and_a_paused_cadence_are_not_called_switched_off():
    """W24 demo: the CLI printed "managed_test_cases.review_reset: 1 row arrived switched off" and the same for a
    paused cadence; each entity now reads what happened to it, in the page's words."""
    lines = projects.report_lines({"switched_off": {"managed_test_cases.review_reset": 1, "run_cadences": 1,
                                                    "releases.deactivated": 2, "github_integrations": 1}})
    assert lines == [
        "github_integrations: 1 row switched off — secret required",
        "managed_test_cases.review_reset: 1 row returned to draft — review requests are not exported",
        "releases.deactivated: 2 rows set inactive — one active release is kept",
        "run_cadences: 1 row paused for 90 days",
    ]


def test_the_cli_version_is_0_4_0():
    import testlookup_cli

    assert testlookup_cli.__version__ == "0.4.0"


# ── W30 ADM-05 phase 2: --history export, the parted resumable import, the history report ──

UPLOADS_PATH = "/api/v1/project-imports/uploads"
PARTS_PREFIX = f"{UPLOADS_PATH}/{JOB_ID}/parts/"
COMPLETE_PATH = f"{UPLOADS_PATH}/{JOB_ID}/complete"
_HISTORY_COUNTS = {
    "test_suites": 3, "test_runs": 2, "test_cases": 40, "releases": 0,
    "documents.raw_allure_json": 4, "documents.run_summaries": 1, "objects": 1,
}


def test_export_with_history_sends_the_body_and_names_the_file(wire, tmp_path, monkeypatch):
    archive = _zip_bytes()
    seen = wire(_export_handler(archive, counts=_HISTORY_COUNTS))
    monkeypatch.chdir(tmp_path)
    result = _run("projects", "export", "Payment Service", "--history", "--days", "365")
    assert result.exit_code == 0, result.output
    post = next(r for r in seen if r.method == "POST")
    assert json.loads(post.content) == {"include_history": True, "history_days": 365, "max_runs": 2000}
    assert (tmp_path / "payment-service-project-data-20261030-1205.zip").read_bytes() == archive
    assert "Exported Payment Service: 2 runs, 45 rows, 5 documents, 1 attachment (" in result.output


def test_export_history_all_days_sends_null_and_the_max_runs(wire, tmp_path):
    seen = wire(_export_handler(_zip_bytes(), counts={"test_runs": 1, "documents.run_summaries": 1, "objects": 0}))
    result = _run("projects", "export", PROJECT_ID, "--history", "--days", "all", "--max-runs", "50",
                  "-o", str(tmp_path / "h.zip"))
    assert result.exit_code == 0, result.output
    post = next(r for r in seen if r.method == "POST")
    assert json.loads(post.content) == {"include_history": True, "history_days": None, "max_runs": 50}
    assert "Exported Payment Service: 1 run, 1 row, 1 document, 0 attachments (" in result.output


def test_export_without_history_stays_body_less(wire, tmp_path):
    seen = wire(_export_handler(_zip_bytes()))
    assert _run("projects", "export", PROJECT_ID, "-o", str(tmp_path / "c.zip")).exit_code == 0
    post = next(r for r in seen if r.method == "POST")
    assert post.content == b""                       # the W24 request, byte for byte: the server reads schema 1


@pytest.mark.parametrize("argv", [("--days", "7"), ("--max-runs", "0"), ("--max-runs", "2001")])
def test_a_bad_history_window_is_refused_with_no_request(wire, argv):
    seen = wire(_export_handler(_zip_bytes()))
    result = _run("projects", "export", PROJECT_ID, "--history", *argv)
    assert result.exit_code == 1
    assert ("Choose 30, 90 or 365 days" in result.output) or ("max_runs must be between 1 and 2000." in result.output)
    assert seen == []


_PART = 16                                   # IMPORT_PART_MAX_BYTES scaled down: 12 parts of a 187-byte file
_HISTORY_REPORT = {
    **_REPORT,
    "history": {
        "runs": 12, "test_cases": 480, "oldest_run_at": "2026-08-05T09:00:00+00:00",
        "newest_run_at": "2026-11-01T18:30:00Z",
        "documents": {"raw_allure_json": 400, "run_summaries": 12, "decision_reports": 0},
        "objects": 1, "object_bytes": 2048,
        "skipped": [{"entity": "objects", "reason": "unsafe_key", "count": 1}],
        "retention_paused_until": "2027-02-01T10:00:00+00:00", "runs_past_retention": 3,
        "recent_runs": 2, "search_reindex": "queued",
    },
}


@pytest.fixture
def big_archive(tmp_path, monkeypatch):
    """A 187-byte "ZIP" that must go up in 12 parts (the 50 MB / 32 MB caps scaled to 64 / 16 bytes)."""
    monkeypatch.setattr(projects, "IMPORT_UPLOAD_MAX_BYTES", 64)
    monkeypatch.setattr(projects, "IMPORT_PART_MAX_BYTES", _PART)
    path = tmp_path / "history.zip"
    path.write_bytes(b"PK\x03\x04" + bytes((i * 7) % 251 for i in range(183)))
    return path


def _parted_handler(*, received: list[int] | None = None, start_status: int = 201, start_job: dict | None = None,
                    put: Callable[[int], httpx.Response | None] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == UPLOADS_PATH and request.method == "POST":
            return httpx.Response(start_status, json=start_job or _job(
                "uploading", kind="import", upload={"parts": 12, "bytes": 187, "received": received or []}))
        if path.startswith(PARTS_PREFIX) and request.method == "PUT":
            part = int(path.rsplit("/", 1)[1])
            answer = put(part) if put else None
            return answer or httpx.Response(200, json={"part": part, "bytes": len(request.content),
                                                       "received": [part], "complete": False})
        if path == COMPLETE_PATH and request.method == "POST":
            return httpx.Response(202, json=_job("queued", kind="import"))
        if path == JOB_PATH:
            return httpx.Response(200, json=_job("succeeded", kind="import", import_report=_HISTORY_REPORT))
        return httpx.Response(404, json={"detail": "Not found"})

    return handler


def test_a_large_file_goes_up_in_parts_with_their_digests(wire, big_archive):
    seen = wire(_parted_handler())
    result = _run("projects", "import", str(big_archive))
    assert result.exit_code == 0, result.output
    data = big_archive.read_bytes()
    start = next(r for r in seen if r.url.path == UPLOADS_PATH)
    body = json.loads(start.content)
    assert body == {"client_token": str(projects.import_client_token(big_archive, None)), "bytes": 187,
                    "parts": 12, "sha256": hashlib.sha256(data).hexdigest()}
    puts = [r for r in seen if r.method == "PUT"]
    assert [int(r.url.path.rsplit("/", 1)[1]) for r in puts] == list(range(1, 13))
    for number, request in enumerate(puts, start=1):
        chunk = data[(number - 1) * _PART:number * _PART]
        assert request.content == chunk
        assert request.headers["x-part-sha256"] == hashlib.sha256(chunk).hexdigest()
        assert request.headers["content-type"] == "application/octet-stream"
    assert [r.url.path for r in seen if r.method == "POST"] == [UPLOADS_PATH, COMPLETE_PATH]
    assert not any(r.url.path == IMPORT_PATH for r in seen)        # never the 50 MB single POST
    assert f"Created Payment Service (imported) ({NEW_PROJECT})." in result.output


def test_a_resumed_upload_sends_only_the_missing_parts(wire, big_archive):
    seen = wire(_parted_handler(received=[1, 2, 3, 4, 5, 6, 7], start_status=200))
    result = _run("projects", "import", str(big_archive))
    assert result.exit_code == 0, result.output
    assert "Resuming upload: 7 of 12 parts already received." in result.output
    assert [int(r.url.path.rsplit("/", 1)[1]) for r in seen if r.method == "PUT"] == [8, 9, 10, 11, 12]


def test_a_part_conflict_prints_its_sentence_and_exits_1(wire, big_archive):
    sentence = "Part 3 was already received with different content."
    conflict = httpx.Response(409, json={"detail": {"code": "part_conflict", "message": sentence}})
    seen = wire(_parted_handler(put=lambda part: conflict if part == 3 else None))
    result = _run("projects", "import", str(big_archive))
    assert result.exit_code == 1
    assert sentence in result.output and "part_conflict" not in result.output
    assert [int(r.url.path.rsplit("/", 1)[1]) for r in seen if r.method == "PUT"] == [1, 2, 3]
    assert not any(r.url.path == COMPLETE_PATH for r in seen)


def test_a_finished_upload_of_the_same_file_reads_already_imported(wire, big_archive):
    done = _job("succeeded", kind="import", import_report=_HISTORY_REPORT)
    seen = wire(_parted_handler(start_status=200, start_job=done))
    result = _run("projects", "import", str(big_archive))
    assert result.exit_code == 0, result.output
    assert f"Already imported as Payment Service (imported) ({NEW_PROJECT})." in result.output
    assert [(r.method, r.url.path) for r in seen] == [("POST", UPLOADS_PATH)]


def test_a_history_import_prints_the_history_block(wire, big_archive):
    wire(_parted_handler())
    result = _run("projects", "import", str(big_archive))
    assert result.exit_code == 0, result.output
    assert "Run history: 12 runs from 2026-08-05 to 2026-11-01" in result.output
    assert "480 test results" in result.output
    assert "412 stored documents (raw_allure_json 400, run_summaries 12)" in result.output
    assert "1 attachment (2.0 KB)" in result.output
    assert "objects: 1 was not imported: its storage key is not a safe path" in result.output
    assert "Retention purge paused until 2027-02-01." in result.output
    assert "2 imported runs are from the last 7 days; an all-projects digest can list them." in result.output
    assert projects.REINDEX_QUEUED in result.output


def test_history_lines_read_in_the_singular_for_one():
    lines = projects.history_lines({
        "runs": 1, "test_cases": 1, "oldest_run_at": "2026-10-01T00:00:00+00:00",
        "newest_run_at": "2026-10-01T00:00:00+00:00", "documents": {"run_summaries": 1}, "objects": 0,
        "object_bytes": 0, "skipped": [], "recent_runs": 1, "search_reindex": "not_queued",
    })
    assert lines == [
        "Run history: 1 run from 2026-10-01 to 2026-10-01", "1 test result",
        "1 stored document (run_summaries 1)", "0 attachments (0 B)",
        "1 imported run is from the last 7 days; an all-projects digest can list them.",
        projects.REINDEX_NOT_QUEUED,
    ]
    assert projects.history_lines(None) == []        # a catalog-only import prints no history block


def test_what_the_export_left_out_reads_in_the_servers_sentences():
    lines = projects.history_lines({
        "runs": 2, "test_cases": 0, "documents": {}, "objects": 0, "object_bytes": 0,
        "skipped": [{"entity": "evidence_artifacts", "reason": "pipeline_scratch", "count": 1},
                    {"entity": "agent_pipeline_runs", "reason": "pipeline_not_terminal", "count": 2}],
    })
    assert "evidence_artifacts: 1 was not imported: it pointed at a pipeline scratch file, which is not exported" in lines
    assert "agent_pipeline_runs: 2 were not imported: its AI pipeline was still running at export" in lines


# ── W37: --release, --include-members / --include-reviews, --add-members / --keep-reviews ──

RELEASE_ID = str(uuid.uuid4())
_PEOPLE_REPORT = {
    **_REPORT,
    "inserted": {**_REPORT["inserted"], "project_members": 2, "test_suite_owners": 1, "test_case_reviews": 4},
    "skipped": [{"entity": "project_members", "reason": "email_not_found", "count": 1},
                {"entity": "project_members", "reason": "email_ambiguous", "count": 2},
                {"entity": "project_members", "reason": "role_unknown", "count": 1},
                {"entity": "test_suite_owners", "reason": "owner_not_lead", "count": 1},
                {"entity": "test_case_comments", "reason": "not_chosen", "count": 3}],
    "switched_off": {"managed_test_cases.review_unclaimed": 1},
    "options_ignored": ["keep_reviews"],
}


def test_export_history_with_a_release_sends_the_release_and_no_day_window(wire, tmp_path):
    seen = wire(_export_handler(_zip_bytes(), counts=_HISTORY_COUNTS))
    result = _run("projects", "export", PROJECT_ID, "--history", "--release", RELEASE_ID, "-o", str(tmp_path / "r.zip"))
    assert result.exit_code == 0, result.output
    post = next(r for r in seen if r.method == "POST")
    assert json.loads(post.content) == {"include_history": True, "max_runs": 2000, "history_release_id": RELEASE_ID}


@pytest.mark.parametrize(("argv", "sentence"), [
    (("--release", RELEASE_ID), projects.RELEASE_NEEDS_HISTORY),
    (("--history", "--release", RELEASE_ID, "--days", "30"), projects.RELEASE_OR_DAYS),
    (("--history", "--release", "not-a-release"), "--release takes a release id"),
])
def test_a_release_without_history_with_days_or_not_an_id_is_refused_with_no_request(wire, argv, sentence):
    seen = wire(_export_handler(_zip_bytes()))
    result = _run("projects", "export", PROJECT_ID, *argv)
    assert result.exit_code == 1 and sentence in result.output
    assert seen == []


@pytest.mark.parametrize(("argv", "body"), [
    (("--include-members",), {"include_members": True}),
    (("--include-reviews",), {"include_reviews": True}),
    (("--include-members", "--include-reviews"), {"include_members": True, "include_reviews": True}),
    (("--history", "--days", "30", "--include-members"),
     {"include_history": True, "history_days": 30, "max_runs": 2000, "include_members": True}),
])
def test_include_members_and_reviews_are_sent_only_when_given(wire, tmp_path, argv, body):
    seen = wire(_export_handler(_zip_bytes()))
    result = _run("projects", "export", PROJECT_ID, *argv, "-o", str(tmp_path / "m.zip"))
    assert result.exit_code == 0, result.output
    post = next(r for r in seen if r.method == "POST")
    assert json.loads(post.content) == body


def test_the_import_options_are_form_fields_only_when_given_and_part_of_the_token(wire, tmp_path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_zip_bytes())
    seen = wire(_import_handler())
    assert _run("projects", "import", str(archive)).exit_code == 0
    assert _run("projects", "import", str(archive), "--add-members").exit_code == 0
    assert _run("projects", "import", str(archive), "--add-members", "--keep-reviews").exit_code == 0
    posts = [r for r in seen if r.method == "POST"]
    assert [(_form_field(r, "add_members"), _form_field(r, "keep_reviews")) for r in posts] == [
        (None, None), ("true", None), ("true", "true")]
    assert b'name="add_members"' not in posts[0].content and b'name="keep_reviews"' not in posts[0].content
    tokens = [_form_field(r, "client_token") for r in posts]
    assert tokens[0] == str(projects.import_client_token(archive, None))           # the W24 token, unchanged
    assert tokens[1] == str(projects.import_client_token(archive, None, options=("add_members",)))
    assert len(set(tokens)) == 3


def test_a_parted_import_sends_the_options_in_the_start_body_only_when_given(wire, big_archive):
    seen = wire(_parted_handler())
    assert _run("projects", "import", str(big_archive), "--keep-reviews").exit_code == 0
    start = json.loads(next(r for r in seen if r.url.path == UPLOADS_PATH).content)
    assert start["keep_reviews"] is True and "add_members" not in start
    assert start["client_token"] == str(projects.import_client_token(big_archive, None, options=("keep_reviews",)))


def test_the_people_and_review_report_lines_read_in_the_servers_sentences(wire, tmp_path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_zip_bytes())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json=_job("queued", kind="import"))
        return httpx.Response(200, json=_job("succeeded", kind="import", import_report=_PEOPLE_REPORT))

    wire(handler)
    result = _run("projects", "import", str(archive), "--add-members", "--keep-reviews")
    assert result.exit_code == 0, result.output
    for line in (
        "project_members: 1 row was skipped: no active account here has this e-mail address",
        "project_members: 2 rows were skipped: more than one account here has this e-mail address",
        "project_members: 1 row was skipped: its project role is not one this deployment knows",
        "test_suite_owners: 1 row was skipped: the owner is not a QA lead in the new project",
        "test_case_comments: 3 rows were skipped: the option to bring them was not chosen",
        "managed_test_cases.review_unclaimed: 1 row returned to Review requested — the reviewer is not a member here",
        projects.OPTIONS_IGNORED["keep_reviews"],
        "project_members: 2", "test_suite_owners: 1",
    ):
        assert line in result.output, line
    assert projects.OPTIONS_IGNORED["add_members"] not in result.output


def test_the_cli_mirrors_the_servers_w37_sentences():
    """The CLI installs without the backend; its copies must read exactly the server's (contract §2.3)."""
    assert {k: projects.SKIP_REASONS[k] for k in ("email_not_found", "email_ambiguous", "role_unknown",
                                                  "owner_not_lead", "not_chosen")} == {
        "email_not_found": "no active account here has this e-mail address",
        "email_ambiguous": "more than one account here has this e-mail address",
        "role_unknown": "its project role is not one this deployment knows",
        "owner_not_lead": "the owner is not a QA lead in the new project",
        "not_chosen": "the option to bring them was not chosen",
    }
    assert projects.OPTIONS_IGNORED == {
        "add_members": "Add members: this file lists no members — export it again with Include members.",
        "keep_reviews": "Keep reviews: this file has no reviews — export it again with Include reviews.",
    }
