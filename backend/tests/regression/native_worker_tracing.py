"""Opt-in Linux PID-1 proof with real prefork children and standalone Beat.

Mount this file and a minimal current-source app package read-only at /proof.
Run only in a disposable network-none container. A separately owned Valkey
fixture may join this container's network namespace and bind loopback:6379.
No application database, provider, production task or live collector is used.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


APP = '''
import os
from pathlib import Path
from celery import signals
from app.worker.tracing import (TracingCelery, initialize_worker_tracing, shutdown_worker_tracing,
                               start_task_trace, finish_task_trace, propagate_traceparent)
root = Path(os.environ["TRACE_PROOF_DIR"])
app = TracingCelery("native-tracing", broker="redis://127.0.0.1:6379/0", backend="cache+memory://")
app.conf.update(task_ignore_result=True, worker_hijack_root_logger=False,
                broker_connection_retry_on_startup=True, broker_connection_max_retries=20,
                broker_transport_options={"socket_connect_timeout":1,"socket_timeout":1},
                beat_schedule={"synthetic": {"task":"native.trace", "schedule":0.5,
                              "args":["synthetic-private-payload"], "options":{"queue":"default"}}},
                beat_max_loop_interval=0.1)
signals.worker_process_init.connect(initialize_worker_tracing, weak=False)
signals.worker_process_shutdown.connect(shutdown_worker_tracing, weak=False)
signals.task_prerun.connect(start_task_trace, weak=False)
signals.task_postrun.connect(finish_task_trace, weak=False)
signals.before_task_publish.connect(propagate_traceparent, weak=False)
@signals.worker_ready.connect(weak=False)
def ready(**kwargs):
    (root / "ready").write_text("yes")
@app.task(bind=True,name="native.trace",max_retries=1)
def task(self, value):
    if self.request.retries == 0:
        raise self.retry(countdown=0)
    (root / ("done-" + self.request.id)).write_text(str(os.getpid()))
'''


def wait(predicate, timeout=35):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("native_trace_condition_timeout")


def main():
    if sys.platform != "linux" or os.getpid() != 1 or os.environ.get("TESTLOOKUP_DISPOSABLE_WORKER_TRACING") != "1":
        raise SystemExit("an explicitly disposable Linux PID-1 container is required")
    received = []
    hang = threading.Event()

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = ExportTraceServiceRequest()
            request.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(MessageToDict(request))
            if hang.is_set():
                time.sleep(6)
            try:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except OSError:
                pass

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=sink.serve_forever, daemon=True).start()
    children = []
    with tempfile.TemporaryDirectory(prefix="testlookup-tracing-") as temporary:
        root = Path(temporary)
        (root / "native_trace_app.py").write_text(APP, encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(root) + ":/proof", "TRACE_PROOF_DIR": str(root),
               "TRACE_PROOF_ENDPOINT": f"http://127.0.0.1:{sink.server_port}",
               "OTEL_BSP_SCHEDULE_DELAY": "60000", "OTEL_LOGS_ROLE": "worker",
               "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS": ""}

        def launch(arguments, role):
            logfile = (root / (role + ".log")).open("wb")
            process = subprocess.Popen(arguments, cwd=root, env={**env, "OTEL_LOGS_ROLE": role},
                                       stdout=logfile, stderr=subprocess.STDOUT)
            children.append((process, logfile))
            return process

        def stop(process, timeout=12):
            started = time.monotonic()
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=timeout)
            return round(time.monotonic() - started, 3)

        try:
            worker = launch([sys.executable,"-m","celery","-A","native_trace_app:app","worker",
                             "-Q","default","--concurrency=1","--max-tasks-per-child=1",
                             "--without-gossip","--without-mingle","--without-heartbeat","--loglevel=WARNING"], "worker")
            sys.stdout.write(json.dumps({"phase":"waiting_for_private_broker"}) + "\n")
            sys.stdout.flush()
            wait(lambda:(root / "ready").exists())
            beat = launch([sys.executable,"-c",
                           "from native_trace_app import app;app.Beat(schedule='" + str(root / "schedule") + "',pidfile='',quiet=True,loglevel='WARNING').run()"], "beat")
            wait(lambda:len(list(root.glob("done-*"))) >= 3)
            beat_exit = stop(beat)
            worker_exit = stop(worker)

            spans = [(resource["resource"], span)
                     for request in received for resource in request.get("resourceSpans", [])
                     for scope in resource.get("scopeSpans", []) for span in scope.get("spans", [])]
            producers = {span["spanId"]: span for _,span in spans if span["name"] == "celery.publish"}
            consumers = [span for _,span in spans if span["name"] == "celery.consume"]
            assert len(consumers) >= 6, "retry and successful consumers must drain before child exit"
            assert len({path.read_text() for path in root.glob("done-*")}) >= 3
            assert all(item["parentSpanId"] in producers for item in consumers)
            assert all(producers[item["parentSpanId"]]["traceId"] == item["traceId"] for item in consumers)
            roles = {next(attr["value"]["stringValue"] for attr in resource["attributes"]
                          if attr["key"] == "testlookup.service.role") for resource,_ in spans}
            assert {"worker","beat"} <= roles
            serialized = json.dumps(received)
            assert "synthetic-private-payload" not in serialized
            assert "native.trace" not in serialized
            assert not any(span["name"] in {"LPUSH", "PUBLISH"} for _,span in spans)

            before_fallback = len(received)
            fallback = launch([sys.executable,"-c",
                               "from app.worker.tracing import initialize_beat_tracing,_tracer;"
                               "initialize_beat_tracing();s=_tracer().start_span('celery.publish');s.end()"], "beat")
            fallback.wait(timeout=4)
            assert fallback.returncode == 0 and len(received) > before_fallback
            assert "Exception ignored in atexit" not in (root / "beat.log").read_text()

            # A non-responsive sink must not extend the 1.5s shutdown budget.
            hang.set()
            stalled = launch([sys.executable,"-c",
                              "from app.worker.tracing import initialize_beat_tracing,shutdown_worker_tracing,_tracer;"
                              "initialize_beat_tracing();s=_tracer().start_span('celery.publish');s.end();"
                              "shutdown_worker_tracing()"], "beat")
            started = time.monotonic()
            stalled.wait(timeout=4)
            stalled_exit = round(time.monotonic() - started, 3)
            assert stalled.returncode == 0 and stalled_exit < 3.5
            sys.stdout.write(json.dumps({"phase":"passed", "checks":{
                "native_prefork_recycle":True,"retry_producer_consumer_parentage":True,
                "standalone_beat_export":True,"task_payload_absent":True,
                "broker_auto_spans_suppressed":True,"stalled_sink_bounded_exit":True,
                "normal_interpreter_exit_drains":True},
                "consumer_count":len(consumers), "producer_count":len(producers),
                "completed_child_pids":len({path.read_text() for path in root.glob('done-*')}),
                "beat_exit_seconds":beat_exit,"worker_exit_seconds":worker_exit,
                "stalled_sink_exit_seconds":stalled_exit,
                "versions":{package:importlib.metadata.version(package) for package in
                            ("celery","opentelemetry-sdk","opentelemetry-exporter-otlp-proto-http")}}) + "\n")
        except Exception:
            for logfile in root.glob("*.log"):
                sys.stderr.write(logfile.name + ":\n" + logfile.read_text(errors="replace")[-6000:] + "\n")
            raise
        finally:
            for process,logfile in children:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                logfile.close()
            sink.shutdown()


if __name__ == "__main__":
    main()
