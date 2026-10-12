"""Regression pins for the 2026-07 backend-audit Fixer hardening.

Covers the audit's Fixer findings end to end:

* Blocker 1 — a failed clone whose output embeds the PAT never leaks the
  token into a stored/returned error (runner-level redaction + the
  workflow's defensive scrub).
* Blocker 2 — the workflow_dispatch poller only trusts a run it can
  attribute to ITS dispatch (correlation id, head_branch fallback); an
  unrelated completed run never yields ``validated``.
* Major 4 — ``runner_image`` is validated at the config write path AND
  re-asserted in ``build_docker_run_argv`` (``--privileged`` rejected at
  both layers).
* Major 6 — the dispatch lock fails OPEN when Redis is down and the gate
  raises ``FixerAlreadyRunning`` when the lock is held.
* Major 7 — the PR-outcome sweep advances ``pr_state`` ONLY after
  ``record_fix_outcome`` succeeds (feedback rows are never dropped).
* Major 8 — a failed ``git checkout`` is an infra error (never a
  validation of the wrong ref); SHA-like refs go through fetch+FETCH_HEAD.
* Minors — env_allowlist wiring, container naming, fail-closed kill
  switch, 503 on enqueue failure.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer.runners import (  # noqa: E402
    DockerEphemeralRunner,
    WorkflowDispatchRunner,
    build_docker_run_argv,
    redact_secrets,
)
from app.agents.fixer.state import (  # noqa: E402
    RESULT_ERROR,
    RESULT_VALIDATED,
    TestIdentity,
    ValidationSpec,
    is_valid_runner_image,
)
from app.services import fixer_service as svc  # noqa: E402

TOKEN = "ghp_SuperSecretToken123456"


def _spec(**over) -> ValidationSpec:
    defaults = dict(
        repo_url="https://github.com/o/r.git",
        ref="main",
        patch="--- a/tests/t.py\n+++ b/tests/t.py\n@@ -1 +1 @@\n-x\n+y\n",
        test_identity=TestIdentity("pytest", "pytest {test_selector}", "tests/t.py::k"),
        reruns=2,
        runner_image="python:3.11-slim",
        clone_token=TOKEN,
    )
    defaults.update(over)
    return ValidationSpec(**defaults)


class _ScriptedRunner(DockerEphemeralRunner):
    """DockerEphemeralRunner with a scripted ``_run`` (no docker needed)."""

    def __init__(self, script):
        super().__init__()
        self.calls: list[list[str]] = []
        self._script = script

    @staticmethod
    async def docker_available() -> bool:  # type: ignore[override]
        return True

    async def _run(self, argv, *, timeout_s, cwd=None):  # type: ignore[override]
        self.calls.append(list(argv))
        return self._script(argv)


# ── Blocker 1: PAT never leaks into a stored/returned error ─────────────────


def test_redact_secrets_scrubs_every_occurrence():
    text = f"fatal: https://{TOKEN}@github.com/o/r.git 403 ({TOKEN})"
    out = redact_secrets(text, TOKEN, None, "")
    assert TOKEN not in out
    assert "***" in out


def test_clone_failure_error_is_redacted():
    def script(argv):
        if argv[:2] == ["git", "clone"]:
            return 128, f"fatal: unable to access 'https://{TOKEN}@github.com/o/r.git/': 403"
        return 0, ""

    runner = _ScriptedRunner(script)
    result = asyncio.run(runner.run_validation(_spec()))
    assert result.status == RESULT_ERROR
    assert "clone failed" in (result.error or "")
    assert TOKEN not in (result.error or "")
    assert "***" in (result.error or "")


# ── Major 8: checkout failures are honest errors; SHA refs fetch first ──────


def test_checkout_failure_is_error_not_wrong_ref_validation():
    def script(argv):
        if argv[:2] == ["git", "clone"]:
            return 0, ""
        if "checkout" in argv:
            return 1, f"error: pathspec 'release-9' did not match (https://{TOKEN}@x)"
        return 0, ""

    runner = _ScriptedRunner(script)
    result = asyncio.run(runner.run_validation(_spec(ref="release-9")))
    assert result.status == RESULT_ERROR
    assert "checkout" in (result.error or "")
    assert TOKEN not in (result.error or "")


def test_sha_ref_uses_fetch_then_fetch_head():
    sha = "a" * 40

    def script(argv):
        return 0, ""

    runner = _ScriptedRunner(script)
    result = asyncio.run(runner.run_validation(_spec(ref=sha, reruns=1)))
    assert result.status == RESULT_VALIDATED
    fetches = [c for c in runner.calls if "fetch" in c and "origin" in c]
    assert fetches and sha in fetches[0]
    checkouts = [c for c in runner.calls if "checkout" in c]
    assert checkouts and "FETCH_HEAD" in checkouts[0]


def test_timeout_removes_named_container():
    seen = {"rm": None}

    def script(argv):
        if argv[:2] == ["docker", "run"]:
            return 124, "timeout"
        if argv[:2] == ["docker", "rm"]:
            seen["rm"] = list(argv)
            return 0, ""
        return 0, ""

    runner = _ScriptedRunner(script)
    result = asyncio.run(runner.run_validation(_spec(reruns=1)))
    assert result.status != RESULT_VALIDATED
    assert seen["rm"] is not None and seen["rm"][2] == "-f"
    assert seen["rm"][3].startswith("fixer-")


# ── Major 4: runner_image validated at BOTH layers ───────────────────────────


@pytest.mark.parametrize("bad", ["--privileged", "-v/etc:/etc", "img; rm -rf /", "IMG:latest", ""])
def test_runner_image_regex_rejects_flags_and_junk(bad):
    assert is_valid_runner_image(bad) is False


@pytest.mark.parametrize("good", [
    "python:3.11-slim", "ghcr.io/org/img:1.2.3", "img",
    "python@sha256:" + "a" * 64,
])
def test_runner_image_regex_accepts_real_references(good):
    assert is_valid_runner_image(good) is True


def test_put_body_rejects_privileged_as_image():
    from pydantic import ValidationError

    from app.routers.fixer import FixerRunner

    with pytest.raises(ValidationError):
        FixerRunner(type="docker", runner_image="--privileged")


def test_coerce_runner_drops_invalid_stored_image():
    coerced = svc._coerce_runner({"type": "docker", "runner_image": "--privileged"})
    assert coerced["runner_image"] is None


def test_build_docker_run_argv_raises_on_flag_image():
    with pytest.raises(ValueError):
        build_docker_run_argv(
            image="--privileged", command_tokens=["pytest"], workspace="/w",
            cpus=1.0, memory="1g", user="1000:1000",
            allow_network_egress=False, timeout_s=60,
        )


def test_runner_returns_error_for_invalid_image_before_any_clone():
    def script(argv):  # pragma: no cover — must never be called
        raise AssertionError("no subprocess should run for an invalid image")

    runner = _ScriptedRunner(script)
    result = asyncio.run(runner.run_validation(_spec(runner_image="--privileged")))
    assert result.status == RESULT_ERROR
    assert "invalid runner_image" in (result.error or "")
    assert runner.calls == []


# ── Minor: env_allowlist wiring + container naming ──────────────────────────


def test_env_allowlist_forwards_only_wellformed_names():
    argv = build_docker_run_argv(
        image="img", command_tokens=["pytest"], workspace="/w",
        cpus=1.0, memory="1g", user="1000:1000",
        allow_network_egress=False, timeout_s=60,
        name="fixer-abc", env_allowlist=("CI", "TZ", "bad-name", "-e", "A B"),
    )
    env_pairs = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-e"]
    assert env_pairs == ["CI", "TZ"]  # malformed names dropped
    assert argv[argv.index("--name") + 1] == "fixer-abc"


# ── Blocker 2: dispatched-run correlation ────────────────────────────────────


def _gh_run(**over):
    run = {
        "id": 1, "name": "Reference validation", "display_title": "unrelated push",
        "head_branch": "main", "status": "completed", "conclusion": "success",
    }
    run.update(over)
    return run


def test_match_prefers_correlation_id_then_head_branch():
    match = WorkflowDispatchRunner.match_dispatched_run
    corr = uuid.uuid4().hex
    ours = _gh_run(id=7, display_title=f"validate {corr}", head_branch="other")
    unrelated = _gh_run(id=8)
    assert match([unrelated, ours], correlation_id=corr, head_branch="main")["id"] == 7
    # No correlation echo → fall back to head_branch.
    assert match([unrelated], correlation_id=corr, head_branch="main")["id"] == 8
    # Nothing attributable → None (an unrelated run is NEVER ours).
    assert match(
        [_gh_run(head_branch="someone-elses-branch")],
        correlation_id=corr, head_branch="main",
    ) is None


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient in the dispatch runner tests."""

    runs_payload: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        return _FakeResponse(204)

    async def get(self, url, **k):
        return _FakeResponse(200, dict(_FakeAsyncClient.runs_payload))


