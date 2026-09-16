from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_order(text: str, *needles: str) -> None:
    positions = [text.index(needle) for needle in needles]
    assert positions == sorted(positions)


def test_migration_job_is_bounded_observable_and_uses_requested_image() -> None:
    runner = _text("scripts/run-k8s-migrations.sh")

    assert "set -euo pipefail" in runner
    assert 'BACKEND_IMAGE="${2:-}"' in runner
    assert 'RELEASE_ID="${3:-}"' in runner
    assert 'ATTEMPT_ID="${MIGRATION_ATTEMPT_ID:-' in runner
    assert 'JOB_NAME="testlookup-migrate-$JOB_SUFFIX-$ATTEMPT_ID"' in runner
    assert "EXISTING_IMAGE" not in runner
    assert "kind: Job" in runner
    assert "backoffLimit: 0" in runner
    assert "activeDeadlineSeconds:" in runner
    assert "automountServiceAccountToken: false" in runner
    assert "initContainers:" in runner
    assert "socket.create_connection" in runner
    assert runner.count('image: ${BACKEND_IMAGE}') == 2
    assert 'command: ["alembic", "upgrade", "head"]' in runner
    assert "name: testlookup-config" not in runner
    assert "name: testlookup-secrets" in runner
    assert "app: testlookup-backend" in runner
    assert "PG_PROCESS_ROLE" in runner
    assert "value: operation" in runner
    assert "Complete=True" in runner
    assert "Failed=True" in runner
    assert "describe job" in runner
    assert 'logs "job/$JOB_NAME"' in runner


def test_deploy_entrypoints_block_on_migration_before_rollout_success() -> None:
    deploy = _text("scripts/deploy-k8s.sh")
    migration = deploy.index("Applying database migrations before application rollout")
    runner = deploy.index(
        'run-k8s-migrations.sh" "$NAMESPACE" "$BACKEND_IMAGE" "$RELEASE_ID"'
    )
    apply = deploy.index('echo "==> Applying overlay..."')
    rollout = deploy.index("rollout status deployment/testlookup-backend")
    assert migration < runner < apply < rollout
    assert '["destination_refs"]["backend"]' in deploy
    assert '["source_sha"]' in deploy

    eks = _text(".github/workflows/deploy-eks.yml")
    assert "scripts/deploy-k8s.sh --cloud=aws --release-manifest=" in eks
    assert "kubectl apply -k" not in eks

    homelab = _text("homelabsetup/deploy-homelab.sh")
    assert "Migrating with the candidate image before the application rollout" in homelab
    assert homelab.index("Migrating with the candidate image") < homelab.index(
        'kubectl apply -k "$REPO_ROOT/k8s/overlays/homelab"'
    )
    assert "Fresh install detected" in homelab

    for workflow in ("deploy-gke.yml", "deploy-aks.yml"):
        assert "scripts/deploy-k8s.sh" in _text(f".github/workflows/{workflow}")

    ci = _text(".github/workflows/ci.yml")
    assert ci.index("run-k8s-migrations.sh testlookup-dev") < ci.index(
        "kubectl apply -k k8s/overlays/dev"
    )
    assert "needs.build-images.outputs.backend_digest" in ci


def test_make_and_offline_paths_include_the_same_runner() -> None:
    makefile = _text("Makefile")
    for target in (
        "k8s-deploy-dev",
        "k8s-deploy-staging",
        "k8s-deploy-prod",
        "k8s-deploy-openshift",
    ):
        block = makefile[makefile.index(f"{target}:") :]
        assert block.index("run-k8s-migrations.sh") < block.index(
            "kubectl apply -k", block.index("run-k8s-migrations.sh")
        )
    assert 'bash scripts/run-k8s-migrations.sh "$(K8S_NAMESPACE)" "$$image"' in makefile
    assert "--dry-run=client" in makefile

    bundle = _text("scripts/release/offline-bundle.sh")
    assert 'scripts/run-k8s-migrations.sh" "$STAGING/run-k8s-migrations.sh"' in bundle

    instructions = _text("scripts/release/import-bundle.sh")
    assert "had_backend=0" in instructions
    assert instructions.count(r'"\$backend_image"') == 2
    assert r'"\$had_backend"' in instructions
    assert '"$backend_image"' not in instructions.replace(r'"\$backend_image"', "")
    assert instructions.index("bash ./run-k8s-migrations.sh testlookup") < instructions.index(
        "kubectl apply -f"
    )
    assert "KCLI=oc" in instructions

    guide = _text("user-guide/air-gapped-install.md")
    assert "had_backend=0" in guide
    assert guide.index("bash ./run-k8s-migrations.sh testlookup") < guide.index(
        "kubectl apply -f"
    )
    assert "KCLI=oc" in guide


