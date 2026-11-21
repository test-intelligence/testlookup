"""An SSO sign-in finishes only in the browser that started it, and its public routes are bounded.

Three rules:

1. Browser binding. ``/sso/login-url`` and ``/sso/oidc/login`` set a short-lived
   ``HttpOnly`` cookie holding a random value and keep only its SHA-256 with the
   SAML request id / OIDC ``state``. ``/sso/acs`` and ``/sso/oidc/callback``
   refuse a response that arrives without that cookie or with another value, so
   a response one person obtained cannot be delivered by another person's
   browser. The SAML cookie is ``SameSite=None; Secure`` (the ACS is a
   cross-site POST), the OIDC one ``SameSite=Lax``. An IdP-initiated SAML
   response (``SAML_ALLOW_IDP_INITIATED``) has no request and so no cookie.
2. The four public SSO routes are rate limited per client address, and a
   ``SAMLResponse`` that fails before its signature verifies is a log line,
   not an ``identity_events`` row (that table has no retention).
3. A SAML assertion must carry ``NotOnOrAfter`` and ``Recipient``, and must
   stop being usable within ``SAML_MAX_ASSERTION_LIFETIME_SECONDS`` plus the
   clock skew.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.core.config import settings
from app.models.postgres import IdentityEvent, IdentityEventType, SSOProviderType, User, UserRole
from app.routers import sso as sso_router
from app.services import oidc_service, sso_service

pytestmark = pytest.mark.regression

APP = "https://app.example.test"
ACS = f"{APP}/api/v1/sso/acs"
SSO_PATHS = (
    "/api/v1/sso/acs",
    "/api/v1/sso/login-url",
    "/api/v1/sso/oidc/login",
    "/api/v1/sso/exchange",
)


# ── Helpers ──────────────────────────────────────────────────────────────────


class _Redis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.ttl[key] = ex
        return True

    async def getdel(self, key):
        return self.data.pop(key, None)


class _Db:
    def __init__(self):
        self.added: list = []
        self.commits = 0

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None, scalar=lambda: None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def refresh(self, _obj):
        return None

    def events(self):
        return [obj for obj in self.added if isinstance(obj, IdentityEvent)]


class _Log:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, message, *args, **_kwargs):
        self.warnings.append(message % args if args else message)

    def info(self, *_a, **_k):
        return None

    error = exception = debug = info


def _request(
    *, method: str = "GET", path: str = "/", accept: str = "application/json",
    cookies: dict | None = None, form: dict | None = None, client: str = "203.0.113.9",
) -> Request:
    headers = [(b"accept", accept.encode()), (b"host", b"app.example.test")]
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    body = b""
    if form is not None:
        body = "&".join(f"{k}={quote(v)}" for k, v in form.items()).encode()
        headers.append((b"content-type", b"application/x-www-form-urlencoded"))
        headers.append((b"content-length", str(len(body)).encode()))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": headers,
        "client": (client, 4321), "server": ("app.example.test", 443),
    }
    return Request(scope, receive)


def _cookie(response, name: str) -> tuple[str, str]:
    """(value, the whole Set-Cookie line) of the cookie ``name`` on ``response``."""
    for key, raw in response.raw_headers:
        line = raw.decode()
        if key == b"set-cookie" and line.startswith(f"{name}="):
            return line.split(";", 1)[0].split("=", 1)[1], line
    raise AssertionError(f"no {name} cookie on the response")


def _saml_config(acs: str = ACS):
    return SimpleNamespace(
        id=uuid.uuid4(), provider_type=SSOProviderType.SAML,
        idp_sso_url="https://idp.example.test/sso", idp_entity_id="https://idp.example.test",
        idp_certificate="-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----",
        sp_entity_id="https://sp.example.test", sp_acs_url=acs, audience=None,
        link_existing_accounts_by_email=False,
    )


def _oidc_config():
    return SimpleNamespace(
        id=uuid.uuid4(), provider_type=SSOProviderType.OIDC, oidc_issuer="https://idp.example.test",
        oidc_client_id="client-1", oidc_scopes=None, oidc_groups_claim=None,
        link_existing_accounts_by_email=False,
    )


def _stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parsed(**over) -> dict:
    parsed = {
        "name_id": "dana@corp.example", "attributes": {}, "issuer": "https://idp.example.test",
        "conditions_present": True, "audiences": ["https://sp.example.test"], "recipient": ACS,
        "in_response_to": "_req1", "assertion_id": "_a1",
        "not_on_or_after": _stamp(timedelta(minutes=5)),
        "subject_not_on_or_after": _stamp(timedelta(minutes=5)),
    }
    parsed.update(over)
    return parsed


def _user() -> User:
    now = datetime.now(timezone.utc)
    return User(
        id=uuid.uuid4(), email="dana@corp.example", username="dana", role=UserRole.TESTER,
        is_active=True, must_change_password=False, hashed_password="not-a-real-hash",
        created_at=now, updated_at=now,
    )


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


@pytest.fixture
def sso_on(monkeypatch):
    monkeypatch.setattr(settings, "SSO_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", APP)
    monkeypatch.setattr(settings, "SAML_BASE_URL", APP)
    monkeypatch.setattr(settings, "SAML_ALLOW_IDP_INITIATED", False)
    monkeypatch.setattr(settings, "SAML_BROWSER_BINDING", True)


@pytest.fixture
def log(monkeypatch):
    recorder = _Log()
    monkeypatch.setattr(sso_router, "logger", recorder)
    return recorder


def _signed_in(monkeypatch):
    """Everything after the checks: a user, tokens and the handoff."""
    monkeypatch.setattr(sso_router, "jit_provision_or_link", AsyncMock(return_value=(_user(), False)))
    issue = AsyncMock(return_value="not-a-real-access-token")
    monkeypatch.setattr(sso_router, "issue_access_jwt", issue)
    monkeypatch.setattr(sso_router, "issue_refresh_token", AsyncMock(return_value="not-a-real-refresh-token"))
    return issue


# ── 1a. SAML: the cookie and what is stored ──────────────────────────────────


async def test_saml_login_url_sets_a_cross_site_cookie_and_stores_only_its_hash(sso_on, redis, monkeypatch):
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=_saml_config()))
    response = Response()
    body = await sso_router.get_sso_login_url(response=response, next=None, db=_Db())

    value, line = _cookie(response, sso_router.SAML_BINDING_COOKIE)
    attributes = {part.strip().lower() for part in line.split(";")[1:]}
    assert {"httponly", "secure", "samesite=none", "path=/api/v1/sso"} <= attributes
    max_age = next(int(a.split("=")[1]) for a in attributes if a.startswith("max-age="))
    assert 0 < max_age <= 900
    assert len(value) >= 32

    stored = redis.data[f"saml:authnreq:{body['request_id']}"]
    assert stored == sso_service.browser_binding_hash(value)
    assert value not in stored and value not in str(body)


async def test_saml_login_url_over_plain_http_is_refused_with_a_message_naming_https(sso_on, redis, monkeypatch):
    config = _saml_config(acs="http://testlookup.internal/api/v1/sso/acs")
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    with pytest.raises(HTTPException) as refused:
        await sso_router.get_sso_login_url(response=Response(), next=None, db=_Db())
    assert refused.value.status_code == 400
    assert "HTTPS" in refused.value.detail and "SAML_BROWSER_BINDING" in refused.value.detail
    assert redis.data == {}


async def test_saml_binding_can_be_switched_off_for_a_plain_http_deployment(sso_on, redis, monkeypatch):
    monkeypatch.setattr(settings, "SAML_BROWSER_BINDING", False)
    config = _saml_config(acs="http://testlookup.internal/api/v1/sso/acs")
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    response = Response()
    body = await sso_router.get_sso_login_url(response=response, next=None, db=_Db())
    assert not any(key == b"set-cookie" for key, _ in response.raw_headers)
    monkeypatch.setattr(sso_service, "claim_assertion_id", AsyncMock(return_value=True))
    parsed = _parsed(in_response_to=body["request_id"], recipient=config.sp_acs_url)
    await sso_service.enforce_saml_security(parsed, config)  # no cookie, no raise


# ── 1b. SAML: the ACS requires the cookie ────────────────────────────────────


async def _start_saml(redis, request_id: str = "_req1") -> str:
    token = "the-value-in-this-browsers-cookie"
    await sso_service.remember_saml_request(request_id, browser_hash=sso_service.browser_binding_hash(token))
    return token


@pytest.mark.parametrize("presented", [None, "", "another-browsers-value"])
async def test_a_saml_response_without_the_starting_browsers_cookie_is_refused(sso_on, redis, monkeypatch, presented):
    await _start_saml(redis)
    monkeypatch.setattr(sso_service, "claim_assertion_id", AsyncMock(return_value=True))
    with pytest.raises(ValueError, match="browser that started"):
        await sso_service.enforce_saml_security(_parsed(), _saml_config(), browser_token=presented)
    # The request id is spent either way: the response cannot be tried again with another cookie.
    assert redis.data == {}


async def test_a_saml_response_with_the_right_cookie_passes(sso_on, redis, monkeypatch):
    token = await _start_saml(redis)
    monkeypatch.setattr(sso_service, "claim_assertion_id", AsyncMock(return_value=True))
    await sso_service.enforce_saml_security(_parsed(), _saml_config(), browser_token=token)


async def test_a_request_stored_without_a_hash_is_refused_while_binding_is_on(sso_on, redis, monkeypatch):
    await sso_service.remember_saml_request("_req1")
    monkeypatch.setattr(sso_service, "claim_assertion_id", AsyncMock(return_value=True))
    with pytest.raises(ValueError, match="browser that started"):
        await sso_service.enforce_saml_security(_parsed(), _saml_config(), browser_token="anything")


async def test_idp_initiated_saml_needs_no_cookie_when_it_is_allowed(sso_on, redis, monkeypatch):
    monkeypatch.setattr(settings, "SAML_ALLOW_IDP_INITIATED", True)
    monkeypatch.setattr(sso_service, "claim_assertion_id", AsyncMock(return_value=True))
    await sso_service.enforce_saml_security(_parsed(in_response_to=None), _saml_config())


async def test_the_acs_signs_in_only_with_the_cookie_and_then_clears_it(sso_on, redis, monkeypatch, log):
    config = _saml_config()
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    monkeypatch.setattr(sso_router, "parse_saml_response", lambda *_a, **_k: _parsed())
    monkeypatch.setattr(sso_service, "claim_assertion_id", AsyncMock(return_value=True))
    issue = _signed_in(monkeypatch)
    form = {"SAMLResponse": "QUJD"}

    # Without the cookie, then with a wrong one: no session.
    for cookies in (None, {sso_router.SAML_BINDING_COOKIE: "another-browsers-value"}):
        await _start_saml(redis)
        with pytest.raises(HTTPException) as refused:
            await sso_router.saml_acs(
                _request(method="POST", form=form, cookies=cookies), response=Response(), db=_Db(),
            )
        assert refused.value.status_code == 401
    issue.assert_not_awaited()

    # The same response in the browser that started the sign-in.
    token = await _start_saml(redis)
    response = Response()
    signed_in = await sso_router.saml_acs(
        _request(method="POST", form=form, cookies={sso_router.SAML_BINDING_COOKIE: token}),
        response=response, db=_Db(),
    )
    assert signed_in.access_token == "not-a-real-access-token"
    value, line = _cookie(response, sso_router.SAML_BINDING_COOKIE)
    assert value in ("", '""') and "max-age=0" in line.lower()

    # ... and as a browser: a redirect into the app that also clears the cookie.
    token = await _start_saml(redis)
    redirect = await sso_router.saml_acs(
        _request(method="POST", accept="text/html", form=form, cookies={sso_router.SAML_BINDING_COOKIE: token}),
        response=Response(), db=_Db(),
    )
    assert redirect.status_code == 303 and redirect.headers["location"].startswith(f"{APP}/sso/callback#code=")
    assert "max-age=0" in _cookie(redirect, sso_router.SAML_BINDING_COOKIE)[1].lower()


# ── 1c. OIDC ─────────────────────────────────────────────────────────────────


async def test_oidc_login_sets_a_lax_cookie_and_parks_only_its_hash(sso_on, redis, monkeypatch):
    config = _oidc_config()
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    monkeypatch.setattr(
        oidc_service, "discover", AsyncMock(return_value={"authorization_endpoint": "https://idp.example.test/auth"}),
    )
    monkeypatch.setattr(oidc_service, "_guard_url", AsyncMock())

    redirect = await sso_router.oidc_login(next="/runs", db=_Db())
    assert redirect.status_code == 302
    value, line = _cookie(redirect, sso_router.OIDC_BINDING_COOKIE)
    attributes = {part.strip().lower() for part in line.split(";")[1:]}
    assert {"httponly", "secure", "samesite=lax", "path=/api/v1/sso"} <= attributes
    state = parse_qs(urlsplit(redirect.headers["location"]).query)["state"][0]
    parked = await oidc_service.consume_state(state)
    assert parked["browser_hash"] == sso_service.browser_binding_hash(value)
    assert value not in str(parked)

    # The SPA's way in (/login-url) binds the same way.
    response = Response()
    body = await sso_router.get_sso_login_url(response=response, next=None, db=_Db())
    value, line = _cookie(response, sso_router.OIDC_BINDING_COOKIE)
    assert "samesite=lax" in line.lower() and "httponly" in line.lower()
    state = parse_qs(urlsplit(body["redirect_url"]).query)["state"][0]
    assert (await oidc_service.consume_state(state))["browser_hash"] == sso_service.browser_binding_hash(value)


async def test_the_oidc_cookie_is_not_secure_on_a_plain_http_deployment(sso_on, redis, monkeypatch):
    """A Secure cookie is dropped by a browser on http, which would end every OIDC sign-in there."""
    monkeypatch.setattr(settings, "SAML_BASE_URL", "http://testlookup.internal")
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=_oidc_config()))
    monkeypatch.setattr(oidc_service, "start_login", AsyncMock(return_value="https://idp.example.test/auth?x=1"))
    redirect = await sso_router.oidc_login(next=None, db=_Db())
    attributes = {part.strip().lower() for part in _cookie(redirect, sso_router.OIDC_BINDING_COOKIE)[1].split(";")[1:]}
    assert "secure" not in attributes and {"httponly", "samesite=lax"} <= attributes


def _oidc_callback_ready(monkeypatch, config, cookie_value: str | None):
    parked = {
        "config_id": str(config.id), "nonce": "n", "code_verifier": "v", "redirect_uri": "r", "next": "/runs",
    }
    if cookie_value is not None:
        parked["browser_hash"] = sso_service.browser_binding_hash(cookie_value)
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    monkeypatch.setattr(oidc_service, "consume_state", AsyncMock(return_value=parked))
    monkeypatch.setattr(sso_router, "read_secret", AsyncMock(return_value="not-a-real-secret"))
    monkeypatch.setattr(oidc_service, "discover", AsyncMock(return_value={}))
    exchange = AsyncMock(return_value={"id_token": "t"})
    monkeypatch.setattr(oidc_service, "exchange_code", exchange)
    monkeypatch.setattr(
        oidc_service, "validate_with_jwks",
        AsyncMock(return_value={"sub": "sub-1", "email": "dana@corp.example", "email_verified": True}),
    )
    return exchange


@pytest.mark.parametrize("presented", [None, "another-browsers-value"])
async def test_an_oidc_callback_without_the_starting_browsers_cookie_is_refused(
    sso_on, redis, monkeypatch, log, presented,
):
    config = _oidc_config()
    exchange = _oidc_callback_ready(monkeypatch, config, "the-value-in-this-browsers-cookie")
    issue = _signed_in(monkeypatch)
    db = _Db()
    cookies = {sso_router.OIDC_BINDING_COOKIE: presented} if presented else None
    response = await sso_router.oidc_callback(_request(cookies=cookies), code="c", state="s", error=None, db=db)
    assert response.status_code == 303 and response.headers["location"] == f"{APP}/login?sso_error=1"
    exchange.assert_not_awaited()  # the code is never redeemed
    issue.assert_not_awaited()
    assert db.events() == [] and db.commits == 0  # anyone can trigger this: a log line, not a row
    assert any("browser that started" in line for line in log.warnings)


async def test_an_oidc_login_parked_without_a_hash_is_refused(sso_on, redis, monkeypatch, log):
    config = _oidc_config()
    exchange = _oidc_callback_ready(monkeypatch, config, None)
    _signed_in(monkeypatch)
    response = await sso_router.oidc_callback(
        _request(cookies={sso_router.OIDC_BINDING_COOKIE: "anything"}), code="c", state="s", error=None, db=_Db(),
    )
    assert response.headers["location"] == f"{APP}/login?sso_error=1"
    exchange.assert_not_awaited()


async def test_an_oidc_callback_with_the_right_cookie_signs_in_and_clears_it(sso_on, redis, monkeypatch, log):
    config = _oidc_config()
    token = "the-value-in-this-browsers-cookie"
    exchange = _oidc_callback_ready(monkeypatch, config, token)
    issue = _signed_in(monkeypatch)
    response = await sso_router.oidc_callback(
        _request(cookies={sso_router.OIDC_BINDING_COOKIE: token}), code="c", state="s", error=None, db=_Db(),
    )
    assert response.status_code == 303 and response.headers["location"].startswith(f"{APP}/sso/callback#code=")
    exchange.assert_awaited_once()
    issue.assert_awaited_once()
    assert "max-age=0" in _cookie(response, sso_router.OIDC_BINDING_COOKIE)[1].lower()


# ── 2. Rate limits and audit rows ────────────────────────────────────────────


@pytest.mark.parametrize("path", SSO_PATHS)
def test_each_public_sso_route_has_a_per_address_limit(path):
    from app.main import _AUTH_LIMITERS, _AUTH_RATE_LIMITS, app

    assert path in _AUTH_RATE_LIMITS, f"{path} has no per-address rate limit"
    limit, message = _AUTH_RATE_LIMITS[path]
    count = int(limit.split("/")[0])
    # Enough for an office behind one address signing in together; far below a flood.
    assert limit.endswith("/minute") and 20 <= count <= 120 and message
    assert path in _AUTH_LIMITERS
    assert any(getattr(route, "path", "") == path for route in app.routes), f"{path} is not mounted"


@pytest.mark.parametrize("path", SSO_PATHS)
async def test_the_limit_answers_429_once_it_is_passed(path, monkeypatch):
    """In production the middleware throttles the route's own method (two of the four are GETs)."""
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    from app import main

    method = next(
        sorted(route.methods - {"HEAD", "OPTIONS"})[0]
        for route in main.app.routes if getattr(route, "path", "") == path
    )
    monkeypatch.setattr(settings, "APP_ENV", "production")
    private = Limiter(key_func=get_remote_address, storage_uri="memory://")
    monkeypatch.setattr(main, "_AUTH_LIMITERS", main._build_auth_limiters(private))
    monkeypatch.setattr(main.app.state, "limiter", private, raising=False)
    allowed = int(main._AUTH_RATE_LIMITS[path][0].split("/")[0])
    reached = 0

    async def call_next(_request):
        nonlocal reached
        reached += 1
        return Response(status_code=204)

    def request(client: str = "198.51.100.7"):
        scope = {
            "type": "http", "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": [(b"host", b"app.example.test")],
            "client": (client, 4321), "server": ("app.example.test", 443), "app": main.app,
        }
        return Request(scope)

    codes = [(await main.rate_limit_auth(request(), call_next)).status_code for _ in range(allowed + 5)]
    assert codes[:allowed] == [204] * allowed, "a sign-in inside the limit was refused"
    assert codes[allowed:] == [429] * 5
    assert reached == allowed
    # Another address has its own budget.
    assert (await main.rate_limit_auth(request("198.51.100.8"), call_next)).status_code == 204


