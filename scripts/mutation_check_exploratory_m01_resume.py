"""Prove login cannot resume the obsolete forced-reset route."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "src" / "pages" / "LoginPage.tsx"
NPM = "npm.cmd" if os.name == "nt" else "npm"
TESTS = (
    NPM,
    "run",
    "test",
    "--",
    "--run",
    "src/pages/LoginPage.test.tsx",
)
GOOD = "  const from = fromPath && fromPath !== '/reset-password' ? fromPath : '/overview';"
BAD = "  const from = fromPath || '/overview';"


def main() -> int:
    original = SOURCE.read_bytes()
    original_hash = hashlib.sha256(original).digest()
    text = original.decode("utf-8")
    count = text.count(GOOD)
    if count != 1:
        raise AssertionError(f"mutation must apply exactly once; found {count}")
    SOURCE.write_text(text.replace(GOOD, BAD, 1), encoding="utf-8")
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
        raise AssertionError("stale reset-route mutation survived")
    if hashlib.sha256(SOURCE.read_bytes()).digest() != original_hash:
        raise AssertionError("source restoration changed the original bytes")
    print("M01 resume mutation check: 1 mutation killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
