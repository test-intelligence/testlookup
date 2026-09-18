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
        try:
            path.write_text(
                source.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            run = subprocess.run(
                [sys.executable, *mutation.command],
                cwd=ROOT / mutation.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=180,
            )
            if run.returncode == 0:
                raise AssertionError(
                    f"{mutation.name} survived\n{run.stdout}{run.stderr}"
                )
        finally:
            restore(path, original)
    print(f"M22 client mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
