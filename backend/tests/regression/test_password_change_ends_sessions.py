"""Regression guard: changing a password must end that user's sessions.

The defect this guards
----------------------
``POST /projects/{id}/default-qa-lead/reset-password`` rotated the stored
hash and nothing else. That endpoint is the documented remediation for the
shared hard-coded QA-lead credential, so "rotated" was supposed to mean the
leaked credential stopped granting access. It did not:

* the attacker's **access token** kept working for the rest of its lifetime
  (``JWT_ACCESS_TOKEN_EXPIRE_MINUTES``, default 720 — twelve hours), and
* their **refresh token** kept minting brand-new access tokens, so the
  access stayed live indefinitely.

Verified against the live deployment before the fix. After a reset:

    POST /api/v1/auth/login  (old password)  -> 401     <- looks fixed
    GET  /api/v1/auth/me     (pre-reset tok) -> 200     <- is not
    POST /api/v1/auth/refresh(pre-reset tok) -> 200, new access token
    GET  /api/v1/users       (that new token)-> 200

``/auth/change-password`` and ``/auth/first-time-reset`` had always done
both revocations. This was a control wired to two of the three paths that
change a password — which is why the guard below is written against **every**
such path rather than against the one that was broken.

Why the guard walks the AST
---------------------------
A substring search for the helper names would be satisfied by the docstring
that explains them — the fixed function names both helpers in prose. The
guard therefore looks for real ``ast.Call`` nodes, resolving import aliases
first (``auth.py`` imports ``_revoke_family as _revoke_refresh_family``), so
only an actual invocation counts.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

# The two revocation scopes a password change has to cover. Names are the
# ORIGINAL (pre-alias) symbols; aliases are resolved per module below.
ACCESS_TOKEN_REVOKERS = {"revoke_all_user_tokens"}
REFRESH_FAMILY_REVOKERS = {"_revoke_family"}


def _alias_map(tree: ast.Module) -> dict[str, str]:
    """local name -> original name, for ``from x import a as b``."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out[alias.asname or alias.name] = alias.name
    return out


def _called_names(fn: ast.AST, aliases: dict[str, str]) -> set[str]:
    """Every function name actually CALLED in ``fn``, alias-resolved.

    Deliberately not a substring scan: a docstring naming the helper must
    not satisfy this guard.
    """
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        local = None
        if isinstance(func, ast.Name):
            local = func.id
        elif isinstance(func, ast.Attribute):
            local = func.attr
        if local:
            names.add(aliases.get(local, local))
    return names


def _password_change_sites() -> list[tuple[pathlib.Path, ast.FunctionDef, dict[str, str]]]:
    """Every production function that reassigns an EXISTING user's password.

    A constructor keyword (``User(hashed_password=...)``) is account
    creation, which has no prior session to end. Only an attribute
    assignment onto an already-persisted row is a password *change*.
    """
    sites: list[tuple[pathlib.Path, ast.FunctionDef, dict[str, str]]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _alias_map(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            mutates = any(
                isinstance(target, ast.Attribute) and target.attr == "hashed_password"
                for node in ast.walk(fn)
                if isinstance(node, ast.Assign)
                for target in node.targets
            )
            if mutates:
                sites.append((path, fn, aliases))
    return sites


@pytest.mark.regression
def test_the_guard_can_see_the_password_change_sites():
    """Fail-open check: an empty scan must not read as 'all clear'.

    If the AST walk stops matching (a refactor to ``setattr``, a moved
    ``app/`` root), the guard below would pass by finding nothing. This
    asserts it still finds the known paths first.
    """
    sites = _password_change_sites()
    found = {(p.name, fn.name) for p, fn, _ in sites}
    assert len(sites) >= 3, f"AST scan found too few password-change sites: {found}"
    assert any(p.name == "default_qa_lead_service.py" for p, _, _ in sites), found
    assert any(p.name == "auth.py" for p, _, _ in sites), found


@pytest.mark.regression
def test_every_password_change_revokes_access_tokens():
    offenders = []
    for path, fn, aliases in _password_change_sites():
        if not (_called_names(fn, aliases) & ACCESS_TOKEN_REVOKERS):
            offenders.append(f"{path.name}::{fn.name}")
    assert not offenders, (
        "These functions change an existing user's password without revoking "
        f"their access tokens, so a stolen token outlives the rotation: {offenders}. "
        f"Call one of {sorted(ACCESS_TOKEN_REVOKERS)}."
    )


@pytest.mark.regression
def test_every_password_change_revokes_the_refresh_family():
    offenders = []
    for path, fn, aliases in _password_change_sites():
        if not (_called_names(fn, aliases) & REFRESH_FAMILY_REVOKERS):
            offenders.append(f"{path.name}::{fn.name}")
    assert not offenders, (
        "These functions change an existing user's password without revoking "
        "their refresh tokens, so the holder can keep minting fresh access "
        f"tokens forever: {offenders}. Call one of {sorted(REFRESH_FAMILY_REVOKERS)}."
    )


@pytest.mark.regression
def test_a_docstring_mentioning_the_helper_does_not_satisfy_the_guard():
    """The guard must key on a call, not on the word appearing in the file.

    The fixed ``reset_default_qa_lead_password`` explains both helpers in its
    docstring. A substring-based guard would have been satisfied by that
    prose alone and would never have failed on the real defect.
    """
    module = ast.parse(
        "def f():\n"
        "    '''Calls revoke_all_user_tokens and _revoke_family.'''\n"
        "    user.hashed_password = h\n"
    )
    fn = module.body[0]
    assert _called_names(fn, {}) & ACCESS_TOKEN_REVOKERS == set()
    assert _called_names(fn, {}) & REFRESH_FAMILY_REVOKERS == set()


@pytest.mark.regression
@pytest.mark.asyncio
async def test_qa_lead_reset_revokes_both_scopes_for_that_user(monkeypatch):
    """Behavioural half: the reset actually invokes both revocations.

    The static guards above prove the calls exist in the source; this proves
    they run, for the right user, on the caller's session.
    """
    import app.services.default_qa_lead_service as svc

    access_calls: list[tuple[object, uuid.UUID]] = []
    refresh_calls: list[tuple[object, uuid.UUID]] = []

    async def fake_access(user_id, db=None):
        access_calls.append((db, user_id))

    async def fake_refresh(db, user_id, reason="revoked"):
        refresh_calls.append((db, user_id))

    monkeypatch.setattr(svc, "revoke_all_user_tokens", fake_access)
    monkeypatch.setattr(svc, "_revoke_refresh_family", fake_refresh)

    user_id = uuid.uuid4()
    existing_user = SimpleNamespace(
        id=user_id,
        role="QA_LEAD",
        hashed_password="legacy-hash",
        must_change_password=True,
    )
    project = SimpleNamespace(
        id=uuid.uuid4(),
        slug="demo",
        name="Demo Project",
        default_qa_lead_user_id=user_id,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=existing_user)
    db.flush = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())

    await svc.reset_default_qa_lead_password(db, project)

    assert [uid for _, uid in access_calls] == [user_id]
    assert [uid for _, uid in refresh_calls] == [user_id]
    # Both durable revocations must ride the CALLER's transaction, so they
    # commit with the new hash or not at all. A separate cutoff transaction can
    # also self-block on the user row this session holds FOR UPDATE.
    assert access_calls[0][0] is db
    assert refresh_calls[0][0] is db
    assert existing_user.hashed_password != "legacy-hash"
