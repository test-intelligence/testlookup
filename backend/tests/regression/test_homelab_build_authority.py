"""Behavioral checks for exact-source homelab deployment authority."""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.shell_utils import bash_environment

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "homelabsetup/deploy-homelab.sh"
MCP_DEPLOYMENT = REPO / "k8s/base/mcp-deployment.yaml"


def _find_bash() -> str | None:
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    return None if found and "system32" in found.lower() else found


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="requires POSIX bash")


def _extract(name: str) -> str:
    source = DEPLOY.read_text(encoding="utf-8")
    match = re.search(rf"^([ \t]*){re.escape(name)}\(\) \{{$", source, re.M)
    assert match, f"{name}() is no longer testable"
    indent = match.group(1)
    lines = source[match.start() :].splitlines()
    for index, line in enumerate(lines[1:], 1):
        if line == f"{indent}}}":
            return "\n".join(lines[: index + 1])
    raise AssertionError(f"no closing brace for {name}()")


def _run_script(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / "runner.sh"
    runner.write_text("set -euo pipefail\n" + body, encoding="utf-8", newline="\n")
    assert BASH is not None
    return subprocess.run(
        [BASH, str(runner)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=bash_environment(BASH),
        timeout=60,
        check=False,
    )


def _git(tmp_path: Path, *args: str) -> None:
    assert BASH is not None
    subprocess.run(
        ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
    )


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    for directory in ("backend", "frontend", "mcp", "client"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "tracked.txt").write_text("clean\n", encoding="utf-8")
    (tmp_path / "backend/app").mkdir()
    (tmp_path / ".gitignore").write_text("*api_key*\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    return tmp_path


def _run_clean_check(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    function = textwrap.dedent(_extract("require_clean_build_inputs"))
    repo_arg = str(repo).replace("\\", "/")
    return _run_script(tmp_path, f'{function}\nrequire_clean_build_inputs "{repo_arg}"\n')


def _run_cleanup_then_clean_check(
    repo: Path, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    cleanup = textwrap.dedent(_extract("cleanup_generated_build_inputs"))
    clean_check = textwrap.dedent(_extract("require_clean_build_inputs"))
    repo_arg = str(repo).replace("\\", "/")
    return _run_script(
        tmp_path,
        f'{cleanup}\n{clean_check}\ncleanup_generated_build_inputs "{repo_arg}"\n'
        f'require_clean_build_inputs "{repo_arg}"\n',
    )


def test_clean_commit_is_accepted(source_repo: Path, tmp_path: Path):
    result = _run_clean_check(source_repo, tmp_path)
    assert result.returncode == 0, result.stderr


def test_owned_ca_and_sdk_staging_leftovers_are_cleaned_before_validation(
    source_repo: Path, tmp_path: Path
):
    for component in ("backend", "frontend", "mcp"):
        cert_dir = source_repo / component / "certs"
        cert_dir.mkdir()
        (cert_dir / "mitm-ca.crt").write_text("generated CA\n", encoding="utf-8")
    staged_sdk = source_repo / "backend/__client_sdks_staged"
    staged_sdk.mkdir()
    (staged_sdk / "stale.py").write_text("stale = True\n", encoding="utf-8")

    result = _run_cleanup_then_clean_check(source_repo, tmp_path)
    assert result.returncode == 0, result.stderr
    assert not staged_sdk.exists()
    for component in ("backend", "frontend", "mcp"):
        assert not (source_repo / component / "certs/mitm-ca.crt").exists()


@pytest.mark.parametrize("state", ["modified", "staged", "untracked", "ignored"])
def test_non_commit_build_input_is_rejected(
    source_repo: Path, tmp_path: Path, state: str
):
    if state == "modified":
        (source_repo / "backend/tracked.txt").write_text("changed\n", encoding="utf-8")
    elif state == "staged":
        (source_repo / "frontend/tracked.txt").write_text("changed\n", encoding="utf-8")
        _git(source_repo, "add", "frontend/tracked.txt")
    elif state == "untracked":
        (source_repo / "mcp/untracked.py").write_text("value = 1\n", encoding="utf-8")
    else:
        (source_repo / "backend/app/local_api_key_override.py").write_text(
            "secret = 'must-not-ship'\n", encoding="utf-8"
        )
    result = _run_clean_check(source_repo, tmp_path)
    assert result.returncode != 0


EXPECTED_IMAGE = "expected/backend:tag"
EXPECTED_ID = "registry/expected/backend@sha256:abc"


def _pod(
    *,
    name: str = "testlookup-backend-7d9f-abcde",
    phase: str = "Running",
    image: str = EXPECTED_IMAGE,
    ready: str = "true",
    image_id: str = EXPECTED_ID,
    deleting: str = "<none>",
) -> str:
    """One `kubectl get pod -o custom-columns=...` row, in the script's order."""
    return f"{name} {phase} {image} {ready} {image_id} {deleting}"


# The retained migration Job pod as the 2026-10-02 homelab deploy listed it:
# same image and digest as the backend, completed, never Ready.
COMPLETED_MIGRATION_POD = _pod(
    name="testlookup-migrate-1253898204-20261002135906-38162-16907-ptvzb",
    phase="Succeeded",
    ready="false",
)


def _run_image_check(
    tmp_path: Path, *, rows: str, component: str = "api"
) -> subprocess.CompletedProcess[str]:
    function = textwrap.dedent(_extract("verify_deployment_image"))
    body = (
        "NAMESPACE=testlookup\n"
        "POD_ROWS=$(cat <<'__PODS__'\n"
        f"{rows}\n"
        "__PODS__\n"
        ")\n"
        "kubectl() {\n"
        "  printf '%s\\n' \"$*\" >> kubectl.log\n"
        "  case \" $* \" in\n"
        f"    *\" get deployment \"*) printf '%s\\n' '{component}' ;;\n"
        "    *) printf '%s\\n' \"$POD_ROWS\" ;;\n"
        "  esac\n"
        "}\n"
        f"{function}\n"
        "status=0\n"
        f"verify_deployment_image testlookup-backend {EXPECTED_IMAGE} sha256:abc"
        " || status=$?\n"
        "cat kubectl.log\n"
        "printf 'FAILURE=%s\\n' \"$IMAGE_AUTHORITY_FAILURE\"\n"
        "exit \"$status\"\n"
    )
    return _run_script(tmp_path, body)


def test_ready_pod_on_exact_tag_and_digest_is_accepted(tmp_path: Path):
    result = _run_image_check(tmp_path, rows=_pod())
    assert result.returncode == 0, result.stderr


def test_deployment_component_excludes_retained_migration_job_pods(tmp_path: Path):
    result = _run_image_check(tmp_path, rows=_pod())
    assert result.returncode == 0, result.stderr
    assert (
        "-l app=testlookup-backend,app.kubernetes.io/component=api"
        in result.stdout
    )


def test_completed_job_pod_beside_ready_replicas_is_accepted(tmp_path: Path):
    """2026-10-02: a finished pod is READY=false forever and serves nothing.

    The check must not rely on the label selector alone to keep it out: the
    fake kubectl returns every row whatever `-l` says.
    """
    result = _run_image_check(
        tmp_path,
        rows="\n".join(
            [
                _pod(name="testlookup-backend-c8546b48d-l2tpk"),
                _pod(name="testlookup-backend-c8546b48d-n8cqd"),
                COMPLETED_MIGRATION_POD,
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_failed_evicted_pod_beside_ready_replicas_is_accepted(tmp_path: Path):
    result = _run_image_check(
        tmp_path,
        rows="\n".join(
            [
                _pod(),
                _pod(
                    name="testlookup-backend-55fd5b7578-whqgb",
                    phase="Failed",
                    image="old/backend:tag",
                    ready="false",
                    image_id="registry/old/backend@sha256:old",
                ),
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_only_finished_pods_are_rejected(tmp_path: Path):
    result = _run_image_check(tmp_path, rows=COMPLETED_MIGRATION_POD)
    assert result.returncode != 0
    assert "FAILURE=no live pod matches" in result.stdout


def test_a_running_pod_is_checked_whatever_owns_it(tmp_path: Path):
    """Only finished pods are skipped. A live pod with the Deployment's labels
    is a Service endpoint, so a running stray on another image still fails."""
    result = _run_image_check(
        tmp_path,
        rows="\n".join(
            [
                _pod(),
                _pod(
                    name="testlookup-migrate-stray-x1",
                    image="old/backend:tag",
                    image_id="registry/old/backend@sha256:old",
                ),
            ]
        ),
    )
    assert result.returncode != 0
    assert "FAILURE=pod testlookup-migrate-stray-x1 " in result.stdout


def test_backend_readiness_and_execs_exclude_retained_migration_job_pods():
    source = DEPLOY.read_text(encoding="utf-8")

    assert (
        'BACKEND_POD_SELECTOR="app=testlookup-backend,'
        'app.kubernetes.io/component=api"'
    ) in source
    assert source.count('wait_for_pods "$BACKEND_POD_SELECTOR"') == 2
    assert source.count('get pod -l "$BACKEND_POD_SELECTOR"') == 2
    assert 'wait_for_pods "app=testlookup-backend"' not in source


def test_deployment_without_component_label_is_rejected(tmp_path: Path):
    result = _run_image_check(tmp_path, rows=_pod(), component="")
    assert result.returncode != 0


def test_terminating_old_replica_is_excluded_from_image_authority(tmp_path: Path):
    result = _run_image_check(
        tmp_path,
        rows="\n".join(
            [
                _pod(
                    image="old/backend:tag",
                    image_id="registry/old/backend@sha256:old",
                    deleting="2026-09-16T20:00:00Z",
                ),
                _pod(),
            ]
        ),
    )
    assert result.returncode == 0, result.stderr


def test_only_terminating_replicas_are_rejected(tmp_path: Path):
    result = _run_image_check(tmp_path, rows=_pod(deleting="2026-09-16T20:00:00Z"))
    assert result.returncode != 0


def test_mcp_deployment_carries_component_label_into_its_pods():
    manifest = yaml.safe_load(MCP_DEPLOYMENT.read_text(encoding="utf-8"))
    deployment_component = manifest["metadata"]["labels"][
        "app.kubernetes.io/component"
    ]
    assert manifest["spec"]["template"]["metadata"]["labels"][
        "app.kubernetes.io/component"
    ] == deployment_component


@pytest.mark.parametrize(
    ("image", "ready", "image_id", "phase"),
    [
        ("old/backend:tag", "true", "registry/old/backend@sha256:abc", "Running"),
        (EXPECTED_IMAGE, "false", EXPECTED_ID, "Running"),
        (EXPECTED_IMAGE, "<none>", "<none>", "Pending"),
        (EXPECTED_IMAGE, "true", "registry/expected/backend@sha256:wrong", "Running"),
    ],
    ids=["wrong-image", "not-ready", "pending", "wrong-digest"],
)
def test_wrong_or_unready_pod_is_rejected(
    tmp_path: Path, image: str, ready: str, image_id: str, phase: str
):
    result = _run_image_check(
        tmp_path,
        rows=_pod(
            name="testlookup-backend-bad-1",
            phase=phase,
            image=image,
            ready=ready,
            image_id=image_id,
        ),
    )
    assert result.returncode != 0
    assert "FAILURE=pod testlookup-backend-bad-1 " in result.stdout


@pytest.mark.parametrize(
    "second_row",
    [
        _pod(image="old/backend:tag", image_id="registry/old/backend@sha256:old"),
        _pod(ready="false"),
        _pod(image_id="registry/expected/backend@sha256:wrong"),
    ],
    ids=["wrong-image", "not-ready", "wrong-digest"],
)
def test_one_correct_pod_cannot_hide_an_invalid_sibling(
    tmp_path: Path, second_row: str
):
    result = _run_image_check(
        tmp_path,
        rows="\n".join([_pod(), COMPLETED_MIGRATION_POD, second_row]),
    )
    assert result.returncode != 0


def test_no_selected_pods_is_rejected(tmp_path: Path):
    result = _run_image_check(tmp_path, rows="")
    assert result.returncode != 0


def test_malformed_pod_row_is_rejected(tmp_path: Path):
    result = _run_image_check(tmp_path, rows=_pod() + " surplus-column")
    assert result.returncode != 0


def _run_image_wait(
    tmp_path: Path, *, unready_checks: int, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Drive wait_for_deployment_image with a pod that turns Ready after N checks.

    `sleep` is stubbed to advance bash's SECONDS instead of waiting, so the
    window is exercised without wall-clock time. It sets SECONDS from its own
    fake clock (`FAKE_NOW`), never from `SECONDS + n`: SECONDS also counts real
    time, and re-reading it on every call accumulated that drift, so on a
    loaded machine (the push gate) the deadline came one check early.
    """
    wait = textwrap.dedent(_extract("wait_for_deployment_image"))
    verify = textwrap.dedent(_extract("verify_deployment_image"))
    unready_row = _pod(ready="false")
    ready_row = _pod()
    body = (
        "NAMESPACE=testlookup\n"
        f"IMAGE_AUTHORITY_TIMEOUT_SECONDS={timeout}\n"
        "IMAGE_AUTHORITY_POLL_SECONDS=5\n"
        "FAKE_NOW=0\n"
        "SECONDS=0\n"
        "sleep() { FAKE_NOW=$((FAKE_NOW + $1)); SECONDS=$FAKE_NOW; }\n"
        "kubectl() {\n"
        "  case \" $* \" in\n"
        "    *\" get deployment \"*) printf 'api\\n' ;;\n"
        "    *)\n"
        "      echo check >> checks.log\n"
        f"      if [ \"$(wc -l < checks.log)\" -le {unready_checks} ]; then\n"
        f"        printf '%s\\n' '{unready_row}'\n"
        "      else\n"
        f"        printf '%s\\n' '{ready_row}'\n"
        "      fi ;;\n"
        "  esac\n"
        "}\n"
        f"{verify}\n{wait}\n"
        "status=0\n"
        f"wait_for_deployment_image testlookup-backend {EXPECTED_IMAGE} sha256:abc"
        " || status=$?\n"
        "printf 'CHECKS=%s\\n' \"$(wc -l < checks.log | tr -d ' ')\"\n"
        "printf 'FAILURE=%s\\n' \"$IMAGE_AUTHORITY_FAILURE\"\n"
        "exit \"$status\"\n"
    )
    return _run_script(tmp_path, body)


def test_briefly_unready_pod_is_accepted_once_it_becomes_ready(tmp_path: Path):
    result = _run_image_wait(tmp_path, unready_checks=3, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHECKS=4" in result.stdout


def test_pod_that_stays_unready_fails_when_the_window_closes(tmp_path: Path):
    result = _run_image_wait(tmp_path, unready_checks=10_000, timeout=20)
    assert result.returncode != 0
    # Checks at t=0, 5, 10, 15 and 20; after that the deadline has passed.
    assert "CHECKS=5" in result.stdout
    assert "FAILURE=pod testlookup-backend-7d9f-abcde " in result.stdout


def test_main_flow_waits_for_image_authority_and_names_the_failing_pod():
    source = DEPLOY.read_text(encoding="utf-8")
    authority = source[source.index("APP_IMAGE_AUTHORITIES=(") :]
    authority = authority[: authority.index("TRAEFIK_IP=")]
    assert 'wait_for_deployment_image "$deployment" "$expected_image" "$digest"' in (
        authority
    )
    assert "verify_deployment_image" not in authority
    assert "${IMAGE_AUTHORITY_FAILURE}" in authority
    assert (
        'IMAGE_AUTHORITY_TIMEOUT_SECONDS="${IMAGE_AUTHORITY_TIMEOUT_SECONDS:-120}"'
        in source
    )


def test_serving_revision_must_equal_candidate(tmp_path: Path):
    function = textwrap.dedent(_extract("verify_serving_revision"))
    good = _run_script(
        tmp_path,
        'curl() { printf \'{"build":{"revision":"candidate"}}\'; }\n'
        + function
        + "\nverify_serving_revision 192.0.2.1 candidate\n",
    )
    assert good.returncode == 0, good.stderr

    bad = _run_script(
        tmp_path,
        'curl() { printf \'{"build":{"revision":"old"}}\'; }\n'
        + function
        + "\nverify_serving_revision 192.0.2.1 candidate\n",
    )
    assert bad.returncode != 0


def test_main_flow_verifies_every_application_deployment_after_rollout():
    source = DEPLOY.read_text(encoding="utf-8")
    authority = source.index("APP_IMAGE_AUTHORITIES=(")
    rollout = source.index("Waiting for rollouts to complete")
    assert authority > rollout
    expected_authorities = {
        "testlookup-backend": "backend|${BACKEND_DIGEST}",
        "testlookup-frontend": "frontend|${FRONTEND_DIGEST}",
        "testlookup-mcp": "mcp|${MCP_DIGEST}",
        "testlookup-worker-critical": "backend|${BACKEND_DIGEST}",
        "testlookup-worker-ingestion": "backend|${BACKEND_DIGEST}",
        "testlookup-worker-ai": "backend|${BACKEND_DIGEST}",
        "testlookup-worker-children": "backend|${BACKEND_DIGEST}",
        "testlookup-worker-default": "backend|${BACKEND_DIGEST}",
        "testlookup-beat": "backend|${BACKEND_DIGEST}",
    }
    for deployment, image_and_digest in expected_authorities.items():
        assert f'"{deployment}|{image_and_digest}"' in source[authority:]
    assert 'verify_serving_revision "$TRAEFIK_IP" "$BUILD_REVISION"' in source[
        authority:
    ]
