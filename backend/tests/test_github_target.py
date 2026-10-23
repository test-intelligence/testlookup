"""The ONE per-project GitHub / GitHub Enterprise resolver (W10 FAIL-09, INT-04).

``services/github_target.resolve_github_target`` reads a project's GitHub
integration and PAT into plain values (DB reads only, no HTTP); the commit
range connector, the project probes and the build-changes tool all resolve
through it. ``github_target_status`` answers the same reason without ever
decrypting the PAT, for read-time reason lines.

The three ``_connector_target`` cases (success, repo mismatch, no PAT) moved
here from ``tests/test_commit_attribution_service.py``: the connector now
resolves through this module, which reads the row through
``github_checks_service.get_integration`` -- the attribute these tests patch.
"""
from __future__ import annotations

import dataclasses
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services import commit_attribution_service as attribution
from app.services import github_checks_service as gh
from app.services import github_target as gt

PROJECT_ID = uuid.uuid4()
PAT = "ghp_fake_secret_value_1234567890"


def _integration(**overrides):
    base = dict(
        enabled=True,
        repo_owner="acme",
        repo_name="webapp",
        api_base_url="https://api.github.com",
        has_pat=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(**overrides):
    base = dict(id=uuid.uuid4(), project_id=PROJECT_ID, ci_repo="acme/webapp")
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)


def _world(*, row=None, allowed=True, pat=PAT):
    """Patches for one resolve: the integration row, the github_checks gate, the PAT."""
    lookup = AsyncMock(return_value=row)
    gate = AsyncMock(return_value=allowed)
    secret = AsyncMock(return_value=pat)
    return lookup, gate, secret


async def _resolve(lookup, gate, secret, **kwargs):
    with patch.object(gh, "get_integration", lookup), \
         patch.object(gh, "_post_allowed", gate), \
         patch("app.services.secret_service.read_secret", secret):
        return await gt.resolve_github_target(MagicMock(), PROJECT_ID, **kwargs)


# ── The moved _connector_target cases ───────────────────────────────────────


@pytest.mark.asyncio
async def test_connector_target_success(online):
    with patch.object(gh, "get_integration", AsyncMock(return_value=_integration())), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value="pat")):
        target = await attribution._connector_target(MagicMock(), _run())
    assert target is not None
    assert target.api_base == "https://api.github.com"
    assert target.repo == "acme/webapp"
    assert target.pat == "pat"


@pytest.mark.asyncio
async def test_connector_target_repo_mismatch(online):
    with patch.object(gh, "get_integration", AsyncMock(return_value=_integration())):
        assert await attribution._connector_target(MagicMock(), _run(ci_repo="other/repo")) is None


@pytest.mark.asyncio
async def test_connector_target_no_pat(online):
    with patch.object(gh, "get_integration", AsyncMock(return_value=_integration())), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value=None)):
        assert await attribution._connector_target(MagicMock(), _run()) is None


@pytest.mark.asyncio
async def test_connector_target_does_not_ask_the_gate_again(online):
    """``resolve_commit_range`` already asked ``_post_allowed``: the connector
    resolves with ``check_gate=False`` (and so also while offline)."""
    lookup, gate, secret = _world(row=_integration(), allowed=False)
    with patch.object(gh, "get_integration", lookup), \
         patch.object(gh, "_post_allowed", gate), \
         patch("app.services.secret_service.read_secret", secret):
        target = await attribution._connector_target(MagicMock(), _run())
    assert target == gt.GitHubTarget(api_base="https://api.github.com", repo="acme/webapp", pat=PAT)
    gate.assert_not_awaited()


def test_the_connector_target_is_the_github_target_with_exactly_three_fields():
    assert attribution._ConnectorTarget is gt.GitHubTarget
    assert [f.name for f in dataclasses.fields(gt.GitHubTarget)] == ["api_base", "repo", "pat"]
    target = attribution._ConnectorTarget(api_base="https://ghe.corp/api/v3", repo="a/b", pat="x")
    assert (target.api_base, target.repo, target.pat) == ("https://ghe.corp/api/v3", "a/b", "x")


# ── Every reason, in order ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_usable_integration_resolves_its_own_base_repo_and_pat(online):
    lookup, gate, secret = _world(row=_integration(api_base_url="https://ghe.corp.example.com/api/v3/"))
    res = await _resolve(lookup, gate, secret, ci_repo="ACME/WebApp")
    assert res.reason is None
    assert res.target == gt.GitHubTarget(api_base="https://ghe.corp.example.com/api/v3", repo="acme/webapp", pat=PAT)
    assert (res.api_base, res.repo, res.project_id) == (
        "https://ghe.corp.example.com/api/v3", "acme/webapp", str(PROJECT_ID),
    )
    lookup.assert_awaited_once()
    assert lookup.await_args.args[1] == PROJECT_ID
    gate.assert_awaited_once()
    assert gate.await_args.kwargs == {"project_id": PROJECT_ID}
    assert secret.await_args.args[1:] == (gh.SECRET_SCOPE, gh._secret_key(PROJECT_ID))