def _dispatch_runner() -> WorkflowDispatchRunner:
    return WorkflowDispatchRunner(
        api_base_url="https://api.github.com",
        repo_owner="o", repo_name="r", pat=TOKEN,
        poll_timeout_s=1,
    )


def _run_dispatch(monkeypatch, runs_payload):
    import httpx

    from app.services import github_checks_service

    async def _no_block(_url):
        return None

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(github_checks_service, "_ssrf_block_reason", _no_block)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    _FakeAsyncClient.runs_payload = runs_payload
    spec = _spec(workflow_ref="flaky.yml", ref="main")
    return asyncio.run(_dispatch_runner().run_validation(spec))


def test_unrelated_completed_run_never_validates(monkeypatch):
    # A foreign completed run on ANOTHER branch with no correlation echo:
    # the old code would have read runs[0].conclusion and returned validated.
    result = _run_dispatch(monkeypatch, {
        "workflow_runs": [_gh_run(head_branch="not-ours", conclusion="success")],
    })
    assert result.status == RESULT_ERROR
    assert "not observed" in (result.error or "")


def test_head_branch_matched_run_is_used(monkeypatch):
    result = _run_dispatch(monkeypatch, {
        "workflow_runs": [
            _gh_run(head_branch="not-ours"),
            _gh_run(id=42, head_branch="main", conclusion="success"),
        ],
    })
    # head_branch fallback: only the run on OUR ref counts. First run in
    # list is on another branch — matcher must skip it.
    assert result.status == RESULT_VALIDATED


