"""Live events must be authorised for the project they are written to.

Re-audit finding H1. ``POST /ws/events/{run_id}`` was gated by
``verify_webhook_secret`` alone:

    @router.post("/events/{run_id}", dependencies=[Depends(verify_webhook_secret)])
    async def ingest_live_event(run_id: str, event: dict = Body(...)):

One shared, deployment-wide secret authenticated the caller but named no
tenant, and the handler took ``project_id`` straight from the request body with
no membership check. Anyone holding that secret — which
``validate_production_secrets`` rated only WARNING, so production booted with
the literal default committed in the source — could POST a ``run_start`` naming
any victim project and then stream fabricated ``test_result`` events into that
project's live dashboard and release-risk signals.

The fix prefers a project-scoped API key (the project is derived from the key,
so the body cannot address another tenant), keeps the shared secret as a legacy
path that can be switched off, compares it in constant time, and binds a run to
one project so later events cannot cross over.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.routers.live import ingest_live_event

PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
RUN = "run-123"


@pytest.fixture
def wired(monkeypatch):
    """Stub only the transport and the store; leave every check real."""
    published: list[tuple[str, dict]] = []

    async def _publish(run_id, event):
        published.append((run_id, event))
        return "1-0"

    monkeypatch.setattr("app.streams.producer.publish_live_event", _publish)
    # Since re-audit N14 a project-key event is handed to the SDK stream's
    # path (services/ws_event_ingest.py) instead of published directly. That
    # adapter is the transport for this branch now, so it is stubbed the same
    # way, into the same list.
    async def _admit(_db, *, project_id, api_key_name, run_id, event):
        published.append((run_id, event))
        from app.services.ws_event_ingest import WsIngestOutcome

        return WsIngestOutcome("session-1")

    monkeypatch.setattr("app.services.ws_event_ingest.ingest_one", _admit)

    class _Coll:
        async def insert_one(self, _doc):
            return None

    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )

    remembered: dict[str, str] = {}

    async def _remember(run_id, project_id):
        # Same contract as the real SET NX: the FIRST writer wins, and the
        # effective owner is returned so a loser can be told immediately.
        return remembered.setdefault(run_id, project_id)

    async def _resolve(run_id):
        return remembered.get(run_id)

    monkeypatch.setattr(
        "app.services.live_event_authz.remember_run_project", _remember
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.resolve_run_project", _resolve
    )

    key_cache: dict[str, str] = {}

    async def _cached_project(api_key):
        return key_cache.get(api_key)

    async def _remember_key(api_key, project_id):
        key_cache[api_key] = project_id

    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project", _cached_project
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.remember_streaming_project", _remember_key
    )

    # Since re-audit H6 the handler counts results into the live-run state
    # itself (it used to count nothing). These tests are about WHO may write,
    # not about counting, so stub the state rather than let it reach Redis.
    # The counting has its own suite: test_ws_events_results_are_counted.py.
    async def _state_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.start", _state_noop
    )
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.record_test_event",
        _state_noop,
    )

    # Since re-audit M4/M3 the route also passes two admission gates (Redis
    # backpressure and a per-project event budget). These tests are about WHO
    # may write, and unstubbed the gates reach for a real Redis and wait out a
    # connection timeout per test. The gates have their own suite:
    # test_live_event_ingest_is_gated.py.
    async def _gate_open(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure",
        _gate_open,
    )
    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_live_event_rate_limit",
        _gate_open,
    )
    # The SHIPPED default is True (see
    # test_the_shared_secret_is_refused_by_default). These cases opt back
    # into the legacy path on purpose, to pin what it does when a
    # deployment re-enables it.
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "the-real-secret")

    return SimpleNamespace(
        published=published, remembered=remembered, key_cache=key_cache
    )


def _key_ctx(project_id):
    return SimpleNamespace(
        user=SimpleNamespace(id="u"),
        project_id=project_id,
        api_key_id="k",
        api_key_name="ci",
    )


async def _call(**kwargs):
    params = {
        "run_id": RUN,
        "event": {"type": "test_result", "test_name": "t", "status": "PASSED"},
        "x_api_key": None,
        "x_webhook_secret": None,
        "db": SimpleNamespace(),
    }
    params.update(kwargs)
    return await ingest_live_event(**params)


# ── The endpoint must demand a credential at all ─────────────────────────────


@pytest.mark.asyncio
async def test_no_credential_is_rejected(wired):
    with pytest.raises(HTTPException) as exc:
        await _call()
    assert exc.value.status_code == 401
    assert wired.published == []


@pytest.mark.asyncio
async def test_a_wrong_shared_secret_is_rejected(wired):
    with pytest.raises(HTTPException) as exc:
        await _call(x_webhook_secret="not-the-secret")
    assert exc.value.status_code == 403
    assert wired.published == []


@pytest.mark.asyncio
async def test_the_legacy_shared_secret_works_when_re_enabled(wired):
    result = await _call(x_webhook_secret="the-real-secret")
    assert result["accepted"] is True
    assert len(wired.published) == 1


@pytest.mark.asyncio
async def test_deployments_can_refuse_the_shared_secret_outright(wired, monkeypatch):
    """The switch that closes the cross-tenant path for good."""
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)

    with pytest.raises(HTTPException) as exc:
        await _call(x_webhook_secret="the-real-secret")
    assert exc.value.status_code == 403
    assert "project-scoped API key" in exc.value.detail
    assert wired.published == []


# ── The forgery the audit reported ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_api_key_cannot_name_another_project(wired, monkeypatch):
    """A key for project A sending run_start for B must write to A.

    The body is not trusted: the project comes from the credential.
    """
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )

    await _call(
        x_api_key="k",
        event={"type": "run_start", "project_id": PROJECT_B, "build_number": "1"},
    )

    _run_id, published = wired.published[0]
    assert published["project_id"] == PROJECT_A, (
        "the caller's forged project_id reached the stream — a key for one "
        "project could open a run against another"
    )
    assert wired.remembered[RUN] == PROJECT_A


@pytest.mark.asyncio
async def test_a_later_event_cannot_cross_into_another_projects_run(
    wired, monkeypatch
):
    """Project A opens the run; project B's key may not append to it."""
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    await _call(
        x_api_key="ka",
        event={"type": "run_start", "project_id": PROJECT_A, "build_number": "1"},
    )

    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_B)),
    )
    with pytest.raises(HTTPException) as exc:
        await _call(x_api_key="kb")

    assert exc.value.status_code == 403
    assert len(wired.published) == 1