@pytest.mark.asyncio
async def test_offline_answers_first_and_reads_nothing(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    lookup, gate, secret = _world(row=_integration())
    res = await _resolve(lookup, gate, secret)
    assert (res.target, res.reason, res.api_base) == (None, "offline_mode", None)
    lookup.assert_not_awaited()
    gate.assert_not_awaited()
    secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_integration(online):
    lookup, gate, secret = _world(row=None)
    res = await _resolve(lookup, gate, secret)
    assert (res.target, res.reason, res.api_base, res.repo) == (None, "no_integration", None, None)
    gate.assert_not_awaited()
    secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_disabled_integration_beats_a_repo_mismatch_and_keeps_its_label(online):
    lookup, gate, secret = _world(row=_integration(enabled=False))
    res = await _resolve(lookup, gate, secret, ci_repo="other/repo")
    assert (res.target, res.reason) == (None, "integration_disabled")
    assert (res.api_base, res.repo) == ("https://api.github.com", "acme/webapp")
    secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repo_mismatch_beats_the_gate(online):
    lookup, gate, secret = _world(row=_integration(), allowed=False)
    res = await _resolve(lookup, gate, secret, ci_repo="  other/repo ")
    assert (res.target, res.reason) == (None, "repo_mismatch")
    gate.assert_not_awaited()
    secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_blank_ci_repo_is_no_mismatch(online):
    lookup, gate, secret = _world(row=_integration())
    res = await _resolve(lookup, gate, secret, ci_repo="   ")
    assert res.reason is None and res.target is not None


@pytest.mark.asyncio
async def test_the_gate_beats_the_token_and_decrypts_nothing(online):
    lookup, gate, secret = _world(row=_integration(), allowed=False)
    res = await _resolve(lookup, gate, secret)
    assert (res.target, res.reason) == (None, "github_checks_off")
    secret.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pat", [None, ""])
async def test_no_token(online, pat):
    lookup, gate, secret = _world(row=_integration(), pat=pat)
    res = await _resolve(lookup, gate, secret)
    assert (res.target, res.reason) == (None, "no_token")
    assert res.api_base == "https://api.github.com"


@pytest.mark.asyncio
async def test_check_gate_false_skips_offline_and_the_flag(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    lookup, gate, secret = _world(row=_integration(), allowed=False)
    res = await _resolve(lookup, gate, secret, check_gate=False)
    assert res.reason is None and res.target is not None
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_require_enabled_false_resolves_a_disabled_row(online):
    lookup, gate, secret = _world(row=_integration(enabled=False))
    res = await _resolve(lookup, gate, secret, require_enabled=False)
    assert res.reason is None
    assert res.target == gt.GitHubTarget(api_base="https://api.github.com", repo="acme/webapp", pat=PAT)


@pytest.mark.asyncio
async def test_a_string_project_id_reads_the_same_row(online):
    lookup, gate, secret = _world(row=_integration())
    with patch.object(gh, "get_integration", lookup), \
         patch.object(gh, "_post_allowed", gate), \
         patch("app.services.secret_service.read_secret", secret):
        res = await gt.resolve_github_target(MagicMock(), str(PROJECT_ID))
    assert lookup.await_args.args[1] == PROJECT_ID
    assert res.project_id == str(PROJECT_ID) and res.target is not None


def test_the_reason_vocabulary_is_fixed():
    assert gt.GITHUB_UNAVAILABLE_REASONS == (
        "offline_mode", "github_checks_off", "no_integration", "integration_disabled", "repo_mismatch", "no_token",
    )


# ── github_target_status: the same reason, never the PAT ────────────────────


async def _status(row, *, allowed=True, ci_repo=None):
    secret = AsyncMock(return_value=PAT)
    gate = AsyncMock(return_value=allowed)
    with patch.object(gh, "get_integration", AsyncMock(return_value=row)), \
         patch.object(gh, "_post_allowed", gate), \
         patch("app.services.secret_service.read_secret", secret):
        out = await gt.github_target_status(MagicMock(), PROJECT_ID, ci_repo=ci_repo)
    secret.assert_not_awaited()
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("row, allowed, ci_repo, expected", [
    (_integration(), True, None, (None, "https://api.github.com")),
    (None, True, None, ("no_integration", None)),
    (_integration(enabled=False), True, "other/repo", ("integration_disabled", "https://api.github.com")),
    (_integration(), False, "other/repo", ("repo_mismatch", "https://api.github.com")),
    (_integration(has_pat=False), False, None, ("github_checks_off", "https://api.github.com")),
    (_integration(has_pat=False), True, None, ("no_token", "https://api.github.com")),
], ids=["usable", "none", "disabled", "mismatch", "gate", "no-token"])
async def test_status_reads_has_pat_and_never_decrypts(online, row, allowed, ci_repo, expected):
    assert await _status(row, allowed=allowed, ci_repo=ci_repo) == expected


@pytest.mark.asyncio
async def test_status_offline_reads_nothing(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    lookup = AsyncMock(return_value=_integration())
    with patch.object(gh, "get_integration", lookup):
        assert await gt.github_target_status(MagicMock(), PROJECT_ID) == ("offline_mode", None)
    lookup.assert_not_awaited()


# ── target_label and secrecy ────────────────────────────────────────────────


@pytest.mark.parametrize("api_base, label", [
    ("https://api.github.com", "api.github.com"),
    ("https://ghe.corp.example.com/api/v3", "ghe.corp.example.com/api/v3"),
    ("https://ghe.corp.example.com/api/v3/", "ghe.corp.example.com/api/v3"),
    ("http://10.0.0.5:18093/api/v3", "10.0.0.5:18093/api/v3"),
    ("https://bot:s3cret@ghe.corp/api/v3?x=1#frag", "ghe.corp/api/v3"),
    ("ghe.corp/api/v3", "ghe.corp/api/v3"),
    ("https://" + "h" * 200 + ".example", ("h" * 200)[:120]),
    ("", None),
    ("   ", None),
    (None, None),
    ("http://[::1/api/v3", None),
])
def test_target_label(api_base, label):
    assert gt.target_label(api_base) == label


@pytest.mark.asyncio
async def test_the_pat_is_in_no_repr(online):
    lookup, gate, secret = _world(row=_integration())
    res = await _resolve(lookup, gate, secret)
    assert res.target is not None and res.target.pat == PAT
    assert PAT not in repr(res.target)
    assert PAT not in repr(res)
    assert PAT not in str(res)
