"""Backend response correlation reaches human and structured CLI failures."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import client, config
from testlookup_cli.app import app
from testlookup_cli.commands import upload
from testlookup_cli.errors import CLIError, response_request_id, validated_request_id

_HTTP = httpx.AsyncClient
TASK = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"


def install(monkeypatch, handler, profiles=None):
    profiles = profiles or {None: {"url": "http://fixture", "auth_type": "api_key", "api_key": "synthetic-key"}}
    monkeypatch.setattr(client, "get_profile", lambda name=None: dict(profiles[name]))
    monkeypatch.setattr(upload, "get_profile", lambda name=None: dict(profiles[name]))
    monkeypatch.setattr(config, "get_profile", lambda name=None: dict(profiles[name]))
    monkeypatch.setattr(config, "get_active_profile_name", lambda: "synthetic-profile")
    monkeypatch.setattr(client, "save_profile", lambda *_args: None)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _HTTP(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize("value", ["a", "a" * 128, "support:job_42.req-7"])
def test_bounded_ascii_request_ids_are_preserved(value):
    assert validated_request_id(value) == value


@pytest.mark.parametrize("value", [None, 42, "", "a" * 129, " padded", "trailing ", "line\nnext", "\x1b[31mred", "é", "a,b", "[link=x]", "https://fixture"])
def test_malformed_request_ids_are_omitted(value):
    error = CLIError("Failure", request_id=value)
    assert error.request_id is None
    assert str(error) == "Failure"


def test_case_and_duplicate_headers_cannot_bypass_validation():
    assert response_request_id(SimpleNamespace(headers={"x-ReQuEsT-Id": "safe-id"})) == "safe-id"
    assert response_request_id(SimpleNamespace(headers={"X-Request-ID": "one", "x-request-id": "two"})) is None
    assert response_request_id(httpx.Response(500, headers=[("X-Request-ID", "one"), ("X-Request-ID", "two")])) is None
    assert response_request_id(SimpleNamespace(headers=None)) is None


@pytest.mark.parametrize("operation", ["request", "download", "download_to_file", "multipart", "part", "login", "logout"])
def test_real_shared_http_paths_preserve_error_metadata(monkeypatch, tmp_path, operation):
    def handler(request):
        return httpx.Response(403, request=request, headers={"x-request-id": "backend.final:7"}, json={"detail": "Not permitted"})

    install(monkeypatch, handler)
    report = tmp_path / "fixture.zip"
    report.write_bytes(b"synthetic-only")
    calls = {
        "request": lambda: client.request("GET", "/api/v1/projects"),
        "download": lambda: client.download("/api/v1/report"),
        "download_to_file": lambda: client.download_to_file("/api/v1/export", tmp_path / "out.zip"),
        "multipart": lambda: client.upload_file("/api/v1/import", report, {}),
        "part": lambda: client.upload_part("job", 0, b"fixture", "a" * 64),
        "login": lambda: client.login("http://fixture", "synthetic-user", "synthetic-password"),
        "logout": lambda: client.logout("http://fixture", "synthetic-token"),
    }
    with pytest.raises(CLIError) as caught:
        asyncio.run(calls[operation]())
    assert caught.value.request_id == "backend.final:7"
    assert str(caught.value).endswith("[request_id=backend.final:7]")
    assert caught.value.exit_code == (3 if operation == "login" else 4)
    if isinstance(caught.value, client.APIError):
        assert caught.value.detail == "Not permitted"
        assert caught.value.status_code == 403
    assert not (tmp_path / "out.zip.part").exists()


@pytest.mark.parametrize("final_id", ["final-request", None])
@pytest.mark.parametrize("operation", ["request", "multipart", "part"])
def test_auth_retry_uses_final_response_id_only(monkeypatch, tmp_path, final_id, operation):
    seen = []

    def handler(request):
        if request.url.path.endswith("/refresh"):
            return httpx.Response(200, headers={"X-Request-ID": "refresh-request"}, json={"access_token": "new-token"})
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, headers={"X-Request-ID": "first-request"}, json={"detail": "Expired"})
        return httpx.Response(403, headers={"X-Request-ID": final_id} if final_id else {}, json={"detail": "Denied"})

    install(monkeypatch, handler, {None: {"url": "http://fixture", "auth_type": "jwt", "access_token": "old-token", "refresh_token": "refresh-token"}})
    report = tmp_path / "fixture.zip"
    report.write_bytes(b"synthetic-only")
    calls = {
        "request": lambda: client.request("GET", "/api/v1/projects"),
        "multipart": lambda: client.upload_file("/api/v1/import", report, {}),
        "part": lambda: client.upload_part("job", 0, b"fixture", "a" * 64),
    }
    with pytest.raises(CLIError) as caught:
        asyncio.run(calls[operation]())
    assert caught.value.request_id == final_id
    assert "first-request" not in str(caught.value) and "refresh-request" not in str(caught.value)
    assert seen == ["Bearer old-token", "Bearer new-token"]


def test_parallel_profiles_keep_credentials_and_response_ids_separate(monkeypatch):
    async def exercise():
        arrived = asyncio.Event()
        seen = []

        async def handler(request):
            seen.append((request.headers.get("X-API-Key"), request.headers.get("Authorization")))
            if len(seen) == 2:
                arrived.set()
            await asyncio.wait_for(arrived.wait(), 2)
            identity = "alice-response" if request.headers.get("X-API-Key") else "bob-response"
            return httpx.Response(403, headers={"X-Request-ID": identity}, json={"detail": "Denied"})

        install(monkeypatch, handler, {"alice": {"url": "http://fixture", "auth_type": "api_key", "api_key": "alice-key"}, "bob": {"url": "http://fixture", "auth_type": "jwt", "access_token": "bob-token"}})
        results = await asyncio.gather(*(client.request("GET", "/api/v1/projects", profile_name=name) for name in ("alice", "bob")), return_exceptions=True)
        assert [error.request_id for error in results] == ["alice-response", "bob-response"]
        assert set(seen) == {("alice-key", None), (None, "Bearer bob-token")}
        assert "bob-response" not in str(results[0]) and "alice-response" not in str(results[1])

    asyncio.run(exercise())


def test_non_json_error_and_invalid_success_body_preserve_header_id(monkeypatch):
    for status in (502, 200):
        install(monkeypatch, lambda request: httpx.Response(status, headers={"X-Request-ID": "proxy-request"}, text="upstream unavailable"))
        with pytest.raises(CLIError) as caught:
            asyncio.run(client.request("GET", "/api/v1/projects"))
        assert caught.value.request_id == "proxy-request"


def test_success_and_no_response_keep_their_existing_contracts(monkeypatch):
    install(monkeypatch, lambda request: httpx.Response(200, headers={"X-Request-ID": "success-request"}, json={"items": [1]}))
    assert asyncio.run(client.request("GET", "/api/v1/projects")) == {"items": [1]}

    def disconnected(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    install(monkeypatch, disconnected)
    with pytest.raises(CLIError) as caught:
        asyncio.run(client.request("GET", "/api/v1/projects"))
    assert caught.value.request_id is None and "request_id=" not in str(caught.value)
    assert caught.value.exit_code == 6


def test_doctor_json_keeps_compatible_fields_and_optional_request_id(monkeypatch):
    install(monkeypatch, lambda request: httpx.Response(503, headers={"X-Request-ID": "doctor-request"}, json={"detail": "Unavailable"}))
    result = CliRunner().invoke(app, ["doctor", "--output", "json"])
    assert result.exit_code == 1
    failure = next(check for check in json.loads(result.stdout)["checks"] if check["name"] == "server")
    assert failure["request_id"] == "doctor-request"
    assert failure["status"] == "fail" and "request_id=doctor-request" in failure["detail"]


@pytest.mark.parametrize("command", ["trends", "execution"])
def test_analytics_rate_limit_message_keeps_response_id(monkeypatch, command):
    install(monkeypatch, lambda request: httpx.Response(429, headers={"X-Request-ID": "analytics-limit"}, json={"detail": "Too many requests"}))
    result = CliRunner().invoke(app, ["analytics", command, "--project", RUN])
    assert result.exit_code == 1
    assert "Rate limited" in result.stderr and "[request_id=analytics-limit]" in result.stderr
    assert result.stdout == ""


def test_raw_upload_retry_reports_final_refusal_id(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"X-Request-ID": "shed-request", "Retry-After": "0"}, json={})
        return httpx.Response(400, headers={"X-Request-ID": "upload-refused"}, json={"detail": "Unsupported report"})

    install(monkeypatch, handler)
    report = tmp_path / "report.xml"
    report.write_text("<testsuite/>", encoding="utf-8")
    with pytest.raises(CLIError) as caught:
        asyncio.run(upload._upload_file(report, "project", "build"))
    assert caught.value.request_id == "upload-refused"
    assert "shed-request" not in str(caught.value)
    assert calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]


@pytest.mark.parametrize("status, body, expected", [(403, {"detail": "Denied"}, CLIError), (200, {"task_id": TASK, "run_id": RUN, "state": "pending"}, upload.UploadNotFinished)])
def test_poll_refusal_and_timeout_retain_current_response_id(monkeypatch, status, body, expected):
    install(monkeypatch, lambda request: httpx.Response(status, headers={"X-Request-ID": "poll-request"}, json=body))
    with pytest.raises(expected) as caught:
        asyncio.run(upload._wait_for_upload(TASK, timeout=0))
    assert caught.value.request_id == "poll-request"
    assert str(caught.value).count("[request_id=poll-request]") == 1


def test_processing_failure_uses_poll_header_metadata_not_body_claim(monkeypatch):
    body = {"task_id": TASK, "run_id": RUN, "state": "failed", "error": {"message": "Parsing refused"}, "request_id": "body-claim"}
    install(monkeypatch, lambda request: httpx.Response(200, headers={"X-Request-ID": "poll-failed"}, json=body))
    status = asyncio.run(upload._wait_for_upload(TASK, timeout=0))
    outcome = upload._outcome({"task_id": TASK, "run_id": RUN}, status)
    assert outcome["request_id"] == "poll-failed"
    with pytest.raises(CLIError, match="request_id=poll-failed"):
        upload._raise_if_refused(outcome)


def test_receipt_timeout_keeps_json_parseable_and_prints_request_id_once(monkeypatch, tmp_path):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(503, headers={"X-Upload-Task-ID": TASK, "X-Upload-Run-ID": RUN, "X-Request-ID": "dispatch-request"}, json={})
        return httpx.Response(200, headers={"X-Request-ID": "last-poll"}, json={"task_id": TASK, "run_id": RUN, "state": "pending"})

    install(monkeypatch, handler)
    clock = [0.0]

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(upload, "_clock", lambda: clock[0])
    monkeypatch.setattr(upload, "_sleep", sleep)
    report = tmp_path / "report.xml"
    report.write_text("<testsuite/>", encoding="utf-8")
    result = CliRunner().invoke(app, ["upload", "file", str(report), "-p", "project", "-b", "build", "--wait", "--wait-timeout", "1", "--no-commit-range", "--no-run-context", "-o", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert (payload["task_id"], payload["run_id"], payload["request_id"]) == (TASK, RUN, "last-poll")
    assert result.stderr.count("[request_id=last-poll]") == 1