# ── Major 6: dispatch lock semantics ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_lock_fails_open_when_redis_unavailable(monkeypatch):
    import app.db.redis_client as redis_client

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis_client, "get_redis", _boom)
    assert await svc.acquire_fixer_run_lock(uuid.uuid4()) is True
    # Release must swallow the fault too.
    await svc.release_fixer_run_lock(uuid.uuid4())


@pytest.mark.asyncio
async def test_gate_raises_already_running_when_lock_held(monkeypatch):
    project_id = uuid.uuid4()

    async def _cfg(_db, _pid):
        return {
            "enabled": True, "mode": "shadow",
            "runner": {"type": "docker", "runner_image": "python:3.11",
                       "command_template": None, "workflow_ref": None},
            "test_globs": ["tests/**"],
            "budgets": dict(svc.DEFAULT_FIXER_BUDGETS), "schedule": "off",
        }

    async def _no_active(_db, _pid):
        return False

    async def _lock_held(_pid):
        return False

    monkeypatch.setattr(svc, "get_effective_config", _cfg)
    monkeypatch.setattr(svc, "has_active_fixer_run", _no_active)
    monkeypatch.setattr(svc, "acquire_fixer_run_lock", _lock_held)
    with pytest.raises(svc.FixerAlreadyRunning):
        await svc.gate_fixer_run(object(), project_id)


# ── Minor: kill switch fails CLOSED on read faults ───────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_read_failure_stops_the_run(monkeypatch):
    from app.agents.fixer import workflow

    async def _boom(_db, _pid):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(svc, "get_fixer_policy_row", _boom)
    assert await workflow._kill_switch_tripped(uuid.uuid4(), db=object()) is True


# ── Minor: run POST → 503 when the broker enqueue fails ─────────────────────


