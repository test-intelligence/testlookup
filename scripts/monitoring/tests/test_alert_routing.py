"""Opt-in routing contracts and real Prometheus -> Alertmanager -> TLS sink."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("configure_alerting", ROOT / "scripts/monitoring/configure_alerting.py")
configure = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(configure)


def _private_files(tmp_path):
    files = {}
    for name in configure.SECRET_ENVIRONMENTS:
        path = tmp_path / name
        path.write_text("https://sink.invalid/" + name if name.endswith("_url") else uuid.uuid4().hex)
        files[name] = path
    return files


@pytest.mark.parametrize("bad", ["", "http://sink.invalid/a", "https://user:secret@sink.invalid/a", "https://sink.invalid/#fragment", "https://sink.invalid:99999/a", "https://sink.invalid/a\nb"])
def test_unsafe_receiver_input_is_rejected_without_echoing_it(tmp_path, bad):
    files = _private_files(tmp_path)
    files["incident_url"].write_text(bad)
    with pytest.raises(configure.ConfigurationError) as error:
        configure.validate_receiver_files(files)
    assert "secret" not in str(error.value)
    assert "sink.invalid" not in str(error.value)


def test_credentials_must_be_nonempty_and_single_line(tmp_path):
    files = _private_files(tmp_path)
    configure.validate_receiver_files(files)
    files["watchdog_token"].write_text("header\r\ninjection")
    with pytest.raises(configure.ConfigurationError):
        configure.validate_receiver_files(files)


def test_generated_configuration_preserves_current_base_and_separate_auth_include():
    base = yaml.safe_load((ROOT / "infra/monitoring/prometheus.yml").read_text())
    generated = configure.render_prometheus(base, installation="fixture", environment="test", owner="platform")
    assert generated["scrape_config_files"] == base["scrape_config_files"]
    assert generated["rule_files"][:-1] == base["rule_files"]
    assert generated["rule_files"][-1] == "alertmanager-contract.yml"
    assert generated["scrape_configs"][:-1] == base["scrape_configs"]
    assert "alerting" not in base
    assert generated["global"]["external_labels"]["installation"] == "fixture"
    assert generated["alerting"]["alertmanagers"][0]["static_configs"] == [{"targets": ["alertmanager:9093"]}]
    assert generated["alerting"]["alert_relabel_configs"][0]["regex"] == ""
    with pytest.raises(configure.ConfigurationError):
        configure.render_prometheus(base, installation="", environment="test", owner="platform")


def test_overlay_pin_inventory_private_api_and_secret_only_receivers():
    overlay = yaml.safe_load((ROOT / "docker-compose.alerting.yml").read_text())
    lock = json.loads((ROOT / "infra/monitoring/alertmanager/image-lock.json").read_text())
    for service in ("alertmanager", "alertmanager-config-check"):
        assert overlay["services"][service]["image"] == lock["image"]
        assert "ports" not in overlay["services"][service]
    assert "@sha256:" in lock["image"]
    config = yaml.safe_load((ROOT / "infra/monitoring/alertmanager/alertmanager.yml").read_text())
    assert {receiver["name"] for receiver in config["receivers"]} == {"incidents", "watchdog"}
    for receiver in config["receivers"]:
        webhook = receiver["webhook_configs"][0]
        assert "url" not in webhook
        assert webhook["url_file"].startswith("/run/secrets/")
        assert webhook["send_resolved"] is True
        http = webhook["http_config"]
        assert http["authorization"]["credentials_file"].startswith("/run/secrets/")
        assert http["follow_redirects"] is False
        assert http["tls_config"]["min_version"] == "TLS12"
    assert lock["bundle"] == "excluded"


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _eventually(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Local synthetic routing condition did not become true before its deadline")


class _IndependentWatchdog:
    """Disposable receiver clock, outside both monitored native processes."""

    def __init__(self, allowance=2.5, *, start_thread=True):
        self.allowance = allowance
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.last_progress = None
        self.last_arrival = None
        self.source_timestamp = None
        self.started = time.monotonic()
        self.progress_missing = False
        self.arrival_missing = False
        self.transitions = []
        self.accepted = 0
        self.repeated = 0
        self.advances = 0
        self.firing_ends_at = set()
        self.thread = threading.Thread(target=self._monitor, daemon=True) if start_thread else None
        if self.thread is not None:
            self.thread.start()

    def record(self, payload, *, authenticated, received_at=None, now_utc=None):
        if not authenticated or payload.get("receiver") != "watchdog" or payload.get("status") != "firing":
            return
        for alert in payload.get("alerts", []):
            labels = alert.get("labels", {})
            if (alert.get("status") != "firing" or labels.get("alertname") != "TestLookupWatchdog"
                    or labels.get("installation") != "fixture" or labels.get("environment") != "test"):
                continue
            now = time.monotonic() if received_at is None else received_at
            utc = time.time() if now_utc is None else now_utc
            raw = alert.get("annotations", {}).get("heartbeat_evaluated_at_unix_seconds")
            valid = isinstance(raw, str) and re.fullmatch(r"[0-9]{1,12}", raw)
            source = int(raw) if valid else None
            # printf rounds the rule value to seconds. Allow that rounding,
            # while refusing old/future data as fresh receiver-side progress.
            fresh = source is not None and 0 < source <= utc + 1 and utc - source <= self.allowance
            with self.lock:
                self.accepted += 1
                self.last_arrival = now
                self.firing_ends_at.add(alert.get("endsAt"))
                if fresh and (self.source_timestamp is None or source > self.source_timestamp):
                    self.source_timestamp = source
                    self.last_progress = now
                    self.advances += 1
                else:
                    self.repeated += 1

    def _monitor(self):
        while not self.stop.wait(0.02):
            self.evaluate()

    def evaluate(self, *, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            for mode, stamp in (("progress", self.last_progress), ("arrival", self.last_arrival)):
                if stamp is None:
                    stamp = self.started
                missing = now - stamp > self.allowance
                attribute = mode + "_missing"
                if getattr(self, attribute) != missing:
                    setattr(self, attribute, missing)
                    self.transitions.append({"mode": mode, "missing": missing, "at": now})

    def snapshot(self):
        with self.lock:
            return {"source_timestamp": self.source_timestamp, "last_progress": self.last_progress,
                    "last_arrival": self.last_arrival, "progress_missing": self.progress_missing,
                    "arrival_missing": self.arrival_missing, "accepted": self.accepted, "repeated": self.repeated,
                    "advances": self.advances,
                    "firing_ends_at": set(self.firing_ends_at), "transitions": list(self.transitions)}

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)


@contextmanager
def _routing_stack(tmp_path, *, independent_watchdog=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    token = uuid.uuid4().hex
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TestLookup disposable sink")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "sink-ca.pem", tmp_path / "sink-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    state = {"flags": {}, "events": [], "watchdog_receipts": [], "fail": False}
    lock = threading.Lock()
    monitor = _IndependentWatchdog() if independent_watchdog else None

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            with lock:
                body = "# TYPE synthetic_alert_active gauge\n" + "".join(
                    f'synthetic_alert_active{{name="{name}",replica="{replica}"}} {active}\n'
                    for name, active in state["flags"].items()
                    for replica in (("a", "b") if name == "Delivery" else ("a",))
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            valid = self.headers.get("Authorization") == "Bearer " + token
            payload = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 1048576)))
            with lock:
                status = 503 if state["fail"] and self.path == "/incident" else (200 if valid else 401)
                state["events"].append((self.path, status, payload))
                if self.path == "/watchdog" and valid:
                    for alert in payload.get("alerts", []):
                        if alert.get("labels", {}).get("alertname") == "TestLookupWatchdog":
                            state["watchdog_receipts"].append({"at": time.monotonic(), "status": alert.get("status"),
                                "source": alert.get("annotations", {}).get("heartbeat_evaluated_at_unix_seconds"),
                                "ends_at": alert.get("endsAt")})
            if monitor is not None and self.path == "/watchdog":
                monitor.record(payload, authenticated=valid)
            self.send_response(status)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    am_port, prom_port = _port(), _port()
    am = yaml.safe_load((ROOT / "infra/monitoring/alertmanager/alertmanager.yml").read_text())
    am["route"].update(group_wait="300ms", group_interval="300ms", repeat_interval="30s")
    for route in am["route"]["routes"]:
        route.update(group_wait="300ms", group_interval="300ms", repeat_interval="30s")
        if route["receiver"] == "watchdog":
            route["repeat_interval"] = "1s"
            if independent_watchdog:
                route.update(group_wait="0s", group_interval="100ms", repeat_interval="300ms")
    for receiver in am["receivers"]:
        webhook = receiver["webhook_configs"][0]
        route_path = "incident" if receiver["name"] == "incidents" else "watchdog"
        url_file, token_file = tmp_path / (route_path + "-url"), tmp_path / (route_path + "-token")
        url_file.write_text(f"https://127.0.0.1:{server.server_port}/{route_path}")
        token_file.write_text(token)
        webhook["url_file"] = str(url_file)
        webhook["http_config"]["authorization"]["credentials_file"] = str(token_file)
        webhook["http_config"]["tls_config"]["ca_file"] = str(cert_path)
    am_config = tmp_path / "alertmanager.yml"
    am_config.write_text(yaml.safe_dump(am))
    rules = []
    for name, severity, family in (
        ("Delivery", "warning", ""), ("Silenced", "warning", ""),
        ("Critical", "critical", "synthetic-family"), ("Inhibited", "warning", "synthetic-family"),
        ("Failure", "critical", ""), ("Watchdog", "none", ""),
    ):
        alertname = "TestLookupWatchdog" if name == "Watchdog" else "Synthetic" + name
        labels = {"severity": severity}
        if family:
            labels["alert_family"] = family
        rules.append({"alert": alertname, "expr": f'synthetic_alert_active{{name="{name}"}} == 1',
                      "labels": labels,
                      "annotations": {"summary": "Disposable delivery proof", "runbook_url": "https://docs.invalid/runbook", "dashboard_url": "https://grafana.invalid/dashboard"}})
    if independent_watchdog:
        production = yaml.safe_load((ROOT / "infra/monitoring/prometheus-rules/testlookup-alerts.yml").read_text())
        canonical = [rule for group in production["groups"] for rule in group["rules"]
                     if rule.get("alert") == "TestLookupWatchdog"]
        assert len(canonical) == 1
        rules = [copy.deepcopy(canonical[0])]
    rule_file = tmp_path / "rules.yml"
    rule_file.write_text(yaml.safe_dump({"groups": [{"name": "synthetic", "rules": rules}]}))
    base = {"global": {"scrape_interval": "500ms", "scrape_timeout": "400ms", "evaluation_interval": "500ms"}, "rule_files": [str(rule_file)], "scrape_configs": [
        {"job_name": "synthetic", "scheme": "https", "tls_config": {"ca_file": str(cert_path)}, "static_configs": [{"targets": [f"127.0.0.1:{server.server_port}"]}]}]}
    prom = configure.render_prometheus(base, installation="fixture", environment="test", owner="platform")
    prom["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] = [f"127.0.0.1:{am_port}"]
    prom["scrape_configs"][-1]["static_configs"][0]["targets"] = [f"127.0.0.1:{am_port}"]
    prom["scrape_configs"][-1]["scrape_interval"] = "500ms"
    prom_file = tmp_path / "prometheus.yml"
    shutil.copyfile(ROOT / "infra/monitoring/alertmanager/telemetry-contract.yml", tmp_path / "alertmanager-contract.yml")
    prom_file.write_text(yaml.safe_dump(prom))
    opener = build_opener(ProxyHandler({}))

    def api(path, body=None, method=None):
        request = Request(f"http://127.0.0.1:{am_port}" + path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"}, method=method)
        with opener.open(request, timeout=1) as response:
            raw = response.read()
            return json.loads(raw) if raw and path.startswith("/api/") else raw.decode()

    def ready():
        try:
            return api("/-/ready")
        except (OSError, HTTPError):
            return False

    def events(alertname, status="firing", code=200):
        with lock:
            return [body for _, received, body in state["events"] if received == code and body["status"] == status
                    and any(alert["labels"]["alertname"] == alertname for alert in body["alerts"])]

    processes = []
    running = {}
    binaries = {
        "alertmanager": (os.environ["ALERTMANAGER_TEST_BINARY"], [f"--config.file={am_config}", f"--web.listen-address=127.0.0.1:{am_port}", f"--storage.path={tmp_path / 'am-data'}", "--cluster.listen-address=", "--log.level=warn"]),
        "prometheus": (os.environ["PROMETHEUS_TEST_BINARY"], [f"--config.file={prom_file}", f"--web.listen-address=127.0.0.1:{prom_port}", f"--storage.tsdb.path={tmp_path / 'prom-data'}", "--rules.alert.resend-delay=500ms", "--log.level=warn"]),
    }

    def launch(name):
        binary, arguments = binaries[name]
        log = (tmp_path / (name + "-" + str(len(processes)) + ".log")).open("wb")
        process = subprocess.Popen([binary, *arguments], stdout=log, stderr=subprocess.STDOUT)
        processes.append((process, log))
        running[name] = process
        return process

    def stop(name):
        process = running[name]
        if process.poll() is None:
            process.kill()  # A real fault, without a graceful final notification.
            process.wait(timeout=5)
        return process.returncode

    def prometheus_ready():
        try:
            with opener.open(f"http://127.0.0.1:{prom_port}/-/ready", timeout=1) as response:
                return response.status == 200
        except (OSError, HTTPError):
            return False

    state["control"] = SimpleNamespace(launch=launch, stop=stop, running=running,
        prometheus_ready=prometheus_ready, alertmanager_ready=ready, monitor=monitor)
    try:
        if os.environ.get("AMTOOL_TEST_BINARY"):
            subprocess.run([os.environ["AMTOOL_TEST_BINARY"], "check-config", str(am_config)], check=True, capture_output=True, timeout=10)
        launch("alertmanager")
        launch("prometheus")
        _eventually(ready)
        yield state, lock, api, events
    finally:
        for process, log in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
        if monitor is not None:
            monitor.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


@pytest.mark.skipif(not (os.environ.get("ALERTMANAGER_TEST_BINARY") and os.environ.get("PROMETHEUS_TEST_BINARY")), reason="requires pinned local Alertmanager and Prometheus binaries")
def test_actual_rule_delivery_recovery_dedup_silence_inhibition_and_receiver_failure(tmp_path):
    with _routing_stack(tmp_path) as (state, lock, api, events):
        with lock:
            state["flags"]["Delivery"] = 1
            state["flags"]["Watchdog"] = 1
        delivered = _eventually(lambda: events("SyntheticDelivery"))[0]
        assert len(delivered["alerts"]) == 2  # Distinct replicas share one notification group.
        alert = delivered["alerts"][0]
        assert alert["labels"]["installation"] == "fixture"
        assert alert["labels"]["environment"] == "test"
        assert alert["labels"]["severity"] == "warning"
        assert alert["labels"]["owner"] == "platform"
        assert alert["annotations"]["runbook_url"] == "https://docs.invalid/runbook"
        assert alert["annotations"]["dashboard_url"] == "https://grafana.invalid/dashboard"
        _eventually(lambda: len(events("TestLookupWatchdog")) >= 2)
        with lock:
            assert all(path == "/watchdog" for path, _, body in state["events"] if any(a["labels"]["alertname"] == "TestLookupWatchdog" for a in body["alerts"]))
        assert len(events("SyntheticDelivery")) == 1  # Prometheus resends did not duplicate delivery.
        with lock:
            state["flags"]["Delivery"] = 0
        _eventually(lambda: events("SyntheticDelivery", "resolved"))

        now = datetime.now(timezone.utc)
        silence = api("/api/v2/silences", {"matchers": [{"name": "alertname", "value": "SyntheticSilenced", "isRegex": False, "isEqual": True}],
            "startsAt": now.isoformat(), "endsAt": (now + timedelta(minutes=2)).isoformat(), "createdBy": "disposable-test", "comment": "synthetic maintenance proof"})
        with lock:
            state["flags"].update(Silenced=1, Critical=1, Inhibited=1)
        _eventually(lambda: events("SyntheticCritical"))
        _eventually(lambda: any(a["labels"]["alertname"] == "SyntheticSilenced" and a["status"]["silencedBy"] for a in api("/api/v2/alerts")))
        _eventually(lambda: any(a["labels"]["alertname"] == "SyntheticInhibited" and a["status"]["inhibitedBy"] for a in api("/api/v2/alerts")))
        assert not events("SyntheticSilenced")
        assert not events("SyntheticInhibited")
        api("/api/v2/silence/" + silence["silenceID"], method="DELETE")
        _eventually(lambda: events("SyntheticSilenced"))

        with lock:
            state["fail"] = True
            state["flags"]["Failure"] = 1
        _eventually(lambda: events("SyntheticFailure", code=503))
        _eventually(lambda: any(float(line.rsplit(" ", 1)[1]) > 0 for line in api("/metrics").splitlines() if line.startswith("alertmanager_notifications_failed_total{")))
        with lock:
            state["fail"] = False
        _eventually(lambda: events("SyntheticFailure"), timeout=25)
        with lock:
            state["flags"]["Failure"] = 0
        _eventually(lambda: events("SyntheticFailure", "resolved"))


def _heartbeat(source="1000", *, status="firing", installation="fixture"):
    return {"receiver": "watchdog", "status": status, "alerts": [{
        "status": status, "labels": {"alertname": "TestLookupWatchdog", "installation": installation,
                                      "environment": "test"},
        "annotations": {"heartbeat_evaluated_at_unix_seconds": source},
        "endsAt": "0001-01-01T00:00:00Z",
    }]}


def test_independent_watchdog_requires_authenticated_scoped_fresh_advancing_source_time():
    receiver = _IndependentWatchdog(start_thread=False)
    receiver.started = 10
    try:
        receiver.record(_heartbeat(), authenticated=False, received_at=10, now_utc=1000)
        receiver.record(_heartbeat(installation="different"), authenticated=True, received_at=10, now_utc=1000)
        receiver.record(_heartbeat(status="resolved"), authenticated=True, received_at=10, now_utc=1000)
        assert receiver.snapshot()["last_progress"] is None
        receiver.evaluate(now=13)
        assert receiver.snapshot()["progress_missing"]

        receiver.record(_heartbeat(), authenticated=True, received_at=14, now_utc=1000)
        receiver.evaluate(now=14)
        assert not receiver.snapshot()["progress_missing"]
        # Authenticated repeats remain arrivals. They cannot renew source progress.
        receiver.record(_heartbeat(), authenticated=True, received_at=16, now_utc=1002)
        receiver.evaluate(now=17)
        snapshot = receiver.snapshot()
        assert snapshot["progress_missing"] and not snapshot["arrival_missing"]
        assert snapshot["advances"] == 1

        for raw in ("malformed", "-1", "1000000000000", "999", "1010"):
            receiver.record(_heartbeat(raw), authenticated=True, received_at=17, now_utc=1003)
        receiver.record(_heartbeat("1001"), authenticated=True, received_at=17, now_utc=1005)
        assert receiver.snapshot()["advances"] == 1
        receiver.record(_heartbeat("1003"), authenticated=True, received_at=18, now_utc=1003)
        receiver.evaluate(now=18)
        assert receiver.snapshot()["advances"] == 2
        assert not receiver.snapshot()["progress_missing"]
    finally:
        receiver.close()


@pytest.mark.skipif(not (os.environ.get("ALERTMANAGER_TEST_BINARY") and os.environ.get("PROMETHEUS_TEST_BINARY")), reason="requires pinned local Alertmanager and Prometheus binaries")
def test_independent_watchdog_detects_each_native_process_outage_and_recovers(tmp_path, record_testsuite_property):
    evidence = {}
    with _routing_stack(tmp_path, independent_watchdog=True) as (state, lock, _, _):
        control = state["control"]
        receiver = control.monitor
        _eventually(control.prometheus_ready)
        _eventually(lambda: receiver.snapshot()["advances"] >= 3)
        assert not receiver.snapshot()["progress_missing"]
        # Firing webhook endsAt is zero in the pinned runtime. The source annotation
        # supplies the evidence that a rule evaluation, rather than delivery, advanced.
        assert receiver.snapshot()["firing_ends_at"] == {"0001-01-01T00:00:00Z"}
        assert receiver.thread.is_alive()

        prometheus_fault = time.monotonic()
        control.stop("prometheus")
        assert control.running["prometheus"].poll() is not None
        assert control.alertmanager_ready()

        def stale_repeats():
            with lock:
                sources = [receipt["source"] for receipt in state["watchdog_receipts"]
                           if receipt["at"] >= prometheus_fault and receipt["status"] == "firing"]
            return len(sources) >= 2 and len(set(sources)) < len(sources)

        _eventually(stale_repeats)
        _eventually(lambda: receiver.snapshot()["progress_missing"], timeout=6)
        assert control.alertmanager_ready()
        _eventually(lambda: receiver.snapshot()["arrival_missing"], timeout=12)
        snapshot = receiver.snapshot()
        with lock:
            stale_sources = [receipt["source"] for receipt in state["watchdog_receipts"]
                             if receipt["at"] >= prometheus_fault and receipt["status"] == "firing"]
        assert stale_sources.count(str(snapshot["source_timestamp"])) >= 2

        def missing_at(mode, after):
            return next(transition["at"] for transition in receiver.snapshot()["transitions"]
                        if transition["mode"] == mode and transition["missing"] and transition["at"] >= after)

        source_detected = missing_at("progress", prometheus_fault)
        arrival_detected = missing_at("arrival", prometheus_fault)
        assert source_detected - prometheus_fault <= receiver.allowance + 1
        assert arrival_detected > source_detected
        evidence["prometheus_outage"] = {
            "source_detection_seconds": round(source_detected - prometheus_fault, 3),
            "arrival_only_detection_seconds": round(arrival_detected - prometheus_fault, 3),
            "stale_repeated_firing_seen": True,
            "repeated_last_source_count": stale_sources.count(str(snapshot["source_timestamp"])),
        }
        old_source = snapshot["source_timestamp"]
        control.launch("prometheus")
        _eventually(control.prometheus_ready)
        _eventually(lambda: receiver.snapshot()["source_timestamp"] > old_source
                    and not receiver.snapshot()["progress_missing"])

        # Let another source timestamp advance before the second independent fault.
        recovered_advances = receiver.snapshot()["advances"]
        _eventually(lambda: receiver.snapshot()["advances"] > recovered_advances)
        alertmanager_fault = time.monotonic()
        control.stop("alertmanager")
        assert control.running["alertmanager"].poll() is not None
        assert control.prometheus_ready()
        _eventually(lambda: receiver.snapshot()["progress_missing"], timeout=6)
        assert control.prometheus_ready()
        evidence["alertmanager_outage"] = {
            "source_detection_seconds": round(missing_at("progress", alertmanager_fault) - alertmanager_fault, 3),
        }
        assert evidence["alertmanager_outage"]["source_detection_seconds"] <= receiver.allowance + 1
        old_source = receiver.snapshot()["source_timestamp"]
        control.launch("alertmanager")
        _eventually(control.alertmanager_ready)
        _eventually(lambda: receiver.snapshot()["source_timestamp"] > old_source
                    and not receiver.snapshot()["progress_missing"], timeout=20)
        assert receiver.thread.is_alive()
        evidence["recoveries"] = 2
        evidence["firing_ends_at"] = sorted(receiver.snapshot()["firing_ends_at"])
        record_testsuite_property("independent_watchdog_evidence", json.dumps(evidence, sort_keys=True))
