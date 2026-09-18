"""Form-body cap and Range stripping, through the real app (Trivy on PR #26).

starlette CVE-2026-54283: request.form() ignores its limits for urlencoded
bodies, and POST /api/v1/auth/login takes one without authentication.
starlette CVE-2025-62727: Range handling in FileResponse; the unauthenticated
SDK download returns one. Both are mitigated in app/middleware/request_hardening.py
until the FastAPI/Starlette upgrade.
"""
from __future__ import annotations

import io
import site
import subprocess
import sys
import time
import tomllib
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

pytest.importorskip("asyncpg")

from app.core.config import settings  # noqa: E402
from app.db.postgres import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.middleware.request_hardening import FormBodyLimitMiddleware  # noqa: E402

CAP = settings.FORM_URLENCODED_MAX_BYTES
FORM = {"content-type": "application/x-www-form-urlencoded"}


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """Answers the login's user lookup with ``user``, everything else with None."""

    def __init__(self, user=None):
        self.user = user
        self.calls = 0

    async def execute(self, *_a, **_kw):
        self.calls += 1
        value = self.user if self.calls == 1 else datetime.now(UTC)
        return _Result(value)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def flush(self):
        pass

    def add(self, _obj):
        pass


@pytest.fixture
def db_calls():
    """Override get_db; records whether any handler dependency ran."""
    state = {"opened": 0, "session": _FakeSession()}

    async def _db():
        state["opened"] += 1
        yield state["session"]

    app.dependency_overrides[get_db] = _db
    yield state
    app.dependency_overrides.pop(get_db, None)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_cap_is_small_and_wired_from_the_setting() -> None:
    assert CAP == 64 * 1024
    [entry] = [m for m in app.user_middleware if m.cls is FormBodyLimitMiddleware]
    assert entry.kwargs["max_bytes"] == CAP
    assert entry.kwargs["path_limits"] == {"/api/v1/sso/acs": settings.FORM_URLENCODED_ACS_MAX_BYTES}


@pytest.mark.asyncio
async def test_a_1mib_urlencoded_login_is_refused_before_the_handler(db_calls) -> None:
    body = b"username=" + b"a" * (1024 * 1024) + b"&password=x"
    start = time.monotonic()
    async with _client() as client:
        response = await client.post("/api/v1/auth/login", content=body, headers=FORM)
    assert response.status_code == 413
    assert db_calls["opened"] == 0, "the handler's dependencies ran"
    assert time.monotonic() - start < 5


@pytest.mark.asyncio
async def test_a_chunked_urlencoded_body_with_no_content_length_is_refused(db_calls) -> None:
    """Raw ASGI, so there is certainly no Content-Length header."""
    chunk = b"a" * (16 * 1024)
    chunks = [b"username=" + chunk] + [chunk] * 16
    messages = [{"type": "http.request", "body": c, "more_body": True} for c in chunks]
    messages[-1]["more_body"] = False
    reads = {"n": 0}

    async def receive():
        reads["n"] += 1
        if messages:
            return messages.pop(0)
        # The body has ENDED (what a real client sends). Neither a disconnect
        # loop nor an endless await: both hang a mutant instead of failing it.
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login", "query_string": b"", "root_path": "",
        "headers": [(b"host", b"test"),
                    (b"content-type", b"application/x-www-form-urlencoded"),
                    (b"transfer-encoding", b"chunked")],
        "client": ("127.0.0.1", 1234), "server": ("test", 80),
    }
    import asyncio

    # A guard that stops refusing must fail this test fast, not hang it.
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    assert db_calls["opened"] == 0
    assert reads["n"] <= CAP // len(chunk) + 2, "kept reading past the cap"


