"""Every executing API and worker process must contribute to Prometheus.

The API and Celery prefork children use an isolated multiprocess directory per
container or pod. Each exporter aggregates its local processes, and Prometheus
scrapes every API pod and both Compose worker services.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
RELEASE_COMPOSE = yaml.safe_load(
    (REPO_ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
)
MONITORING_COMPOSE = yaml.safe_load(
    (REPO_ROOT / "docker-compose.monitoring.yml").read_text(encoding="utf-8")
)
PROMETHEUS = yaml.safe_load(
    (REPO_ROOT / "infra" / "monitoring" / "prometheus.yml").read_text(encoding="utf-8")
)
BACKEND_DEPLOYMENT = yaml.safe_load(
    (REPO_ROOT / "k8s" / "base" / "backend-deployment.yaml").read_text(encoding="utf-8")
)
NETWORK_POLICIES = list(
    yaml.safe_load_all(
        (REPO_ROOT / "k8s" / "base" / "networkpolicy.yaml").read_text(encoding="utf-8")
    )
)
MULTIPROC_DIR = "/tmp/prometheus-multiproc"
WORKER_SERVICES = ("worker", "worker-children")


def _scrape_targets(job_name: str) -> set[str]:
    jobs = {
        job["job_name"]: job
        for job in PROMETHEUS["scrape_configs"]
    }
    return {
        target
        for config in jobs[job_name]["static_configs"]
        for target in config["targets"]
    }


def test_api_image_enables_multiprocess_metrics_before_four_workers_start():
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    command = [line for line in dockerfile.splitlines() if line.startswith("CMD ")][-1]
    assert "PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc" in dockerfile
    assert 'rm -f \\"$PROMETHEUS_MULTIPROC_DIR\\"/*.db' in command
    assert "gunicorn -c gunicorn_conf.py app.main:app" in command
    assert "COPY --from=builder /app/gunicorn_conf.py ./gunicorn_conf.py" in dockerfile
    config = (REPO_ROOT / "backend" / "gunicorn_conf.py").read_text(encoding="utf-8")
    assert "workers = 4" in config


def test_api_supervisor_reaps_dead_worker_gauges(tmp_path: Path, monkeypatch):
    import gunicorn_conf

    stale = tmp_path / "gauge_livesum_123.db"
    stale.write_text("stale", encoding="utf-8")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    gunicorn_conf.child_exit(None, type("Worker", (), {"pid": 123})())

    assert not stale.exists()


def test_kubernetes_scrapes_every_api_pod():
    annotations = BACKEND_DEPLOYMENT["spec"]["template"]["metadata"]["annotations"]
    assert annotations["prometheus.io/scrape"] == "true"
    assert annotations["prometheus.io/port"] == "8000"
    assert annotations["prometheus.io/path"] == "/metrics"


def test_kubernetes_api_processes_share_ephemeral_metric_storage():
    pod_spec = BACKEND_DEPLOYMENT["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    mounts = {mount["name"]: mount["mountPath"] for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
    assert mounts["prometheus-multiproc"] == MULTIPROC_DIR
    assert volumes["prometheus-multiproc"]["emptyDir"] == {}


@pytest.mark.parametrize("compose", (COMPOSE, RELEASE_COMPOSE), ids=("development", "release"))
@pytest.mark.parametrize("service_name", WORKER_SERVICES)
def test_compose_worker_has_isolated_writable_multiprocess_storage(
    compose: dict, service_name: str
):
    service = compose["services"][service_name]
    environment = service["environment"]
    assert f"PROMETHEUS_MULTIPROC_DIR={MULTIPROC_DIR}" in environment
    assert "WORKER_METRICS_PORT=9100" in environment
    assert any(
        str(entry).split(":", 1)[0] == MULTIPROC_DIR
        for entry in service.get("tmpfs", [])
    ), f"{service_name} has no private writable tmpfs at {MULTIPROC_DIR}"


def test_prometheus_scrapes_every_compose_executor_on_the_bound_port():
    assert _scrape_targets("testlookup-backend") == {"backend:8000"}
    assert _scrape_targets("testlookup-worker") == {
        "worker:9100",
        "worker-children:9100",
    }


def test_monitoring_overlay_does_not_require_a_precreated_network():
    network = MONITORING_COMPOSE["networks"]["testlookup_net"]
    assert network.get("external") is not True


def test_worker_exporter_default_matches_prometheus():
    source = (REPO_ROOT / "backend" / "app" / "worker" / "celery_app.py").read_text(
        encoding="utf-8"
    )
    assert 'os.environ.get("WORKER_METRICS_PORT", "9100")' in source


def test_worker_exporter_failure_is_visible_without_breaking_boot(monkeypatch):
    import prometheus_client

    from app.worker import celery_app

    events = []

    class Logger:
        def error(self, event, **fields):
            events.append((event, fields))

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(REPO_ROOT))
    monkeypatch.setattr(
        prometheus_client,
        "start_http_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("port busy")),
    )
    monkeypatch.setattr("structlog.get_logger", lambda *_args, **_kwargs: Logger())

    celery_app._start_metrics_server()

    assert events == [
        (
            "worker_metrics_server_start_failed",
            {"port": "9100", "error": "port busy"},
        )
    ]


def test_kubernetes_network_policies_allow_only_monitoring_scrapes():
    policies = {policy["metadata"]["name"]: policy for policy in NETWORK_POLICIES}
    expected_ports = {"allow-backend": 8000, "allow-workers": 9100}
    for name, expected_port in expected_ports.items():
        ingress = policies[name]["spec"].get("ingress", [])
        monitoring_rules = []
        for rule in ingress:
            namespaces = rule.get("from", [])
            names = {
                value
                for source in namespaces
                for expression in source.get("namespaceSelector", {}).get(
                    "matchExpressions", []
                )
                if expression.get("key") == "kubernetes.io/metadata.name"
                and expression.get("operator") == "In"
                for value in expression.get("values", [])
            }
            opted_in = any(
                source.get("namespaceSelector", {}).get("matchLabels", {}).get(
                    "testlookup.io/metrics-scraper"
                )
                == "true"
                for source in namespaces
            )
            if {
                "monitoring",
                "openshift-monitoring",
                "openshift-user-workload-monitoring",
            } <= names and opted_in:
                monitoring_rules.append(rule)
        assert monitoring_rules, f"{name} blocks the monitoring namespace"
        assert expected_port in {
            port["port"] for rule in monitoring_rules for port in rule.get("ports", [])
        }


def test_multiprocess_registry_sums_known_task_counts(tmp_path: Path):
    """Two independent worker processes must appear as one exact fleet count."""
    metric_dir = tmp_path / "prometheus"
    metric_dir.mkdir()
    env = os.environ | {"PROMETHEUS_MULTIPROC_DIR": str(metric_dir)}
    emit = (
        "from prometheus_client import Counter; "
        "Counter('m08_tasks_total', 'tasks').inc(int(__import__('sys').argv[1]))"
    )
    for count in (2, 3):
        subprocess.run(
            [sys.executable, "-c", emit, str(count)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    collect = (
        "from prometheus_client import CollectorRegistry, generate_latest, multiprocess; "
        "r=CollectorRegistry(); multiprocess.MultiProcessCollector(r); "
        "print(generate_latest(r).decode())"
    )
    result = subprocess.run(
        [sys.executable, "-c", collect],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "m08_tasks_total 5.0" in result.stdout


def test_multiprocess_registry_sums_real_http_instrumentation(tmp_path: Path):
    """Requests handled by separate API processes produce one exact count."""
    metric_dir = tmp_path / "http-prometheus"
    metric_dir.mkdir()
    env = os.environ | {"PROMETHEUS_MULTIPROC_DIR": str(metric_dir)}
    emit = """
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_fastapi_instrumentator import Instrumentator
app = FastAPI()
@app.get('/known')
def known():
    return {'ok': True}
