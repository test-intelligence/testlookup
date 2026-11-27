"""Opt-in routing contracts and real Prometheus -> Alertmanager -> TLS sink."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time
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


@contextmanager
def _routing_stack(tmp_path):
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
    state = {"flags": {}, "events": [], "fail": False}
    lock = threading.Lock()

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
    try:
        if os.environ.get("AMTOOL_TEST_BINARY"):
            subprocess.run([os.environ["AMTOOL_TEST_BINARY"], "check-config", str(am_config)], check=True, capture_output=True, timeout=10)
        for binary, arguments in (
            (os.environ["ALERTMANAGER_TEST_BINARY"], [f"--config.file={am_config}", f"--web.listen-address=127.0.0.1:{am_port}", f"--storage.path={tmp_path / 'am-data'}", "--cluster.listen-address=", "--log.level=warn"]),
            (os.environ["PROMETHEUS_TEST_BINARY"], [f"--config.file={prom_file}", f"--web.listen-address=127.0.0.1:{prom_port}", f"--storage.tsdb.path={tmp_path / 'prom-data'}", "--rules.alert.resend-delay=500ms", "--log.level=warn"]),
        ):
            log = (tmp_path / (Path(binary).stem + ".log")).open("wb")
            processes.append((subprocess.Popen([binary, *arguments], stdout=log, stderr=subprocess.STDOUT), log))
        _eventually(ready)
        yield state, lock, api, events
    finally:
        for process, log in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
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
