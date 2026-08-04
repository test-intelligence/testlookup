"""Python SDK wiring for local-git commit-range collection (US-8.1 follow-up).

Two transports carry the range:

  * ``TestLookupReporter.session()`` → ``POST /api/v1/stream/sessions``, which
    has a typed ``commit_range`` field on ``LiveSessionCreate``.
  * ``LiveStream`` → ``POST /api/v1/stream/ingest``, which has no typed field;
    the server copies ``meta.metadata`` verbatim into
    ``LiveSession.extra_metadata`` and reads ``extra_metadata["commit_range"]``
    back at persist time — the same channel ``ci_context`` rides.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import testlookup_reporter as tr

_RANGE = [
    {
        "sha": "a" * 40,
        "author": "Dev Example",
        "message": "fix the thing",
        "files": ["src/app.py"],
        "committed_at": "2026-08-04T10:00:00+00:00",
    }
]
_OTHER_RANGE = [{**_RANGE[0], "sha": "b" * 40, "message": "different"}]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No stray TESTLOOKUP_* / CI vars from the developer's shell."""
    for key in list(tr.os.environ):
        if key.startswith(("TESTLOOKUP_", "GITHUB_", "GITLAB_", "CI_", "CIRCLE")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(tr.ConfigLoader, "load", classmethod(lambda cls, overrides=None: {}))


def _reporter(monkeypatch, collected=_RANGE, **kwargs) -> tr.TestLookupReporter:
    monkeypatch.setattr(tr, "resolve_commit_range", lambda **_kw: collected)
    reporter = tr.TestLookupReporter(
        base_url="http://localhost:8000", token="tok",
        project_id="11111111-1111-1111-1111-111111111111", **kwargs,
    )
    reporter._http = MagicMock()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "session_id": "s-1", "session_token": "st-1", "run_id": "r-1",
        "project_id": "11111111-1111-1111-1111-111111111111", "expires_in": 3600,
    })
    reporter._http.post = AsyncMock(return_value=response)
    return reporter


def _session_payload(reporter, **kwargs) -> dict:
    asyncio.run(reporter._create_session(**kwargs))
    return reporter._http.post.call_args.kwargs["json"]


# ── Reporter / session-create path ────────────────────────────────────────────

def test_session_payload_carries_the_collected_range(monkeypatch):
    payload = _session_payload(_reporter(monkeypatch))
    assert payload["commit_range"] == _RANGE


def test_range_is_collected_once_and_reused_across_sessions(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        tr, "resolve_commit_range", lambda **kw: (calls.append(kw), _RANGE)[1],
    )
    reporter = tr.TestLookupReporter(
        base_url="http://localhost:8000", token="tok",
        project_id="11111111-1111-1111-1111-111111111111",
    )
    reporter._http = MagicMock()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "session_id": "s", "session_token": "st", "run_id": "r",
        "project_id": "p", "expires_in": 3600,
    })
    reporter._http.post = AsyncMock(return_value=response)

    asyncio.run(reporter._create_session())
    asyncio.run(reporter._create_session())
    assert len(calls) == 1, "the checkout doesn't change mid-run"


def test_explicit_per_session_range_wins(monkeypatch):
    payload = _session_payload(_reporter(monkeypatch), commit_range=_OTHER_RANGE)
    assert payload["commit_range"] == _OTHER_RANGE


def test_payload_omits_the_field_when_nothing_was_collected(monkeypatch):
    payload = _session_payload(_reporter(monkeypatch, collected=None))
    assert "commit_range" not in payload


def test_constructor_args_reach_the_collector(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        tr, "resolve_commit_range", lambda **kw: (seen.append(kw), None)[1],
    )
    tr.TestLookupReporter(
        base_url="http://localhost:8000", token="tok",
        project_id="11111111-1111-1111-1111-111111111111",
        commit_range_base="origin/main", collect_commit_range=False,
    )
    assert seen[0]["explicit_base"] == "origin/main"
    assert seen[0]["enabled"] is False


# ── LiveStream / ingest path ──────────────────────────────────────────────────

def _live_stream(monkeypatch, collected=_RANGE, **kwargs) -> tr.LiveStream:
    monkeypatch.setattr(tr, "resolve_commit_range", lambda **_kw: collected)
    return tr.LiveStream(
        api_key="tlk_test", run_id="ci-build-42",
        base_url="http://localhost:8000", **kwargs,
    )


def test_live_stream_meta_carries_the_range(monkeypatch):
    stream = _live_stream(monkeypatch)
    assert stream._meta["metadata"]["commit_range"] == _RANGE


def test_live_stream_omits_the_key_when_nothing_collected(monkeypatch):
    stream = _live_stream(monkeypatch, collected=None)
    assert "commit_range" not in (stream._meta.get("metadata") or {})


def test_caller_supplied_metadata_range_wins(monkeypatch):
    stream = _live_stream(monkeypatch, metadata={"commit_range": _OTHER_RANGE})
    assert stream._meta["metadata"]["commit_range"] == _OTHER_RANGE


def test_live_stream_range_does_not_clobber_ci_context(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/widgets")
    stream = _live_stream(monkeypatch)
    metadata = stream._meta["metadata"]
    assert metadata["ci_context"]["ci_repo"] == "acme/widgets"
    assert metadata["commit_range"] == _RANGE


def test_live_stream_constructor_args_reach_the_collector(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        tr, "resolve_commit_range", lambda **kw: (seen.append(kw), None)[1],
    )
    tr.LiveStream(
        api_key="tlk_test", run_id="ci-build-42", base_url="http://localhost:8000",
        commit_range_base="deadbeef", collect_commit_range=False,
    )
    assert seen[0]["explicit_base"] == "deadbeef"
    assert seen[0]["enabled"] is False


# ── Config surface ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,expected", [
    ("testlookup.commit_range", ("ci", "collect_commit_range")),
    ("testlookup.commit_range_base", ("ci", "commit_range_base")),
])
def test_config_file_keys_are_mapped(key, expected):
    assert tr.ConfigLoader.CANONICAL_MAP[key] == expected


@pytest.mark.parametrize("key,expected", [
    ("TESTLOOKUP_COMMIT_RANGE", ("ci", "collect_commit_range")),
    ("TESTLOOKUP_COMMIT_RANGE_BASE", ("ci", "commit_range_base")),
])
def test_env_vars_are_mapped(key, expected):
    assert tr.ConfigLoader.ENV_MAP[key] == expected


def test_config_overrides_reach_the_collector_keys():
    cfg = {"ci": {"collect_commit_range": False, "commit_range_base": "origin/main"}}
    overrides = tr._commit_range_overrides_from_config(cfg)
    assert overrides == {
        "collect_commit_range": False,
        "commit_range_base": "origin/main",
    }
