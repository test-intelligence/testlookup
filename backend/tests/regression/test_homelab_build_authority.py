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
        "verify_deployment_image testlookup-backend expected/backend:tag sha256:abc\n"
        "cat kubectl.log\n"
    )
    return _run_script(tmp_path, body)


def test_ready_pod_on_exact_tag_and_digest_is_accepted(tmp_path: Path):
    result = _run_image_check(
        tmp_path,
        rows="expected/backend:tag true registry/expected/backend@sha256:abc",
    )
    assert result.returncode == 0, result.stderr


def test_deployment_component_excludes_retained_migration_job_pods(tmp_path: Path):
    result = _run_image_check(
        tmp_path,
        rows="expected/backend:tag true registry/expected/backend@sha256:abc",
    )
    assert result.returncode == 0, result.stderr
    assert (
        "-l app=testlookup-backend,app.kubernetes.io/component=api"
        in result.stdout
    )


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
    result = _run_image_check(
        tmp_path,
        rows="expected/backend:tag true registry/expected/backend@sha256:abc",
        component="",
    )
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
    ("image", "ready", "image_id"),
    [
        ("old/backend:tag", "true", "registry/old/backend@sha256:abc"),
        ("expected/backend:tag", "false", "registry/expected/backend@sha256:abc"),
        ("expected/backend:tag", "true", "registry/expected/backend@sha256:wrong"),
    ],
)
def test_wrong_or_unready_pod_is_rejected(tmp_path: Path, image: str, ready: str, image_id: str):
    result = _run_image_check(tmp_path, rows=f"{image} {ready} {image_id}")
    assert result.returncode != 0


@pytest.mark.parametrize(
    "second_row",
    [
        "old/backend:tag true registry/old/backend@sha256:old",
        "expected/backend:tag false registry/expected/backend@sha256:abc",
        "expected/backend:tag true registry/expected/backend@sha256:wrong",
    ],
)
def test_one_correct_pod_cannot_hide_an_invalid_sibling(
    tmp_path: Path, second_row: str
):
    result = _run_image_check(
        tmp_path,
        rows=(
            "expected/backend:tag true registry/expected/backend@sha256:abc\n"
            + second_row
        ),
    )
    assert result.returncode != 0


def test_no_selected_pods_is_rejected(tmp_path: Path):
    result = _run_image_check(tmp_path, rows="")
    assert result.returncode != 0


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
