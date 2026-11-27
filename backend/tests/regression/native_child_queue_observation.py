"""Opt-in native queue observation proof in a disposable Linux PID namespace.

Supply /proof/source/backend/app with only the current source files named by
SOURCE_FILES. This fixture extracts the actual queue refresh and worker metric
functions, and runs them with real prefork Celery and an isolated loopback
Valkey. It never imports production tasks or connects to application stores.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


SOURCE_FILES = (
    "core/metrics.py", "core/metric_dimensions.py", "db/redis_client.py",
    "services/celery_queue_metrics.py", "worker/queue_inventory.py",
    "worker/ingestion_routing.py", "worker/queue_wait.py", "worker/celery_app.py", "bootstrap.py",
)
HOOKS = (
    "_build_task_queues", "_record_task_start", "_record_task_runtime", "_metric_task_names",
    "_metric_task_name", "_record_task_outcome", "_start_metrics_server",
    "_reap_dead_child_metric_gauges", "_start_metrics_file_guard", "_clear_dead_child_metrics",
)
PREFIX = "testlookup-native:"


def extract(source: Path, names: tuple[str, ...]) -> str:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    return ast.unparse(ast.Module(body=nodes, type_ignores=[]))


def prepare(root: Path, source: Path) -> dict[str, str]:
    hashes = {}
    for package in ("app", "app/core", "app/db", "app/services", "app/worker"):
        (root / package).mkdir(exist_ok=True)
        (root / package / "__init__.py").write_text("", encoding="utf-8")
    for name in SOURCE_FILES:
        content = (source / name).read_bytes()
        hashes[name] = hashlib.sha256(content).hexdigest()
        if name not in {"worker/celery_app.py", "bootstrap.py"}:
            (root / "app" / name).write_bytes(content)
    (root / "app/core/config.py").write_text('''
from types import SimpleNamespace
REDIS_SOCKET_TIMEOUT_SECONDS=1
settings=SimpleNamespace(REDIS_URL="redis://127.0.0.1:6379/0",CELERY_BROKER_URL="redis://127.0.0.1:6379/0",
                         LIVE_INGEST_SHARD_COUNT=1)
''', encoding="utf-8")
    (root / "app/bootstrap.py").write_text(extract(source / "bootstrap.py", ("_refresh_celery_queue_depths",)), encoding="utf-8")
    worker = '''
from pathlib import Path
from functools import lru_cache
import os,re,json
from celery import Celery,signals
from kombu import Exchange,Queue
from app.worker.queue_inventory import all_task_queue_names
from app.worker.queue_wait import stamp_publish_time,observe_queue_wait,finish_queue_wait
task_prerun=signals.task_prerun
task_postrun=signals.task_postrun
worker_ready=signals.worker_ready
worker_process_shutdown=signals.worker_process_shutdown
root=Path(os.environ['QUEUE_PROOF_DIR'])
celery_app=Celery('native-queue',broker='redis://127.0.0.1:6379/0',backend='cache+memory://')
celery_app.conf.update(task_ignore_result=True,worker_hijack_root_logger=False,worker_prefetch_multiplier=1,
                      task_acks_late=True,broker_connection_retry_on_startup=True,
                      broker_transport_options={'global_keyprefix':'testlookup-native:','sep':'|',
                                                'priority_steps':[0,2,5,9],'socket_timeout':1,'socket_connect_timeout':1})
_default_exchange=Exchange('default',type='direct')
_TASK_STARTED_AT={}
'''
    worker += extract(source / "worker/celery_app.py", HOOKS)
    worker += '''
celery_app.conf.task_queues=_build_task_queues()
signals.before_task_publish.connect(stamp_publish_time,weak=False)
signals.task_prerun.connect(observe_queue_wait,weak=False)
signals.task_postrun.connect(finish_queue_wait,weak=False)
@signals.worker_ready.connect(weak=False)
def ready(**kwargs):
    (root/'ready').write_text('yes')
@celery_app.task(bind=True,name='native.queue_probe')
def probe(self,correlation):
    path=root/('done-'+self.request.id)
    with path.open('a') as output:
        output.write(json.dumps({'id':self.request.id,'correlation':correlation,
                                 'queue':self.request.delivery_info['routing_key'],'pid':os.getpid()})+'\\n')
'''
    (root / "app/worker/celery_app.py").write_text(worker, encoding="utf-8")
    return hashes


def wait(predicate, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("native_queue_condition_timeout")


def main():
    if sys.platform != "linux" or os.getpid() != 1 or os.getenv("TESTLOOKUP_DISPOSABLE_CHILD_QUEUE") != "1":
        raise SystemExit("an explicitly disposable Linux PID-1 container is required")
    # The observation process has one local registry. Only the worker and its
    # children use their separate multiprocess directory.
    os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
    os.environ.pop("prometheus_multiproc_dir", None)
    source = Path("/proof/source/backend/app")
    sys.stdout.write(json.dumps({"phase":"waiting_for_private_broker"}) + "\n")
    sys.stdout.flush()

    def broker_ready():
        try:
            with socket.create_connection(("127.0.0.1",6379), timeout=0.2):
                return True
        except OSError:
            return False

    wait(broker_ready)
    with tempfile.TemporaryDirectory(prefix="testlookup-child-queue-") as temporary, asyncio.Runner() as runner:
        root = Path(temporary)
        hashes = prepare(root, source)
        sys.path.insert(0, str(root))
        os.environ["QUEUE_PROOF_DIR"] = str(root)
        from app import bootstrap
        from app.core import metrics
        from app.db import redis_client
        from app.services.celery_queue_metrics import read_celery_queue_depths
        from app.worker.celery_app import celery_app
        from app.worker.queue_inventory import all_task_queue_names, redis_queue_keys
        from app.worker.queue_wait import stamp_publish_time
        from celery import signals
        from prometheus_client.parser import text_string_to_metric_families
        import redis
        import redis.asyncio as async_redis

        admin = redis.Redis.from_url("redis://127.0.0.1:6379/0", socket_timeout=1, socket_connect_timeout=1)
        multiproc = root / "worker-metrics"
        multiproc.mkdir()
        environment = {**os.environ, "PYTHONPATH":str(root),"QUEUE_PROOF_DIR":str(root),
                       "PROMETHEUS_MULTIPROC_DIR":str(multiproc),"WORKER_METRICS_PORT":"9100"}
        logfile = (root / "worker.log").open("wb")
        worker = subprocess.Popen([sys.executable,"-m","celery","-A","app.worker.celery_app:celery_app","worker",
                                   "--hostname=native-child@fixture","-Q","agent_children,default","--concurrency=1",
                                   "--without-gossip","--without-mingle","--without-heartbeat","--loglevel=WARNING"],
                                  cwd=root,env=environment,stdout=logfile,stderr=subprocess.STDOUT)

        def worker_samples():
            with urlopen("http://127.0.0.1:9100/metrics", timeout=2) as response:
                assert response.status == 200
                return [sample for family in text_string_to_metric_families(response.read().decode()) for sample in family.samples]

        def value(name, **labels):
            return sum(sample.value for sample in worker_samples() if sample.name == name
                       and all(sample.labels.get(key) == selected for key,selected in labels.items()))

        observations = []

        def observe(phase, expected_success=True):
            assert runner.run(bootstrap._refresh_celery_queue_depths()) is expected_success
            snapshot = {queue:{"depth":metrics.celery_queue_length.labels(queue_name=queue)._value.get(),
                               "observed_at":metrics.celery_queue_depth_observed_timestamp_seconds.labels(queue_name=queue)._value.get()}
                        for queue in all_task_queue_names()}
            exported = worker_samples()
            item = {"phase":phase,"at":time.time(),"queues":snapshot,"broker_ping":bool(admin.ping()),
                    "queue_collection_success":expected_success,"worker_exporter_reachable":True,
                    "worker_exporter_http_status":200,"worker_metric_series":len(exported),
                    "child_finished_count":value("celery_task_runtime_seconds_count",queue_name="agent_children"),
                    "child_wait_count":value("celery_task_queue_wait_seconds_count",queue_name="agent_children")}
            observations.append(item)
            return item

        try:
            wait(lambda:(root / "ready").exists())
            assert len(all_task_queue_names()) == 6
            observe("healthy_empty")
            cancelled = celery_app.control.cancel_consumer("agent_children",destination=["native-child@fixture"],reply=True,timeout=3)
            assert cancelled and "ok" in cancelled[0]["native-child@fixture"]
            active = celery_app.control.inspect(destination=["native-child@fixture"],timeout=3).active_queues()
            assert all(queue["name"] != "agent_children" for queue in active["native-child@fixture"])
            known = []
            for priority in (0,5):
                result = celery_app.send_task("native.queue_probe",args=["synthetic-child"],queue="agent_children",priority=priority)
                known.append(result.id)
            # An old valid envelope has no new stamp. LLEN must remain correct.
            signals.before_task_publish.disconnect(stamp_publish_time)
            legacy = celery_app.send_task("native.queue_probe",args=["synthetic-legacy"],queue="agent_children",priority=9)
            signals.before_task_publish.connect(stamp_publish_time,weak=False)
            known.append(legacy.id)
            # An outstanding BRPOP can return just as cancel_consumer takes
            # effect. Wait for Kombu to restore that undeliverable envelope
            # before taking the stable stopped-consumer observations.
            wait(lambda:runner.run(read_celery_queue_depths())["agent_children"] == 3)
            for _ in range(3):
                item = observe("consumer_stopped")
                assert item["queues"]["agent_children"]["depth"] == 3
                assert item["child_finished_count"] == item["child_wait_count"] == 0
                time.sleep(0.25)
            lengths = {key:admin.llen(key) for key in redis_queue_keys("agent_children")}
            assert sorted(lengths.values()) == [0,1,1,1]

            # A fixture-only reader can LLEN all declared keys except one bin.
            # No message value, script or administrative command is authorized.
            denied = redis_queue_keys("agent_children")[-1]
            allowed = [key for queue in all_task_queue_names() for key in redis_queue_keys(queue) if key != denied]
            admin.execute_command("ACL","SETUSER","native-sampler","reset","on","nopass","-@all",
                                  "+llen","+client|setinfo","+select",*("~"+key for key in allowed))
            restricted = async_redis.Redis(host="127.0.0.1",port=6379,username="native-sampler",protocol=2,
                                           socket_timeout=1,socket_connect_timeout=1,decode_responses=True)
            original = redis_client.get_broker_redis
            redis_client.get_broker_redis = lambda: restricted
            try:
                partial = runner.run(read_celery_queue_depths())
                assert partial["agent_children"] is None and all(partial[queue] == 0 for queue in all_task_queue_names() if queue != "agent_children")
                observe("acl_denied_child_bin", expected_success=False)
                assert metrics.celery_queue_length.labels(queue_name="agent_children")._value.get() == 3
                assert metrics.celery_queue_depth_observed_timestamp_seconds.labels(queue_name="agent_children")._value.get() == 0
                assert metrics.celery_queue_depth_observed_timestamp_seconds.labels(queue_name="default")._value.get() > 0
            finally:
                redis_client.get_broker_redis = original
                runner.run(restricted.aclose())
            observe("read_recovered_consumer_still_stopped")
            resumed_at = time.time()
            resumed = celery_app.control.add_consumer("agent_children",destination=["native-child@fixture"],reply=True,timeout=3)
            assert resumed and "ok" in resumed[0]["native-child@fixture"]
            wait(lambda:all((root / ("done-"+identifier)).exists() for identifier in known))
            wait(lambda:value("celery_task_runtime_seconds_count",queue_name="agent_children") == 3)
            recovered = observe("recovered_empty")
            assert recovered["queues"]["agent_children"]["depth"] == 0 and recovered["child_wait_count"] == 2
            missing = value("celery_task_queue_wait_unavailable_total",queue_name="agent_children",reason="missing_stamp")
            assert missing == 1
            receipts = [json.loads((root / ("done-"+identifier)).read_text()) for identifier in known]
            assert {entry["id"] for entry in receipts} == set(known)
            assert all(entry["queue"] == "agent_children" for entry in receipts)
            assert all(len((root / ("done-"+identifier)).read_text().splitlines()) == 1 for identifier in known)
            sys.stdout.write(json.dumps({"phase":"passed","source_sha256":hashes,"extracted_worker_hooks":HOOKS,
                "scope":"current LLEN reader/refresh and worker metric hooks, synthetic settings/tasks, real isolated prefork and Valkey",
                "queue_inventory":list(all_task_queue_names()),"physical_child_depths_while_stopped":lengths,
                "observations":observations,"child_consumer_cancelled":True,"worker_exporter_kept_reachable":True,
                "read_only_acl_bad_bin_unknown":True,"known_task_ids":known,"unique_completion_ids":[entry["id"] for entry in receipts],
                "completed":len(receipts),"child_wait_observations":recovered["child_wait_count"],"legacy_missing_stamp":missing,
                "recovery_seconds":round(time.time()-resumed_at,3),
                "versions":{package:importlib.metadata.version(package) for package in ("celery","kombu","redis","prometheus-client")}})+"\n")
        except Exception:
            sys.stderr.write(json.dumps({"observations":observations})+"\n")
            sys.stderr.write((root / "worker.log").read_text(errors="replace")[-8000:]+"\n")
            raise
        finally:
            if worker.poll() is None:
                worker.send_signal(signal.SIGTERM)
                try:
                    worker.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)
            logfile.close()
            runner.run(redis_client.close_redis())
            admin.close()


if __name__ == "__main__":
    main()