@pytest.mark.asyncio
async def test_a_normal_login_still_works(db_calls, monkeypatch) -> None:
    from app.core.security import get_password_hash
    from app.models.postgres import User, UserRole
    from app.routers import auth as auth_router
    from app.services import mfa_service

    user = User(
        id=uuid.uuid4(), username="alice", email="alice@example.test",
        hashed_password=get_password_hash("correct horse"), is_active=True,
        role=UserRole.VIEWER.value, mfa_enabled=False, must_change_password=False,
        locked_until=None,
    )
    db_calls["session"] = _FakeSession(user)

    async def _refresh(*_a, **_kw):
        return "refresh-token"

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(auth_router, "issue_refresh_token", _refresh)
    monkeypatch.setattr(mfa_service, "register_successful_login", _noop)
    monkeypatch.setattr(auth_router.settings, "SSO_ENABLED", False)

    async with _client() as client:
        ok = await client.post("/api/v1/auth/login",
                               data={"username": "alice", "password": "correct horse"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"]
    assert db_calls["opened"] == 1


@pytest.mark.asyncio
async def test_a_multipart_upload_over_the_cap_is_not_refused_by_this_guard(db_calls) -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("report.xml", b"<testsuite>" + b"x" * (200 * 1024) + b"</testsuite>")},
            data={"build_number": "1"},
        )
    assert response.status_code != 413
    assert response.status_code in (401, 403), response.status_code  # reached auth


@pytest.mark.asyncio
async def test_a_json_body_over_the_cap_is_not_refused_by_this_guard(db_calls) -> None:
    async with _client() as client:
        response = await client.post("/api/v1/auth/register", json={"pad": "x" * (200 * 1024)})
    assert response.status_code != 413


@pytest.mark.asyncio
async def test_range_is_ignored_so_the_sdk_download_is_always_whole(tmp_path, monkeypatch) -> None:
    from app.routers import sdk as sdk_router

    payload = b"#" * 1000
    for filename in sdk_router._SDK_CONFIGS["python"]["files"]:
        (tmp_path / filename).write_bytes(
            payload if filename == "testlookup_reporter.py" else filename.encode()
        )
    monkeypatch.setattr(sdk_router, "SDK_BASE_PATH", str(tmp_path))
    async with _client() as client:
        response = await client.get("/api/v1/sdk/python",
                                    headers={"Range": "bytes=0-9,20-29,40-49"})
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(
        'filename="testlookup-python-sdk.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("python/testlookup_reporter.py") == payload
        assert set(archive.namelist()) == {
            "python/testlookup_reporter.py",
            "python/ci_context.py",
            "python/commit_range.py",
            "python/pyproject.toml",
            "python/README.md",
            "python/testlookup.yaml.example",
        }


@pytest.mark.asyncio
async def test_the_real_python_sdk_archive_imports_in_an_isolated_directory(
    tmp_path, monkeypatch
) -> None:
    from app.routers import sdk as sdk_router

    client_dir = Path(__file__).resolve().parents[3] / "client"
    monkeypatch.setattr(sdk_router, "SDK_BASE_PATH", str(client_dir))
    response = await sdk_router.download_sdk("python")

    archive_path = tmp_path / "testlookup-python-sdk.zip"
    archive_path.write_bytes(response.body)
    extract_dir = tmp_path / "extracted"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extract_dir)

    package_dir = extract_dir / "python"
    metadata = tomllib.loads((package_dir / "pyproject.toml").read_text("utf-8"))
    assert metadata["tool"]["setuptools"]["py-modules"] == [
        "testlookup_reporter",
        "ci_context",
        "commit_range",
    ]

    site_packages = next(
        path for path in site.getsitepackages() if (Path(path) / "httpx").is_dir()
    )
    isolated = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            (
                "import sys; "
                f"sys.path[:0] = [{str(package_dir)!r}, {site_packages!r}]; "
                "import testlookup_reporter"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert isolated.returncode == 0, isolated.stderr


@pytest.mark.asyncio
async def test_a_declared_oversize_body_is_refused_without_reading_it(db_calls) -> None:
    """Content-Length over the cap: 413 before a single body byte is read."""
    import asyncio

    reads = {"n": 0}

    async def receive():
        reads["n"] += 1
        # More than the cap, then a clean end of body.
        more = reads["n"] < 200
        return {"type": "http.request", "body": b"a" * 1024, "more_body": more}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login", "query_string": b"", "root_path": "",
        "headers": [(b"host", b"test"),
                    (b"content-type", b"application/x-www-form-urlencoded"),
                    (b"content-length", str(1024 * 1024).encode())],
        "client": ("127.0.0.1", 1234), "server": ("test", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    assert reads["n"] == 0, "read the body although Content-Length already exceeded the cap"
    assert db_calls["opened"] == 0


@pytest.mark.asyncio
async def test_the_saml_acs_keeps_room_for_a_large_signed_response(db_calls) -> None:
    """The ACS (POST binding) takes an urlencoded SAMLResponse up to its route's
    own 1 MB limit; a blanket 64 KiB cap would break SSO for large assertions."""
    from app.routers import sso as sso_router

    assert settings.FORM_URLENCODED_ACS_MAX_BYTES > sso_router._MAX_SAML_RESPONSE_BYTES
    body = b"SAMLResponse=" + b"A" * (200 * 1024)
    async with _client() as client:
        response = await client.post("/api/v1/sso/acs", content=body, headers=FORM)
    assert response.status_code != 413, response.text
    assert db_calls["opened"] == 1, "the ACS handler was not reached"


@pytest.mark.asyncio
async def test_the_saml_acs_is_still_bounded(db_calls) -> None:
    body = b"SAMLResponse=" + b"A" * (3 * 1024 * 1024)
    async with _client() as client:
        response = await client.post("/api/v1/sso/acs", content=body, headers=FORM)
    assert response.status_code == 413
    assert db_calls["opened"] == 0


# ── R-B45-T-1: latin-1 whitespace in the Content-Type ────────────────────────
#
# bytes.strip() leaves \xa0 / \x85; starlette's parse_options_header strips
# them and parses the body as a form anyway. Every variant must still be 413,
# with the handler never reached.

_BYPASS = [
    b"application/x-www-form-urlencoded\xa0",
    b"\xa0application/x-www-form-urlencoded",
    b"application/x-www-form-urlencoded\x85",
    b"application/x-www-form-urlencoded\xa0;charset=utf-8",
]


async def _raw_post(path: str, headers: list, body: bytes) -> tuple[int, dict]:
    import asyncio

    step = 64 * 1024
    chunks = [body[i:i + step] for i in range(0, len(body), step)] or [b""]
    messages = [{"type": "http.request", "body": c, "more_body": True} for c in chunks]
    messages[-1]["more_body"] = False

    async def receive():
        if messages:
            return messages.pop(0)
        # The body has ENDED (what a real client sends). Neither a disconnect
        # loop nor an endless await: both hang a mutant instead of failing it.
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": [(b"host", b"test"), *headers],
        "client": ("127.0.0.1", 1234), "server": ("test", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=20)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], dict(start["headers"])


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False], ids=["content-length", "chunked"])
@pytest.mark.parametrize("content_type", _BYPASS,
                         ids=["nbsp-trailing", "nbsp-leading", "nel-trailing", "nbsp-before-charset"])
