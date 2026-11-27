"""Celery workers must expose their own Prometheus scrape target.

`TestLookupTaskLatencyHigh` alerts on `celery_task_runtime_seconds`. Only the
worker sees task execution, so the backend cannot emit it — and PR #506 could
only fix the *queue-depth* half of the ghost-metric problem for that reason,
leaving this alert inert.

Three things have to line up, and each fails silently on its own:

1. the histogram exists and is recorded by a task signal;
2. every worker pod carries `PROMETHEUS_MULTIPROC_DIR` **and** a writable volume
   at that exact path — prefork children keep private registries, so without
   multiprocess mode the process serving /metrics publishes only its own view;
3. the pod is annotated for scraping on the port the exporter actually binds.

A mismatch between the env var and the mount path, or between the annotation
port and the container port, produces a worker that looks instrumented and
reports nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKERS_YAMLS = (
    REPO_ROOT / "k8s" / "base" / "worker-deployments.yaml",
    REPO_ROOT / "k8s" / "base" / "worker-children-deployment.yaml",
)
METRICS_PORT = 9100


def _worker_deployments():
    out = {}
    for manifest in WORKERS_YAMLS:
        for d in yaml.safe_load_all(manifest.read_text(encoding="utf-8")):
            if not d or d.get("kind") != "Deployment":
                continue
            name = d["metadata"]["name"]
            # beat schedules and executes no tasks, so it has nothing to report.
            if "beat" in name:
                continue
            out[name] = d
    return out


def test_the_fixture_found_the_workers():
    """Guards the rest: a silent parse failure would make every check vacuous."""
    workers = _worker_deployments()
    assert len(workers) >= 5, f"expected >=5 worker deployments, parsed {list(workers)}"


class TestTheHistogram:
    def test_it_exists_with_the_alerted_name(self):
        from app.core.metrics import celery_task_runtime_seconds

        assert celery_task_runtime_seconds._name == "celery_task_runtime_seconds"

    def test_buckets_span_the_alert_threshold(self):
        """The alert asks for p99 > 120s. prometheus_client's default buckets
        top out at 10s, which would dump every slow task into +Inf and make the
        quantile meaningless — the metric would exist and still not answer the
        question."""
        from app.core.metrics import celery_task_runtime_seconds

        buckets = celery_task_runtime_seconds._upper_bounds
        assert max(b for b in buckets if b != float("inf")) >= 120

    def test_a_task_signal_records_it(self):
        import inspect

        from app.worker import celery_app

        src = inspect.getsource(celery_app)
        assert "task_postrun" in src and "celery_task_runtime_seconds" in src

    def test_instrumentation_cannot_fail_a_task(self):
        """A metrics error must never propagate into task execution."""
        from app.worker.celery_app import _record_task_runtime

        _record_task_runtime(task_id="never-started", task=None)  # must not raise


class TestEveryWorkerIsScrapable:
    @pytest.mark.parametrize("name", sorted(_worker_deployments()))
    def test_multiproc_dir_matches_a_real_mount(self, name):
        dep = _worker_deployments()[name]
        spec = dep["spec"]["template"]["spec"]
        container = spec["containers"][0]

        env = {e["name"]: e.get("value") for e in container.get("env", [])}
        multiproc = env.get("PROMETHEUS_MULTIPROC_DIR")
        assert multiproc, f"{name} has no PROMETHEUS_MULTIPROC_DIR"

        mounts = {m["name"]: m["mountPath"] for m in container.get("volumeMounts", [])}
        assert multiproc in mounts.values(), (
            f"{name}: PROMETHEUS_MULTIPROC_DIR={multiproc} but no volume is mounted "
            f"there ({mounts}) — children would write into the container filesystem "
            f"and the exporter would aggregate nothing"
        )

        volumes = {v["name"] for v in spec.get("volumes", [])}
        mounted_at = [n for n, path in mounts.items() if path == multiproc]
        assert set(mounted_at) <= volumes, f"{name}: mount references an undeclared volume"

    @pytest.mark.parametrize("name", sorted(_worker_deployments()))
    def test_scrape_annotation_matches_the_container_port(self, name):
        dep = _worker_deployments()[name]
        template = dep["spec"]["template"]
        annotations = template["metadata"].get("annotations", {})
        container = template["spec"]["containers"][0]

        assert annotations.get("prometheus.io/scrape") == "true", f"{name} is not scraped"
        ports = [p.get("containerPort") for p in container.get("ports", [])]
        assert METRICS_PORT in ports, f"{name} does not expose {METRICS_PORT}: {ports}"
        assert annotations.get("prometheus.io/port") == str(METRICS_PORT), (
            f"{name}: scrape annotation points at "
            f"{annotations.get('prometheus.io/port')} but the container exposes {ports} — "
            f"Prometheus would scrape a closed port and the target would just be down"
        )

    @pytest.mark.parametrize("name", sorted(_worker_deployments()))
    def test_the_multiproc_volume_is_ephemeral(self, name):
        """The files describe *this pod's* live processes. Persisting them across
        restarts would resurrect metrics for children that no longer exist."""
        dep = _worker_deployments()[name]
        spec = dep["spec"]["template"]["spec"]
        for vol in spec.get("volumes", []):
            if vol["name"] == "prometheus-multiproc":
                assert "emptyDir" in vol, f"{name}: multiproc volume must be emptyDir"
                return
        pytest.fail(f"{name} declares no prometheus-multiproc volume")


def test_dead_children_are_reaped():
    """--max-tasks-per-child recycles prefork children constantly; without
    marking them dead the multiproc directory grows a file set per PID for the
    life of the pod."""
    import inspect

    from app.worker import celery_app

    src = inspect.getsource(celery_app)
    assert "worker_process_shutdown" in src and "mark_process_dead" in src


def test_worker_metric_files_have_a_bounded_container_lifecycle():
    """Dead counter/histogram files are retained until a graceful restart."""
    import inspect

    from app.worker import celery_app

    src = inspect.getsource(celery_app)
    assert "WORKER_METRICS_MAX_FILES" in src
    assert 'WORKER_METRICS_FINAL_SCRAPE_GRACE_SECONDS", "65"' in src
    assert "worker_metrics_file_limit_reached" in src
    assert "signal.SIGTERM" in src


def test_parent_reaps_only_confirmed_dead_live_gauges(tmp_path, monkeypatch):
    """SIGKILL skips child shutdown; ambiguity must never erase a live gauge."""
    import errno
    import os

    from prometheus_client import multiprocess

    from app.worker import celery_app

    dead_files = {
        f"gauge_{mode}_100.db"
        for mode in ("liveall", "livesum", "livemin", "livemax", "livemostrecent")
    }
    keep_files = {
        "gauge_livemax_101.db",  # process exists
        "gauge_livemax_102.db",  # permission denied
        "gauge_livemax_103.db",  # unrelated OS failure
        "gauge_livemax_104.db",  # PID is not representable on this platform
        "counter_100.db",
        "histogram_100.db",
        "gauge_sum_100.db",
        "gauge_mostrecent_100.db",
        "gauge_livemax_0.db",
        "gauge_livemax_-1.db",
        "gauge_livemax_notpid.db",
        "gauge_livemax_1.0.db",
        "gauge_livemax_001.db",
        "gauge_liveunknown_999.db",
        "README",
    }
    for name in dead_files | keep_files:
        (tmp_path / name).write_bytes(b"metric observation")
    # A matching directory is not a process's gauge file either.
    (tmp_path / "gauge_livemax_105.db").mkdir()
    probes = []
    reaped = []
    real_mark_process_dead = multiprocess.mark_process_dead

    def probe(pid, sig):
        assert sig == 0
        probes.append(pid)
        if pid == 100:
            raise ProcessLookupError(errno.ESRCH, "synthetic dead child")
        if pid == 102:
            raise PermissionError(errno.EPERM, "synthetic protected process")
        if pid == 103:
            raise OSError(errno.EIO, "synthetic unavailable process probe")
        if pid == 104:
            raise OverflowError("synthetic unrepresentable PID")
        assert pid == 101

    def mark_process_dead(pid, path=None):
        reaped.append((pid, path))
        real_mark_process_dead(pid, path=path)

    # Scope os.name to this call so Windows can verify the POSIX branch without
    # changing pathlib's platform selection during fixture/pytest operations.
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "posix")
        patch.setattr(os, "kill", probe)
        patch.setattr(multiprocess, "mark_process_dead", mark_process_dead)
        celery_app._reap_dead_child_metric_gauges(tmp_path)

    assert sorted(probes) == [100, 101, 102, 103, 104]
    assert reaped == [(100, str(tmp_path))]
    assert all(not (tmp_path / name).exists() for name in dead_files)
    assert all((tmp_path / name).read_bytes() == b"metric observation" for name in keep_files)
    assert (tmp_path / "gauge_livemax_105.db").is_dir()


def test_parent_gauge_reaper_is_disabled_off_posix(tmp_path, monkeypatch):
    import os

    from app.worker import celery_app

    gauge = tmp_path / "gauge_livemax_100.db"
    gauge.write_bytes(b"keep")

    def unexpected_probe(*_args):
        pytest.fail("non-POSIX platforms must not probe or signal processes")

    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        patch.setattr(os, "kill", unexpected_probe)
        celery_app._reap_dead_child_metric_gauges(tmp_path)
    assert gauge.read_bytes() == b"keep"


def test_periodic_guard_reaps_before_enforcing_the_file_limit(tmp_path, monkeypatch):
    """A dead live gauge must not trigger a needless container recycle."""
    import threading

    from app.worker import celery_app

    stale = tmp_path / "gauge_livemax_100.db"
    stale.write_bytes(b"dead")
    (tmp_path / "counter_100.db").write_bytes(b"retained")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_METRICS_MAX_FILES", "1")
    monkeypatch.setenv("WORKER_METRICS_FILE_CHECK_SECONDS", "7")
    monkeypatch.setenv("WORKER_METRICS_FINAL_SCRAPE_GRACE_SECONDS", "65")
    calls = []

    class StopGuard(Exception):
        pass

    def reap(metric_dir):
        assert metric_dir == tmp_path
        calls.append("reap")
        stale.unlink()

    class Event:
        def wait(self, timeout):
            calls.append(timeout)
            raise StopGuard

    class Thread:
        def __init__(self, *, target, name, daemon):
            assert name == "prometheus-multiproc-file-guard" and daemon is True
            self.target = target

        def start(self):
            with pytest.raises(StopGuard):
                self.target()

    monkeypatch.setattr(celery_app, "_reap_dead_child_metric_gauges", reap)
    monkeypatch.setattr(threading, "Event", Event)
    monkeypatch.setattr(threading, "Thread", Thread)
    celery_app._start_metrics_file_guard()
    assert calls == ["reap", 7.0]
    assert (tmp_path / "counter_100.db").read_bytes() == b"retained"
