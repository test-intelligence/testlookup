"""THE core Fixer invariant: zero unvalidated fixes are ever surfaced (AI-2).

A PR is opened ONLY from a ``validated`` result in suggest mode with an open
slot and a usable GitHub integration. ``error`` (infra trouble), ``failed``
(the fix did not validate), shadow mode, and the NoRunner default NEVER open a
PR. These are pure-decision tests (no DB) so the invariant always runs in CI.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer.pipeline import outcome_for_pr  # noqa: E402
from app.agents.fixer.runners import NoRunner  # noqa: E402
from app.agents.fixer.state import (  # noqa: E402
    RESULT_ERROR,
    RESULT_FAILED,
    RESULT_VALIDATED,
    STATUS_ERROR,
    STATUS_FAILED_VALIDATION,
    STATUS_VALIDATED,
    TestIdentity,
    ValidationSpec,
)
from app.agents.fixer.workflow import classify_validation_terminal  # noqa: E402


def _spec() -> ValidationSpec:
    return ValidationSpec(
        repo_url="https://github.com/x/y.git", ref="main", patch="",
        test_identity=TestIdentity("pytest", "pytest {test_selector}", "t"),
    )


# ── The invariant: only validated+suggest+slot+github opens a PR ─────────────


@pytest.mark.parametrize("mode", ["shadow", "suggest"])
def test_error_never_opens_pr(mode):
    d = classify_validation_terminal(
        mode, RESULT_ERROR, passed=0, reruns=5, open_pr_slots=5, github_available=True,
    )
    assert d.status == STATUS_ERROR
    assert d.open_pr is False


@pytest.mark.parametrize("mode", ["shadow", "suggest"])
def test_failed_validation_never_opens_pr(mode):
    d = classify_validation_terminal(
        mode, RESULT_FAILED, passed=3, reruns=5, open_pr_slots=5, github_available=True,
    )
    assert d.status == STATUS_FAILED_VALIDATION
    assert d.open_pr is False


def test_shadow_validated_never_opens_pr():
    d = classify_validation_terminal(
        "shadow", RESULT_VALIDATED, passed=5, reruns=5, open_pr_slots=5, github_available=True,
    )
    assert d.status == STATUS_VALIDATED
    assert d.open_pr is False


def test_suggest_validated_opens_pr_only_with_slot_and_github():
    ok = classify_validation_terminal(
        "suggest", RESULT_VALIDATED, passed=5, reruns=5, open_pr_slots=1, github_available=True,
    )
    assert ok.status == STATUS_VALIDATED and ok.open_pr is True

    no_slot = classify_validation_terminal(
        "suggest", RESULT_VALIDATED, passed=5, reruns=5, open_pr_slots=0, github_available=True,
    )
    assert no_slot.status == STATUS_VALIDATED and no_slot.open_pr is False

    no_gh = classify_validation_terminal(
        "suggest", RESULT_VALIDATED, passed=5, reruns=5, open_pr_slots=3, github_available=False,
    )
    assert no_gh.status == STATUS_VALIDATED and no_gh.open_pr is False


def test_unknown_status_is_error_never_pr():
    d = classify_validation_terminal(
        "suggest", "weird", passed=0, reruns=0, open_pr_slots=9, github_available=True,
    )
    assert d.status == STATUS_ERROR and d.open_pr is False


# ── NoRunner: the type:"none" default always errors (never validates) ────────


def test_no_runner_always_errors():
    result = asyncio.run(NoRunner().run_validation(_spec()))
    assert result.status == RESULT_ERROR
    assert result.runs == []
    # And feeding that into the classifier can never produce a PR.
    d = classify_validation_terminal(
        "suggest", result.status, passed=0, reruns=0, open_pr_slots=9, github_available=True,
    )
    assert d.open_pr is False


# ── Outcome mapping (AI-5 feedback loop) ─────────────────────────────────────


def test_outcome_mapping():
    assert outcome_for_pr(True) == "fixed"
    assert outcome_for_pr(False) == "not_fixed"
