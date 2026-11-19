"""A patch that no runner executed is never presented as validated.

The ``workflow_dispatch`` runner dispatches the customer's workflow on the
default branch and does not send it the patch, so a green run only says the
test passed on the UNPATCHED code. The Fixer used to mark such an attempt
``validated`` with the reason "validated — opening draft PR", commit the
model-written patch to a branch and open a draft pull request whose body said
the change "was validated ... in an isolated sandbox".

The rule now:

* the dispatch runner's result says the patch was not applied;
* such a result never opens a pull request, in any mode. Whatever the rerun's
  outcome, the attempt is stored with status ``error`` (the attempts list must
  not show a green "validated" for a patch nobody ran, nor "failed validation"
  for a fix nobody tried) and a reason that says "baseline only", that the
  patch itself was not run, and that the fix's merit is unknown;
* the run counts it under ``baseline_only`` only — not ``validated``, not
  ``failed_validation``, not ``error``;
* the pull-request step refuses the dispatch runner itself (second layer);
* the pull-request body states what was run for the runner type used.

A runner that does apply the patch (docker) still opens the draft pull request.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer import pipeline, workflow  # noqa: E402
from app.agents.fixer.runners import (  # noqa: E402
    FakeRunner,
    ValidationRunner,
    WorkflowDispatchRunner,
)
from app.agents.fixer.state import (  # noqa: E402
    RESULT_FAILED,
    RESULT_VALIDATED,
    STATUS_ERROR,
    STATUS_VALIDATED,
    FixCandidate,
    RunAttempt,
    TestIdentity,
    ValidationResult,
    ValidationSpec,
    to_validation_result,
)
from app.agents.fixer.workflow import classify_validation_terminal  # noqa: E402
from tests.test_fixer_pipeline_e2e import (  # noqa: E402
    PROJECT_ID,
    _cand,
    _config,
    _gen_ok,
    _status,
    _Store,
    _wire,
)

pytestmark = pytest.mark.regression

SENTINEL_PATCH = (
    "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n-assert flaky\n"
    "+assert True  # SENTINEL-PATCH-BODY\n"
)

GITHUB = {
    "api_base_url": "https://api.github.com", "repo_owner": "o", "repo_name": "r",
    "pat": "not-a-real-token", "repo_url": "https://github.com/o/r.git",
    "default_branch": "main",
}


# ── The runner itself ────────────────────────────────────────────────────────


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class _DispatchClient:
    """Stands in for httpx.AsyncClient: records request bodies, sends nothing."""

    conclusion = "success"
    sent: list[str] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        _DispatchClient.sent.append(repr((url, k.get("json"))))
        return _Response(204)

    async def get(self, url, **k):
        _DispatchClient.sent.append(repr((url, k.get("params"))))
        return _Response(200, {"workflow_runs": [{
            "id": 1, "name": "validate", "display_title": "validate",
            "head_branch": "main", "status": "completed",
            "conclusion": _DispatchClient.conclusion,
        }]})


def _dispatch(monkeypatch, conclusion: str) -> ValidationResult:
    import httpx

    from app.services import github_checks_service

    async def _no_block(_url):
        return None

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(github_checks_service, "_ssrf_block_reason", _no_block)
    monkeypatch.setattr(httpx, "AsyncClient", _DispatchClient)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    _DispatchClient.conclusion = conclusion
    _DispatchClient.sent = []
    runner = WorkflowDispatchRunner(
        api_base_url="https://api.github.com", repo_owner="o", repo_name="r",
        pat="not-a-real-token", poll_timeout_s=1,
    )
    spec = ValidationSpec(
        repo_url="https://github.com/o/r.git", ref="main", patch=SENTINEL_PATCH,
        test_identity=TestIdentity("pytest", "pytest {test_selector}", "tests/test_x.py::k"),
        reruns=2, workflow_ref="flaky.yml",
    )
    return asyncio.run(runner.run_validation(spec))


@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_the_dispatch_runner_says_it_did_not_apply_the_patch(monkeypatch, conclusion):
    result = _dispatch(monkeypatch, conclusion)
    # The premise: the patch is not in any request the runner makes.
    assert _DispatchClient.sent and not any("SENTINEL" in body for body in _DispatchClient.sent)
    assert result.runs, "the dispatched run was observed"
    assert result.patch_applied is False


def test_a_runner_that_applies_the_patch_reports_it_by_default():
    result = asyncio.run(FakeRunner(outcome=RESULT_VALIDATED).run_validation(
        ValidationSpec(
            repo_url="", ref="main", patch=SENTINEL_PATCH,
            test_identity=TestIdentity("pytest", "pytest {test_selector}", "t"),
        ),
    ))
    assert result.patch_applied is True
    assert to_validation_result({"status": "validated"}).patch_applied is True
    assert to_validation_result({"status": "validated", "patch_applied": False}).patch_applied is False


# ── The decision ─────────────────────────────────────────────────────────────


def _decide(mode="suggest", status=RESULT_VALIDATED, **over):
    kwargs = dict(passed=5, reruns=5, open_pr_slots=2, github_available=True)
    if status == RESULT_FAILED:
        kwargs["passed"] = 0
    kwargs.update(over)
    return classify_validation_terminal(mode, status, **kwargs)


@pytest.mark.parametrize("mode", ["suggest", "shadow"])
@pytest.mark.parametrize("status", [RESULT_VALIDATED, RESULT_FAILED])
@pytest.mark.parametrize("over", [
    {"patch_applied": False},
    {"runner_type": "workflow_dispatch"},
    {"patch_applied": False, "runner_type": "workflow_dispatch"},
    # A dispatch result is baseline only even if something claims otherwise.
    {"patch_applied": True, "runner_type": "workflow_dispatch"},
])
def test_a_baseline_only_result_is_an_error_and_never_opens_a_pull_request(mode, status, over):
    decision = _decide(mode, status, **over)
    assert decision.open_pr is False
    assert decision.baseline_only is True
    # Neither "validated" nor "failed_validation": nobody ran the patch.
    assert decision.status == STATUS_ERROR
    assert decision.reason.startswith("baseline only")
    assert "patch itself was not run" in decision.reason
    assert "fix merit unknown" in decision.reason
    assert "opening draft PR" not in decision.reason
    assert "fix did not validate" not in decision.reason


def test_the_reason_reports_the_baseline_tally_without_judging_the_fix():
    green = _decide(runner_type="workflow_dispatch", patch_applied=False)
    red = _decide(status=RESULT_FAILED, runner_type="workflow_dispatch", patch_applied=False)
    assert "5/5 runs passed" in green.reason and "0/5 runs passed" in red.reason
    assert "validated" not in green.reason and "validated" not in red.reason


def test_a_result_from_a_runner_that_applied_the_patch_still_opens_a_pull_request():
    decision = _decide(runner_type="docker", patch_applied=True)
    assert decision.open_pr is True and decision.baseline_only is False
    assert decision.status == STATUS_VALIDATED
    # The two arguments are optional: older callers keep the patch-applied meaning.
    assert _decide().open_pr is True


# ── End to end ───────────────────────────────────────────────────────────────


class _StubDispatchRunner(ValidationRunner):
    """A dispatch-type runner whose workflow run came back green (or red)."""

    type = "workflow_dispatch"

    def __init__(self, *, claims_patch_applied: bool = False, green: bool = True) -> None:
        self._claims = claims_patch_applied
        self._green = green

    async def run_validation(self, spec: ValidationSpec) -> ValidationResult:
        return ValidationResult(
            status=RESULT_VALIDATED if self._green else RESULT_FAILED,
            runs=[RunAttempt(n=1, passed=self._green, duration_ms=0, log_digest="d")],
            patch_applied=self._claims,
        )


async def _run(monkeypatch, runner, *, runner_type):
    store = _Store()
    _wire(
        monkeypatch, store, config=_config("suggest", runner_type), candidates=[_cand()],
        github=dict(GITHUB),
    )
    opened: list[dict] = []

    async def _open_pr(**kwargs):
        opened.append(kwargs)
        return {"pr_url": "https://github.com/o/r/pull/7", "pr_number": 7}

    monkeypatch.setattr(pipeline, "open_draft_pr", _open_pr)
    outcome = await workflow.run_fixer_run(
        str(PROJECT_ID), str(uuid.uuid4()), "manual",
        runner_override=runner, generate_fn=_gen_ok,
    )
    return store, opened, outcome


@pytest.mark.parametrize("green", [True, False])
@pytest.mark.parametrize("claims", [False, True])
async def test_suggest_mode_with_the_dispatch_runner_ends_as_an_error_without_a_pull_request(
    monkeypatch, claims, green,
):
    store, opened, outcome = await _run(
        monkeypatch, _StubDispatchRunner(claims_patch_applied=claims, green=green),
        runner_type="workflow_dispatch",
    )
    assert opened == [], "the pull-request step must not be reached"
    assert _status(store) == ["error"]
    attempt = store.attempts[0]
    assert attempt.pr_url is None and attempt.pr_state is None
    assert attempt.completed_at is not None
    assert attempt.reason.startswith("baseline only")
    assert "patch itself was not run" in attempt.reason
    assert "fix merit unknown" in attempt.reason
    # The baseline tally is still recorded: it is honest data about the rerun.
    assert attempt.validation_reruns == 1 and attempt.validation_passed == (1 if green else 0)
    counters = outcome["counters"]
    assert counters["baseline_only"] == 1
    # Its own counter only: not a validated fix, not a failed fix, not an infra error.
    assert counters["validated"] == 0 and counters["failed_validation"] == 0
    assert counters["error"] == 0 and counters["pr_opened"] == 0
    assert outcome["actions_taken"] == []


async def test_suggest_mode_with_a_runner_that_applies_the_patch_still_opens_one(monkeypatch):
    store, opened, outcome = await _run(
        monkeypatch, FakeRunner(outcome=RESULT_VALIDATED), runner_type="docker",
    )
    assert _status(store) == ["pr_opened"]
    assert len(opened) == 1 and opened[0]["runner_type"] == "fake"
    assert outcome["counters"]["validated"] == 1 and outcome["counters"]["baseline_only"] == 0
    assert outcome["counters"]["pr_opened"] == 1


def test_the_run_summary_does_not_count_a_baseline_only_attempt_as_validated():
    counters = {
        "selected": 1, "validated": 0, "baseline_only": 1, "failed_validation": 0,
        "rejected_globs": 0, "pr_opened": 0, "error": 0, "skipped_budget": 0,
    }
    summary = workflow._run_summary("suggest", "workflow_dispatch", counters)
    assert "validated 0" in summary
    assert "baseline only (patch not run) 1" in summary


# ── The pull-request step and its text ───────────────────────────────────────


def _candidate() -> FixCandidate:
    return FixCandidate(
        test_fingerprint="f" * 64, test_name="tests/test_x.py::test_k",
        suite_name="s", flip_rate=0.5, flip_window_size=10,
    )


async def test_the_pull_request_step_refuses_the_dispatch_runner(monkeypatch):
    import httpx

    def _no_http(*a, **k):
        raise AssertionError("no request may be made for a baseline-only attempt")

    monkeypatch.setattr(httpx, "AsyncClient", _no_http)
    result = await pipeline.open_draft_pr(
        api_base_url="https://api.github.com", repo_owner="o", repo_name="r",
        pat="not-a-real-token", candidate=_candidate(), patch=SENTINEL_PATCH,
        validation={"reruns": 1, "passed": 1}, reasoning="r",
        ledger_deep_link="/fixer/runs/x", runner_type="workflow_dispatch",
    )
    assert "pr_url" not in result
    assert "patch itself was not run" in result["skipped"]


def test_the_pull_request_body_says_what_the_docker_runner_ran():
    body = pipeline._pr_body(
        _candidate(), {"reruns": 5, "passed": 5}, "seed the rng", "/fixer/runs/x",
        runner_type="docker",
    )
    assert "applied to a fresh clone" in body and "container" in body
    assert "5 times" in body and "5 passed" in body
    assert "not run" not in body.lower()


def test_the_pull_request_body_names_any_other_runner_without_inventing_a_sandbox():
    body = pipeline._pr_body(
        _candidate(), {"reruns": 5, "passed": 5}, "seed the rng", "/fixer/runs/x",
        runner_type="fake",
    )
    assert "`fake` runner" in body
    assert "sandbox" not in body and "container" not in body


def test_the_pull_request_body_never_calls_a_dispatch_run_validated():
    body = pipeline._pr_body(
        _candidate(), {"reruns": 1, "passed": 1}, "seed the rng", "/fixer/runs/x",
        runner_type="workflow_dispatch",
    )
    assert "The patch itself was not run" in body
    assert "validated" not in body.lower().replace("not validated", "")
    assert "sandbox" not in body
