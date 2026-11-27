"""Bounded BYO infrastructure contracts and a real Prometheus mTLS data path."""
from __future__ import annotations

from copy import deepcopy
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
import sys
import threading
import time
from urllib.request import ProxyHandler, build_opener

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("configure_infrastructure_metrics", ROOT / "scripts/monitoring/configure_infrastructure_metrics.py")
configure = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(configure)


def site(profile="compose"):
    value = yaml.safe_load((ROOT / "infra/monitoring/infrastructure/site.example.yml").read_text(encoding="utf-8"))
    value["profile"] = profile
    return value


def test_closed_scoped_jobs_have_limits_private_transport_and_independent_inventory():
    selected = site()
    scrapes = configure.render_scrapes(selected)["scrape_configs"]
    rules = configure.render_contract(selected)["groups"][0]["rules"]
    assert len(scrapes) == len(rules) == 2
    for job,record in zip(scrapes,rules):
        labels = job["static_configs"][0]["labels"]
        target = next(item for item in selected["targets"] if item["instance"] == labels["instance"])
        assert job["scheme"] == "https" and job["metrics_path"] == "/metrics"
        assert job["follow_redirects"] is False and job["proxy_from_environment"] is False
        assert job["honor_labels"] is False and job["honor_timestamps"] is False
        assert job["target_limit"] == 1 and job["sample_limit"] == 128 and job["body_size_limit"] == "2MB"
        assert job["label_limit"] == 10 and job["label_value_length_limit"] == 128
        assert job["tls_config"] == {"ca_file":configure.COMPOSE_TLS+"/ca.crt",
            "cert_file":configure.COMPOSE_TLS+"/client.crt","key_file":configure.COMPOSE_TLS+"/client.key",
            "server_name":target["server_name"],
            "insecure_skip_verify":False,"min_version":"TLS12"}
        assert set(labels) == set(configure.SCOPE)
        assert labels == record["labels"] and record["expr"] == "vector(1)"
        assert record["record"] == "testlookup:infrastructure_expected_target"
        assert labels["job"] == configure.ROLES[labels["telemetry_role"]]
    # Expected records stay present independently of discovery/job selection.
    scrapes.clear()
    assert len(configure.render_contract(selected)["groups"][0]["rules"]) == 2


@pytest.mark.parametrize("address", ["http://private.internal:9100","https://private.internal:9100","user:private@private.internal:9100",
    "private.internal:9100/metrics","private.internal:9100?token=private","private.internal:9100#private",
    "8.8.8.8:9100","169.254.169.254:9100","0.0.0.0:9100","[2001:4860::1]:9100","private.internal","private.internal:0",
    "private.internal:65536","private.internal:9100\n"])
def test_nonprivate_or_url_components_are_refused_without_echo(address):
    selected = site()
    selected["targets"][0]["address"] = address
    with pytest.raises(configure.ConfigurationError) as error:
        configure.validate_site(selected)
    assert address not in str(error.value)


@pytest.mark.parametrize("address", ["10.4.1.2:9100","172.16.0.1:9100","192.168.1.4:9100","[fd00::1234]:9100","127.0.0.1:9100"])
def test_private_literals_are_supported_with_explicit_dns_certificate_name(address):
    selected = site()
    selected["targets"][0]["address"] = address
    configure.validate_site(selected)


@pytest.mark.parametrize("mutate", [lambda value:value.update(unreviewed=True),
    lambda value:value.update(profile=[]),lambda value:value.update(cluster=""),
    lambda value:value["targets"][0].update(private_endpoint=False),
    lambda value:value["targets"][0].update(insecure_skip_verify=True),
    lambda value:value["targets"][0].update(role="redis-exporter"),
    lambda value:value["targets"][0].update(server_name="*.private.internal"),
    lambda value:value["targets"].append(deepcopy(value["targets"][0])),
    lambda value:value.update(targets=[]),lambda value:value.update(targets=value["targets"]*9)])
def test_unknown_fields_ambiguous_scope_and_unbounded_inventory_are_refused(mutate):
    selected = site()
    mutate(selected)
    with pytest.raises(configure.ConfigurationError):
        configure.validate_site(selected)


def test_duplicate_yaml_and_unsafe_cli_do_not_write_or_echo_inputs(tmp_path):
    selected = tmp_path / "site.yml"
    selected.write_text("schema_version: 1\nschema_version: 1\n",encoding="utf-8")
    with pytest.raises(configure.ConfigurationError):
        configure.load_site(selected)
    selected.write_text(yaml.safe_dump({**site(),"private-marker":"private-input"}),encoding="utf-8")
    result = subprocess.run([sys.executable,str(ROOT / "scripts/monitoring/configure_infrastructure_metrics.py"),
                             "--site",str(selected),"--scrape-out",str(tmp_path/"scrapes.yml"),
                             "--rules-out",str(tmp_path/"rules.yml")],capture_output=True,text=True,timeout=5)
    assert result.returncode == 2 and "private-input" not in result.stderr and not result.stdout
    assert not (tmp_path/"scrapes.yml").exists() and not (tmp_path/"rules.yml").exists()


