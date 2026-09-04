"""Regression: releases can actually be pulled in from GitHub and Jira (W2).

Two findings, and shipping either alone would have been wrong
-------------------------------------------------------------
``sync_milestones`` and ``sync_fix_versions`` shipped complete, gated and
tested, and **nothing called either of them**. The knock-on is bigger than two
idle functions: rung 2 of the attribution ladder matches a run against a release
carrying a ``source_system``, and nothing in the product created one — so that
rung could never fire in production.

And ``jira_release_sync`` read its credential from ``settings.JIRA_API_TOKEN``.
A deployment configured through Settings → Integrations keeps that token in the
SECRET SERVICE — ``resolve_jira_config`` exists precisely because "the stored
AppSetting value is stripped of secrets". So an endpoint wired to the old code
would have refused to run, with "No Jira credential configured", on a Jira that
works for every other feature in the product. Fixing the credential source is a
precondition for wiring, not a bonus.

What is deliberately NOT shared with ``defect_jira_service``
-------------------------------------------------------------
Its ``_jira_get`` issues requests with no SSRF check, and the Jira domain is
operator-configurable — an SSRF sink by design. This module keeps its own
egress, and keeps being the only one in the file.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from fastapi import HTTPException  # noqa: E402

from app.routers import releases as router_mod  # noqa: E402
from app.services import github_release_sync as gh  # noqa: E402
from app.services import jira_release_sync as jira  # noqa: E402

PROJECT = uuid.uuid4()


class _User:
    id = uuid.uuid4()


class _Session:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def execute(self, *a, **kw):
        class _R:
            def scalar_one_or_none(self_inner):
                return None

        return _R()


def _body(source, key=None):
    return router_mod.ReleaseSyncIn(
        project_id=str(PROJECT), source=source, jira_project_key=key
    )


def _patch_scope(mp, scoped=PROJECT):
    async def _scope(_db, _user, _pid):
        return scoped, None

    mp.setattr(router_mod, "resolve_project_scope", _scope)


# ── The credential comes from where the product stores it ────────────────────


def test_the_jira_credential_is_resolved_not_read_from_settings(monkeypatch):
    """The bug that would have made a freshly-wired endpoint useless.

    ``settings`` carries no token — which is the normal state for a deployment
    configured through the Settings UI, because the AppSetting value is
    stripped of secrets and the real token lives in the secret service. If the
    gate still consults ``settings`` directly, this raises "No Jira credential
    configured" on a fully-working Jira.
    """
    import asyncio

    for k, v in {
        "AI_OFFLINE_MODE": False,
        "JIRA_ENABLED": False,
        "JIRA_DOMAIN": None,
        "JIRA_EMAIL": None,
        "JIRA_API_TOKEN": None,
    }.items():
        monkeypatch.setattr(jira.settings, k, v, raising=False)

    async def _resolved(_db):
        return {
            "enabled": True,
            "domain": "x.atlassian.net",
            "email": "a@b.c",
            "api_token": "from-the-secret-service",
            "default_project_key": "QA",
        }

    monkeypatch.setattr(jira, "resolve_jira_config", _resolved)

    async def _allowed(_url):
        return None

    monkeypatch.setattr(jira, "_ssrf_block_reason", _allowed)

    seen = {}

    class _Resp:
        status_code = 200
        content = b"[]"

        def json(self):
            return []

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            seen["url"] = url
            seen["auth"] = headers["Authorization"]
            return _Resp()

    monkeypatch.setattr(jira.httpx, "AsyncClient", _Client)

    asyncio.run(jira._authorized_get(_Session(), "/project/QA/versions"))
    assert seen["url"].startswith("https://x.atlassian.net/"), (
        "the domain came from settings, not the resolved config"
    )
    assert seen["auth"].startswith("Basic "), "no credential was sent"


def test_offline_mode_still_outranks_the_resolved_config(monkeypatch):
    """The kill switch is process config, not a per-deployment document.

    An air-gapped install must not egress because somebody enabled the
    integration in the UI — so ``AI_OFFLINE_MODE`` is checked BEFORE the config
    is even resolved.
    """
    import asyncio

    monkeypatch.setattr(jira.settings, "AI_OFFLINE_MODE", True, raising=False)

    async def _resolved(_db):
        raise AssertionError("config resolved before the offline gate")

    monkeypatch.setattr(jira, "resolve_jira_config", _resolved)

    with pytest.raises(jira.JiraSyncUnavailable, match="AI_OFFLINE_MODE"):
        asyncio.run(jira._authorized_get(_Session(), "/x"))


def test_the_gate_still_says_WHICH_half_is_missing(monkeypatch):
    """``availability_reason`` collapses "no domain" and "no credential" into
    one ``not_configured``. A reader who has to fix this needs to know which,
    so the four distinct messages are kept rather than delegated."""
    import asyncio

    monkeypatch.setattr(jira.settings, "AI_OFFLINE_MODE", False, raising=False)

    def _cfg(**over):
        base = {
            "enabled": True, "domain": "x.atlassian.net",
            "email": "a@b.c", "api_token": "tok", "default_project_key": "QA",
        }
        base.update(over)

        async def _resolved(_db):
            return base

        return _resolved

    for over, expected in [
        ({"enabled": False}, "not enabled"),
        ({"domain": None}, "domain"),
        ({"api_token": None}, "credential"),
        ({"email": None}, "credential"),
    ]:
        monkeypatch.setattr(jira, "resolve_jira_config", _cfg(**over))
        with pytest.raises(jira.JiraSyncUnavailable, match=expected):
            asyncio.run(jira._authorized_get(_Session(), "/x"))


def test_the_default_project_key_is_resolved_too(monkeypatch):
    """The same override story as the credential.

    A deployment sets its Jira project key through Settings -> Integrations,
    so falling back to ``settings.JIRA_DEFAULT_PROJECT_KEY`` asks the wrong
    Jira project for its versions — and answers "0 created", which reads
    exactly like a project with no fix versions rather than like a
    misconfiguration.
    """
    import asyncio

    monkeypatch.setattr(
        jira.settings, "JIRA_DEFAULT_PROJECT_KEY", "FROM-ENV", raising=False
    )

    async def _resolved(_db):
        return {
            "enabled": True, "domain": "x.atlassian.net", "email": "a@b.c",
            "api_token": "tok", "default_project_key": "FROM-UI",
        }

    monkeypatch.setattr(jira, "resolve_jira_config", _resolved)

    asked = {}

    async def _get(db, path, params=None):
        asked["path"] = path
        return []

    monkeypatch.setattr(jira, "_authorized_get", _get)

    asyncio.run(jira.sync_fix_versions(_Session(), PROJECT))
    assert asked["path"] == "/project/FROM-UI/versions", (
        "the sync asked the env-configured Jira project, ignoring the "
        "deployment's own setting"
    )


# ── The endpoint reaches both syncs ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_github_source_reaches_sync_milestones():
    called = {}

    async def _sync(db, project_id):
        called["project_id"] = project_id
        return {"created": 2, "updated": 1, "skipped": 0}

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp)
        mp.setattr(gh, "sync_milestones", _sync)
        out = await router_mod.sync_releases_from_external(
            body=_body("github"), db=db, current_user=_User()
        )

    assert called["project_id"] == PROJECT
    assert out == {"source": "github", "created": 2, "updated": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_jira_source_reaches_sync_fix_versions():
    called = {}

    async def _sync(db, project_id, key=None):
        called["key"] = key
        return {"created": 1, "updated": 0, "skipped": 3}

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp)
        mp.setattr(jira, "sync_fix_versions", _sync)
        out = await router_mod.sync_releases_from_external(
            body=_body("jira", key="QA"), db=db, current_user=_User()
        )

    assert called["key"] == "QA"
    assert out["source"] == "jira"


@pytest.mark.asyncio
async def test_the_router_commits_because_the_services_do_not():
    """One commit covers the whole sync, so a failure part-way through leaves
    no half-imported set of releases."""
    async def _sync(db, project_id):
        return {"created": 1, "updated": 0, "skipped": 0}

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp)
        mp.setattr(gh, "sync_milestones", _sync)
        await router_mod.sync_releases_from_external(
            body=_body("github"), db=db, current_user=_User()
        )
    assert db.commits == 1


@pytest.mark.asyncio
async def test_a_blocked_sync_becomes_a_503_carrying_the_gates_own_words():
    """"AI_OFFLINE_MODE is enabled" and "No Jira domain configured" are
    different problems with different fixes. Collapsing them is how an operator
    re-checks credentials that were never the issue."""
    async def _sync(db, project_id, key=None):
        raise jira.JiraSyncUnavailable("No Jira domain configured")

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp)
        mp.setattr(jira, "sync_fix_versions", _sync)
        with pytest.raises(HTTPException) as exc:
            await router_mod.sync_releases_from_external(
                body=_body("jira"), db=db, current_user=_User()
            )
    assert exc.value.status_code == 503
    assert "domain" in str(exc.value.detail)
    assert db.commits == 0, "a failed sync must not commit"


@pytest.mark.asyncio
async def test_the_body_carried_project_is_access_checked():
    """``require_role(QA_LEAD)`` gates by ROLE, never by project membership,
    and the architectural ratchet matches ``project_id`` only in the PATH — so
    without this call a QA lead of one project could sync releases into any
    other project on the deployment. Same repair as ``create_release``.
    """
    checked = {}

    async def _scope(_db, _user, pid):
        checked["pid"] = pid
        return PROJECT, None

    async def _sync(db, project_id):
        return {"created": 0, "updated": 0, "skipped": 0}

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(router_mod, "resolve_project_scope", _scope)
        mp.setattr(gh, "sync_milestones", _sync)
        await router_mod.sync_releases_from_external(
            body=_body("github"), db=db, current_user=_User()
        )
    assert checked["pid"] == str(PROJECT), (
        "the body's project_id never reached an access check"
    )


@pytest.mark.asyncio
async def test_an_unresolvable_project_is_a_400_not_a_project_wide_sync():
    async def _scope(_db, _user, _pid):
        return None, None

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(router_mod, "resolve_project_scope", _scope)
        with pytest.raises(HTTPException) as exc:
            await router_mod.sync_releases_from_external(
                body=_body("github"), db=db, current_user=_User()
            )
    assert exc.value.status_code == 400


def test_an_unknown_source_is_refused_by_the_schema():
    """A closed set, so a typo is a 422 naming the valid sources rather than a
    silent no-op reporting "0 created" — which reads exactly like an empty
    milestone list."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        router_mod.ReleaseSyncIn(project_id=str(PROJECT), source="gitlab")