@pytest.mark.asyncio
async def test_the_owning_project_may_continue_its_own_run(wired, monkeypatch):
    """The binding must not block the legitimate producer."""
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    await _call(
        x_api_key="ka",
        event={"type": "run_start", "project_id": PROJECT_A, "build_number": "1"},
    )
    await _call(x_api_key="ka")

    assert len(wired.published) == 2


@pytest.mark.asyncio
async def test_an_unbound_run_is_not_blocked(wired, monkeypatch):
    """No binding means "cannot verify", which must not break existing runs."""
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    result = await _call(x_api_key="ka")
    assert result["accepted"] is True


# ── Secret handling ──────────────────────────────────────────────────────────


def test_the_secret_is_compared_in_constant_time():
    import inspect

    from app.core import deps
    from app.routers import live

    assert "hmac.compare_digest" in inspect.getsource(deps.verify_webhook_secret)
    assert "hmac.compare_digest" in inspect.getsource(live.ingest_live_event)


def test_a_default_webhook_secret_refuses_to_boot_production(monkeypatch):
    """It gates a cross-tenant write, so it is CRITICAL, not a WARNING."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "change-me-webhook-secret")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "a-real-jwt-secret")
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "a-real-app-secret")
    monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", False)

    critical = settings.critical_security_failures()
    assert any("WEBHOOK_SECRET" in item for item in critical), (
        "a default WEBHOOK_SECRET no longer blocks production startup, so a "
        "deployment can ship with the secret published in the source tree"
    )


# ── The default has to be the safe one ───────────────────────────────────
#
# The first cut of this fix shipped LIVE_EVENTS_REQUIRE_PROJECT_KEY=False, so
# out of the box the endpoint still accepted a deployment-wide secret that
# names no tenant, still read project_id from the body, and — because the
# binding check is reached only on the API-key branch — still let that caller
# write test_result and run_complete onto another project's run. The fix was
# real but nothing was fixed by default.


def test_the_shared_secret_is_refused_by_default():
    """Read the field default, not the live value: the env can override it."""
    from app.core.config import Settings

    field = Settings.model_fields["LIVE_EVENTS_REQUIRE_PROJECT_KEY"]
    assert field.default is True, (
        "the shared webhook secret is accepted out of the box again — it "
        "authenticates a caller but names no project, so any holder can "
        "stream fabricated results into any tenant"
    )


@pytest.mark.asyncio
async def test_a_shared_secret_call_is_refused_under_the_shipped_default(
    wired, monkeypatch
):
    """The same thing, through the handler, with the default restored."""
    from app.core.config import Settings

    monkeypatch.setattr(
        settings,
        "LIVE_EVENTS_REQUIRE_PROJECT_KEY",
        Settings.model_fields["LIVE_EVENTS_REQUIRE_PROJECT_KEY"].default,
    )
    with pytest.raises(HTTPException) as exc:
        await _call(
            x_webhook_secret="the-real-secret",
            event={"type": "run_start", "project_id": PROJECT_B, "build_number": "1"},
        )
    assert exc.value.status_code == 403
    assert wired.published == []


# ── A caller-chosen run_id will collide ──────────────────────────────────
#
# validate_live_identifier accepts any printable string, so run_id is whatever
# the producer picked. Two projects choosing "build-42" is an ordinary
# collision, not an attack, and the binding cannot be allowed to turn it into a
# silent 25-hour outage for whichever one arrived second.


@pytest.mark.asyncio
async def test_a_second_project_opening_the_same_run_id_is_told_immediately(
    wired, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    await _call(
        x_api_key="ka",
        event={"type": "run_start", "project_id": PROJECT_A, "build_number": "1"},
    )

    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_B)),
    )
    with pytest.raises(HTTPException) as exc:
        await _call(
            x_api_key="kb",
            event={"type": "run_start", "project_id": PROJECT_B, "build_number": "1"},
        )

    assert exc.value.status_code == 409, (
        "the losing run_start was accepted silently — every later event of "
        "that run then 403s for 25 hours while the run shows as started and "
        "permanently empty, with nothing to point at"
    )
    assert "already in use" in exc.value.detail
    assert len(wired.published) == 1


@pytest.mark.asyncio
async def test_reopening_your_own_run_is_not_a_conflict(wired, monkeypatch):
    """A retried run_start must stay idempotent."""
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    start = {"type": "run_start", "project_id": PROJECT_A, "build_number": "1"}
    await _call(x_api_key="ka", event=dict(start))
    result = await _call(x_api_key="ka", event=dict(start))

    assert result["accepted"] is True
    assert len(wired.published) == 2


@pytest.mark.asyncio
async def test_a_binding_that_cannot_be_written_refuses_the_run(wired, monkeypatch):
    """Fail closed where it matters.

    A swallowed write leaves the run unbound, which disables the cross-tenant
    check for its entire life with nothing in the response to say so. A read
    that fails still fails open — that is only "cannot verify".
    """
    from app.services.live_event_authz import RunProjectBindingUnavailable

    async def _boom(_run_id, _project_id):
        raise RunProjectBindingUnavailable("redis down")

    monkeypatch.setattr("app.services.live_event_authz.remember_run_project", _boom)
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )

    with pytest.raises(HTTPException) as exc:
        await _call(
            x_api_key="ka",
            event={"type": "run_start", "project_id": PROJECT_A, "build_number": "1"},
        )
    assert exc.value.status_code == 503
    assert wired.published == []


# ── The placeholder secrets this repo actually ships ─────────────────────


@pytest.mark.parametrize(
    "field, placeholder",
    [
        ("JWT_SECRET_KEY", "change-me-generate-with-openssl-rand-hex-32"),
        ("APP_SECRET_KEY", "change-me-in-production-use-openssl-rand-hex-32"),
        ("WEBHOOK_SECRET", "change-me-generate-with-openssl-rand-hex-32"),
    ],
)
def test_env_example_placeholders_block_production(monkeypatch, field, placeholder):
    """The check compared against the field DEFAULT, which none of these equal.

    Every value here is copied verbatim from .env.example, so the most likely
    way to reach production with a published secret was also the one way the
    guard could not see.
    """
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "a-real-jwt-secret")
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "a-real-app-secret")
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "a-real-webhook-secret")
    monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", False)
    assert settings.critical_security_failures() == []

    monkeypatch.setattr(settings, field, placeholder)
    assert any(field in item for item in settings.critical_security_failures()), (
        field + "=" + placeholder + " is straight out of .env.example and "
        "still boots production"
    )


def test_a_generated_secret_is_never_mistaken_for_a_placeholder(monkeypatch):
    """openssl rand -hex is hexadecimal, so it cannot start with 'change-me'."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", False)
    for name in ("JWT_SECRET_KEY", "APP_SECRET_KEY", "WEBHOOK_SECRET"):
        monkeypatch.setattr(settings, name, "c0ffee" * 10)
    assert settings.critical_security_failures() == []


