"""ValidationRunner implementations for the Fixer (Agentic plan AI-2).

A ``ValidationRunner`` reruns a single test M times against a candidate patch
and returns ``validated`` (ALL reruns passed), ``failed`` (the fix did not
validate), or ``error`` (infra trouble — the fix's merit is unknown). The
Fixer opens a PR ONLY on ``validated`` in suggest mode; ``failed`` and
``error`` NEVER surface a fix.

Runners (behind the common interface):

* :class:`NoRunner` — the ``type:"none"`` default. Always ``error`` "no
  runner configured". The Fixer can then only run shadow diagnostics.
* :class:`DockerEphemeralRunner` — the flagship. Ephemeral container on the
  worker: shallow clone at ref (scoped token in the clone argv only), apply
  patch, run the command M times, destroy the workspace. CPU/mem/timeout
  limits, ``--network=none`` by default, non-root user, no secret/DB mounts.
  Built via the ``docker`` CLI with a strict argv LIST — NEVER ``shell=True``
  and NEVER f-string-interpolating an untrusted test name/patch into a shell
  string. This is security-critical (see :func:`build_command_tokens`).
* :class:`WorkflowDispatchRunner` — GitHub ``workflow_dispatch`` + poll the
  run conclusion, reusing the GitHub integration's PAT/SSRF/error patterns.
* :class:`FakeRunner` — deterministic in-memory runner for tests.

NOTE (quality gate ``agents.base-agent-subclass``): this module is listed in
``_SUPPORT_AGENT_FILES`` — the runners are sandbox executors, not LangGraph
pipeline agents, so they intentionally do not subclass ``BaseAgent``.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import shutil
import stat
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Optional

import structlog

from app.agents.fixer.state import (
    RESULT_ERROR,
    RESULT_FAILED,
    RESULT_VALIDATED,
    RunAttempt,
    ValidationResult,
    ValidationSpec,
)

logger = structlog.get_logger("agents.fixer.runners")

# Placeholder the policy's command_template uses for the (untrusted) test
# selector. Substituted as a SINGLE argv element — never concatenated into a
# shell string.
_SELECTOR_PLACEHOLDER = "{test_selector}"


class ValidationRunner(ABC):
    """Interface: rerun a test against a patch and report validated/failed/error."""

    type: str = "abstract"

    @abstractmethod
    async def run_validation(self, spec: ValidationSpec) -> ValidationResult:
        ...


# ── argv builders (pure, security-critical, unit-tested) ─────────────────────


def build_command_tokens(command_template: str, test_selector: str) -> list[str]:
    """Tokenize the policy command template and substitute the UNTRUSTED test
    selector as a single argv element.

    Security: ``command_template`` is split with ``shlex`` into tokens; the
    ``{test_selector}`` placeholder is replaced token-locally with the raw
    selector value. Because the result is an argv LIST fed to
    ``execve``-style ``create_subprocess_exec`` / ``docker run`` (no shell,
    no word-splitting), a selector like ``foo; rm -rf / #`` stays one inert
    argument and cannot inject a command. NEVER build a shell string from
    these values.
    """
    tokens = shlex.split(command_template)
    out: list[str] = []
    for tok in tokens:
        if _SELECTOR_PLACEHOLDER in tok:
            out.append(tok.replace(_SELECTOR_PLACEHOLDER, test_selector))
        else:
            out.append(tok)
    if _SELECTOR_PLACEHOLDER not in command_template:
        # Template didn't reference the selector — append it as one trailing
        # argument so the command still targets the specific test.
        out.append(test_selector)
    return out


def build_clone_argv(repo_url: str, ref: str, workspace: str, token: Optional[str]) -> list[str]:
    """Argv for the shallow clone. The scoped token is injected ONLY here (into
    the remote URL), never into the container env. Returns a LIST; the caller
    runs it with ``create_subprocess_exec`` (no shell)."""
    url = repo_url
    if token and repo_url.startswith("https://"):
        # https://<token>@host/owner/repo.git — token confined to this argv.
        url = "https://" + token + "@" + repo_url[len("https://"):]
    # ``--`` guards against a repo_url that begins with a dash.
    return ["git", "clone", "--depth", "1", "--no-single-branch", "--", url, workspace]


def build_docker_run_argv(
    *,
    image: str,
    command_tokens: list[str],
    workspace: str,
    cpus: float,
    memory: str,
    user: str,
    allow_network_egress: bool,
    timeout_s: int,
) -> list[str]:
    """Argv for one ephemeral, sandboxed rerun. Strict LIST — no shell.

    Enforced: ``--rm`` (ephemeral), ``--network=none`` unless the policy
    opened egress, cpu/memory caps, non-root ``--user``, read-write bind of
    ONLY the cloned workspace (no secret/DB mounts), and an in-container
    ``timeout`` wrapper so a hung test cannot outlive its budget.
    """
    network = "bridge" if allow_network_egress else "none"
    argv = [
        "docker", "run", "--rm",
        "--network", network,
        "--cpus", str(cpus),
        "--memory", memory,
        "--pids-limit", "512",
        "--user", user,
        "--workdir", "/work",
        # Bind ONLY the workspace. No -e/--env-file: nothing from the worker
        # environment (secrets, DB creds) reaches the container.
        "-v", workspace + ":/work",
        "--entrypoint", "",
        image,
        # In-container hard timeout so a hang is killed even if the outer
        # asyncio timeout races. ``timeout`` is a single argv token.
        "timeout", str(int(timeout_s)),
        *command_tokens,
    ]
    return argv


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


# ── NoRunner ─────────────────────────────────────────────────────────────────


class NoRunner(ValidationRunner):
    """The ``type:"none"`` default. Always ``error`` — a fix is NEVER validated
    and therefore NEVER surfaced as a PR. This is the invariant's backstop."""

    type = "none"

    async def run_validation(self, spec: ValidationSpec) -> ValidationResult:
        return ValidationResult(
            status=RESULT_ERROR,
            runs=[],
            error="no runner configured — set fixer.runner.type to docker or workflow_dispatch",
        )


