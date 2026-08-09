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
WORKERS_YAML = REPO_ROOT / "k8s" / "base" / "worker-deployments.yaml"
METRICS_PORT = 9100


def _worker_deployments():
    docs = list(yaml.safe_load_all(WORKERS_YAML.read_text(encoding="utf-8")))
    out = {}
    for d in docs:
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
    assert len(workers) >= 4, f"expected >=4 worker deployments, parsed {list(workers)}"


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
