"""W19 INT-06: GitHub App installation tokens beside PATs, through ONE credential reader.

Against the contract double (``tests/fakes/fake_integrations.py``) over an ASGI transport:

* the App JWT is RS256, ``iss`` = the App id, ``iat`` 60 s back, ``exp`` <= 10 minutes;
* an ``app`` project gets an installation token (``ghs_…``) that the fake then
  accepts for the installation's repos; it is cached and reused until near expiry;
* a ``pat`` project gets its PAT; an ``app`` project with no installation, or no
  App configured, gets None (or the reason, when asked) — never the PAT;
* ``AI_OFFLINE_MODE`` refuses the exchange; a JWT signed by another key is refused;
* the install ``state`` verifies only for the same user, unexpired and untampered;
* nothing outside ``github_auth`` reads the GitHub PAT or the App private key.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import time
import uuid
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.services import github_auth
from tests.fakes.fake_integrations import FakeState, create_app

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


PRIV, PUB = _keypair()
OTHER_PRIV, _ = _keypair()
APP = {"app_id": "4242", "slug": "testlookup-demo", "private_key": PRIV}


@pytest.fixture
def fake(monkeypatch):
    state = FakeState()
    state.github_app_keys["4242"] = PUB
    state.github_installations["777"] = {"app_id": "4242", "repos": {"acme/payments"}}
    transport = httpx.ASGITransport(app=create_app(state))
    real = httpx.AsyncClient

    class Routed(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", Routed)

    async def _no_block(url):
        return None

    import app.services.url_safety as url_safety

    monkeypatch.setattr(url_safety, "egress_block_reason", _no_block)
    monkeypatch.setattr(github_auth.settings, "AI_OFFLINE_MODE", False)
    github_auth.clear_cache()
    yield state
    github_auth.clear_cache()


def test_the_app_jwt_claims():
    now = 1_800_000_000
    token = github_auth.app_jwt("4242", PRIV, now=now)
    claims = jwt.decode(token, PUB, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    assert claims == {"iat": now - 60, "exp": now + 540, "iss": "4242"}
    assert claims["exp"] - claims["iat"] <= 600


def test_an_installation_token_is_minted_accepted_and_cached(fake):
    token = asyncio.run(github_auth.installation_token("http://github.fake", 777, APP))
    assert token.startswith("ghs_")
    assert fake.github_tokens[token] == {"acme/payments"}
    again = asyncio.run(github_auth.installation_token("http://github.fake", 777, APP))
    assert again == token
    exchanges = [r for r in fake.requests if r["path"].endswith("/access_tokens")]
    assert len(exchanges) == 1 and exchanges[0]["token_kind"] == "app_jwt"

    async def read_repo():
        async with httpx.AsyncClient() as c:
            return await c.get("http://github.fake/repos/acme/payments", headers={"Authorization": f"Bearer {token}"})

    assert asyncio.run(read_repo()).status_code == 200
    assert fake.requests[-1]["token_kind"] == "installation"


def test_a_jwt_from_another_key_is_refused(fake):
    with pytest.raises(github_auth.GitHubAppUnavailable) as exc:
        asyncio.run(github_auth.installation_token("http://github.fake", 777, {**APP, "private_key": OTHER_PRIV}))
    assert "401" in str(exc.value)


def test_offline_mode_refuses_the_exchange(fake, monkeypatch):
    monkeypatch.setattr(github_auth.settings, "AI_OFFLINE_MODE", True)
    with pytest.raises(github_auth.GitHubAppUnavailable, match="AI_OFFLINE_MODE"):
        asyncio.run(github_auth.installation_token("http://github.fake", 777, APP))


def _row(kind, installation=None):
    return SimpleNamespace(auth_kind=kind, installation_id=installation, api_base_url="http://github.fake")


def test_the_one_reader_picks_the_credential(fake, monkeypatch):
    from app.services import secret_service

    async def _secret(db, scope, key):
        return "fake-gh-pat" if scope == github_auth.PAT_SCOPE else None

    async def _app(db):
        return APP

    monkeypatch.setattr(secret_service, "read_secret", _secret)
    monkeypatch.setattr(github_auth, "load_app_config", _app)
    pid = uuid.uuid4()
    pat = asyncio.run(github_auth.get_github_token(None, pid, integration=_row("pat")))
    assert pat.kind == "pat" and pat.token == "fake-gh-pat"
    app = asyncio.run(github_auth.get_github_token(None, pid, integration=_row("app", 777)))
    assert app.kind == "app_installation" and app.token.startswith("ghs_")
    assert "ghs_" not in repr(app) and "fake-gh-pat" not in repr(pat)


def test_an_app_project_without_installation_or_app_never_falls_back_to_the_pat(fake, monkeypatch):
    from app.services import secret_service

    async def _secret(db, scope, key):
        return "fake-gh-pat"

    async def _no_app(db):
        return None

    monkeypatch.setattr(secret_service, "read_secret", _secret)
    monkeypatch.setattr(github_auth, "load_app_config", _no_app)
    pid = uuid.uuid4()
    assert asyncio.run(github_auth.get_github_token(None, pid, integration=_row("app"))) is None
    assert asyncio.run(github_auth.get_github_token(None, pid, integration=_row("app", 777))) is None
    with pytest.raises(github_auth.GitHubAppUnavailable, match="not configured"):
        asyncio.run(github_auth.get_github_token(None, pid, integration=_row("app", 777), raise_app_errors=True))
    with pytest.raises(github_auth.GitHubAppUnavailable, match="not installed"):
        asyncio.run(github_auth.get_github_token(None, pid, integration=_row("app"), raise_app_errors=True))


def test_the_install_state_is_signed_user_bound_and_expires():
    from app.routers.github_app import sign_state, verify_state

    pid, uid = uuid.uuid4(), uuid.uuid4()
    state = sign_state(pid, uid)
    assert verify_state(state, uid) == pid
    for bad, who, now in (
        (state, uuid.uuid4(), None),                                  # another user
        (state[:-2] + ("00" if not state.endswith("00") else "11"), uid, None),  # tampered
        (sign_state(pid, uid, now=time.time() - 700), uid, None),   # expired
        ("garbage.value", uid, None),
    ):
        with pytest.raises(HTTPException) as exc:
            verify_state(bad, who, now=now)
        assert exc.value.status_code == 400


def test_only_github_auth_reads_the_github_credentials():
    """The PAT secret and the App private key have one reader; writes stay with their owners."""
    offenders = []
    for path in APP_ROOT.rglob("*.py"):
        rel = path.relative_to(APP_ROOT).as_posix()
        if rel == "services/github_auth.py":
            continue
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"read_secret\(([^)]*)\)", src, re.S):
            args = m.group(1)
            if "SECRET_SCOPE" in args and ("gh." in args or "github" in rel or "fixer" in rel or "codeowners" in rel) \
                    or "github_integration" in args or "APP_SCOPE" in args or "github_app" in args:
                offenders.append(f"{rel}: read_secret({' '.join(args.split())[:60]})")
    assert offenders == []