async def test_latin1_whitespace_in_the_content_type_is_still_a_form(db_calls, content_type, declared) -> None:
    from starlette.requests import parse_options_header

    # Premise: starlette would parse this body as an urlencoded form.
    assert parse_options_header(content_type.decode("latin-1"))[0] == b"application/x-www-form-urlencoded"
    body = b"username=" + b"a" * (1024 * 1024) + b"&password=x"
    headers = [(b"content-type", content_type)]
    if declared:
        headers.append((b"content-length", str(len(body)).encode()))
    status, _ = await _raw_post("/api/v1/auth/login", headers, body)
    assert status == 413
    assert db_calls["opened"] == 0, "the login handler ran"


# ── R-B45-T-3: the 413 is seen by CORS and Telemetry ─────────────────────────


@pytest.mark.asyncio
async def test_a_cross_origin_413_carries_cors_headers_and_is_seen_by_telemetry(db_calls) -> None:
    origin = settings.CORS_ORIGINS[0]
    body = b"username=" + b"a" * (1024 * 1024)
    async with _client() as client:
        response = await client.post("/api/v1/auth/login", content=body,
                                     headers={**FORM, "Origin": origin})
    assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("x-request-id"), "Telemetry did not see the 413"
    assert db_calls["opened"] == 0
