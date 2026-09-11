"""``POST /ingest`` and ``POST /ingest/file`` must pass the admission gates.

Re-audit finding M4. ``/api/v1/stream/*`` has had a per-project rate limit and
a Redis-memory backpressure check since the scalable-ingestion work. The two
routes CI actually uploads through had neither, so one runaway pipeline could
fill Redis and the worker queue for every project.

Two properties matter as much as the gates existing:

* They run AFTER the project is authorised. Charging the per-project bucket
  before the scope check would let any caller spend another tenant's quota.
* On ``/ingest/file`` they run BEFORE the upload is read, which is where shedding
  load actually saves something: that read is up to 50 MB.

These drive the real handlers and stop them right after the gates, by making
the next step raise, so no storage or worker stubs are needed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.schemas import IngestPayload, IngestTestResult
from app.routers import ingest as ingest_router

pytestmark = pytest.mark.regression

PROJECT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER = SimpleNamespace(id="u-1", is_active=True)


class _Stop(Exception):
    """Raised by the step after the gates, so a test can stop there."""


@pytest.fixture
def gates(monkeypatch):
    calls: list[str] = []

    async def _authz(_db, _user, _project):
        calls.append("authz")

    async def _backpressure():
        calls.append("backpressure")

    async def _rate_limit(project_id, *, cost=1):
        calls.append(f"rate_limit:{project_id}")

    for target in ("app.routers.ingest.resolve_project_scope", "app.core.deps.resolve_project_scope"):
        monkeypatch.setattr(target, _authz, raising=False)
    monkeypatch.setattr(
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure",
        _backpressure,
    )
    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_ingest_rate_limit", _rate_limit
    )
    return calls


# ── POST /ingest ─────────────────────────────────────────────────────────


def _payload() -> IngestPayload:
    return IngestPayload(
        project_id=PROJECT,
        build_number="42",
        results=[IngestTestResult(test_name="t", status="PASSED")],
    )


@pytest.mark.asyncio
async def test_ingest_passes_both_gates_after_authz(gates, monkeypatch):
    async def _stop(*_a, **_k):
        gates.append("next_step")
        raise _Stop()

    monkeypatch.setattr(ingest_router, "_resolve_run_id", _stop)

    with pytest.raises(_Stop):
        await ingest_router.ingest_batch(
            payload=_payload(), db=SimpleNamespace(), auth=(USER, None)
        )

    assert gates == ["authz", "backpressure", f"rate_limit:{PROJECT}", "next_step"], (
        "the admission gates did not run between authorisation and the rest of "
        f"the ingest: {gates}"
    )


@pytest.mark.asyncio
async def test_an_unauthorised_ingest_spends_no_quota(gates, monkeypatch):
    async def _deny(_db, _user, _project):
        raise HTTPException(403, detail="not a member")

    for target in ("app.routers.ingest.resolve_project_scope", "app.core.deps.resolve_project_scope"):
        monkeypatch.setattr(target, _deny, raising=False)

    with pytest.raises(HTTPException) as exc:
        await ingest_router.ingest_batch(
            payload=_payload(), db=SimpleNamespace(), auth=(USER, None)
        )
    assert exc.value.status_code == 403
    assert not any(c.startswith("rate_limit") for c in gates), (
        "a caller with no access to the project was charged against its "
        "bucket — anyone could exhaust another tenant's quota"
    )


@pytest.mark.asyncio
async def test_a_429_stops_the_ingest_before_any_work(gates, monkeypatch):
    async def _over(project_id, *, cost=1):
        raise HTTPException(429, detail="over", headers={"Retry-After": "9"})

    async def _must_not_run(*_a, **_k):
        raise AssertionError("ingest continued past a 429")

    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_ingest_rate_limit", _over
    )
    monkeypatch.setattr(ingest_router, "_resolve_run_id", _must_not_run)

    with pytest.raises(HTTPException) as exc:
        await ingest_router.ingest_batch(
            payload=_payload(), db=SimpleNamespace(), auth=(USER, None)
        )
    assert exc.value.status_code == 429


# ── POST /ingest/file ────────────────────────────────────────────────────


async def _upload(**overrides):
    """Call the handler directly with EVERY form field explicit.

    A direct call receives FastAPI's ``Form(...)`` default objects, which are
    truthy, rather than None -- so nothing may be left to its default.
    """
    params = {
        "file": SimpleNamespace(filename="results.xml"),
        "project_id": PROJECT,
        "build_number": "42",
        "branch": None,
        "commit_hash": None,
        "release_name": None,
        "format": "auto",
        "run_ai": True,
        "ci_provider": None,
        "ci_repo": None,
        "pr_number": None,
        "ci_actor": None,
        "ci_run_url": None,
        "jenkins_job": None,
        "environment": None,
        "executed_at": None,
        "commit_range": None,
        "db": SimpleNamespace(),
        "auth": (USER, None),
    }
    params.update(overrides)
    return await ingest_router.ingest_file(**params)


@pytest.mark.asyncio
async def test_file_upload_passes_both_gates_before_reading_the_body(gates, monkeypatch):
    async def _read(*_a, **_k):
        gates.append("read_upload")
        raise _Stop()

    monkeypatch.setattr(ingest_router, "_read_upload_bounded", _read)

    with pytest.raises(_Stop):
        await _upload()

    assert gates == ["authz", "backpressure", f"rate_limit:{PROJECT}", "read_upload"], (
        "the gates did not run between authorisation and reading the upload: "
        f"{gates}"
    )


@pytest.mark.asyncio
async def test_a_429_means_the_upload_is_never_read(gates, monkeypatch):
    """Shedding load only saves something if the 50 MB read is skipped."""

    async def _over(project_id, *, cost=1):
        raise HTTPException(429, detail="over", headers={"Retry-After": "9"})

    async def _must_not_read(*_a, **_k):
        raise AssertionError("the upload was read despite a 429")

    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_ingest_rate_limit", _over
    )
    monkeypatch.setattr(ingest_router, "_read_upload_bounded", _must_not_read)

    with pytest.raises(HTTPException) as exc:
        await _upload()
    assert exc.value.status_code == 429