# ── DockerEphemeralRunner ────────────────────────────────────────────────────


class DockerEphemeralRunner(ValidationRunner):
    """Flagship runner: ephemeral Docker container on the worker."""

    type = "docker"

    def __init__(self, *, user: str = "1000:1000") -> None:
        self._user = user

    @staticmethod
    async def docker_available() -> bool:
        """True when the ``docker`` CLI is present and responsive. Never raises."""
        if shutil.which("docker") is None:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "version", "--format", "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    async def _run(self, argv: list[str], *, timeout_s: int, cwd: Optional[str] = None) -> tuple[int, str]:
        """Run an argv list (NO shell) and return (returncode, combined_output)."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return 124, "timeout"
        return int(proc.returncode or 0), (out or b"").decode("utf-8", "replace")

    async def run_validation(self, spec: ValidationSpec) -> ValidationResult:
        if not await self.docker_available():
            return ValidationResult(
                status=RESULT_ERROR, runs=[], error="docker CLI unavailable on the worker",
            )
        if not spec.runner_image:
            return ValidationResult(
                status=RESULT_ERROR, runs=[], error="runner_image not configured",
            )

        workspace = tempfile.mkdtemp(prefix="fixer-ws-")
        try:
            # 1. Shallow clone at ref — token confined to the clone argv.
            clone_argv = build_clone_argv(spec.repo_url, spec.ref, workspace, spec.clone_token)
            rc, out = await self._run(clone_argv, timeout_s=min(120, spec.timeout_s))
            if rc != 0:
                return ValidationResult(
                    status=RESULT_ERROR, runs=[], error=f"clone failed: {out[-300:]}",
                )
            # Best-effort checkout of the exact ref (SHA or branch).
            await self._run(
                ["git", "-C", workspace, "checkout", "--quiet", spec.ref],
                timeout_s=60,
            )

            # 2. Apply the candidate patch.
            patch_path = os.path.join(workspace, ".fixer.patch")
            with open(patch_path, "w", encoding="utf-8") as fh:
                fh.write(spec.patch)
            rc, out = await self._run(
                ["git", "-C", workspace, "apply", "--verbose", patch_path],
                timeout_s=60,
            )
            if rc != 0:
                return ValidationResult(
                    status=RESULT_ERROR, runs=[], error=f"patch did not apply: {out[-300:]}",
                )
            os.remove(patch_path)

            # Make the workspace writable by the non-root container user
            # (pytest writes __pycache__ / .pytest_cache).
            _chmod_tree_world_writable(workspace)

            # 3. Run the command M times in ephemeral containers.
            command_tokens = build_command_tokens(
                spec.test_identity.command_template, spec.test_identity.test_selector,
            )
            runs: list[RunAttempt] = []
            for n in range(1, max(1, spec.reruns) + 1):
                argv = build_docker_run_argv(
                    image=spec.runner_image,
                    command_tokens=command_tokens,
                    workspace=workspace,
                    cpus=spec.cpus,
                    memory=spec.memory,
                    user=self._user,
                    allow_network_egress=spec.allow_network_egress,
                    timeout_s=spec.timeout_s,
                )
                started = asyncio.get_event_loop().time()
                rc, out = await self._run(argv, timeout_s=spec.timeout_s + 30)
                duration_ms = int((asyncio.get_event_loop().time() - started) * 1000)
                runs.append(RunAttempt(
                    n=n, passed=(rc == 0), duration_ms=duration_ms, log_digest=_digest(out),
                ))

            all_passed = bool(runs) and all(r.passed for r in runs)
            return ValidationResult(
                status=RESULT_VALIDATED if all_passed else RESULT_FAILED,
                runs=runs,
                logs_ref=None,
            )
        except Exception as exc:  # noqa: BLE001 — infra fault is error, not failed
            logger.warning("docker_runner_error", error=str(exc))
            return ValidationResult(status=RESULT_ERROR, runs=[], error=str(exc)[:300])
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


def _chmod_tree_world_writable(root: str) -> None:
    """Best-effort: let the non-root container user write pytest caches."""
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in [dirpath] + [os.path.join(dirpath, d) for d in dirnames]:
                try:
                    os.chmod(name, 0o777)
                except OSError:
                    pass
            for f in filenames:
                try:
                    os.chmod(os.path.join(dirpath, f), stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                except OSError:
                    pass
    except OSError:
        pass


# ── WorkflowDispatchRunner ───────────────────────────────────────────────────


class WorkflowDispatchRunner(ValidationRunner):
    """GitHub ``workflow_dispatch`` v1: dispatch a reference validation workflow
    and poll its run conclusion. Reuses the GitHub integration's PAT/SSRF/
    error patterns. When the integration is missing/offline, returns
    ``error`` — never a spurious ``validated``."""

    type = "workflow_dispatch"

    def __init__(
        self,
        *,
        api_base_url: Optional[str],
        repo_owner: Optional[str],
        repo_name: Optional[str],
        pat: Optional[str],
        poll_timeout_s: int = 900,
    ) -> None:
        self._api_base_url = (api_base_url or "").rstrip("/")
        self._repo_owner = repo_owner
        self._repo_name = repo_name
        self._pat = pat
        self._poll_timeout_s = poll_timeout_s

    async def run_validation(self, spec: ValidationSpec) -> ValidationResult:
        if not (self._api_base_url and self._repo_owner and self._repo_name and self._pat):
            return ValidationResult(
                status=RESULT_ERROR, runs=[], error="github integration not configured for workflow_dispatch",
            )
        if not spec.workflow_ref:
            return ValidationResult(
                status=RESULT_ERROR, runs=[], error="workflow_ref not configured",
            )
        import httpx

        from app.services.github_checks_service import _ssrf_block_reason

        dispatch_url = (
            f"{self._api_base_url}/repos/{self._repo_owner}/{self._repo_name}"
            f"/actions/workflows/{spec.workflow_ref}/dispatches"
        )
        block = await _ssrf_block_reason(dispatch_url)
        if block:
            return ValidationResult(status=RESULT_ERROR, runs=[], error=f"blocked target: {block}")
        headers = {
            "Authorization": f"Bearer {self._pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "TestLookup/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        inputs = {
            "ref": spec.ref,
            "test_selector": spec.test_identity.test_selector,
            "reruns": str(spec.reruns),
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    dispatch_url, headers=headers,
                    json={"ref": spec.ref, "inputs": inputs},
                )
            if resp.status_code not in (201, 204):
                return ValidationResult(
                    status=RESULT_ERROR, runs=[],
                    error=f"dispatch HTTP {resp.status_code}: {resp.text[:200]}",
                )
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(status=RESULT_ERROR, runs=[], error=f"dispatch failed: {exc}")

        conclusion = await self._poll_conclusion(headers, spec.workflow_ref)
        if conclusion is None:
            return ValidationResult(status=RESULT_ERROR, runs=[], error="workflow run not observed before poll timeout")
        passed = conclusion == "success"
        return ValidationResult(
            status=RESULT_VALIDATED if passed else RESULT_FAILED,
            runs=[RunAttempt(n=1, passed=passed, duration_ms=0, log_digest=_digest(conclusion))],
            logs_ref=None,
        )

    async def _poll_conclusion(self, headers: dict[str, str], workflow_ref: str) -> Optional[str]:
        import httpx

        runs_url = (
            f"{self._api_base_url}/repos/{self._repo_owner}/{self._repo_name}"
            f"/actions/workflows/{workflow_ref}/runs?per_page=1"
        )
        deadline = asyncio.get_event_loop().time() + self._poll_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(10)
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.get(runs_url, headers=headers)
                if resp.status_code != 200:
                    continue
                runs = (resp.json() or {}).get("workflow_runs") or []
                if not runs:
                    continue
                latest = runs[0]
                if latest.get("status") == "completed":
                    return str(latest.get("conclusion") or "")
            except Exception:  # noqa: BLE001
                continue
        return None


# ── FakeRunner (tests) ───────────────────────────────────────────────────────


class FakeRunner(ValidationRunner):
    """Deterministic in-memory runner for the FakeRunner end-to-end tests.

    ``outcome`` is one of validated | failed | error. It records the spec it
    received so tests can assert on selectors/patches without a container."""

    type = "fake"

    def __init__(self, *, outcome: str = RESULT_VALIDATED, reruns: int = 5) -> None:
        self._outcome = outcome
        self._reruns = reruns
        self.received_specs: list[ValidationSpec] = []

    async def run_validation(self, spec: ValidationSpec) -> ValidationResult:
        self.received_specs.append(spec)
        if self._outcome == RESULT_ERROR:
            return ValidationResult(status=RESULT_ERROR, runs=[], error="fake infra error")
        m = max(1, self._reruns if self._reruns else spec.reruns)
        passed = self._outcome == RESULT_VALIDATED
        runs = [
            RunAttempt(n=i + 1, passed=passed if i < m else False, duration_ms=5, log_digest=_digest(f"fake-{i}"))
            for i in range(m)
        ]
        if self._outcome == RESULT_FAILED:
            # First rerun fails → not all pass → failed.
            runs[0] = RunAttempt(n=1, passed=False, duration_ms=5, log_digest=_digest("fake-fail"))
        return ValidationResult(
            status=RESULT_VALIDATED if passed else RESULT_FAILED, runs=runs,
        )


# ── Factory ──────────────────────────────────────────────────────────────────


def build_runner(
    runner_config: dict[str, Any],
    *,
    github_ctx: Optional[dict[str, Any]] = None,
) -> ValidationRunner:
    """Construct the runner named by ``runner_config['type']``. Unknown/absent
    type resolves to :class:`NoRunner` (fail-safe — never validates)."""
    rtype = str((runner_config or {}).get("type") or "none")
    if rtype == "docker":
        return DockerEphemeralRunner()
    if rtype == "workflow_dispatch":
        ctx = github_ctx or {}
        return WorkflowDispatchRunner(
            api_base_url=ctx.get("api_base_url"),
            repo_owner=ctx.get("repo_owner"),
            repo_name=ctx.get("repo_name"),
            pat=ctx.get("pat"),
        )
    if rtype == "fake":  # tests only — not an exposed config value
        return FakeRunner()
    return NoRunner()