async def test_twenty_invalid_saml_responses_write_no_identity_event(sso_on, redis, monkeypatch, log):
    """The real parser refuses these before any signature verifies."""
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=_saml_config()))
    db = _Db()
    bodies = ["AAAA", "not base64 !!", base64.b64encode(b"<a/>").decode(), base64.b64encode(b"<saml").decode()]
    for index in range(20):
        with pytest.raises(HTTPException) as refused:
            await sso_router.saml_acs(
                _request(method="POST", form={"SAMLResponse": bodies[index % len(bodies)]}),
                response=Response(), db=db,
            )
        assert refused.value.status_code == 401
        assert refused.value.detail == "SAML assertion validation failed."
    assert db.events() == [] and db.added == [] and db.commits == 0
    assert len(log.warnings) == 20  # still visible to the operator


@pytest.mark.parametrize("stage", ["signed_claims", "issuer", "binding", "jit"])
async def test_a_refusal_after_a_valid_signature_is_still_audited(sso_on, redis, monkeypatch, log, stage):
    config = _saml_config()
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))

    def parse(*_a, **_k):
        if stage == "signed_claims":
            raise sso_service.SignedAssertionRefused("SAML Assertion has expired (NotOnOrAfter)")
        return _parsed(issuer="https://someone-else.example.test" if stage == "issuer" else config.idp_entity_id)

    monkeypatch.setattr(sso_router, "parse_saml_response", parse)
    monkeypatch.setattr(
        sso_router, "enforce_saml_security",
        AsyncMock(side_effect=ValueError("replay detected") if stage == "binding" else None),
    )
    monkeypatch.setattr(
        sso_router, "jit_provision_or_link",
        AsyncMock(side_effect=ValueError("An account with this e-mail already exists")),
    )
    db = _Db()
    with pytest.raises(HTTPException):
        await sso_router.saml_acs(_request(method="POST", form={"SAMLResponse": "QUJD"}), response=Response(), db=db)
    (event,) = db.events()
    assert event.event_type == IdentityEventType.SSO_LOGIN_FAILED and event.success is False
    assert db.commits == 1


