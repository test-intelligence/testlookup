"""H01 deployment entry points must perform the one-time protocol cutover."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_cutover_disables_hpa_before_scaling_legacy_backend():
    script = (REPO_ROOT / "scripts/prepare-live-fanout-cutover.sh").read_text(encoding="utf-8")

    delete_hpa = script.index('delete hpa "$hpa"')
    scale_zero = script.index('scale deployment "$deployment" --replicas=0')
    wait_for_delete = script.index("wait --for=delete pod")
    assert delete_hpa < scale_zero < wait_for_delete
    assert 'KCLI="${KCLI:-kubectl}"' in script
    assert "pod_protocols=" in script


def test_every_supported_kubernetes_apply_path_invokes_cutover():
    expected = {
        "Makefile": 4,
        "homelabsetup/deploy-homelab.sh": 1,
        "openshiftsetup/deploy-openshift-artifactory.sh": 1,
        "scripts/deploy-k8s.sh": 1,
        ".github/workflows/ci.yml": 1,
        "scripts/release/import-bundle.sh": 1,
        "scripts/release/offline-bundle.sh": 1,
    }
    for relative_path, minimum_count in expected.items():
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert text.count("prepare-live-fanout-cutover.sh") >= minimum_count, relative_path


def test_eks_delegates_cutover_and_migration_to_central_deploy_helper():
    workflow = (REPO_ROOT / ".github/workflows/deploy-eks.yml").read_text(
        encoding="utf-8"
    )
    assert "scripts/deploy-k8s.sh --cloud=aws --release-manifest=" in workflow


def _find_bash() -> str | None:
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    return None if found and "system32" in found.lower() else found


def _shell_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if os.name == "nt" and len(resolved) > 2 and resolved[1] == ":":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


SERVING_SELECTOR = "app=testlookup-backend,app.kubernetes.io/component!=migration"
# The pod the migration Job leaves behind on every deploy: it carries
# `app: testlookup-backend` but no live-fanout-protocol label.
RETAINED_MIGRATION_POD = (
    "testlookup-migrate-1253898204-20261002135906-38162-16907-ptvzb="
)


def _run_cutover(tmp_path: Path, serving_pods: str) -> str:
    """Run the cutover against a fake kubectl that honours the pod selector.

    A bare `app=testlookup-backend` query also returns the retained migration
    Job pod, as the real API server does. Returns the kubectl call log.
    """
    bash = _find_bash()
    if bash is None:
        pytest.skip("requires POSIX bash")
    fake = tmp_path / "kubectl"
    log = tmp_path / "kubectl.log"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
        'args=" $* "\n'
        'if [[ "$args" == *" get deployment "*"jsonpath="* ]]; then\n'
        "  printf v1; exit 0\n"
        "fi\n"
        f'if [[ "$args" == *" get pods -l {SERVING_SELECTOR} "* ]]; then\n'
        "  printf '%s' \"$FAKE_SERVING_PODS\"; exit 0\n"
        "fi\n"
        'if [[ "$args" == *" get pods -l app=testlookup-backend "* ]]; then\n'
        f"  printf '%s%s\\n' \"$FAKE_SERVING_PODS\" '{RETAINED_MIGRATION_POD}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(0o755)
    env = bash_environment(bash)
    env.update(
        {
            "KCLI": _shell_path(fake),
            "FAKE_KUBECTL_LOG": _shell_path(log),
            "FAKE_SERVING_PODS": serving_pods,
        }
    )
    completed = subprocess.run(
        [
            bash,
            _shell_path(REPO_ROOT / "scripts/prepare-live-fanout-cutover.sh"),
            "testlookup",
        ],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return log.read_text(encoding="utf-8")


def test_retained_migration_pod_does_not_repeat_the_one_time_cutover(
    tmp_path: Path,
):
    """2026-10-02 deploy log: the HPA was deleted and the API scaled to zero
    although every serving pod was already v1. The only unlabelled pod was the
    migration Job's, which every deploy re-creates just before this script."""
    calls = _run_cutover(
        tmp_path,
        "testlookup-backend-c8546b48d-l2tpk=v1\n"
        "testlookup-backend-c8546b48d-n8cqd=v1\n",
    )
    assert "get pods -l " + SERVING_SELECTOR in calls
    assert "delete hpa" not in calls
    assert "--replicas=0" not in calls


def test_legacy_serving_pod_still_triggers_the_cutover(tmp_path: Path):
    calls = _run_cutover(
        tmp_path,
        "testlookup-backend-c8546b48d-l2tpk=v1\n"
        "testlookup-backend-55fd5b7578-whqgb=\n",
    )
    assert "delete hpa testlookup-backend-hpa" in calls
    assert "scale deployment testlookup-backend --replicas=0" in calls