Instrumentator(should_ignore_untemplated=True).instrument(app)
client = TestClient(app)
for _ in range(int(__import__('sys').argv[1])):
    assert client.get('/known').status_code == 200
"""
    for count in (2, 3):
        subprocess.run(
            [sys.executable, "-c", emit, str(count)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    collect = (
        "from prometheus_client import CollectorRegistry, generate_latest, multiprocess; "
        "r=CollectorRegistry(); multiprocess.MultiProcessCollector(r); "
        "print(generate_latest(r).decode())"
    )
    result = subprocess.run(
        [sys.executable, "-c", collect],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    matching = [
        line
        for line in result.stdout.splitlines()
        if line.startswith('http_requests_total{')
        and 'handler="/known"' in line
        and 'method="GET"' in line
        and 'status="2xx"' in line
    ]
    assert len(matching) == 1, result.stdout
    assert matching[0].endswith(" 5.0")


def test_multiprocess_absolute_gauge_uses_only_latest_writer(tmp_path: Path):
    """Four API workers observing one Redis queue must expose one depth."""
    metric_dir = tmp_path / "gauge-prometheus"
    metric_dir.mkdir()
    env = os.environ | {"PROMETHEUS_MULTIPROC_DIR": str(metric_dir)}
    emit = (
        "from prometheus_client import Gauge; "
        "Gauge('m08_queue_depth', 'depth', multiprocess_mode='mostrecent').set(26)"
    )
    for _ in range(4):
        subprocess.run(
            [sys.executable, "-c", emit],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    collect = (
        "from prometheus_client import CollectorRegistry, generate_latest, multiprocess; "
        "r=CollectorRegistry(); multiprocess.MultiProcessCollector(r); "
        "print(generate_latest(r).decode())"
    )
    result = subprocess.run(
        [sys.executable, "-c", collect],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "m08_queue_depth 26.0" in result.stdout


def test_application_info_survives_real_multiprocess_collection(tmp_path: Path):
    metric_dir = tmp_path / "info-prometheus"
    metric_dir.mkdir()
    env = os.environ | {"PROMETHEUS_MULTIPROC_DIR": str(metric_dir)}
    emit = (
        "from prometheus_client import Gauge; "
        "Gauge('testlookup_app_info', 'metadata', "
        "['version', 'env', 'llm_provider'], "
        "multiprocess_mode='mostrecent').labels('1.2.3', 'test', 'offline').set(1)"
    )
    subprocess.run(
        [sys.executable, "-c", emit],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    collect = (
        "from prometheus_client import CollectorRegistry, generate_latest, multiprocess; "
        "r=CollectorRegistry(); multiprocess.MultiProcessCollector(r); "
        "print(generate_latest(r).decode())"
    )
    result = subprocess.run(
        [sys.executable, "-c", collect],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert 'testlookup_app_info{env="test",llm_provider="offline",version="1.2.3"} 1.0' in result.stdout


def test_integration_health_changes_from_healthy_to_skipped(tmp_path: Path):
    metric_dir = tmp_path / "health-prometheus"
    metric_dir.mkdir()
    env = os.environ | {"PROMETHEUS_MULTIPROC_DIR": str(metric_dir)}
    emit = """
from prometheus_client import Gauge
import sys
g = Gauge('testlookup_integration_health', 'health', ['provider'],
          multiprocess_mode='mostrecent')
current = sys.argv[1]
values = {'healthy': 1.0, 'degraded': 0.5, 'down': 0.0, 'skipped': -1.0}
g.labels(provider='jira').set(values[current])
"""
    for current in ("healthy", "skipped"):
        subprocess.run(
            [sys.executable, "-c", emit, current],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    collect = (
        "from prometheus_client import CollectorRegistry, generate_latest, multiprocess; "
        "r=CollectorRegistry(); multiprocess.MultiProcessCollector(r); "
        "print(generate_latest(r).decode())"
    )
    result = subprocess.run(
        [sys.executable, "-c", collect],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert 'testlookup_integration_health{provider="jira"} -1.0' in result.stdout


def test_worker_parent_clears_only_stale_metric_files(tmp_path: Path, monkeypatch):
    from app.worker import celery_app

    stale = tmp_path / "counter_123.db"
    keep = tmp_path / "README"
    stale.write_text("old", encoding="utf-8")
    keep.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    celery_app._prepare_worker_metrics_directory()

    assert not stale.exists()
    assert keep.read_text(encoding="utf-8") == "keep"
