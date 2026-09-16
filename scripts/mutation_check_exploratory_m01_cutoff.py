"""Prove the M01 revocation-cutoff regressions require precise ordering."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
UV = "uv.exe" if os.name == "nt" else "uv"
TESTS = (
    UV,
    "run",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=.pytest-tmp-exploratory-m01-cutoff-mutation",
    "tests/core/test_access_token_precision.py",
    "tests/core/test_token_revocation.py",
    "tests/core/test_deps_revocation_fail_closed.py::test_fractional_iat_reaches_cutoff_check",
    "tests/regression/test_durable_token_revocation.py::test_durable_cutoff_preserves_subsecond_ordering",
)

SECURITY = BACKEND / "app" / "core" / "security.py"
DEPS = BACKEND / "app" / "core" / "deps.py"
REVOCATION = BACKEND / "app" / "core" / "token_revocation.py"
MUTATIONS = (
    (SECURITY, '        "iat": now.timestamp(),', '        "iat": int(now.timestamp()),'),
    (DEPS, "        iat_value = float(iat)", "        iat_value = float(int(iat))"),
    (
        REVOCATION,
        "    now = datetime.now(timezone.utc).timestamp()",
        "    now = float(int(datetime.now(timezone.utc).timestamp()))",
    ),
    (
        REVOCATION,
        "token_iat <= cutoff_value.timestamp()",
        "token_iat <= int(cutoff_value.timestamp())",
    ),
    (REVOCATION, "        precise_cutoff = float(legacy)", "        precise_cutoff = int(legacy)"),
    (REVOCATION, "        cutoff = float(cutoff_str)", "        cutoff = int(cutoff_str)"),
)


def main() -> int:
    original_by_path = {path: path.read_bytes() for path, _, _ in MUTATIONS}
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(ROOT / ".uv-cache")

    for source_path, good, bad in MUTATIONS:
        original = original_by_path[source_path]
        text = original.decode("utf-8")
        count = text.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation must apply exactly once: {good!r}; found {count}"
            )
        mutated = text.replace(good, bad, 1)
        if mutated == text:
            raise AssertionError(f"mutation did not change source: {good!r}")
        source_path.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                TESTS,
                cwd=BACKEND,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        finally:
            source_path.write_bytes(original)
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {bad!r}")
        if hashlib.sha256(source_path.read_bytes()).digest() != hashlib.sha256(original).digest():
            raise AssertionError(f"source restoration failed: {source_path}")

    print(f"M01 cutoff mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
