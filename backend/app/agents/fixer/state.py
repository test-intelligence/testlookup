"""Value types + constants for the Fixer (Agentic plan AI-2).

Support module (no agent implementation here): the dataclasses that cross the
``ValidationRunner`` boundary and the fixed vocab shared by the pipeline, the
runners, and the config service. Kept import-light so runners can be unit
tested without pulling the DB layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Identity / config vocab ──────────────────────────────────────────────────

FIXER_AGENT_ID = "fixer"

# Config modes the Fixer accepts. ``act`` is reserved and rejected at the
# policy layer (mirrors the Investigator) — the Fixer never merges anything.
VALID_FIXER_MODES: tuple[str, ...] = ("shadow", "suggest")

VALID_RUNNER_TYPES: tuple[str, ...] = ("docker", "workflow_dispatch", "none")

VALID_SCHEDULES: tuple[str, ...] = ("daily", "weekly", "off")

# Docker image reference the policy may configure (security-critical: the
# image lands in ``docker run``'s argv, so a value like ``--privileged`` must
# never be accepted as an "image"). Deliberately strict: lowercase repo path,
# optional tag, optional sha256 digest. Enforced at the config write path
# (router validator + fixer_service._coerce_runner) AND re-asserted in
# runners.build_docker_run_argv (defense in depth).
RUNNER_IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]*(:[\w.-]+)?(@sha256:[a-f0-9]{64})?$"
)


def is_valid_runner_image(image: Optional[str]) -> bool:
    """True when ``image`` is a well-formed docker image reference (and in
    particular can never be parsed by the docker CLI as a flag)."""
    return bool(image) and RUNNER_IMAGE_RE.match(str(image)) is not None

# Defaults for a project that has never configured the Fixer (API contract).
DEFAULT_TEST_GLOBS: tuple[str, ...] = ("tests/**", "**/*.spec.*", "**/*.test.*")
DEFAULT_FIXER_BUDGETS: dict[str, int] = {
    "max_tests_per_run": 3,
    "max_attempts_per_test": 2,
    "validation_reruns": 5,
    "max_concurrent_open_prs": 2,
}

# fix_attempts.status vocabulary (pinned API contract).
STATUS_SELECTED = "selected"
STATUS_DIAGNOSING = "diagnosing"
STATUS_GENERATING = "generating"
STATUS_VALIDATING = "validating"
STATUS_VALIDATED = "validated"
STATUS_REJECTED_GLOBS = "rejected_globs"
STATUS_FAILED_VALIDATION = "failed_validation"
STATUS_PR_OPENED = "pr_opened"
STATUS_ERROR = "error"
STATUS_SKIPPED_BUDGET = "skipped_budget"

# ValidationResult.status vocabulary.
RESULT_VALIDATED = "validated"
RESULT_FAILED = "failed"
RESULT_ERROR = "error"


# ── Runner boundary types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class TestIdentity:
    """How to name + invoke the single test under validation."""

    __test__ = False  # not a pytest test class (silence collection warning)

    framework: str                 # pytest | playwright | ...
    command_template: str          # e.g. "pytest -x {test_selector}"
    test_selector: str             # UNTRUSTED — never string-interpolated into a shell


@dataclass(frozen=True)
class ValidationSpec:
    """Immutable request handed to a :class:`ValidationRunner`."""

    repo_url: str
    ref: str
    patch: str
    test_identity: TestIdentity
    reruns: int = 5
    timeout_s: int = 300
    env_allowlist: tuple[str, ...] = ()
    # Runner-specific knobs (ignored by runners that don't use them).
    runner_image: Optional[str] = None
    workflow_ref: Optional[str] = None
    cpus: float = 1.0
    memory: str = "1g"
    # Default is NO network in the sandbox; policy may open egress. Recorded
    # in the ledger when True.
    allow_network_egress: bool = False
    # Short-lived clone token (injected ONLY into the clone argv, never env).
    clone_token: Optional[str] = None


@dataclass(frozen=True)
class RunAttempt:
    """One rerun of the test inside the sandbox."""

    n: int
    passed: bool
    duration_ms: int
    log_digest: str


@dataclass(frozen=True)
class ValidationResult:
    """Runner outcome. ``validated`` iff EVERY rerun passed."""

    status: str                    # validated | failed | error
    runs: list[RunAttempt] = field(default_factory=list)
    logs_ref: Optional[str] = None
    error: Optional[str] = None

    @property
    def reruns(self) -> int:
        return len(self.runs)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.runs if r.passed)


@dataclass
class FixCandidate:
    """A flaky/quarantined test the Fixer may attempt."""

    test_fingerprint: str
    test_name: Optional[str]
    suite_name: Optional[str]
    flip_rate: Optional[float]
    flip_window_size: Optional[int]
    prior_attempts: int = 0


def to_validation_result(payload: dict[str, Any]) -> ValidationResult:
    """Coerce a plain-dict runner return (e.g. from a subprocess boundary or a
    mock) into a :class:`ValidationResult`. Defensive: unknown status → error."""
    status = str(payload.get("status") or RESULT_ERROR)
    if status not in (RESULT_VALIDATED, RESULT_FAILED, RESULT_ERROR):
        status = RESULT_ERROR
    runs = [
        RunAttempt(
            n=int(r.get("n", i + 1)),
            passed=bool(r.get("passed")),
            duration_ms=int(r.get("duration_ms") or 0),
            log_digest=str(r.get("log_digest") or ""),
        )
        for i, r in enumerate(payload.get("runs") or [])
    ]
    return ValidationResult(
        status=status,
        runs=runs,
        logs_ref=payload.get("logs_ref"),
        error=payload.get("error"),
    )
