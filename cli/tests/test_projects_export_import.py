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

import io
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


def _export_handler(archive: bytes, *, post: httpx.Response | None = None) -> Callable[[httpx.Request], httpx.Response]:
    polls = {"n": 0}

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
                "succeeded", counts={"test_suites": 3, "managed_test_cases": 8, "releases": 1},
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
        (lambda p: p.write_bytes(b"PK\x03\x04" + b"x" * 64), projects.UPLOAD_TOO_LARGE),
        (lambda p: p.write_bytes(b"<xml/>"), projects.NOT_A_ZIP),
        (lambda p: p.write_bytes(b""), projects.UPLOAD_EMPTY),
        (lambda p: p.mkdir(), "Not a file"),
    ],
    ids=["over-50-mib", "not-zip-magic", "empty", "a-directory"],
)
def test_import_refuses_locally_with_no_request(wire, tmp_path, monkeypatch, make, sentence):
    monkeypatch.setattr(projects, "IMPORT_UPLOAD_MAX_BYTES", 32)
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


def test_the_cli_version_is_0_3_0():
    import testlookup_cli

    assert testlookup_cli.__version__ == "0.3.0"
