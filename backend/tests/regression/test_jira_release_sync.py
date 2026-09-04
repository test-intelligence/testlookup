"""Regression: Jira fix-version sync (S3c).

The sibling of ``github_release_sync``, and tested as one. Two syncs that differ
in shape are two syncs somebody must reason about separately, and the second one
is where a gate gets forgotten — so several of these tests assert the two agree
rather than testing this one in isolation.

Three gates, and the order matters
-----------------------------------
``AI_OFFLINE_MODE`` is checked FIRST and is a hard kill switch: an air-gapped
deployment must not egress because somebody enabled an integration. Then the
integration flag, then a credential, then an SSRF check on the host — re-checked
at egress rather than at configuration time, which also defends against DNS
rebinding and against rows configured before the guard existed.

Keyed on id, never on name
---------------------------
Renaming a fix version must UPDATE the existing release. A name-keyed sync
orphans the release's entire run history and mints a second one, silently, the
first time somebody tidies a version string — and the damage is invisible
because both rows look plausible.

What is deliberately NOT synced
--------------------------------
Jira's ``released`` and ``archived`` booleans. They describe the version's
lifecycle in Jira, not whether this product's gate has passed. Mapping them onto
``Release.status`` would let an external tool mark a release shipped that the
gate never approved.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest

from app.services import jira_release_sync as jira

PROJECT = uuid.uuid4()


class _Session:
    def __init__(self, existing=None):
        self.added = []
        self._existing = existing

    async def execute(self, *a, **kw):
        existing = self._existing

        class _R:
            def scalar_one_or_none(self_inner):
                return existing

        return _R()

    def add(self, obj):
        self.added.append(obj)


def _version(vid="10001", name="2.4.0", description="ship it"):
    return {"id": vid, "name": name, "description": description,
            "self": f"https://x.atlassian.net/rest/api/3/version/{vid}",
            "released": False, "archived": False}


def _sync(monkeypatch, payload, session=None, **setting_overrides):
    settings_defaults = {
        "AI_OFFLINE_MODE": False, "JIRA_ENABLED": True,
        "JIRA_DOMAIN": "x.atlassian.net", "JIRA_EMAIL": "a@b.c",
        "JIRA_API_TOKEN": "tok",
    }
    settings_defaults.update(setting_overrides)
    for k, v in settings_defaults.items():
        monkeypatch.setattr(jira.settings, k, v, raising=False)

    async def _get(path, params=None):
        return payload

    monkeypatch.setattr(jira, "_authorized_get", _get)
    session = session or _Session()
    result = asyncio.run(jira.sync_fix_versions(session, PROJECT))
    return result, session


class TestTheGatesCannotBeBypassed:
    def _attempt(self, monkeypatch, **overrides):
        defaults = {
            "AI_OFFLINE_MODE": False, "JIRA_ENABLED": True,
            "JIRA_DOMAIN": "x.atlassian.net", "JIRA_EMAIL": "a@b.c",
            "JIRA_API_TOKEN": "tok",
        }
        defaults.update(overrides)
        for k, v in defaults.items():
            monkeypatch.setattr(jira.settings, k, v, raising=False)
        return asyncio.run(jira._authorized_get("/project/QA/versions"))

    def test_offline_mode_blocks_before_anything_else(self, monkeypatch):
        with pytest.raises(jira.JiraSyncUnavailable, match="AI_OFFLINE_MODE"):
            # Checked FIRST, and with every other setting valid. An air-gapped
            # deployment must not egress because somebody enabled an
            # integration.
            self._attempt(monkeypatch, AI_OFFLINE_MODE=True)

    def test_a_disabled_integration_blocks(self, monkeypatch):
        with pytest.raises(jira.JiraSyncUnavailable, match="not enabled"):
            self._attempt(monkeypatch, JIRA_ENABLED=False)

    def test_a_missing_domain_blocks(self, monkeypatch):
        with pytest.raises(jira.JiraSyncUnavailable, match="domain"):
            self._attempt(monkeypatch, JIRA_DOMAIN=None)

    def test_half_a_credential_blocks_with_a_readable_reason(self, monkeypatch):
        # Without both halves the Basic header is well-formed and useless, so
        # the request would 401 rather than fail here with something a reader
        # can act on.
        with pytest.raises(jira.JiraSyncUnavailable, match="credential"):
            self._attempt(monkeypatch, JIRA_API_TOKEN=None)
        with pytest.raises(jira.JiraSyncUnavailable, match="credential"):
            self._attempt(monkeypatch, JIRA_EMAIL=None)

    def test_a_blocked_host_is_refused_at_egress(self, monkeypatch):
        # The one gate every other test in this class raises BEFORE reaching:
        # offline, disabled, no domain and no credential all short-circuit
        # first, so nothing exercised the SSRF check and removing it survived
        # the whole suite. It is the guard that matters most here — the Jira
        # domain is operator-configurable, so it is an SSRF sink by design.
        async def _blocked(url):
            return "resolves to a private address"

        monkeypatch.setattr(jira, "_ssrf_block_reason", _blocked)

        with pytest.raises(jira.JiraSyncUnavailable, match="not allowed"):
            self._attempt(monkeypatch)

    def test_a_permitted_host_passes_the_egress_check(self, monkeypatch):
        # The mirror, so the guard cannot be satisfied by blocking everything.
        async def _allowed(url):
            return None

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
                return _Resp()

        monkeypatch.setattr(jira, "_ssrf_block_reason", _allowed)
        monkeypatch.setattr(jira.httpx, "AsyncClient", _Client)

        result = self._attempt(monkeypatch)

        assert result == []
        assert seen["url"].startswith("https://x.atlassian.net/rest/api/3/")

    def test_every_request_funnels_through_one_gate(self):
        src = inspect.getsource(jira)

        # One egress point, so a future caller cannot add a request that skips
        # a gate. If a second `httpx` call appears, this fails.
        assert src.count("httpx.AsyncClient") == 1
        # The GUARD, not the word: `AI_OFFLINE_MODE` also appears in prose
        # explaining why it is checked, and counting those made this assert
        # against 3 rather than 1.
        assert src.count("if settings.AI_OFFLINE_MODE:") == 1


class TestIdentityIsKeyedOnIdNotName:
    def test_a_new_version_creates_a_release(self, monkeypatch):
        result, session = _sync(monkeypatch, [_version()])

        assert result["created"] == 1
        assert session.added[0].external_id == "10001"
        assert session.added[0].source_system == "jira"

    def test_a_renamed_version_updates_rather_than_duplicating(self, monkeypatch):
        from app.models.postgres import Release

        existing = Release(
            id=uuid.uuid4(), project_id=PROJECT, name="2.4.0-rc",
            source_system="jira", external_id="10001",
        )
        result, session = _sync(
            monkeypatch, [_version(name="2.4.0 GA")], session=_Session(existing)
        )

        # A name-keyed sync orphans the release's entire run history and mints a
        # second row, silently, the first time somebody tidies a version string.
        assert result["updated"] == 1
        assert result["created"] == 0
        assert existing.name == "2.4.0 GA"
        assert session.added == []

    def test_a_version_with_no_name_is_skipped_not_invented(self, monkeypatch):
        result, session = _sync(monkeypatch, [_version(name="  ")])

        # A release nobody can refer to is worse than no release.
        assert result["skipped"] == 1
        assert session.added == []

    def test_a_version_with_no_id_is_skipped(self, monkeypatch):
        payload = [{"name": "2.4.0"}]
        result, _ = _sync(monkeypatch, payload)

        # Without an id there is nothing stable to key on, so the next sync
        # would create a duplicate.
        assert result["skipped"] == 1

    def test_junk_in_the_payload_is_skipped_not_fatal(self, monkeypatch):
        result, session = _sync(monkeypatch, ["not-a-dict", _version()])

        # One malformed entry must not abandon the rest of the sync.
        assert result["skipped"] == 1
        assert result["created"] == 1


class TestOnlyIdentityIsWritten:
    def test_a_synced_release_is_not_marked_auto_named(self, monkeypatch):
        _, session = _sync(monkeypatch, [_version()])

        # A person named this in Jira, so it must not trigger the "name your
        # release" prompt, and it IS eligible to become active on rotation.
        assert session.added[0].is_auto_named is False

    def test_jira_lifecycle_flags_are_not_mapped_onto_status(self, monkeypatch):
        _, session = _sync(monkeypatch, [dict(_version(), released=True, archived=True)])

        # `released` describes the version's lifecycle in Jira, not whether this
        # product's gate passed. Mapping it would let an external tool mark a
        # release shipped that the gate never approved.
        assert session.added[0].status == "planning"

    def test_the_field_projection_is_narrow(self):
        fields = jira._version_fields(dict(_version(), released=True, archived=True,
                                           startDate="2026-01-01"))

        # Only identity the external system owns. Parsing more invites writing
        # more, and criteria/policy stay TestLookup's regardless of origin.
        assert set(fields) == {"name", "external_id", "external_url", "description"}


class TestItMatchesItsGitHubSibling:
    def test_both_syncs_declare_a_source_system(self):
        from app.services import github_release_sync as gh

        assert jira.SOURCE_SYSTEM == "jira"
        assert gh.SOURCE_SYSTEM == "github"
        # Distinct, or one sync would update the other's rows.
        assert jira.SOURCE_SYSTEM != gh.SOURCE_SYSTEM

    def test_neither_sync_commits(self):
        from app.services import github_release_sync as gh

        # Repo rule: the caller owns the transaction. A commit here would also
        # split one sync across several.
        for module in (jira, gh):
            src = inspect.getsource(module)
            assert "db.commit()" not in src
            assert "db.rollback()" not in src

    def test_both_scope_the_lookup_by_project_and_source(self):
        from app.services import github_release_sync as gh

        for module in (jira, gh):
            src = inspect.getsource(module)
            # Without BOTH, a sync could update another project's release, or
            # a GitHub sync could claim a Jira-sourced row.
            assert "Release.project_id == project_id" in src
            assert "Release.source_system == SOURCE_SYSTEM" in src
            assert "Release.external_id ==" in src