# ── The credential check cannot run per event against Postgres ───────────
#
# get_streaming_api_key_context costs two SELECTs and a last_used_at UPDATE.
# /stream/events/batch amortises that over a whole batch; this endpoint is one
# event per call, and once a project-scoped key became the default credential
# every live event would have paid it against three connections per worker.


@pytest.mark.asyncio
async def test_the_credential_is_verified_once_not_once_per_event(wired, monkeypatch):
    checks = AsyncMock(return_value=_key_ctx(PROJECT_A))
    monkeypatch.setattr("app.routers.live.get_streaming_api_key_context", checks)

    await _call(
        x_api_key="ka",
        event={"type": "run_start", "project_id": PROJECT_A, "build_number": "1"},
    )
    for _ in range(5):
        await _call(x_api_key="ka")

    assert len(wired.published) == 6
    assert checks.await_count == 1, (
        "the full credential check ran " + str(checks.await_count) + " times for "
        "6 events — two SELECTs and an UPDATE each, against PG_POOL_SIZE=2 "
        "plus PG_MAX_OVERFLOW=1"
    )


@pytest.mark.asyncio
async def test_an_unknown_credential_still_gets_the_full_check(wired, monkeypatch):
    """A miss must degrade to the real check, never to a bypass."""
    checks = AsyncMock(return_value=_key_ctx(PROJECT_A))
    monkeypatch.setattr("app.routers.live.get_streaming_api_key_context", checks)

    await _call(x_api_key="key-one")
    await _call(x_api_key="key-two")

    assert checks.await_count == 2