def test_a_failure_after_the_signature_verified_is_told_apart(monkeypatch):
    """parse_saml_response marks what it refuses after the signature check."""
    with monkeypatch.context() as verified:
        verified.setattr(sso_service, "_verify_xml_signature", lambda *_a: object())
        verified.setattr(sso_service, "_lxml_to_bytes", lambda _signed: b"<Response/>")
        with pytest.raises(sso_service.SignedAssertionRefused):
            sso_service.parse_saml_response(base64.b64encode(b"<x/>").decode(), "a-certificate")
    # Before it: a plain ValueError, never the marked one.
    with pytest.raises(ValueError) as unsigned:
        sso_service.parse_saml_response("AAAA", "a-certificate")
    assert not isinstance(unsigned.value, sso_service.SignedAssertionRefused)


# ── 3. The assertion itself ──────────────────────────────────────────────────


@pytest.fixture
def stores(monkeypatch, sso_on, redis):
    claimed: dict = {}

    async def claim(assertion_id, ttl):
        claimed[assertion_id] = ttl
        return True

    monkeypatch.setattr(sso_service, "claim_assertion_id", claim)
    monkeypatch.setattr(settings, "SAML_ALLOW_IDP_INITIATED", True)
    monkeypatch.setattr(settings, "SAML_CLOCK_SKEW_SECONDS", 60)
    monkeypatch.setattr(settings, "SAML_MAX_ASSERTION_LIFETIME_SECONDS", 600)
    return claimed


