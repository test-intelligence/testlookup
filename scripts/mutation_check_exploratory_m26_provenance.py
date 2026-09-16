"""Prove the M26 homelab provenance tests kill wrong deploy behavior."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "homelabsetup/deploy-homelab.sh"
TESTS = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=backend/.pytest-tmp-exploratory-m26-provenance-mutation",
    "backend/tests/test_health_build_provenance.py::test_homelab_backend_image_receives_exact_build_provenance",
    "backend/tests/regression/test_homelab_build_authority.py",
)

MUTATIONS = (
    ('BUILD_REVISION="$(git rev-parse HEAD)"', 'BUILD_REVISION="unknown"'),
    ('BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"', 'BUILD_DATE="unknown"'),
    (
        'git -C "$repo_root" diff --quiet --ignore-submodules -- \\',
        'true # git -C "$repo_root" diff --quiet --ignore-submodules -- \\',
    ),
    (
        'git -C "$repo_root" diff --cached --quiet --ignore-submodules -- \\',
        'true # git -C "$repo_root" diff --cached --quiet --ignore-submodules -- \\',
    ),
    (
        '--untracked-files=all --ignored=matching -- \\',
        '--untracked-files=all -- \\',
    ),
    (
        '"$repo_root/mcp/certs/mitm-ca.crt"',
        '"$repo_root/mcp/certs/left-behind.crt"',
    ),
    (
        '--build-arg BUILD_REVISION="$BUILD_REVISION" \\',
        '--build-arg BUILD_REVISION="unknown" \\',
    ),
    (
        '--build-arg BUILD_DATE="$BUILD_DATE" \\',
        '--build-arg BUILD_DATE="unknown" \\',
    ),
    (
        '[ "$image" = "$expected_image" ] || return 1',
        '[ "$image" = "$expected_image" ] || continue',
    ),
    (
        '[ "$ready" = "true" ] || return 1',
        '[ "$ready" = "true" ] || continue',
    ),
    (
        '[ "$actual_digest" = "$expected_digest" ] || return 1',
        '[ "$actual_digest" != "$expected_digest" ] || return 1',
    ),
    (
        '[ "$revision" = "$expected_revision" ]',
        '[ "$revision" != "$expected_revision" ]',
    ),
    (
        '"testlookup-backend|backend|${BACKEND_DIGEST}"',
        '"testlookup-backend|frontend|${FRONTEND_DIGEST}"',
    ),
    (
        'verify_serving_revision "$TRAEFIK_IP" "$BUILD_REVISION" \\',
        'verify_serving_revision "$TRAEFIK_IP" "unknown" \\',
    ),
)


def main() -> int:
    original = DEPLOY.read_text(encoding="utf-8")
    for good, bad in MUTATIONS:
        count = original.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation must apply exactly once: {good!r}; found {count}"
            )
        mutated = original.replace(good, bad, 1)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {good!r}")
        DEPLOY.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                TESTS,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        finally:
            DEPLOY.write_text(original, encoding="utf-8")
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {bad!r}")
    print(f"M26 provenance mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
