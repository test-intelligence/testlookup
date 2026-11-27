"""Disposable Linux PID-namespace proof of retained-state worker replacement.

Run as PID 1 in a private, network-none container with this folder mounted
read-only. Uses the image's Celery/prometheus_client and the supplied helper.
No project application, database, external broker or provider is loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

FIXTURE = '''
from pathlib import Path
import os
import time
from celery import Celery, signals
from kombu import Connection
from prometheus_client import Counter
import worker_process as lifecycle

root = Path(os.environ["READINESS_FIXTURE_DIR"])
counter = Counter("testlookup_native_parent_startups_total", "Current native fixture parent startup")
counter.inc()
(root / "importing").write_text(str(os.getpid()))
time.sleep(float(os.environ.get("READINESS_IMPORT_DELAY", "0")))
broker = os.environ.get("READINESS_BROKER_URL", "memory://")
if broker != "memory://":
    original_connect = Connection._establish_connection
    def observed_connect(self, *args, **kwargs):
        try:
            return original_connect(self, *args, **kwargs)
        except Exception:
            (root / "broker-unavailable-observed").write_text("yes")
            raise
    Connection._establish_connection = observed_connect
app = Celery("readiness-fixture", broker=broker, backend="cache+memory://")
app.conf.update(task_ignore_result=True, worker_hijack_root_logger=False,
                broker_connection_retry_on_startup=True,
                broker_transport_options={"socket_connect_timeout": 1, "socket_timeout": 1})

@app.task(name="native.readiness_hold")
def hold():
    (root / "task-started").write_text(str(os.getpid()))
    time.sleep(3)

@signals.celeryd_init.connect(weak=False)
def initialized(**kwargs):
    metric = Path(os.environ["PROMETHEUS_MULTIPROC_DIR"]) / ("counter_%d.db" % os.getpid())
    before = metric.exists()
    lifecycle.prepare_startup()
    (root / "metrics-preserved").write_text("yes" if before and metric.exists() else "no")

@signals.worker_ready.connect(weak=False)
def ready(**kwargs):
    lifecycle.mark_ready()
    (root / "consumer-ready").write_text(str(os.getpid()))
    if os.environ.get("READINESS_HOLD_TASK") == "1":
        app.send_task("native.readiness_hold")

@signals.worker_shutting_down.connect(weak=False)
def draining(**kwargs):
    lifecycle.clear_ready()

@signals.worker_shutdown.connect(weak=False)
def stopped(**kwargs):
    lifecycle.clear_current()
'''


def wait(predicate, *, timeout=25):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("native_readiness_condition_timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--real-broker", action="store_true", help="Delay an isolated loopback Valkey broker until the unavailable-stage marker.")
    args = parser.parse_args()
    if sys.platform != "linux" or os.getpid() != 1 or os.getenv("TESTLOOKUP_DISPOSABLE_WORKER_READINESS") != "1":
        parser.error("an explicitly disposable Linux PID-1 container is required")
    spec = importlib.util.spec_from_file_location("worker_process", args.helper)
    lifecycle = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(lifecycle)
    checks, workers, durations = [], [], []
    with tempfile.TemporaryDirectory(prefix="testlookup-readiness-fixture-") as temporary:
        root = Path(temporary)
        (root / "worker_readiness_fixture.py").write_text(FIXTURE, encoding="utf-8")
        metrics = root / "metrics"
        metrics.mkdir()
        (metrics / "README").write_text("preserve", encoding="utf-8")
        (metrics / "counter_999999.db").write_bytes(b"stale")
        ready = Path(lifecycle.READY_SENTINEL)
        ready.write_text("ready\n", encoding="utf-8")

        def check_probe(pid, mode="ready"):
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(args.helper), "probe", "--mode=" + mode, "--pid", str(pid)],
                                    capture_output=True, timeout=5)
            durations.append(time.monotonic() - started)
            assert not result.stdout and not result.stderr
            return result.returncode == 0

        def launch(delay, hold=False, real_broker=False):
            for name in ("importing", "consumer-ready", "metrics-preserved", "task-started", "broker-unavailable-observed"):
                (root / name).unlink(missing_ok=True)
            environment = {**os.environ, "PYTHONPATH": str(root) + os.pathsep + str(args.helper.parent),
                           "PROMETHEUS_MULTIPROC_DIR": str(metrics), "READINESS_FIXTURE_DIR": str(root),
                           "READINESS_IMPORT_DELAY": str(delay), "READINESS_HOLD_TASK": "1" if hold else "0",
                           "READINESS_BROKER_URL": "redis://127.0.0.1:6379/0" if real_broker else "memory://"}
            process = subprocess.Popen([sys.executable, str(args.helper), "celery", "-A", "worker_readiness_fixture:app", "worker",
                                        "--loglevel=WARNING", "--pool=prefork", "--concurrency=2", "--without-gossip", "--without-mingle", "--without-heartbeat"],
                                       cwd=root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            workers.append(process)
            return process

        try:
            assert not check_probe(1)
            checks.append("legacy_marker_rejected_before_launch")
            first = launch(1)
            wait(lambda: (root / "importing").exists())
            assert not ready.exists() and not check_probe(first.pid)
            assert check_probe(first.pid, "alive")
            assert not (metrics / "counter_999999.db").exists()
            checks.append("early_reset_before_celeryd_init")
            wait(lambda: check_probe(first.pid))
            original = ready.read_bytes()
            assert (root / "metrics-preserved").read_text() == "yes"
            assert len(Path(f"/proc/{first.pid}/task/{first.pid}/children").read_text().split()) >= 2
            checks.append("native_prefork_consumer_ready")
            checks.append("parent_metric_mapping_survives_celeryd_init")
            os.killpg(first.pid, signal.SIGKILL)
            first.wait(timeout=10)
            assert ready.read_bytes() == original and not check_probe(first.pid)
            checks.append("crash_retains_state_but_probe_rejects_dead_parent")

            second = launch(1, hold=True, real_broker=args.real_broker)
            assert not check_probe(second.pid)
            wait(lambda: (root / "importing").exists())
            assert not ready.exists() and not check_probe(second.pid)
            checks.append("replacement_stays_not_ready_during_preinit_import")
            if args.real_broker:
                wait(lambda: (root / "broker-unavailable-observed").exists())
                for _ in range(4):
                    assert not check_probe(second.pid) and check_probe(second.pid, "alive")
                    time.sleep(0.2)
                checks.append("actual_broker_connection_failure_keeps_replacement_not_ready")
                print(json.dumps({"stage": "broker_unavailable", "ready": False, "alive": True}), flush=True)
            wait(lambda: check_probe(second.pid))
            if args.real_broker:
                checks.append("consumer_ready_after_real_broker_recovery")
            assert ready.read_bytes() != original
            assert (metrics / "README").read_text() == "preserve"
            assert (root / "metrics-preserved").read_text() == "yes"
            checks.append("replacement_generation_ready")

            # A fresh reader sees only this generation's startup, not the old
            # parent's retained counter file or a deleted current mmap handle.
            environment = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(metrics)}
            code = "from prometheus_client import CollectorRegistry,multiprocess; r=CollectorRegistry(); multiprocess.MultiProcessCollector(r); print(r.get_sample_value('testlookup_native_parent_startups_total'))"
            value = subprocess.run([sys.executable, "-c", code], env=environment, check=True, capture_output=True, text=True, timeout=5)
            assert float(value.stdout.strip()) == 1
            checks.append("replacement_counter_resets_once")
            wait(lambda: (root / "task-started").exists())
            assert int((root / "task-started").read_text()) != second.pid
            second.send_signal(signal.SIGTERM)
            wait(lambda: not check_probe(second.pid), timeout=5)
            assert second.poll() is None and check_probe(second.pid, "alive")
            checks.append("warm_shutdown_not_ready_while_parent_drains")
            second.wait(timeout=20)
            assert second.returncode == 0 and not ready.exists() and not lifecycle._owner_path(ready).exists()
            checks.append("graceful_shutdown_clears_owned_state")
        finally:
            for process in workers:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
            while True:
                try:
                    if os.waitpid(-1, os.WNOHANG)[0] == 0:
                        break
                except ChildProcessError:
                    break
    import celery
    from importlib.metadata import version

    broker_version = None
    if args.real_broker:
        import redis

        client = redis.Redis(host="127.0.0.1", port=6379, socket_connect_timeout=1, socket_timeout=1)
        info = client.info("server")
        broker_version = info.get("valkey_version", info.get("redis_version"))
        client.close()

    print(json.dumps({"passed": True, "scope": "disposable Linux prefork worker, " + ("real loopback Valkey recovery" if args.real_broker else "memory broker"), "checked": checks,
                      "celery": celery.__version__, "prometheus_client": version("prometheus-client"),
                      "broker_version": broker_version,
                      "python": sys.version.split()[0], "build_revision": os.environ.get("BUILD_REVISION", "unknown"),
                      "helper_sha256": hashlib.sha256(args.helper.read_bytes()).hexdigest(),
                      "probe_max_seconds": round(max(durations), 4)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
