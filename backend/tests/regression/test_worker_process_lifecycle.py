"""Readiness belongs to the current parent generation, including retained /tmp."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.worker import worker_process as lifecycle

pytestmark = pytest.mark.regression


@pytest.fixture
def state(tmp_path, monkeypatch):
    identity = {"started": "100", "pid_namespace": 200, "boot_id": "b" * 36}
    def snapshot(pid):
        return {"pid": pid, **identity} if pid > 0 else None
    monkeypatch.setattr(lifecycle, "process_identity", snapshot)
    monkeypatch.setattr(lifecycle, "_launcher_running", lambda pid: False)
    monkeypatch.delenv(lifecycle.GENERATION_ENV, raising=False)
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    ready = tmp_path / "ready"
    return ready, identity


def test_retained_plain_marker_is_not_ready_before_any_celery_hook(state):
    ready, _ = state
    ready.write_text("ready\n", encoding="utf-8")
    assert not lifecycle.probe("ready", pid=os.getpid(), ready_file=str(ready))


@pytest.mark.parametrize("changed,value", [("started", "101"), ("pid_namespace", 201), ("boot_id", "c" * 36)])
def test_replacement_resets_old_marker_and_metrics_before_imports_then_becomes_ready(state, tmp_path, monkeypatch, changed, value):
    ready, identity = state
    metric_dir = tmp_path / "metrics"
    metric_dir.mkdir()
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(metric_dir))
    lifecycle.prepare_startup(str(ready), force=True)
    assert lifecycle.mark_ready(str(ready))
    (metric_dir / "counter_123.db").write_bytes(b"retained")
    (metric_dir / "README").write_text("keep", encoding="utf-8")
    original = json.loads(ready.read_text())

    # Same PID namespace can reuse a PID after a crash. The kernel start time
    # differs before a replacement has imported Celery or called celeryd_init.
    identity[changed] = value
    assert not lifecycle.probe("ready", pid=os.getpid(), ready_file=str(ready))
    lifecycle.prepare_startup(str(ready), force=True)
    assert not ready.exists() and not (metric_dir / "counter_123.db").exists()
    assert (metric_dir / "README").read_text() == "keep"
    assert lifecycle.probe("alive", pid=os.getpid(), ready_file=str(ready))
    assert not lifecycle.probe("ready", pid=os.getpid(), ready_file=str(ready))
    assert lifecycle.mark_ready(str(ready))
    assert json.loads(ready.read_text())["generation"] != original["generation"]
    assert lifecycle.probe("ready", pid=os.getpid(), ready_file=str(ready))


def test_same_pid_exec_replacement_is_not_ready_while_launcher_runs(state, monkeypatch):
    ready, _ = state
    lifecycle.prepare_startup(str(ready), force=True)
    lifecycle.mark_ready(str(ready))
    monkeypatch.setattr(lifecycle, "_launcher_running", lambda pid: True)
    assert not lifecycle.probe("ready", pid=os.getpid(), ready_file=str(ready))


def test_init_after_import_preserves_current_generation_metrics(state, tmp_path, monkeypatch):
    ready, _ = state
    metric_dir = tmp_path / "metrics"
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(metric_dir))
    lifecycle.prepare_startup(str(ready), force=True)
    current = metric_dir / f"counter_{os.getpid()}.db"
    current.write_bytes(b"already mapped during task imports")
    lifecycle.prepare_startup(str(ready))
    assert current.read_bytes() == b"already mapped during task imports"


def test_warm_shutdown_clears_readiness_while_live_parent_drains(state):
    ready, _ = state
    lifecycle.prepare_startup(str(ready), force=True)
    lifecycle.mark_ready(str(ready))
    assert lifecycle.clear_ready(str(ready))
    assert not lifecycle.probe("ready", pid=os.getpid(), ready_file=str(ready))
    assert lifecycle.probe("alive", pid=os.getpid(), ready_file=str(ready))
    assert lifecycle.clear_current(str(ready))
    assert not lifecycle.probe("alive", pid=os.getpid(), ready_file=str(ready))


def test_old_generation_shutdown_cannot_clear_replacement(state, monkeypatch):
    ready, _ = state
    lifecycle.prepare_startup(str(ready), force=True)
    previous = os.environ[lifecycle.GENERATION_ENV]
    lifecycle.prepare_startup(str(ready), force=True)
    lifecycle.mark_ready(str(ready))
    current = ready.read_bytes()
    monkeypatch.setenv(lifecycle.GENERATION_ENV, previous)
    assert not lifecycle.clear_current(str(ready))
    assert not lifecycle.clear_ready(str(ready))
    assert ready.read_bytes() == current


def test_live_sibling_state_and_metrics_are_preserved(state, tmp_path, monkeypatch):
    ready, _ = state
    lifecycle.prepare_startup(str(ready), force=True)
    lifecycle.mark_ready(str(ready))
    owner_path = lifecycle._owner_path(ready)
    owner = json.loads(owner_path.read_bytes())
    owner["identity"]["pid"] = os.getpid() + 1000
    owner_path.write_text(json.dumps(owner))
    metric_dir = tmp_path / "metrics"
    metric_dir.mkdir()
    metric = metric_dir / "counter_123.db"
    metric.write_bytes(b"live sibling")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(metric_dir))
    with pytest.raises(lifecycle.WorkerStartupError, match="live_owner"):
        lifecycle.prepare_startup(str(ready), force=True)
    assert metric.read_bytes() == b"live sibling" and ready.exists()


def test_only_current_parent_and_matching_state_can_be_ready(state):
    ready, _ = state
    lifecycle.prepare_startup(str(ready), force=True)
    lifecycle.mark_ready(str(ready))
    assert not lifecycle.probe("ready", pid=os.getpid() + 1, ready_file=str(ready))
    value = json.loads(ready.read_bytes())
    value["generation"] = "a" * 32
    ready.write_text(json.dumps(value))
    assert not lifecycle.probe("ready", pid=os.getpid(), ready_file=str(ready))


def test_worker_launcher_executes_unchanged_celery_arguments_before_import(monkeypatch):
    calls = []
    class Executed(Exception):
        pass
    monkeypatch.setattr(lifecycle, "prepare_startup", lambda **kwargs: calls.append(("prepare", kwargs)))
    def execute(program, arguments, environment):
        calls.append((program, arguments))
        raise Executed
    monkeypatch.setattr(lifecycle.os, "execvpe", execute)
    argv = ["celery", "-A", "app.worker.celery_app", "worker", "-Q", "default,critical", "--concurrency=2"]
    with pytest.raises(Executed):
        lifecycle.main(argv)
    assert calls == [("prepare", {"force": True}), ("celery", argv)]
    assert lifecycle.main(["celery", "-A", "app.worker.celery_app", "beat"]) == 2


def test_probe_imports_no_framework_and_returns_in_profile_timeout(tmp_path):
    backend = Path(__file__).resolve().parents[2]
    script = "import sys; from app.worker import worker_process; assert 'celery' not in sys.modules; assert 'prometheus_client' not in sys.modules"
    subprocess.run([sys.executable, "-c", script], cwd=backend, check=True, capture_output=True, timeout=5)
    result = subprocess.run([sys.executable, "-m", "app.worker.worker_process", "probe", "--ready-file", str(tmp_path / "missing")],
                            cwd=backend, capture_output=True, timeout=5)
    assert result.returncode == 1 and not result.stdout and not result.stderr


@pytest.mark.parametrize("files", [
    ("docker-compose.yml",), ("docker-compose.release.yml",), ("docker-compose.dev-lite.yml",),
    ("docker-compose.yml", "docker-compose.gcp-vm.yml"),
    ("docker-compose.release.yml", "docker-compose.airgap.yml"),
    ("docker-compose.release.yml", "docker-compose.demo.yml"),
])
def test_supported_compose_workers_keep_early_launch_probe_and_pid_one_contract(files):
    import shlex
    from compose_topology_contract import load_compose_topology

    root = Path(__file__).resolve().parents[3]
    topology = load_compose_topology(root / path for path in files)
    for name in ("worker", "worker-children"):
        service = topology["services"][name]
        argv = shlex.split(service["command"]) if isinstance(service["command"], str) else service["command"]
        assert argv[:4] == ["python", "-m", "app.worker.worker_process", "celery"]
        assert "worker" in argv and not service.get("init")
        assert service["healthcheck"]["test"] == ["CMD", "python", "-m", "app.worker.worker_process", "probe", "--mode=ready"]


def test_development_and_release_images_copy_the_worker_helper_with_the_application():
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    development = dockerfile.split(" AS development", 1)[1].split(" AS builder", 1)[0]
    assert "COPY . ." in development
    assert "COPY --from=builder /app/app ./app" in dockerfile
    assert not any(line.startswith("ENTRYPOINT") for line in dockerfile.splitlines())
