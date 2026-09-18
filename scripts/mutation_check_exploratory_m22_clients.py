"""Prove the M22 SDK bundle and CLI exit-code regressions are detected."""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    command: tuple[str, ...]
    cwd: str
    executable: str = sys.executable


MUTATIONS = (
    Mutation(
        "python-sdk-required-sibling",
        "backend/app/routers/sdk.py",
        '            "ci_context.py",\n',
        "",
        (
            "-m", "pytest", "-q",
            "tests/regression/test_request_hardening.py::test_range_is_ignored_so_the_sdk_download_is_always_whole",
            "-p", "no:testlookup", "--basetemp=.pytest-tmp-m22-sdk-mutation",
        ),
        "backend",
    ),
    Mutation(
        "cli-stable-exit-code",
        "cli/testlookup_cli/errors.py",
        "    return exc.exit_code if isinstance(exc, CLIError) else EXIT_ERROR\n",
        "    return EXIT_ERROR\n",
        (
            "-m", "pytest", "-q", "cli/tests/test_command_exit_codes.py",
            "--basetemp=.pytest-tmp-m22-cli-mutation",
        ),
        ".",
    ),
    Mutation(
        "cli-bad-request-is-validation",
        "cli/testlookup_cli/errors.py",
        "    if status_code in (400, 422):\n",
        "    if status_code == 422:\n",
        (
            "-m", "pytest", "-q",
            "cli/tests/test_client_connection_errors.py::test_bad_request_uses_the_validation_exit_code",
            "--basetemp=.pytest-tmp-m22-cli-validation-mutation",
        ),
        ".",
    ),
    Mutation(
        "opaque-run-membership-denial",
        "backend/app/core/deps.py",
        (
            "        if not membership.scalar_one_or_none():\n"
            "            raise HTTPException(\n"
            "                status_code=status.HTTP_404_NOT_FOUND,\n"
            "                detail=\"Test run not found\",\n"
            "            )\n"
        ),
        (
            "        if not membership.scalar_one_or_none():\n"
            "            raise HTTPException(\n"
            "                status_code=status.HTTP_403_FORBIDDEN,\n"
            "                detail=\"Test run not found\",\n"
            "            )\n"
        ),
        (
            "-m", "pytest", "-q",
            "tests/test_authorization_guards.py::TestRequireRunAccess::test_non_member_gets_404_without_confirming_the_run",
            "-p", "no:testlookup", "--basetemp=.pytest-tmp-m22-run-denial-mutation",
        ),
        "backend",
    ),
    Mutation(
        "opaque-run-api-key-binding",
        "backend/app/core/deps.py",
        "        if bound_project_id is not None and bound_project_id != project_id:\n",
        "        if False and bound_project_id is not None and bound_project_id != project_id:\n",
        (
            "-m", "pytest", "-q",
            "tests/test_authorization_guards.py::TestRequireRunAccess::test_foreign_project_key_gets_404_without_confirming_the_run",
            "-p", "no:testlookup", "--basetemp=.pytest-tmp-m22-key-denial-mutation",
        ),
        "backend",
    ),
    Mutation(
        "python-sdk-install-metadata",
        "client/pyproject.toml",
        'py-modules = ["testlookup_reporter", "ci_context", "commit_range"]\n',
        'py-modules = ["testlookup_reporter", "ci_context"]\n',
        (
            "-m", "pytest", "-q",
            "tests/regression/test_request_hardening.py::test_the_real_python_sdk_archive_imports_in_an_isolated_directory",
            "-p", "no:testlookup", "--basetemp=.pytest-tmp-m22-sdk-metadata-mutation",
        ),
        "backend",
    ),
    Mutation(
        "upload-post-stable-exit-code",
        "cli/testlookup_cli/commands/upload.py",
        '                    raise map_http_error(resp.status_code, f"{detail}{gave_up}")\n',
        '                    raise Exception(f"HTTP {resp.status_code}: {detail}{gave_up}")\n',
        (
            "-m", "pytest", "-q",
            "cli/tests/test_upload_retry_and_wait.py::test_upload_http_errors_keep_the_stable_exit_code",
            "--basetemp=.pytest-tmp-m22-upload-post-mutation",
        ),
        ".",
    ),
    Mutation(
        "upload-poll-stable-exit-code",
        "cli/testlookup_cli/commands/upload.py",
        (
            "                    raise map_http_error(\n"
            "                        code, f\"while waiting for upload {task_id}: {detail}\"\n"
            "                    )\n"
        ),
        (
            "                    raise Exception(\n"
            "                        f\"HTTP {code} while waiting for upload {task_id}: {detail}\"\n"
            "                    )\n"
        ),
        (
            "-m", "pytest", "-q",
            "cli/tests/test_upload_retry_and_wait.py::test_an_answer_that_cannot_change_fails_the_wait_at_once",
            "--basetemp=.pytest-tmp-m22-upload-poll-mutation",
        ),
        ".",
    ),
    Mutation(
        "python-sdk-ui-consumer-contract",
        "frontend/src/pages/LiveExecutionPage.tsx",
        (
            "      return `# Download and extract the Python ZIP from SDK Downloads above\n"
            "cd python\n"
            "pip install .\n"
        ),
        (
            "      return `pip install httpx pyyaml\n"
            "# Copy the reporter from the SDK Downloads button above\n"
        ),
        (
            "node_modules/vitest/vitest.mjs", "run",
            "src/pages/LiveExecutionPage.sdk-download.test.ts",
        ),
        "frontend",
        "node",
    ),
    Mutation(
        "python-sdk-ui-archive-label",
        "frontend/src/pages/LiveExecutionPage.tsx",
        "  { sdkLang: 'python',     label: 'Python (.zip)',        backendLang: 'python' },\n",
        "  { sdkLang: 'python',     label: 'Python (.py)',         backendLang: 'python' },\n",
        (
            "node_modules/vitest/vitest.mjs", "run",
            "src/pages/LiveExecutionPage.sdk-download.test.ts",
        ),
        "frontend",
        "node",
    ),
)


def restore(path: Path, content: bytes) -> None:
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        original = path.read_bytes()
        source = original.decode("utf-8")
        count = source.count(mutation.safe)
        if count != 1:
            raise AssertionError(
                f"{mutation.name} mutation must apply exactly once; found {count}"
            )
        baseline = subprocess.run(
            [mutation.executable, *mutation.command],
            cwd=ROOT / mutation.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=180,
        )
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed with {baseline.returncode}\n"
                f"{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                source.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            run = subprocess.run(
                [mutation.executable, *mutation.command],
                cwd=ROOT / mutation.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=180,
            )
            if run.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} expected pytest failure 1, got {run.returncode}\n"
                    f"{run.stdout}{run.stderr}"
                )
        finally:
            restore(path, original)
    print(f"M22 client mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
