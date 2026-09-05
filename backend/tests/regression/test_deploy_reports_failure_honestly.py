"""The deploy must not report success when the new image never started.

On 2026-08-15 a homelab deploy printed **"Health check passed"** and
**"Deployment Complete"**, and exited 0, while the backend it had just built
was in CrashLoopBackOff for its entire life:

```
testlookup-backend-5c49cc97b9-mgztv   0/1   CrashLoopBackOff   7
testlookup-backend-6fd7db796c-zxjfl   1/1   Running            (previous RS)

asyncpg.exceptions.InvalidPasswordError:
  password authentication failed for user "testlookup_user"
```

Three separate weaknesses combined:

1. **The health check is answered by any Ready pod.** It curls the ingress, and
   the ReplicaSet being *replaced* was still serving. A check the old pod can
   satisfy says nothing about the new one.
2. **A stalled rollout only warned.** `rollout status ... || warn` let the run
   continue to the success banner and `exit 0`.
3. **The root cause was self-inflicted by the same script.** Step 4 regenerates
   every credential whenever `testlookup-secrets` is absent, but Postgres keeps
   a persistent PVC and the role password it was first initialised with — so
   `POSTGRES_PASSWORD` described a password the database did not have.

A fourth, related failure in the same run: the MCP provisioning step read
`$BACKEND_POD`, a variable assigned only inside the *admin* step's success
branch. The admin step had been skipped (backend unhealthy), so provisioning
silently did nothing.

The guards below are the classes:
  * a degraded outcome must reach the exit code, not just a warning;
  * the pod state must be consulted, not only an HTTP endpoint anything can
    answer;
  * credentials the script regenerates must be reconciled with the stateful
    service that already has them;
  * a step must not depend on a variable another step may never have set.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
DEPLOY = REPO / "homelabsetup" / "deploy-homelab.sh"

pytestmark = pytest.mark.skipif(
    not DEPLOY.exists(), reason="homelab deploy script not present in this checkout"
)


def _script() -> str:
    return DEPLOY.read_text(encoding="utf-8")


# ── 1. Degraded must reach the exit code ────────────────────────────────────


def test_a_degraded_deploy_exits_non_zero():
    """The regression. The run that broke the cluster exited 0."""
    src = _script()
    assert "DEPLOY_DEGRADED" in src, "nothing tracks a degraded outcome"
    tail = src.split("# ── Summary")[-1]
    assert re.search(r"DEPLOY_DEGRADED.*?\n(?:.*\n)*?\s*exit 1", tail), (
        "a degraded deploy still falls through to the success banner and exit 0"
    )


def test_a_stalled_rollout_marks_the_deploy_degraded():
    """`rollout status || warn` was the whole reason a crash-looping image
    reached the success banner."""
    src = _script()
    block = src.split("Waiting for rollouts to complete")[1].split("log \"App deployments")[0]
    assert "DEPLOY_DEGRADED=true" in block, (
        "a rollout that never completes only warns; the run still reports success"
    )


# ── 2. Ask the cluster, not just the ingress ────────────────────────────────


def test_pod_state_is_checked_not_only_the_ingress():
    """The ingress health check is answered by whichever backend pod is Ready,
    including one from the ReplicaSet being replaced."""
    src = _script()
    # Match the ASSIGNMENT, not the bare phrase: an earlier version of this
    # assertion passed with the command replaced by `echo ""` because the
    # original text survived in a trailing comment.
    assert re.search(r"NOT_READY=\$\(\s*kubectl[^\n]*get pods", src), (
        "the deploy never inspects pod state; it trusts an HTTP endpoint that "
        "the pod being replaced can answer"
    )
    assert "CrashLoopBackOff" in src, (
        "nothing tells the operator that a crash-looping NEW pod is why the old "
        "one is still answering"
    )


def test_a_passing_ingress_check_does_not_override_unhealthy_pods():
    """The dangerous combination is 'pods broken' AND 'HTTP 200' — that is
    exactly what happened. The 200 must not be reported as unqualified success."""
    src = _script()
    block = src.split("Testing health endpoint")[1]
    idx = block.find("Health check passed")
    assert idx != -1
    guard = block[:idx]
    assert "DEPLOY_DEGRADED" in guard, (
        "'Health check passed' is printed without first considering whether the "
        "responding pod belongs to the ReplicaSet being replaced"
    )


# ── 3. Regenerated credentials must be reconciled ───────────────────────────


def test_the_postgres_role_password_is_converged_to_the_secret():
    """Step 4 regenerates POSTGRES_PASSWORD whenever the secret is absent, but
    Postgres keeps its PVC and its original role password. Without reconciling,
    every new backend and worker pod dies on InvalidPasswordError."""
    src = _script()
    # Must be an ALTER USER actually handed to psql. Asserting the bare phrase
    # passed with the statement neutered, because the failure-help text also
    # says "run ALTER USER by hand" — prose satisfied the guard.
    assert re.search(r"psql[^\n]*ALTER USER", src), (
        "regenerated POSTGRES_PASSWORD is never applied to the existing database "
        "role, so the secret describes a password the database does not have"
    )
    assert "InvalidPasswordError" in src, (
        "the failure mode this protects against is not recorded for the next reader"
    )


def test_the_trust_auth_trap_is_recorded():
    """pg_hba is `trust` for local/127.0.0.1 in this image, so a psql check from
    inside the pod accepts ANY password. Verifying the credential that way
    proves nothing — it cost real debugging time."""
    src = _script()
    assert "pg_hba" in src and "trust" in src, (
        "the local-trust trap is undocumented; the next person will 'verify' the "
        "password from inside the pod and be misled"
    )


# ── 4. Network MCP must not provision a shared backend principal ────────────


def test_network_mcp_shared_principal_provisioning_is_removed():
    """Remote callers authenticate independently; the deployment must not create
    or report credentials for a process-wide MCP backend account."""
    src = _script()
    for forbidden in ("MCP_BACKEND_POD", "createServiceAccount.py"):
        assert forbidden not in src, (
            f"deploy script still contains shared MCP identity marker {forbidden!r}"
        )
    assert "retireLegacyMcpServiceAccount.py" in src


# ── The parser must not be vacuous ──────────────────────────────────────────


def test_the_checks_are_actually_reading_the_script():
    """Every assertion above is a substring search over one file. If the script
    moved or the section headers were renamed, they would all pass against an
    empty string. (Two guards written earlier this session were vacuous for
    exactly this kind of reason.)"""
    src = _script()
    assert len(src) > 10_000, f"deploy script looks truncated ({len(src)} bytes)"
    for marker in (
        "Waiting for rollouts to complete",
        "Testing health endpoint",
        "# ── Summary",
    ):
        assert marker in src, f"section marker {marker!r} is gone — checks are blind"
