"""Prove the M01 reset regression kills unsafe post-reset behavior."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "src" / "pages" / "ResetPasswordPage.tsx"
NPM = "npm.cmd" if os.name == "nt" else "npm"
TESTS = (
    NPM,
    "run",
    "test",
    "--",
    "--run",
    "src/pages/ResetPasswordPage.test.tsx",
)

MUTATIONS = (
    ("      logout();", "      void logout;"),
    (
        "      toast.success('Password updated. Sign in with your new password.');",
        "      toast.success('Password updated successfully. Welcome!');",
    ),
    (
        "      navigate('/login', { replace: true, state: null });",
        "      navigate('/overview', { replace: true, state: null });",
    ),
    (
        "      navigate('/login', { replace: true, state: null });",
        "      navigate('/login', { replace: true });",
    ),
)


def main() -> int:
    original = SOURCE.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    original_text = original.decode("utf-8")

    for good, bad in MUTATIONS:
        count = original_text.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation must apply exactly once: {good!r}; found {count}"
            )
        mutated = original_text.replace(good, bad, 1)
        if mutated == original_text:
            raise AssertionError(f"mutation did not change source: {good!r}")
        SOURCE.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                TESTS,
                cwd=ROOT / "frontend",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
        finally:
            SOURCE.write_bytes(original)
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {bad!r}")
        restored_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        if restored_hash != original_hash:
            raise AssertionError("source restoration changed the original bytes")

    print(f"M01 reset mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