@pytest.mark.parametrize("content", [b"#"*65_537,b"\xff\xfe"],ids=["over-limit","invalid-utf8"])
def test_site_input_read_has_a_byte_and_encoding_bound(tmp_path,content):
    path = tmp_path/"site.yml"
    path.write_bytes(content)
    with pytest.raises(configure.ConfigurationError):
        configure.load_site(path)


def test_operator_uses_native_no_redirect_jobs_and_namespace_local_secret_paths():
    selected = site("kubernetes")
    secret,rule = configure.render_operator(selected)
    assert secret["metadata"]["namespace"] == selected["monitoring_namespace"]
    assert secret["kind"] == "Secret" and secret["metadata"]["name"] == "testlookup-infrastructure-scrapes"
    jobs = yaml.safe_load(secret["stringData"]["scrapes.yml"])
    assert jobs == configure.render_scrapes(selected)["scrape_configs"]
    assert all(job["tls_config"]["key_file"] == configure.OPERATOR_TLS+"/client.key" for job in jobs)
    assert all(job["follow_redirects"] is False for job in jobs)
    assert "PRIVATE KEY" not in json.dumps(secret)
    assert rule["kind"] == "PrometheusRule" and rule["spec"] == configure.render_contract(selected)
    assert rule["metadata"]["labels"]["role"] == "alert-rules"
    assert "infrastructure" not in (ROOT / "k8s/monitoring/kustomization.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("base", ["docker-compose.yml","docker-compose.release.yml"])
def test_mount_only_overlay_keeps_exporter_hosts_and_credentials_out_of_the_app(base):
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose renderer is unavailable")
    environment = {**os.environ,**{name:"./fixture-only" for name in (
        "INFRASTRUCTURE_SCRAPE_CONFIG_SOURCE","INFRASTRUCTURE_RULES_SOURCE","INFRASTRUCTURE_CA_SOURCE",
        "INFRASTRUCTURE_CLIENT_CERT_SOURCE","INFRASTRUCTURE_CLIENT_KEY_SOURCE")},
        **{name:"fixture-only" for name in ("GF_SECURITY_ADMIN_PASSWORD","POSTGRES_PASSWORD","MONGO_PASSWORD",
           "MINIO_ACCESS_KEY","MINIO_SECRET_KEY","MINIO_ROOT_USER","MINIO_ROOT_PASSWORD","FLOWER_PASSWORD","REDIS_PASSWORD")}}
    result = subprocess.run(["docker","compose","-f",base,"-f","docker-compose.monitoring.yml","-f",
                             "docker-compose.infrastructure-metrics.yml","config","--no-env-resolution","--format","json"],
                            cwd=ROOT,env=environment,capture_output=True,text=True,timeout=30,check=True)
    rendered = json.loads(result.stdout)
    overlay = yaml.safe_load((ROOT / "docker-compose.infrastructure-metrics.yml").read_text(encoding="utf-8"))
    assert set(overlay["services"]) == {"prometheus"}
    service = rendered["services"]["prometheus"]
    assert {secret["target"] for secret in service["secrets"]} == {configure.COMPOSE_TLS+"/"+name for name in ("ca.crt","client.crt","client.key")}
    assert all(volume["read_only"] for volume in service["volumes"] if "infrastructure" in volume["target"])
    assert all("infrastructure" not in json.dumps(rendered["services"][name].get("secrets",[])) for name in ("backend","worker","worker-children","beat"))


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


def _certificates(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"disposable infrastructure CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(ca_key,hashes.SHA256()))
    (root/"ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for name,usage in (("server",ExtendedKeyUsageOID.SERVER_AUTH),("client",ExtendedKeyUsageOID.CLIENT_AUTH)):
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        certificate = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)]))
                       .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
                       .add_extension(x509.ExtendedKeyUsage([usage]),critical=False)
                       .add_extension(x509.SubjectAlternativeName([x509.DNSName("exporter.fixture.internal")]),critical=False)
                       .sign(ca_key,hashes.SHA256()))
        (root/(name+".crt")).write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        (root/(name+".key")).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))


