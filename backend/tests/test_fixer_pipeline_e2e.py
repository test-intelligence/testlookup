"""End-to-end Fixer orchestration per mode + budgets + kill switch (AI-2).

Drives ``workflow.run_fixer_run`` with a FakeRunner and a stub generator over
an in-memory fake session, so the full stage machine (select → diagnose →
generate → glob → validate → PR), the budgets, and the kill switch are
exercised without a live database. The validated-only-PR invariant is asserted
here too (shadow never PRs; failed/error never PR).
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer import pipeline, workflow  # noqa: E402
from app.agents.fixer.runners import FakeRunner, NoRunner  # noqa: E402
from app.agents.fixer.state import (  # noqa: E402
    RESULT_ERROR,
    RESULT_FAILED,
    RESULT_VALIDATED,
    FixCandidate,
)
from app.models.postgres import FixAttempt  # noqa: E402
from app.services import fixer_service  # noqa: E402

PROJECT_ID = uuid.uuid4()

TEST_ONLY_PATCH = (
    "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n-assert flaky\n+assert True\n"
)
PRODUCT_PATCH = (
    "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x\n+y\n"
)


class _Result:
    def __init__(self, store):
        self._store = store

    def scalar(self):
        return 0  # _count_open_prs → no open PRs

    def scalar_one(self):
        return self._store.last

    def scalar_one_or_none(self):
        return self._store.last

    def scalars(self):
        store = self._store

        class _S:
            def all(self_inner):
                return list(store.attempts)
        return _S()


class _Store:
    def __init__(self):
        self.attempts: list[FixAttempt] = []
        self.last = None
        self.ledger = []


class _FakeSession:
    def __init__(self, store):
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        if isinstance(obj, FixAttempt):
            self._store.attempts.append(obj)
            self._store.last = obj
        else:
            self._store.ledger.append(obj)

    async def flush(self):
        for obj in self._store.attempts + self._store.ledger:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        return None

    async def execute(self, *_a, **_k):
        return _Result(self._store)


def _wire(monkeypatch, store, *, config, candidates, kill_switch=False, github=None,
          pr_result=None):
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: _FakeSession(store))

    async def _cfg(_db, _pid):
        return config
    monkeypatch.setattr(fixer_service, "get_effective_config", _cfg)

    async def _select(_db, _pid):
        return list(candidates)
    monkeypatch.setattr(pipeline, "select_candidates", _select)

    async def _diag(_db, _pid, _cand):
        return {"text": "flip 60%", "sources": ["flip_history"]}
    monkeypatch.setattr(pipeline, "gather_diagnosis", _diag)

    async def _kill(_pid):
        return kill_switch
    monkeypatch.setattr(workflow, "_kill_switch_tripped", _kill)

    async def _ghctx(_pid):
        return github
    monkeypatch.setattr(workflow, "_github_ctx", _ghctx)

    async def _open_pr(**_k):
        return pr_result or {"error": "no pr"}
    monkeypatch.setattr(pipeline, "open_draft_pr", _open_pr)

    import app.services.pipeline_event_log as pel

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(pel, "emit_event", _noop)


def _config(mode="shadow", runner_type="none", **budget_over):
    budgets = {
        "max_tests_per_run": 3, "max_attempts_per_test": 2,
        "validation_reruns": 5, "max_concurrent_open_prs": 2,
    }
    budgets.update(budget_over)
    return {
        "enabled": True, "mode": mode,
        "runner": {"type": runner_type, "runner_image": "python:3.11",
                   "command_template": "pytest {test_selector}", "workflow_ref": None},
        "test_globs": ["tests/**", "**/*.spec.*", "**/*.test.*"],
        "budgets": budgets, "schedule": "off",
    }


def _cand(fp="a" * 12, prior=0, name="tests/test_x.py::test_k"):
    return FixCandidate(test_fingerprint=fp, test_name=name, suite_name="s",
                        flip_rate=0.6, flip_window_size=10, prior_attempts=prior)


async def _gen_ok(**_k):
    return {"can_fix": True, "patch": TEST_ONLY_PATCH, "tokens": 42, "reasoning": "seed rng"}


async def _gen_product(**_k):
    return {"can_fix": True, "patch": PRODUCT_PATCH, "tokens": 10, "reasoning": "touch src"}


async def _gen_none(**_k):
    return {"can_fix": False, "patch": None, "tokens": 0, "reasoning": "offline"}


def _status(store):
    return [a.status for a in store.attempts]


# ── Shadow: validated, NEVER a PR ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_validated_no_pr(monkeypatch):
    store = _Store()
    _wire(monkeypatch, store, config=_config("shadow", "docker"), candidates=[_cand()])
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_VALIDATED), generate_fn=_gen_ok,
    )
    assert _status(store) == ["validated"]
    assert all(a.pr_url is None for a in store.attempts)
    assert store.attempts[0].validation_reruns == 5


# ── Suggest: validated → draft PR ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suggest_validated_opens_draft_pr(monkeypatch):
    store = _Store()
    _wire(
        monkeypatch, store, config=_config("suggest", "docker"), candidates=[_cand()],
        github={"api_base_url": "https://api.github.com", "repo_owner": "o",
                "repo_name": "r", "pat": "t", "repo_url": "https://github.com/o/r.git"},
        pr_result={"pr_url": "https://github.com/o/r/pull/7", "pr_number": 7},
    )
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_VALIDATED), generate_fn=_gen_ok,
    )
    assert _status(store) == ["pr_opened"]
    a = store.attempts[0]
    assert a.pr_url == "https://github.com/o/r/pull/7"
    assert a.pr_number == 7 and a.pr_state == "open"


@pytest.mark.asyncio
async def test_suggest_validated_no_github_stays_validated(monkeypatch):
    store = _Store()
    _wire(monkeypatch, store, config=_config("suggest", "docker"), candidates=[_cand()], github=None)
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_VALIDATED), generate_fn=_gen_ok,
    )
    assert _status(store) == ["validated"]
    assert store.attempts[0].pr_url is None


# ── failed / error / no-runner NEVER open a PR ───────────────────────────────


@pytest.mark.asyncio
async def test_failed_validation_no_pr(monkeypatch):
    store = _Store()
    _wire(monkeypatch, store, config=_config("suggest", "docker"), candidates=[_cand()],
          github={"api_base_url": "x", "repo_owner": "o", "repo_name": "r", "pat": "t", "repo_url": "u"},
          pr_result={"pr_url": "SHOULD-NOT-BE-USED", "pr_number": 1})
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_FAILED), generate_fn=_gen_ok,
    )
    assert _status(store) == ["failed_validation"]
    assert all(a.pr_url is None for a in store.attempts)


@pytest.mark.asyncio
async def test_error_no_pr(monkeypatch):
    store = _Store()
    _wire(monkeypatch, store, config=_config("suggest", "docker"), candidates=[_cand()],
          github={"api_base_url": "x", "repo_owner": "o", "repo_name": "r", "pat": "t", "repo_url": "u"},
          pr_result={"pr_url": "SHOULD-NOT-BE-USED", "pr_number": 1})
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_ERROR), generate_fn=_gen_ok,
    )
    assert _status(store) == ["error"]
    assert all(a.pr_url is None for a in store.attempts)


@pytest.mark.asyncio
async def test_no_runner_no_pr(monkeypatch):
    store = _Store()
    _wire(monkeypatch, store, config=_config("suggest", "none"), candidates=[_cand()],
          github={"api_base_url": "x", "repo_owner": "o", "repo_name": "r", "pat": "t", "repo_url": "u"})
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=NoRunner(), generate_fn=_gen_ok,
    )
    assert _status(store) == ["error"]
    assert all(a.pr_url is None for a in store.attempts)


# ── Glob rejection BEFORE execution ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_product_patch_rejected_before_validation(monkeypatch):
    store = _Store()
    seen = {"validated": False}

    class _Watch(FakeRunner):
        async def run_validation(self, spec):
            seen["validated"] = True
            return await super().run_validation(spec)

    _wire(monkeypatch, store, config=_config("suggest", "docker"), candidates=[_cand()])
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=_Watch(outcome=RESULT_VALIDATED), generate_fn=_gen_product,
    )
    assert _status(store) == ["rejected_globs"]
    assert seen["validated"] is False  # runner never invoked


# ── Offline / no fix generated → error, no PR ────────────────────────────────


@pytest.mark.asyncio
async def test_no_fix_generated_records_error(monkeypatch):
    store = _Store()
    _wire(monkeypatch, store, config=_config("shadow", "docker"), candidates=[_cand()])
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_VALIDATED), generate_fn=_gen_none,
    )
    assert _status(store) == ["error"]


# ── Budgets ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_tests_per_run_caps_candidates(monkeypatch):
    store = _Store()
    cands = [_cand(fp=f"{i:012d}") for i in range(5)]
    _wire(monkeypatch, store, config=_config("shadow", "docker", max_tests_per_run=2), candidates=cands)
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_VALIDATED), generate_fn=_gen_ok,
    )
    assert len(store.attempts) == 2  # only 2 processed


@pytest.mark.asyncio
async def test_max_attempts_per_test_skips_budget(monkeypatch):
    store = _Store()
    # prior_attempts already at the cap (2) → skipped_budget.
    _wire(monkeypatch, store, config=_config("shadow", "docker", max_attempts_per_test=2),
          candidates=[_cand(prior=2)])
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_VALIDATED), generate_fn=_gen_ok,
    )
    assert _status(store) == ["skipped_budget"]


# ── Kill switch (cooperative stop) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_stops_run(monkeypatch):
    store = _Store()
    cands = [_cand(fp=f"{i:012d}") for i in range(3)]
    _wire(monkeypatch, store, config=_config("shadow", "docker"), candidates=cands, kill_switch=True)
    await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=FakeRunner(outcome=RESULT_VALIDATED), generate_fn=_gen_ok,
    )
    # First candidate → skipped_budget (kill switch), then the run stops.
    assert _status(store) == ["skipped_budget"]
    assert "kill switch" in (store.attempts[0].reason or "")
