"""Prove the M26 homelab provenance tests kill wrong deploy behavior."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "homelabsetup/deploy-homelab.sh"
MIGRATION_RUNNER = ROOT / "scripts/run-k8s-migrations.sh"
MCP_DEPLOYMENT = ROOT / "k8s/base/mcp-deployment.yaml"
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
    "backend/tests/regression/test_k8s_migration_runner.py::test_runner_uses_unique_attempt_jobs_and_readiness_init_container",
)

DEPLOY_MUTATIONS = (
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
    (
        '-l "app=${deployment},app.kubernetes.io/component=${component}" \\',
        '-l "app=${deployment}" \\',
    ),
    (
        '[ -n "$component" ] || return 1',
        'true # [ -n "$component" ] || return 1',
    ),
)

MIGRATION_MUTATIONS = (
    (
        'ends with "USER testlookup" — a NAME —',
        'ends with `USER testlookup` — a NAME —',
    ),
)

MCP_DEPLOYMENT_MUTATIONS = (
    (
        "        app: testlookup-mcp\n        app.kubernetes.io/component: mcp-server\n",
        "        app: testlookup-mcp\n",
    ),
)

MUTATIONS = tuple((DEPLOY, good, bad) for good, bad in DEPLOY_MUTATIONS) + tuple(
    (MIGRATION_RUNNER, good, bad) for good, bad in MIGRATION_MUTATIONS
) + tuple(
    (MCP_DEPLOYMENT, good, bad) for good, bad in MCP_DEPLOYMENT_MUTATIONS
)


def main() -> int:
    for source_path, good, bad in MUTATIONS:
        original = source_path.read_text(encoding="utf-8")
        count = original.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation must apply exactly once: {good!r}; found {count}"
            )
        mutated = original.replace(good, bad, 1)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {good!r}")
        source_path.write_text(mutated, encoding="utf-8")
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
            source_path.write_text(original, encoding="utf-8")
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {bad!r}")
    print(f"M26 provenance mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
