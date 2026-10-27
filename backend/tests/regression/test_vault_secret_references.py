"""W20 ADM-10: secret references to a Vault-compatible KV v2 store, against the fake Vault.

* ``vault:<path>#<key>`` parses; a malformed or traversing reference is refused with the reason;
* a reference resolves (token and approle auth), is cached, and a change takes effect after the TTL;
* not configured / refused / missing path or key → ``None`` and ``secret_read_failures_total``, never a raise;
* ``store_secret`` keeps the pointer (no ciphertext) and ``read_secret`` resolves it; a value written over a
  reference turns the row back into an encrypted ``db`` row;
* the status route reports the host and login health, never a token.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.secret_providers import vault
from tests.fakes.fake_integrations import FakeState, create_app

ADDR = "http://vault.fake:8200"


@pytest.fixture
def fake(monkeypatch):
    state = FakeState()
    transport = httpx.ASGITransport(app=create_app(state))
    real = httpx.AsyncClient

    class Routed(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    monkeypatch.setattr(vault.httpx, "AsyncClient", Routed)
    s = vault.settings
    monkeypatch.setattr(s, "SECRET_PROVIDER_VAULT_ADDR", ADDR)
    monkeypatch.setattr(s, "SECRET_PROVIDER_VAULT_AUTH", "token")
    monkeypatch.setattr(s, "SECRET_PROVIDER_VAULT_TOKEN", "fake-vault-token")
    monkeypatch.setattr(s, "SECRET_PROVIDER_VAULT_MOUNT", "secret")
    monkeypatch.setattr(s, "SECRET_PROVIDER_VAULT_CACHE_SECONDS", 60)
    vault.clear_cache()
    yield state
    vault.clear_cache()


@pytest.mark.parametrize("text,path,key", [
    ("vault:testlookup/jira#api_token", "testlookup/jira", "api_token"),
    ("  vault:/testlookup/jira/#api_token ", "testlookup/jira", "api_token"),
])
def test_a_reference_parses(text, path, key):
    ref = vault.parse_reference(text)
    assert (ref.path, ref.key, str(ref)) == (path, key, f"vault:{path}#{key}")


@pytest.mark.parametrize("text", ["vault:testlookup/jira", "vault:#k", "vault:a/../b#k", "vault:a b#k", "vault:a#k?x=1"])
def test_a_malformed_reference_is_refused(text):
    with pytest.raises(vault.VaultReferenceError):
        vault.parse_reference(text)


def test_a_reference_resolves_and_is_cached_until_the_ttl(fake, monkeypatch):
    assert asyncio.run(vault.read("vault:testlookup/jira#api_token")) == "fake-jira-token"
    fake.vault_kv["testlookup/jira"] = {"api_token": "rotated"}
    assert asyncio.run(vault.read("vault:testlookup/jira#api_token")) == "fake-jira-token"  # cached
    calls = [r for r in fake.requests if r["service"] == "vault"]
    assert len(calls) == 1 and calls[0]["auth"] == "ok"
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_CACHE_SECONDS", 0)
    vault.clear_cache()
    assert asyncio.run(vault.read("vault:testlookup/jira#api_token")) == "rotated"


def test_approle_login_then_read(fake, monkeypatch):
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_AUTH", "approle")
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_ROLE_ID", "fake-role-id")
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_SECRET_ID", "fake-secret-id")
    assert asyncio.run(vault.read("vault:testlookup/jira#api_token")) == "fake-jira-token"
    paths = [r["path"] for r in fake.requests if r["service"] == "vault"]
    assert paths == ["/v1/auth/approle/login", "/v1/secret/data/testlookup/jira"]


def test_failures_answer_none_and_count(fake, monkeypatch):
    from app.core.metrics import secret_read_failures_total

    counter = secret_read_failures_total.labels(scope="vault")
    before = counter._value.get()
    assert asyncio.run(vault.read("vault:testlookup/nothing#api_token")) is None
    assert asyncio.run(vault.read("vault:testlookup/jira#nokey")) is None
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_TOKEN", "wrong")
    vault.clear_cache()
    assert asyncio.run(vault.read("vault:testlookup/jira#api_token")) is None
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_ADDR", "")
    assert asyncio.run(vault.read("vault:testlookup/jira#api_token")) is None
    assert counter._value.get() - before == 4


def _db_with(row):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    return db


def test_store_keeps_the_pointer_and_read_resolves_it(fake):
    from app.services import secret_service

    db = _db_with(None)
    masked = asyncio.run(secret_service.store_secret(db, "integrations", "jira_api_token", " vault:testlookup/jira#api_token"))
    row = db.add.call_args.args[0]
    assert (row.provider, row.external_ref, row.encrypted_value) == ("vault", "vault:testlookup/jira#api_token", None)
    assert masked == "vault:testlookup/jira#api_token"
    assert asyncio.run(secret_service.read_secret(_db_with(row), "integrations", "jira_api_token")) == "fake-jira-token"


def test_a_value_over_a_reference_becomes_an_encrypted_row(fake):
    from app.services import secret_service

    existing = SimpleNamespace(provider="vault", external_ref="vault:x#y", encrypted_value=None,
                               masked_value="vault:x#y", updated_by=None, rotation_status="active")
    asyncio.run(secret_service.store_secret(_db_with(existing), "integrations", "jira_api_token", "plain-token-value"))
    assert existing.provider == "db" and existing.external_ref is None
    assert secret_service.decrypt_value(existing.encrypted_value) == "plain-token-value"


def test_a_malformed_reference_in_a_secret_field_is_refused(fake):
    from app.services import secret_service

    with pytest.raises(vault.VaultReferenceError):
        asyncio.run(secret_service.store_secret(_db_with(None), "integrations", "jira_api_token", "vault:../etc#x"))


def test_health_reports_host_and_login_never_a_token(fake, monkeypatch):
    out = asyncio.run(vault.health())
    assert out["configured"] and out["healthy"] and out["address_host"] == "vault.fake"
    assert "fake-vault-token" not in repr(out)
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_TOKEN", "wrong")
    bad = asyncio.run(vault.health())
    assert not bad["healthy"] and "403" in bad["message"]
    monkeypatch.setattr(vault.settings, "SECRET_PROVIDER_VAULT_ADDR", "")
    assert asyncio.run(vault.health())["message"].startswith("Not configured")
