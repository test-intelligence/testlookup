"""Open-source release: the infrastructure TestLookup ships stays OSI-licensed.

Three images and one model default were swapped before the Apache-2.0 release
(audit findings A-1..A-4):

* ``redis:7-alpine`` resolved to Redis 7.4.x, RSALv2/SSPLv1 -> ``valkey/valkey``
  (BSD-3-Clause), under the same ``redis`` service / ``testlookup-redis`` names;
* ``pgsty/silo`` + ``pgsty/mc`` (AGPL-3.0) -> ``ghcr.io/versity/versitygw``
  (Apache-2.0), under the same ``minio`` / ``testlookup-minio`` names, with
  buckets created by ``backend/scripts/s3_setup.py`` instead of ``mc``;
* ``mongo:7`` (SSPL-1.0) stays a referenced prerequisite but is no longer
  redistributed in the offline bundle (bundle class ``external``);
* the default local model ``qwen2.5:3b-instruct`` (Qwen RESEARCH licence,
  non-commercial) -> ``qwen2.5:7b-instruct`` (Apache-2.0).

Each guard below fails if one of them comes back through any surface.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = sorted(ROOT.glob("docker-compose*.yml"))
K8S_FILES = sorted((ROOT / "k8s").rglob("*.yaml"))
MANIFEST = ROOT / "deploy" / "images.manifest.txt"
CI = ROOT / ".github" / "workflows" / "ci.yml"

VALKEY = "valkey/valkey:8.1-alpine"
VERSITYGW = "ghcr.io/versity/versitygw:v1.8.0"

# An image whose licence the release cannot ship: Redis 7.4+ (RSAL/SSPL, AGPL
# from 8), MinIO / PGSTY Silo and their mc clients (AGPL-3.0).
_BANNED_IMAGE = re.compile(
    r"(?:^|/)(?:redis|silo|mc)(?::|$)|(?:^|/)pgsty/|(?:^|/)minio/(?:minio|mc)(?::|$)|quay\.io/minio/"
)
# The whole value, not \S+: an air-gap override writes
# `image: ${VAR:?message with spaces}/redis:7-alpine` (one scalar, many words).
_IMAGE_LINE = re.compile(r"^\s*(?:-\s*)?(?:image|newName):\s*(?P<ref>\S.*?)\s*(?:\s#.*)?$")


def _image_refs(path: Path) -> list[tuple[int, str]]:
    refs = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = _IMAGE_LINE.match(line)
        if match:
            ref = match.group("ref").strip().strip("'\"")
            # Drop a ${REGISTRY...}/ prefix: judge the upstream name.
            ref = re.sub(r"^\$\{[^}]*\}/", "", ref)
            refs.append((lineno, ref))
    return refs


def _manifest_rows() -> list[tuple[str, str, str, str]]:
    rows = []
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            ref, role, surfaces, bundle = line.split()
            rows.append((ref, role, surfaces, bundle))
    return rows


def _compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _k8s_docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


# ── No banned image anywhere ──────────────────────────────────────────────────

def test_the_scan_sees_the_surfaces():
    assert len(COMPOSE_FILES) >= 4 and len(K8S_FILES) > 20
    refs = [ref for path in COMPOSE_FILES + K8S_FILES for _, ref in _image_refs(path)]
    assert VALKEY in refs and VERSITYGW in refs, "the image scan reads nothing"


@pytest.mark.parametrize("path", COMPOSE_FILES + K8S_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_deployment_surface_uses_a_source_available_or_agpl_image(path):
    bad = [(lineno, ref) for lineno, ref in _image_refs(path) if _BANNED_IMAGE.search(ref)]
    assert bad == []


def test_kustomize_image_overrides_name_no_banned_image():
    for path in (ROOT / "k8s").rglob("kustomization.yaml"):
        for doc in _k8s_docs(path):
            for entry in doc.get("images") or []:
                assert not _BANNED_IMAGE.search(str(entry.get("name", ""))), (path, entry)


def test_the_manifest_and_mirror_lists_name_no_banned_image():
    for ref, *_ in _manifest_rows():
        assert not _BANNED_IMAGE.search(ref), ref
    for path in sorted((ROOT / "openshiftsetup").glob("*")) + sorted((ROOT / "scripts" / "release").glob("*.sh")):
        text = path.read_text(encoding="utf-8")
        for token in re.findall(r"[\w./-]+:[\w.-]+", text):
            if "/" in token or token.split(":")[0] in {"redis", "mongo", "valkey"}:
                assert not _BANNED_IMAGE.search(token) or "://" in token, (path, token)


@pytest.mark.parametrize("pattern", [r"\bmc (?:alias|mb|anonymous|admin|event|mirror)\b", r"pgsty/"])
def test_no_deployment_script_drives_the_mc_client(pattern):
    paths = COMPOSE_FILES + [ROOT / "Makefile"] + sorted((ROOT / "openshiftsetup").glob("*"))
    paths += sorted((ROOT / "scripts" / "release").glob("*.sh"))
    hits = [p.relative_to(ROOT).as_posix() for p in paths if re.search(pattern, p.read_text(encoding="utf-8"))]
    assert hits == []


# ── The offline bundle's licence boundary ─────────────────────────────────────

def test_offline_bundle_excludes_mongo_and_carries_valkey_and_versitygw():
    rows = _manifest_rows()
    bundled = {ref for ref, _, _, bundle in rows if bundle in ("core", "llm")}
    external = {ref for ref, _, _, bundle in rows if bundle == "external"}
    assert VALKEY in bundled and VERSITYGW in bundled
    assert "mongo:7" in external
    assert not any(ref.split(":")[0] == "mongo" for ref in bundled)
    # MongoDB is still a deployed dependency: compose and k8s reference it.
    assert ("mongo:7", "infra", "compose,k8s", "external") in rows


def test_the_bundle_script_only_saves_core_and_llm_rows():
    script = (ROOT / "scripts" / "release" / "offline-bundle.sh").read_text(encoding="utf-8")
    assert 'BUNDLE_FILTER="core"' in script and 'BUNDLE_FILTER="core,llm"' in script
    assert 'INFRA_REFS="$(manifest_refs any infra "$BUNDLE_FILTER")"' in script
    assert 'EXTERNAL_REFS="$(manifest_refs any infra external)"' in script
    assert '"external_prerequisites": [$EXTERNAL_JSON]' in script
    workflow = (ROOT / ".github" / "workflows" / "offline-bundle.yml").read_text(encoding="utf-8")
    assert "external_prerequisites" in workflow and '"mongo"' in workflow


def test_the_air_gap_override_still_repoints_mongo_for_operators_who_mirror_it():
    airgap = _compose(ROOT / "docker-compose.airgap.yml")
    assert airgap["services"]["mongo"]["image"].endswith("/mongo:7")
    assert airgap["services"]["redis"]["image"].endswith("/" + VALKEY)
    assert airgap["services"]["minio"]["image"].endswith("/" + VERSITYGW)


# ── CI pulls nothing from Docker Hub and runs the real S3 suite ───────────────

def test_ci_brokers_are_valkey_from_ecr_and_s3_is_versitygw():
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    brokers = {job: spec["services"]["redis"]["image"]
               for job, spec in jobs.items() if "redis" in (spec.get("services") or {})}
    assert brokers and set(brokers.values()) == {"public.ecr.aws/valkey/valkey:8.1-alpine"}
    for job, spec in jobs.items():
        if "redis" in (spec.get("services") or {}):
            assert "valkey-cli ping" in spec["services"]["redis"]["options"], job

    backend = jobs["backend-test"]
    s3 = backend["services"]["s3"]
    assert s3["image"] == VERSITYGW
    assert s3["env"]["VGW_BACKEND"] == "posix" and s3["env"]["VGW_HEALTH"] == "/health"
    run_tests = next(step for step in backend["steps"] if step.get("name") == "Run tests")
    assert run_tests["env"]["TESTLOOKUP_S3_TEST_ENDPOINT"] == "http://localhost:9000"
    assert run_tests["env"]["TESTLOOKUP_S3_TEST_ACCESS_KEY"] == s3["env"]["ROOT_ACCESS_KEY"]
    assert run_tests["env"]["TESTLOOKUP_S3_TEST_SECRET_KEY"] == s3["env"]["ROOT_SECRET_KEY"]
    assert "pytest tests/" in run_tests["run"], "the step that gets the env must run the suite"
    assert (ROOT / "backend" / "tests" / "integration" / "test_s3_storage_versitygw.py").exists()


# ── Valkey: same names, valkey binaries ───────────────────────────────────────

@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.release.yml", "docker-compose.dev-lite.yml"])
def test_compose_broker_is_valkey_under_the_redis_name(name):
    redis = _compose(ROOT / name)["services"]["redis"]
    assert redis["image"] == VALKEY
    assert "valkey-server" in redis["command"] and "--appendonly yes" in redis["command"]
    assert "valkey-cli" in " ".join(redis["healthcheck"]["test"])
    assert any(str(v).endswith(":/data") for v in redis["volumes"])


@pytest.mark.parametrize("overlay", ["homelab", "openshift-artifactory"])
def test_k8s_broker_is_valkey_under_the_redis_names(overlay):
    docs = _k8s_docs(ROOT / "k8s" / "overlays" / overlay / "infra-redis.yaml")
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
    assert deployment["metadata"]["name"] == "testlookup-redis" and pvc["metadata"]["name"] == "redis-data"
    [container] = deployment["spec"]["template"]["spec"]["containers"]
    assert container["image"] == VALKEY
    assert container["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]
    assert container["command"][0] in ("valkey-server", "redis-server")


def test_homelab_broker_differs_from_redis_7_only_by_its_image():
    """The owner's running homelab pins the old image with a one-line patch,
    so the command and probes must work with BOTH images: the Valkey image
    ships redis-server / redis-cli symlinks, redis:7 has no valkey-* binaries."""
    docs = _k8s_docs(ROOT / "k8s" / "overlays" / "homelab" / "infra-redis.yaml")
    [container] = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"]
    assert container["command"][0] == "redis-server"
    assert container["readinessProbe"]["exec"]["command"] == ["redis-cli", "ping"]
    assert container["livenessProbe"]["exec"]["command"] == ["redis-cli", "ping"]
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "768Mi"}


# ── versitygw: same names, plain-file store under /data/s3 ────────────────────

@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.release.yml", "docker-compose.dev-lite.yml"])
def test_compose_object_store_is_versitygw_under_the_minio_name(name):
    minio = _compose(ROOT / name)["services"]["minio"]
    env = minio["environment"]
    assert minio["image"] == VERSITYGW
    assert env["VGW_BACKEND"] == "posix"
    assert env["VGW_BACKEND_ARG"] == "/data/s3", "never serve a legacy MinIO /data directory"
    assert env["VGW_PORT"] == ":9000" and env["VGW_HEALTH"] == "/health"
    assert "MINIO_ACCESS_KEY" in env["ROOT_ACCESS_KEY"] and "MINIO_SECRET_KEY" in env["ROOT_SECRET_KEY"]
    assert 'mkdir -p "$$VGW_BACKEND_ARG"' in minio["entrypoint"][-1]
    assert minio["ports"] == ["9000:9000"], "no console port any more"
    assert any(str(v).endswith(":/data") for v in minio["volumes"])
    assert "http://127.0.0.1:9000/health" in minio["healthcheck"]["test"]


@pytest.mark.parametrize("overlay", ["homelab", "openshift-artifactory"])
def test_k8s_object_store_is_versitygw_under_the_minio_names(overlay):
    docs = _k8s_docs(ROOT / "k8s" / "overlays" / overlay / "infra-minio.yaml")
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
    assert deployment["metadata"]["name"] == "testlookup-minio" and pvc["metadata"]["name"] == "minio-data"
    [container] = deployment["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") or e["valueFrom"]["secretKeyRef"]["key"] for e in container["env"]}
    assert container["name"] == "minio" and container["image"] == VERSITYGW
    assert env["VGW_BACKEND_ARG"] == "/data/s3" and env["VGW_HEALTH"] == "/health"
    assert env["ROOT_ACCESS_KEY"] == "MINIO_ACCESS_KEY" and env["ROOT_SECRET_KEY"] == "MINIO_SECRET_KEY"
    assert [p["containerPort"] for p in container["ports"]] == [9000]
    assert container["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"] == {"path": "/health", "port": 9000}
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "1Gi"}


def test_the_minio_service_exposes_only_the_s3_api():
    docs = _k8s_docs(ROOT / "k8s" / "base" / "services.yaml")
    service = next(d for d in docs if d["metadata"]["name"] == "testlookup-minio")
    assert [p["port"] for p in service["spec"]["ports"]] == [9000]


def test_bucket_setup_runs_s3_setup_in_the_backend_image():
    for name in ("docker-compose.yml", "docker-compose.dev-lite.yml", "docker-compose.release.yml"):
        setup = _compose(ROOT / name)["services"]["minio-setup"]
        assert setup["command"] == ["python", "scripts/s3_setup.py"], name
        assert "build" in setup or "/backend:" in str(setup.get("image", "")), name
        assert setup["depends_on"]["minio"]["condition"] == "service_healthy", name
    airgap = (ROOT / "docker-compose.airgap.yml").read_text(encoding="utf-8")
    assert "  minio-setup:\n    <<: *airgap-backend-image" in airgap
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"(?m)^setup-minio:.*\n\t\$\(DOCKER_COMPOSE\) run --rm minio-setup$", makefile)
    openshift = (ROOT / "openshiftsetup" / "deploy-openshift-artifactory.sh").read_text(encoding="utf-8")
    assert "python /app/scripts/s3_setup.py" in openshift


# ── Health probes accept the bundled store and the legacy one ─────────────────

def _client(statuses: list[int]) -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(side_effect=[SimpleNamespace(status_code=s) for s in statuses])
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses, expected, calls", [
    ([200], {"status": "ok"}, ["/health"]),
    ([403, 200], {"status": "ok"}, ["/health", "/minio/health/live"]),
    ([404, 503], {"status": "degraded", "detail": "HTTP 503"}, ["/health", "/minio/health/live"]),
])
async def test_storage_health_probe(monkeypatch, statuses, expected, calls):
    from app.core import http_client
    from app.routers import health
    from app.services import storage_config_service

    monkeypatch.setattr(storage_config_service, "get_effective_storage_config",
                        AsyncMock(return_value={"minio_endpoint": "s3:9000", "minio_use_ssl": False}))
    client = _client(statuses)
    monkeypatch.setattr(http_client, "get_http_client", lambda: client)
    assert await health._check_minio() == expected
    assert [c.args[0] for c in client.get.await_args_list] == [f"http://s3:9000{p}" for p in calls]


def test_backup_wait_accepts_the_bundled_and_the_legacy_health_route(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "minio_sync_licences", ROOT / "k8s" / "components" / "backup" / "minio_sync.py")
    sync = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(sync)
    monkeypatch.setenv("MINIO_ENDPOINT", "s3:9000")
    asked: list[str] = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout):
        asked.append(url)
        if url.endswith("/minio/health/live"):
            return _Response()
        raise OSError("404")

    monkeypatch.setattr(sync.urllib.request, "urlopen", fake_urlopen)
    assert sync.minio_ready() is True
    assert asked == ["http://s3:9000/health", "http://s3:9000/minio/health/live"]


# ── No hard-coded admin password in the OpenShift path ────────────────────────

def test_openshift_admin_password_is_generated_never_defaulted():
    script = (ROOT / "openshiftsetup" / "deploy-openshift-artifactory.sh").read_text(encoding="utf-8")
    example = (ROOT / "openshiftsetup" / "artifactory.env.example").read_text(encoding="utf-8")
    assert re.search(r"ADMIN_PASSWORD:-[^}]", script) is None, "a ${ADMIN_PASSWORD:-literal} default is back"
    assert "openssl rand -base64 18 | tr -d '=/+' | head -c 20" in script
    assert re.search(r'(?m)^ADMIN_PASSWORD="[^"]+"', example) is None
    assert 'ADMIN_PASSWORD=""' in example


# ── Default local models are licensed for commercial use ──────────────────────

#: Non-commercial / restricted families that must never be a default. Qwen2.5
#: 3B (and Qwen2.5-Coder 3B) are under the Qwen RESEARCH licence; the other
#: sizes are Apache-2.0. Llama and DeepSeek carry use-based licences.
_RESTRICTED_MODEL = re.compile(r"qwen2\.5(?:-coder)?:3b|llama3\.\d|deepseek")
_APACHE_DEFAULTS = {"qwen2.5:7b-instruct", "qwen2.5:14b-instruct-q5_K_M", "nomic-embed-text:v1.5"}


def test_backend_default_models_are_apache_licensed():
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["LLM_MODEL"].default in _APACHE_DEFAULTS
    assert fields["EMBEDDING_MODEL"].default in _APACHE_DEFAULTS
    assert not _RESTRICTED_MODEL.search(fields["LLM_MODEL"].default)


@pytest.mark.parametrize("rel", [
    ".env.example", "Makefile", "k8s/base/configmap.yaml", "docker-compose.yml",
    "docker-compose.release.yml", "docker-compose.dev-lite.yml",
    "openshiftsetup/deploy-openshift-artifactory.sh", "openshiftsetup/artifactory.env.example",
    "scripts/local_dev_setup_common.sh", "scripts/local_dev_setup_windows.ps1",
])
def test_no_default_names_a_restricted_model(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert _RESTRICTED_MODEL.findall(text) == [], rel


def test_makefile_model_defaults_are_the_apache_pair():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    defaults = dict(re.findall(r"(?m)^(OLLAMA_\w+_MODEL)\s*\?=\s*(\S+)\s*$", makefile))
    assert defaults == {
        "OLLAMA_SLM_MODEL": "qwen2.5:7b-instruct",
        "OLLAMA_LLM_MODEL": "qwen2.5:14b-instruct-q5_K_M",
        "OLLAMA_EMBEDDING_MODEL": "nomic-embed-text:v1.5",
    }
    assert "llama3.2:8b" not in makefile, "that tag does not exist"