@pytest.mark.asyncio
async def test_run_post_returns_503_when_enqueue_fails(monkeypatch):
    from fastapi import HTTPException

    from app.routers import fixer as router_mod

    async def _gate_ok(_db, _pid):
        return {"enabled": True}

    released = {"called": False}

    async def _release(_pid):
        released["called"] = True

    monkeypatch.setattr(router_mod.svc, "gate_fixer_run", _gate_ok)
    monkeypatch.setattr(router_mod.svc, "enqueue_fixer_run", lambda *a, **k: False)
    monkeypatch.setattr(router_mod.svc, "release_fixer_run_lock", _release)

    with pytest.raises(HTTPException) as exc_info:
        await router_mod.start_fixer_run(
            project_id=uuid.uuid4(), db=object(), current_user=object(), _writer=object(),
        )
    assert exc_info.value.status_code == 503
    assert released["called"] is True  # gate's lock freed on failure


# ── Major 7: pr_state advances ONLY after record_fix_outcome succeeds ────────


class _SweepAttempt:
    def __init__(self, pr_number, fingerprint):
        self.id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.pr_number = pr_number
        self.pr_url = f"https://github.com/o/r/pull/{pr_number}"
        self.test_fingerprint = fingerprint
        self.pr_state = "open"


class _SweepResult:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._row


class _SweepSession:
    """Fake AsyncSessionLocal for the sweep: snapshot select → rows; the
    per-attempt reload → the matching attempt object."""

    attempts: list[_SweepAttempt] = []
    committed = 0
    rolled_back = 0

    def __init__(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, *a, **k):
        text = str(stmt)
        # Per-attempt reload carries a WHERE fix_attempts.id = :id bindparam.
        if "fix_attempts.id =" in text:
            try:
                wanted = list(stmt.compile().params.values())[0]
            except Exception:
                wanted = None
            for attempt in _SweepSession.attempts:
                if wanted is None or attempt.id == wanted:
                    return _SweepResult(row=attempt)
            return _SweepResult(row=None)
        # Snapshot select — return Row-like objects (the attempts themselves
        # quack correctly: .id/.project_id/.pr_number/.pr_url/.test_fingerprint).
        return _SweepResult(rows=list(_SweepSession.attempts))

    async def commit(self):
        _SweepSession.committed += 1

    async def rollback(self):
        _SweepSession.rolled_back += 1


@pytest.mark.asyncio
async def test_sweep_keeps_pr_open_when_outcome_record_fails(monkeypatch):
    from app.agents.fixer import pipeline
    from app.core.config import settings as app_settings
    from app.db import postgres as pg
    from app.services import feedback_service, github_checks_service, secret_service

    ok_attempt = _SweepAttempt(1, "fp-ok")
    bad_attempt = _SweepAttempt(2, "fp-bad")
    # Same project for both so one credential entry covers them.
    bad_attempt.project_id = ok_attempt.project_id
    _SweepSession.attempts = [ok_attempt, bad_attempt]
    _SweepSession.committed = 0
    _SweepSession.rolled_back = 0

    monkeypatch.setattr(app_settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(pg, "AsyncSessionLocal", _SweepSession)

    class _Integration:
        enabled = True
        api_base_url = "https://api.github.com"
        repo_owner = "o"
        repo_name = "r"

    async def _integration(_db, _pid):
        return _Integration()

    async def _secret(_db, _scope, _key):
        return TOKEN

    async def _no_block(_url):
        return None

    monkeypatch.setattr(github_checks_service, "get_integration", _integration)
    monkeypatch.setattr(secret_service, "read_secret", _secret)
    monkeypatch.setattr(github_checks_service, "_ssrf_block_reason", _no_block)

    class _Client(_FakeAsyncClient):
        async def get(self, url, **k):
            # Both PRs closed; #1 merged, #2 closed-unmerged.
            merged = url.endswith("/pulls/1")
            return _FakeResponse(200, {
                "state": "closed",
                "merged_at": "2026-07-16T00:00:00Z" if merged else None,
            })

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    async def _record(db, project_id, fingerprint, outcome, **k):
        if fingerprint == "fp-bad":
            raise RuntimeError("no analysis row")
        return None

    monkeypatch.setattr(feedback_service, "record_fix_outcome", _record)

    summary = await pipeline.poll_open_fixer_prs()

    assert summary["checked"] == 2
    assert ok_attempt.pr_state == "merged"          # advanced after success
    assert bad_attempt.pr_state == "open"           # NOT advanced — retried next sweep
    assert summary["merged"] == 1 and summary["closed"] == 0
    assert _SweepSession.committed >= 1
    assert _SweepSession.rolled_back >= 1