@pytest.mark.asyncio
async def test_a_cache_miss_still_runs_the_full_check(wired, monkeypatch):
    """A miss must reach the real credential check, not bypass it.

    The outage path itself (Redis raising) is covered against a fake Redis
    in test_live_event_authz_redis.py — this stub returns None, which is an
    ordinary miss, and the earlier name claimed more than it exercised.
    """

    async def _broken(_api_key):
        return None

    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project", _broken
    )
    rejects = AsyncMock(side_effect=HTTPException(401, detail="Invalid API key"))
    monkeypatch.setattr("app.routers.live.get_streaming_api_key_context", rejects)

    with pytest.raises(HTTPException) as exc:
        await _call(x_api_key="revoked")
    assert exc.value.status_code == 401
    assert wired.published == []


def test_the_cache_never_stores_the_credential_itself():
    from app.services.live_event_authz import streaming_key_cache_key

    secret = "qai_super_secret_value"
    key = streaming_key_cache_key(secret)
    assert secret not in key, "the raw API key is used as a Redis key name"
    assert key.startswith("testlookup:live:key-project:")


def test_the_cached_credential_expires_quickly():
    """It is also the revocation lag, so it has to stay short."""
    from app.services.live_event_authz import STREAMING_KEY_TTL_SECONDS

    assert 0 < STREAMING_KEY_TTL_SECONDS <= 60, (
        "a cache hit skips the active flag, the expiry, the scope and the "
        "owner's account state, so this is how long a revoked key keeps working"
    )