def _fake_kubectl(tmp_path: Path) -> tuple[Path, Path]:
    fake = tmp_path / "kubectl"
    log = tmp_path / "kubectl.log"
    fake.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_KUBECTL_LOG"
args=" $* "
if [[ "$args" == *" create -f -"* ]]; then
  payload="$(cat)"
  printf '%s\\n' "$payload" >> "$FAKE_KUBECTL_LOG"
  exit 0
fi
if [[ "$args" == *" get job "* && "$args" == *"status.conditions"* ]]; then
  case "${FAKE_KUBECTL_RESULT:-complete}" in
    complete) printf 'Complete=True\\n' ;;
    failed) printf 'Failed=True\\n' ;;
    pending) : ;;
  esac
  exit 0
fi
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(0o755)
    return fake, log


def _run_runner(
    tmp_path: Path,
    image: str,
    release_id: str,
    *,
    attempt_id: str,
    result: str = "complete",
):
    if os.name == "nt":
        bash_path = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Git/bin/bash.exe"
        )
        bash = str(bash_path) if bash_path.exists() else None
    else:
        bash = shutil.which("bash")
    if not bash:
        pytest.skip("Git Bash or bash is required")

    def shell_path(path: Path) -> str:
        resolved = path.resolve().as_posix()
        if os.name == "nt" and len(resolved) > 2 and resolved[1] == ":":
            return f"/{resolved[0].lower()}{resolved[2:]}"
        return resolved

    fake, log = _fake_kubectl(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "KCLI": shell_path(fake),
            "FAKE_KUBECTL_LOG": shell_path(log),
            "FAKE_KUBECTL_RESULT": result,
            "MIGRATION_ATTEMPT_ID": attempt_id,
            "MIGRATION_POLL_SECONDS": "0",
            "MIGRATION_TIMEOUT_SECONDS": "2",
        }
    )
    completed = subprocess.run(
        [
            bash,
            shell_path(ROOT / "scripts/run-k8s-migrations.sh"),
            "testlookup",
            image,
            release_id,
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return completed, log


def _created_job_names(log: Path) -> list[str]:
    return re.findall(
        r"^  name: (testlookup-migrate-[a-z0-9-]+)$",
        log.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )


def test_runner_uses_unique_attempt_jobs_and_readiness_init_container(tmp_path: Path):
    image = "ghcr.io/anandtopu/testlookup/backend@sha256:" + "a" * 64
    release_id = "b" * 40

    first, log = _run_runner(
        tmp_path, image, release_id, attempt_id="attempt-one"
    )
    assert first.returncode == 0, first.stderr
    second, log = _run_runner(
        tmp_path, image, release_id, attempt_id="attempt-two"
    )
    assert second.returncode == 0, second.stderr

    names = _created_job_names(log)
    assert len(names) == 2
    assert names[0] != names[1]
    payload = log.read_text(encoding="utf-8")
    assert payload.count(f"image: {image}") == 4
    assert "name: wait-for-postgres" in payload
    assert 'url = make_url(os.environ["DATABASE_URL"])' in payload
    assert "value: operation" in payload
    assert "USER testlookup" in payload
    assert "command not found" not in first.stderr
    assert "command not found" not in second.stderr


def test_runner_failed_condition_exits_promptly_with_diagnostics(tmp_path: Path):
    image = "registry.example/backend@sha256:" + "a" * 64
    result, log = _run_runner(
        tmp_path,
        image,
        "d" * 40,
        attempt_id="failed-attempt",
        result="failed",
    )

    assert result.returncode != 0
    calls = log.read_text(encoding="utf-8")
    assert calls.count("status.conditions") == 1
    assert " describe job " in calls
    assert " logs job/" in calls
