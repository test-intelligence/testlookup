"""A stored integration credential is only ever sent to the host it was set for.

Three rules, one per section:

1. The deployment's GitHub App identity is an instance-ADMIN setting. An
   installation token is minted only against the deployment's GitHub API base
   (``GITHUB_APP_API_BASE_URL``, an operator setting), never against the base
   URL a project integration row carries, which a project QA lead writes.
2. A project's stored GitHub or GitLab token was entered for one host. When
   the integration's base URL moves to another host and the same request brings
   no new token, the stored one is cleared, so a later call cannot deliver it
   to the new host.
3. A ``vault:<path>#<key>`` value is a pointer into the deployment's external
   store. Only the ADMIN settings routes may store one; a project-level route
   that accepted one would let its caller name any entry the server can read.
"""
from __future__ import annotations

import ast
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
BACKEND = Path(__file__).resolve().parents[2]
REFERENCE = "vault:testlookup/jira#api_token"


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="lead")


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


# -- 1. the GitHub App token is minted only against the deployment's base ----


def _app_row(api_base_url: str):
    return SimpleNamespace(project_id=PROJECT, auth_kind="app", installation_id=7, api_base_url=api_base_url)


@pytest.fixture
def mint(monkeypatch):
    from app.services import github_auth

    calls: list[str] = []

    async def _fake_mint(api_base, installation_id, app):
        calls.append(api_base)
        return "minted"

    monkeypatch.setattr(github_auth, "installation_token", _fake_mint)
    monkeypatch.setattr(github_auth, "load_app_config", AsyncMock(return_value={"app_id": "424242"}))
    return calls


@pytest.mark.parametrize("project_base", [
    "https://collector.example",
    "http://10.0.0.5:8195",
    "https://api.github.com.collector.example",
    "http://api.github.com",
])
def test_app_token_is_not_minted_for_a_project_chosen_host(mint, project_base):
    from app.services import github_auth

    with pytest.raises(github_auth.GitHubAppUnavailable):
        asyncio.run(github_auth.get_github_token(
            _db(), PROJECT, integration=_app_row(project_base), raise_app_errors=True))
    assert mint == []

    # The quiet variant (background posts) returns no credential and mints nothing.
    assert asyncio.run(github_auth.get_github_token(_db(), PROJECT, integration=_app_row(project_base))) is None
    assert mint == []


@pytest.mark.parametrize("project_base", ["https://api.github.com", "https://API.github.com/", "https://api.github.com:443"])
def test_app_token_is_minted_against_the_deployment_base(mint, project_base):
    from app.services import github_auth

    cred = asyncio.run(github_auth.get_github_token(
        _db(), PROJECT, integration=_app_row(project_base), raise_app_errors=True))

    assert cred is not None and cred.kind == "app_installation"
    # Always the operator's value, not the row's spelling of it.
    assert mint == ["https://api.github.com"]


def test_an_operator_can_point_the_app_at_github_enterprise(mint, monkeypatch):
    from app.core.config import settings
    from app.services import github_auth

    monkeypatch.setattr(settings, "GITHUB_APP_API_BASE_URL", "https://ghe.corp.example/api/v3")

    cred = asyncio.run(github_auth.get_github_token(
        _db(), PROJECT, integration=_app_row("https://ghe.corp.example/api/v3/"), raise_app_errors=True))
    assert cred is not None and mint == ["https://ghe.corp.example/api/v3"]

    with pytest.raises(github_auth.GitHubAppUnavailable):
        asyncio.run(github_auth.get_github_token(
            _db(), PROJECT, integration=_app_row("https://api.github.com"), raise_app_errors=True))


# -- 2. a stored token does not follow the integration to a new host ---------


@pytest.fixture
def stored(monkeypatch):
    """Record what the services ask the store to keep."""
    from app.services import secret_service

    writes: list[tuple[str, str]] = []

    async def _store(db, scope, key_name, raw_value, actor_id=None, **kwargs):
        writes.append((scope, raw_value))
        return "****"

    monkeypatch.setattr(secret_service, "store_secret", _store)
    return writes


def _github_row(api_base_url="https://api.github.com"):
    return SimpleNamespace(
        project_id=PROJECT, repo_owner="acme", repo_name="shop", api_base_url=api_base_url, enabled=True,
        has_pat=True, auth_kind="pat", pr_comment_mode="failures_only", updated_by_user_id=None, updated_at=None,
    )


def _github_upsert(monkeypatch, row, **overrides):
    from app.services import github_checks_service as svc

    monkeypatch.setattr(svc, "get_integration", AsyncMock(return_value=row))
    args = dict(project_id=PROJECT, actor=_actor(), enabled=True, repo_owner="acme", repo_name="shop",
                api_base_url=row.api_base_url, pat=None)
    args.update(overrides)
    return asyncio.run(svc.upsert_integration(_db(), **args))


def test_github_token_is_cleared_when_the_host_changes_without_a_new_one(monkeypatch, stored):
    row = _github_row()
    _github_upsert(monkeypatch, row, api_base_url="https://collector.example")

    assert row.has_pat is False
    assert stored == [("github_integration", "")]