def _idp_initiated(**over) -> dict:
    return _parsed(in_response_to=None, **over)


async def test_a_well_formed_assertion_passes(stores):
    await sso_service.enforce_saml_security(_idp_initiated(), _saml_config())
    assert 300 <= stores["_a1"] <= 300 + 60 + 120, "the single-use entry must outlive the assertion"


async def test_an_assertion_without_not_on_or_after_is_refused(stores):
    with pytest.raises(ValueError, match="NotOnOrAfter"):
        await sso_service.enforce_saml_security(
            _idp_initiated(not_on_or_after=None, subject_not_on_or_after=None), _saml_config(),
        )
    # A SubjectConfirmationData time alone does not replace the Conditions one.
    with pytest.raises(ValueError, match="NotOnOrAfter"):
        await sso_service.enforce_saml_security(_idp_initiated(not_on_or_after=None), _saml_config())
    assert stores == {}


async def test_an_assertion_without_a_recipient_is_refused(stores):
    for missing in (None, ""):
        with pytest.raises(ValueError, match="Recipient"):
            await sso_service.enforce_saml_security(_idp_initiated(recipient=missing), _saml_config())
    assert stores == {}


async def test_an_assertion_that_stays_valid_too_long_is_refused(stores):
    long_lived = _idp_initiated(
        not_on_or_after=_stamp(timedelta(hours=8)), subject_not_on_or_after=None,
    )
    with pytest.raises(ValueError, match="SAML_MAX_ASSERTION_LIFETIME_SECONDS"):
        await sso_service.enforce_saml_security(long_lived, _saml_config())
    # Just inside the limit plus the skew: accepted.
    edge = _idp_initiated(not_on_or_after=_stamp(timedelta(seconds=650)), subject_not_on_or_after=None)
    await sso_service.enforce_saml_security(edge, _saml_config())