@pytest.mark.skipif(not os.environ.get("PROMETHEUS_TEST_BINARY"),reason="requires the pinned local Prometheus binary")
def test_real_prometheus_mtls_allowlist_redirection_and_scope(tmp_path):
    _certificates(tmp_path)
    state = {"redirect":False,"available":20,"pg_up":1,"cleartext_hits":0,"certified_requests":0}

    class Cleartext(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_GET(self):
            state["cleartext_hits"] += 1
            self.send_response(200)
            self.end_headers()

    cleartext = ThreadingHTTPServer(("127.0.0.1",0),Cleartext)

    class Exporter(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_GET(self):
            assert self.connection.getpeercert()
            state["certified_requests"] += 1
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location",f"http://127.0.0.1:{cleartext.server_port}/metrics")
                self.end_headers()
                return
            body = (f'node_filesystem_avail_bytes{{mountpoint="/",device="private-device",private_label="private-marker"}} {state["available"]}\n'
                    'node_filesystem_size_bytes{mountpoint="/",device="private-device"} 100\n'
                    'node_filesystem_avail_bytes{mountpoint="/unselected"} 1\nnode_vmstat_oom_kill 10\n'
                    f'pg_up{{datname="private-database"}} {state["pg_up"]}\npg_settings_max_connections{{server="private-address"}} 100\n'
                    'pg_stat_activity_query{query="private-query"} 1\nunselected_private_metric 1\n')
            self.send_response(200)
            self.send_header("Content-Type","text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body.encode())

    exporter = ThreadingHTTPServer(("127.0.0.1",0),Exporter)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(tmp_path/"server.crt",tmp_path/"server.key")
    tls.load_verify_locations(tmp_path/"ca.crt")
    tls.verify_mode = ssl.CERT_REQUIRED
    exporter.socket = tls.wrap_socket(exporter.socket,server_side=True)
    for server in (exporter,cleartext):
        threading.Thread(target=server.serve_forever,daemon=True).start()
    selected = site()
    for target in selected["targets"]:
        target.update(address=f"127.0.0.1:{exporter.server_port}",server_name="exporter.fixture.internal")
    selected["targets"].append({**selected["targets"][0],"instance":"wrong-server-name",
                                "server_name":"unmatched.fixture.internal"})
    generated = configure.render_scrapes(selected)
    for job in generated["scrape_configs"]:
        job.update(scrape_interval="250ms",scrape_timeout="200ms")
        for field,filename in (("ca_file","ca.crt"),("cert_file","client.crt"),("key_file","client.key")):
            job["tls_config"][field] = str(tmp_path/filename)
    missing_client = deepcopy(generated["scrape_configs"][0])
    missing_client["job_name"] += "-missing-client"
    missing_client["static_configs"][0]["labels"]["instance"] = "missing-client"
    missing_client["tls_config"].pop("cert_file")
    missing_client["tls_config"].pop("key_file")
    generated["scrape_configs"].append(missing_client)
    contract = configure.render_contract(selected)
    contract["groups"][0]["interval"] = "250ms"
    (tmp_path/"contract.yml").write_text(yaml.safe_dump(contract),encoding="utf-8")
    generated["global"] = {"scrape_interval":"250ms","scrape_timeout":"200ms","evaluation_interval":"250ms"}
    generated["rule_files"] = [str(tmp_path/"contract.yml")]
    config = tmp_path/"prometheus.yml"
    config.write_text(yaml.safe_dump(generated),encoding="utf-8")
    port = _port()
    output = (tmp_path/"prometheus.log").open("wb")
    process = subprocess.Popen([os.environ["PROMETHEUS_TEST_BINARY"],f"--config.file={config}",
                                f"--web.listen-address=127.0.0.1:{port}",f"--storage.tsdb.path={tmp_path/'data'}","--log.level=warn"],
                               stdout=output,stderr=subprocess.STDOUT)
    opener = build_opener(ProxyHandler({}))

    def query(expression):
        from urllib.parse import urlencode
        with opener.open(f"http://127.0.0.1:{port}/api/v1/query?"+urlencode({"query":expression}),timeout=1) as response:
            return json.load(response)["data"]["result"]

    def until(predicate):
        deadline = time.monotonic()+12
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except OSError:
                pass
            time.sleep(0.05)
        raise AssertionError("local infrastructure scrape condition timed out")

    def both_up():
        values = query('up{job=~"testlookup-infra-.+",instance=~"host-a|postgres-a"}')
        return len(values) == 2 and all(item["value"][1] == "1" for item in values)

    try:
        until(both_up)
        until(lambda:len(query("testlookup:infrastructure_expected_target")) == 3)
        until(lambda:len(query('up{instance=~"wrong-server-name|missing-client"} == 0')) == 2)
        samples = query('{job=~"testlookup-infra-.+",__name__=~"node_.+|pg_.+"}')
        assert len(samples) == 5 and {sample["metric"]["__name__"] for sample in samples} == set().union(*configure.METRICS.values())
        assert all(set(sample["metric"]) <= {"__name__",*configure.SCOPE,"mountpoint"} for sample in samples)
        assert "private-marker" not in json.dumps(samples) and "private-database" not in json.dumps(samples)
        state["available"],state["pg_up"] = 5,0
        until(lambda:query('node_filesystem_avail_bytes{job="testlookup-infra-host"}')[0]["value"][1] == "5")
        until(lambda:query('pg_up{job="testlookup-infra-postgres"}')[0]["value"][1] == "0")
        state["redirect"] = True
        until(lambda:len(query('up{job=~"testlookup-infra-.+",instance=~"host-a|postgres-a"} == 0')) == 2)
        assert state["cleartext_hits"] == 0
        assert len(query("testlookup:infrastructure_expected_target")) == 3
        state["redirect"],state["available"],state["pg_up"] = False,20,1
        until(both_up)
        assert state["certified_requests"] > 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        output.close()
        for server in (exporter,cleartext):
            server.shutdown()
            server.server_close()