# ── Every example file this repo ships, walked from disk ─────────────────
#
# The guard first knew only .env.example's wording, so it closed the hole for
# one of three shipped example files. Enumerating them from disk means a new
# example file is covered the day it lands, rather than the day someone
# remembers to extend a hand-written list.

EXAMPLE_ENV_FILES = (
    ".env.example",
    ".env.gcp-vm.example",
    "infra/cloudrun/backend.env.example",
)

#: Settings fields whose default value would boot production on a published
#: credential. Each is checked by critical_security_failures().
GUARDED_SECRET_FIELDS = ("JWT_SECRET_KEY", "APP_SECRET_KEY", "WEBHOOK_SECRET")


def _example_env_values(relative_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    path = root / relative_path
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip()
    return values


def test_the_example_files_are_where_this_test_thinks_they_are():
    """Guards the guard: a missing file would make the scan below vacuous."""
    for relative_path in EXAMPLE_ENV_FILES:
        values = _example_env_values(relative_path)
        assert values, (
            relative_path + " could not be read or parsed, so the placeholder "
            "scan over it proves nothing"
        )


@pytest.mark.parametrize("relative_path", EXAMPLE_ENV_FILES)
def test_no_shipped_example_secret_can_boot_production(monkeypatch, relative_path):
    """Copy an example file, forget a line, and production must refuse.

    Both of the newer files ship APP_ENV=production themselves, so the check
    really does run for an operator following their deployment guide.
    """
    from app.core.config import _is_placeholder_secret

    values = _example_env_values(relative_path)
    checked = 0
    for field in GUARDED_SECRET_FIELDS:
        if field not in values:
            continue
        checked += 1
        assert _is_placeholder_secret(values[field]), (
            relative_path + " ships " + field + "=" + values[field] + ", which "
            "the guard does not recognise as a placeholder — copying this file "
            "and deploying it boots production on a published secret"
        )
    assert checked, (
        relative_path + " declares none of " + str(GUARDED_SECRET_FIELDS)
        + "; if the names changed this test is no longer checking anything"
    )


@pytest.mark.parametrize(
    "generated",
    [
        "c0ffee" * 10,
        "9f8e7d6c5b4a49388271",
        "a3f5b1c9d7e2408f96b4c1d8e5a2f7b3",
    ],
)
def test_a_generated_secret_is_never_a_placeholder(generated):
    """openssl rand -hex is what every script here tells the operator to run."""
    from app.core.config import _is_placeholder_secret

    assert not _is_placeholder_secret(generated)


# ── The producer this endpoint's own docs describe ───────────────────────


@pytest.mark.asyncio
async def test_an_api_key_producer_need_not_send_a_project_id(wired, monkeypatch):
    """The server derives the project — that is the entire point of the key.

    The required-field check ran BEFORE the server-derived override, so a
    correct client following the documented contract got a 400. Making the
    project-scoped key the default credential made that the only path.
    """
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )

    result = await _call(
        x_api_key="ka",
        event={"type": "run_start", "build_number": "1", "total_tests": 3},
    )

    assert result["accepted"] is True
    _run_id, published = wired.published[0]
    assert published["project_id"] == PROJECT_A
    assert wired.remembered[RUN] == PROJECT_A


@pytest.mark.asyncio
async def test_the_legacy_path_must_still_name_its_project(wired):
    """Nothing else names a tenant for the shared secret, so it is required."""
    with pytest.raises(HTTPException) as exc:
        await _call(
            x_webhook_secret="the-real-secret",
            event={"type": "run_start", "build_number": "1"},
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_an_empty_cached_project_is_not_treated_as_resolved(wired, monkeypatch):
    """A falsy cache entry must reach the real check, not stand in for it."""

    async def _empty(_api_key):
        return ""

    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project", _empty
    )
    checks = AsyncMock(return_value=_key_ctx(PROJECT_A))
    monkeypatch.setattr("app.routers.live.get_streaming_api_key_context", checks)

    await _call(
        x_api_key="ka",
        event={"type": "run_start", "build_number": "1"},
    )

    assert checks.await_count == 1, (
        "an empty cache entry satisfied the resolution test, so the credential "
        "was never actually verified"
    )
    _run_id, published = wired.published[0]
    assert published["project_id"] == PROJECT_A