async def test_the_subject_confirmation_time_is_read(stores):
    # The usual Entra ID / AD FS shape: Conditions valid for an hour, the bearer confirmation for five minutes.
    await sso_service.enforce_saml_security(
        _idp_initiated(not_on_or_after=_stamp(timedelta(minutes=70))), _saml_config(),
    )
    assert stores["_a1"] <= 300 + 60 + 120
    # A bearer confirmation that has run out is refused although the Conditions window is open.
    with pytest.raises(ValueError, match="expired"):
        await sso_service.enforce_saml_security(
            _idp_initiated(assertion_id="_a2", subject_not_on_or_after=_stamp(timedelta(minutes=-10))),
            _saml_config(),
        )
    with pytest.raises(ValueError, match="NotOnOrAfter"):
        await sso_service.enforce_saml_security(
            _idp_initiated(assertion_id="_a3", subject_not_on_or_after="yesterday"), _saml_config(),
        )
    assert "_a2" not in stores and "_a3" not in stores


def test_the_parser_surfaces_the_subject_confirmation_time():
    xml = (
        '<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_a1">'
        "<saml:Issuer>https://idp.example.test</saml:Issuer>"
        "<saml:Subject><saml:NameID>dana@corp.example</saml:NameID>"
        '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData Recipient="{ACS}" NotOnOrAfter="{_stamp(timedelta(minutes=5))}"/>'
        "</saml:SubjectConfirmation></saml:Subject>"
        f'<saml:Conditions NotOnOrAfter="{_stamp(timedelta(minutes=60))}"/>'
        "</saml:Assertion>"
    )
    parsed = sso_service.parse_saml_response_unverified(base64.b64encode(xml.encode()).decode())
    assert parsed["subject_not_on_or_after"] and parsed["subject_not_on_or_after"] != parsed["not_on_or_after"]
    assert parsed["recipient"] == ACS