def test_github_token_is_kept_when_the_host_is_unchanged(monkeypatch, stored):
    row = _github_row("https://ghe.corp.example/api/v3")
    _github_upsert(monkeypatch, row, api_base_url="https://GHE.corp.example/api/v3/", repo_name="other")

    assert row.has_pat is True
    assert stored == []


def test_github_token_sent_with_the_new_host_is_stored_for_it(monkeypatch, stored):
    row = _github_row()
    _github_upsert(monkeypatch, row, api_base_url="https://ghe.corp.example/api/v3", pat="value-for-the-new-host")

    assert row.has_pat is True
    assert stored == [("github_integration", "value-for-the-new-host")]


def _gitlab_row(base_url="https://gitlab.com"):
    return SimpleNamespace(
        project_id=PROJECT, base_url=base_url, project_path="acme/shop", enabled=True, has_pat=True,
        mr_comment_mode="failures_only", commit_status_enabled=True, updated_by_user_id=None,
    )


def _gitlab_upsert(monkeypatch, row, **overrides):
    from app.services import gitlab_integration_service as svc

    monkeypatch.setattr(svc, "get_integration", AsyncMock(return_value=row))
    args = dict(project_id=PROJECT, actor=_actor(), enabled=True, base_url=row.base_url, project_path="acme/shop")
    args.update(overrides)
    return asyncio.run(svc.upsert_integration(_db(), **args))


def test_gitlab_token_is_cleared_when_the_host_changes_without_a_new_one(monkeypatch, stored):
    row = _gitlab_row()
    _gitlab_upsert(monkeypatch, row, base_url="https://collector.example")

    assert row.has_pat is False
    assert stored == [("gitlab_integration", "")]


def test_gitlab_token_is_kept_when_the_host_is_unchanged(monkeypatch, stored):
    row = _gitlab_row()
    _gitlab_upsert(monkeypatch, row, base_url="https://gitlab.com/", project_path="acme/other")

    assert row.has_pat is True
    assert stored == []


def test_gitlab_token_sent_with_the_new_host_is_stored_for_it(monkeypatch, stored):
    row = _gitlab_row()
    _gitlab_upsert(monkeypatch, row, base_url="https://gitlab.corp.example", token="value-for-the-new-host")

    assert row.has_pat is True
    assert stored == [("gitlab_integration", "value-for-the-new-host")]


# -- 3. only ADMIN settings routes may store an external-store reference -----


def _store_db(row=None):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    return db


@pytest.mark.parametrize("value", [REFERENCE, "  " + REFERENCE, "vault:../etc#x"])
def test_a_reference_is_refused_by_default(value):
    from app.services import secret_service

    db = _store_db()
    with pytest.raises(secret_service.SecretReferenceNotAllowed):
        asyncio.run(secret_service.store_secret(db, "github_integration", f"project:{PROJECT}:pat", value))
    db.add.assert_not_called()


def test_a_reference_is_kept_when_the_caller_allows_it():
    from app.services import secret_service

    db = _store_db()
    asyncio.run(secret_service.store_secret(db, "integrations", "jira_api_token", REFERENCE, allow_reference=True))
    row = db.add.call_args.args[0]
    assert (row.provider, row.external_ref) == ("vault", REFERENCE)


def test_an_ordinary_value_is_still_encrypted_by_default():
    from app.services import secret_service

    db = _store_db()
    asyncio.run(secret_service.store_secret(db, "github_integration", f"project:{PROJECT}:pat", "an-ordinary-value"))
    row = db.add.call_args.args[0]
    assert row.provider == "db" and secret_service.decrypt_value(row.encrypted_value) == "an-ordinary-value"


#: The only modules that may pass ``allow_reference=True``: routes guarded by
#: ``require_role(UserRole.ADMIN)``. Adding a file here is a security decision.
_REFERENCE_ALLOWED_IN = (
    "app/routers/app_settings.py",
    "app/routers/github_app.py",
    "app/routers/sso.py",
)


def _passes_allow_reference(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and any(k.arg == "allow_reference" for k in n.keywords) for n in ast.walk(node)
    )


def test_only_admin_settings_routes_allow_references():
    offenders: list[str] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        if relative in _REFERENCE_ALLOWED_IN or relative == "app/services/secret_service.py":
            continue
        if _passes_allow_reference(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(relative)
    assert not offenders, f"allow_reference passed outside the ADMIN settings routes: {offenders}"


@pytest.mark.parametrize("module", _REFERENCE_ALLOWED_IN)
def test_every_reference_allowing_route_is_admin_only(module):
    """Each function that passes allow_reference=True carries the ADMIN guard in its own signature."""
    guard = "require_role(UserRole.ADMIN)"
    source = (BACKEND / module).read_text(encoding="utf-8")
    checked = 0
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, ast.AsyncFunctionDef) and _passes_allow_reference(fn):
            checked += 1
            assert guard in ast.get_source_segment(source, fn), f"{module}:{fn.name} allows references without {guard}"
    assert checked, f"{module} no longer passes allow_reference; remove it from this list"
