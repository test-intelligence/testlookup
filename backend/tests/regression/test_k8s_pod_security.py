"""Pod security of the Kubernetes manifests, as rendered.

The rules:

* Every application pod (API, the Celery workers, beat, the frontend, MCP)
  sets ``runAsNonRoot`` and the default seccomp profile at POD level, so a
  container added later inherits them.
* Every application container, and every ``wait-for-redis`` init container,
  runs as a fixed non-zero uid, cannot escalate, drops every capability and
  adds none, and has a read-only root filesystem.
* A read-only root needs every path the process writes on a volume. The paths
  are listed here per workload; a new one fails the test until it is mounted.
* Nothing runs as uid 0 by request, in the base or in any overlay, except the
  listed data-store and model-server pods whose images start as root.
* The namespace enforces the ``baseline`` Pod Security Standard, and every
  pod of every overlay passes it (checked here, since no cluster does it for us).

The manifests are rendered with kustomize; there is no cluster.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from functools import cache
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
K8S = ROOT / "k8s"
OVERLAYS = sorted(p.name for p in (K8S / "overlays").iterdir() if (p / "kustomization.yaml").exists())
TARGETS = ["base", *(f"overlays/{name}" for name in OVERLAYS)]

BACKEND_IMAGE_WORKLOADS = {
    "testlookup-backend": "backend",
    "testlookup-worker-critical": "worker",
    "testlookup-worker-ingestion": "worker",
    "testlookup-worker-ai": "worker",
    "testlookup-worker-default": "worker",
    "testlookup-worker-children": "worker",
    "testlookup-beat": "beat",
}
APP_WORKLOADS = {**BACKEND_IMAGE_WORKLOADS, "testlookup-frontend": "frontend", "testlookup-mcp": "mcp"}
WAITS_FOR_REDIS = sorted(set(BACKEND_IMAGE_WORKLOADS) - {"testlookup-backend"})

# Every path a container writes, per workload. Sources: backend/Dockerfile
# (HOME=/tmp, PROMETHEUS_MULTIPROC_DIR), the Deployments' own env
# (CHROMA_ONNX_MODEL_DIR), k8s/base/configmap.yaml (ML_MODEL_DIR), the Celery
# ready file and beat's schedule and pid files (/tmp), and frontend/nginx.main.conf.
_WORKER_PATHS = {"/tmp", "/tmp/prometheus-multiproc", "/var/cache/testlookup/chroma-onnx",
                 "/var/lib/testlookup/ml-models"}
WRITABLE = {
    "testlookup-backend": _WORKER_PATHS,
    "testlookup-worker-critical": _WORKER_PATHS,
    "testlookup-worker-ingestion": _WORKER_PATHS,
    "testlookup-worker-ai": _WORKER_PATHS,
    "testlookup-worker-default": _WORKER_PATHS,
    "testlookup-worker-children": _WORKER_PATHS,
    "testlookup-beat": {"/tmp"},
    "testlookup-mcp": {"/tmp"},
    # /var/cache/nginx and /run are written only by a frontend image from
    # before nginx.main.conf (its stock nginx.conf); see the Deployment.
    "testlookup-frontend": {"/tmp", "/etc/nginx/conf.d", "/var/cache/nginx", "/run"},
}

# Pods whose image starts as root and changes user itself, or needs root. They
# may run as root (baseline allows it); they still may not ASK for uid 0.
ROOT_BY_IMAGE = {
    "testlookup-ollama": "the Ollama image runs as root and writes /root/.ollama",
}

# What the baseline Pod Security Standard lets a container add.
BASELINE_CAPABILITIES = {
    "AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD",
    "NET_BIND_SERVICE", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT",
}
BASELINE_SYSCTLS = {
    "kernel.shm_rmid_forced", "net.ipv4.ip_local_port_range", "net.ipv4.ip_unprivileged_port_start",
    "net.ipv4.tcp_syncookies", "net.ipv4.ping_group_range", "net.ipv4.ip_local_reserved_ports",
    "net.ipv4.tcp_keepalive_time", "net.ipv4.tcp_fin_timeout", "net.ipv4.tcp_keepalive_intvl",
    "net.ipv4.tcp_keepalive_probes",
}


@cache
def _render(target: str) -> tuple[dict, ...]:
    directory = str(K8S / target)
    kustomize, kubectl = shutil.which("kustomize"), shutil.which("kubectl")
    if kustomize:
        command = [kustomize, "build", directory]
    elif kubectl:
        command = [kubectl, "kustomize", directory]
    else:
        pytest.skip("neither kustomize nor kubectl is installed (rendering only; no cluster)")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode != 0 and 'unknown command "kustomize"' in result.stderr:
        pytest.skip(f"{command[0]} has no kustomize command")
    assert result.returncode == 0, f"{target} does not render:\n{result.stderr[-2000:]}"
    return tuple(doc for doc in yaml.safe_load_all(result.stdout) if doc)


def _pods(target: str) -> dict[str, dict]:
    """``{workload name: pod spec}`` for every pod template in the render."""
    pods: dict[str, dict] = {}
    for doc in _render(target):
        kind, name = doc.get("kind"), doc["metadata"]["name"]
        if kind in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
            pods[name] = doc["spec"]["template"]["spec"]
        elif kind == "CronJob":
            pods[name] = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        elif kind == "Pod":
            pods[name] = doc["spec"]
    return pods


def _containers(pod: dict) -> list[tuple[str, dict]]:
    return [(kind, c) for kind in ("initContainers", "containers") for c in pod.get(kind) or []]


def _effective(pod: dict, container: dict, key: str):
    own = container.get("securityContext") or {}
    return own[key] if key in own else (pod.get("securityContext") or {}).get(key)


# ── the base: application pods ──────────────────────────────────────────────


def test_every_application_workload_is_in_the_base() -> None:
    assert set(APP_WORKLOADS) <= set(_pods("base")), sorted(set(APP_WORKLOADS) - set(_pods("base")))


@pytest.mark.parametrize("name", sorted(APP_WORKLOADS))
def test_the_shared_settings_sit_at_pod_level(name: str) -> None:
    context = _pods("base")[name].get("securityContext") or {}
    assert context.get("runAsNonRoot") is True, f"{name}: no pod-level runAsNonRoot"
    assert context.get("seccompProfile") == {"type": "RuntimeDefault"}, f"{name}: no pod-level seccomp profile"
    # A fixed fsGroup is refused by OpenShift's restricted SCC, and every
    # writable volume here is an emptyDir (world-writable), so none is set.
    assert "fsGroup" not in context, name


@pytest.mark.parametrize("name", sorted(APP_WORKLOADS))
def test_every_application_container_is_locked_down(name: str) -> None:
    pod = _pods("base")[name]
    for kind, container in _containers(pod):
        where = f"{name} {kind} {container['name']}"
        context = container.get("securityContext") or {}
        assert context.get("allowPrivilegeEscalation") is False, where
        assert context.get("readOnlyRootFilesystem") is True, where
        capabilities = context.get("capabilities") or {}
        assert capabilities.get("drop") == ["ALL"], where
        assert not capabilities.get("add"), where
        assert context.get("privileged") is not True, where
        # Fixed and non-zero: runAsNonRoot cannot verify an image whose user
        # is a name (the backend image's is), and busybox defaults to root.
        assert isinstance(context.get("runAsUser"), int) and context["runAsUser"] > 0, where
        assert _effective(pod, container, "runAsNonRoot") is True, where
        assert _effective(pod, container, "seccompProfile") == {"type": "RuntimeDefault"}, where


@pytest.mark.parametrize("name", WAITS_FOR_REDIS)
def test_the_wait_for_redis_init_container_matches_its_main_container(name: str) -> None:
    pod = _pods("base")[name]
    (init,) = pod["initContainers"]
    (main,) = pod["containers"]
    assert init["name"] == "wait-for-redis"
    assert init.get("securityContext") == main["securityContext"], name


def test_the_backend_has_no_init_container_to_cover() -> None:
    """Positive case for the list above: six pods wait for Redis, the API does not."""
    pods = _pods("base")
    waiting = sorted(name for name, pod in pods.items() if pod.get("initContainers"))
    assert waiting == WAITS_FOR_REDIS


# ── a read-only root needs every written path on a volume ───────────────────


def _writable_mounts(pod: dict, container: dict) -> set[str]:
    volumes = {volume["name"]: volume for volume in pod.get("volumes") or []}
    paths = set()
    for mount in container.get("volumeMounts") or []:
        volume = volumes[mount["name"]]
        if not mount.get("readOnly") and ("emptyDir" in volume or "persistentVolumeClaim" in volume):
            paths.add(mount["mountPath"])
    return paths


def _covered(path: str, mounts: set[str]) -> bool:
    return any(path == mount or path.startswith(mount.rstrip("/") + "/") for mount in mounts)


@pytest.mark.parametrize("name", sorted(WRITABLE))
def test_every_written_path_is_a_writable_volume(name: str) -> None:
    pod = _pods("base")[name]
    (container,) = pod["containers"]
    assert _writable_mounts(pod, container) == WRITABLE[name], name


def test_the_backend_image_writes_only_where_the_pods_mount() -> None:
    """HOME and the metrics directory come from the image; the caches from the
    pod's env and the ConfigMap. Each must land on a mounted volume."""
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    production = dockerfile.split(" AS production", 1)[1]
    assert re.search(r"(?m)^\s+HOME=/tmp \\$", production), "the image's HOME moved: remount it"
    multiproc = re.search(r"PROMETHEUS_MULTIPROC_DIR=(\S+)", production).group(1)
    configmap = next(d for d in _render("base")
                     if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "testlookup-config")
    for name, role in BACKEND_IMAGE_WORKLOADS.items():
        pod = _pods("base")[name]
        (container,) = pod["containers"]
        mounts = _writable_mounts(pod, container)
        assert _covered("/tmp", mounts), name
        if role == "beat":
            continue
        env = {item["name"]: item.get("value") for item in container.get("env") or []}
        assert _covered(multiproc, mounts), name
        assert _covered(env["CHROMA_ONNX_MODEL_DIR"], mounts), name
        assert _covered(configmap["data"]["ML_MODEL_DIR"], mounts), name


@pytest.mark.parametrize("target", TARGETS)
def test_beat_keeps_its_schedule_and_pid_file_under_tmp(target: str) -> None:
    """Beat's default schedule file is ./celerybeat-schedule, in /app."""
    (container,) = _pods(target)["testlookup-beat"]["containers"]
    command = container["command"]
    schedule = [arg.split("=", 1)[1] for arg in command if arg.startswith("--schedule=")]
    schedule += [command[i + 1] for i, arg in enumerate(command) if arg == "-s"]
    assert len(schedule) == 1 and schedule[0].startswith("/tmp/"), (target, command)
    for arg in command:
        if arg.startswith("--pidfile="):
            assert arg.split("=", 1)[1].startswith("/tmp/"), (target, arg)


def test_the_frontend_can_render_or_is_given_its_server_block() -> None:
    """Positive case: /etc/nginx/conf.d is where the entrypoint writes the
    rendered template, so the base gives it an emptyDir by that name. An
    overlay that ships a rendered config swaps the volume's source."""
    pod = _pods("base")["testlookup-frontend"]
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["nginx-conf-d"] == {"name": "nginx-conf-d", "emptyDir": {}}
    (container,) = pod["containers"]
    assert {"name": "nginx-conf-d", "mountPath": "/etc/nginx/conf.d"} in container["volumeMounts"]


# ── every overlay ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("target", TARGETS)
def test_no_container_asks_for_uid_0(target: str) -> None:
    for name, pod in _pods(target).items():
        for kind, container in _containers(pod):
            where = f"{target}: {name} {kind} {container['name']}"
            assert _effective(pod, container, "runAsUser") != 0, where
            if name not in ROOT_BY_IMAGE:
                assert _effective(pod, container, "runAsNonRoot") is not False, where


@pytest.mark.parametrize("target", TARGETS)
def test_the_frontend_keeps_its_base_restrictions_in_every_overlay(target: str) -> None:
    """The two reference installs used to replace the frontend's whole
    securityContext with uid 0, which also dropped ``capabilities.drop``."""
    pod = _pods(target)["testlookup-frontend"]
    (container,) = pod["containers"]
    context = container["securityContext"]
    assert context.get("allowPrivilegeEscalation") is False, target
    assert context.get("readOnlyRootFilesystem") is True, target
    assert (context.get("capabilities") or {}).get("drop") == ["ALL"], target
    assert _effective(pod, container, "runAsNonRoot") is True, target
    # /tmp must still be there after the overlay's own volume patches.
    assert _covered("/tmp", _writable_mounts(pod, container)), target


def _baseline_violations(name: str, pod: dict) -> list[str]:
    found = []
    for field in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(field):
            found.append(f"{name}: {field}")
    for volume in pod.get("volumes") or []:
        if "hostPath" in volume:
            found.append(f"{name}: hostPath volume {volume['name']}")
    pod_context = pod.get("securityContext") or {}
    for sysctl in pod_context.get("sysctls") or []:
        if sysctl["name"] not in BASELINE_SYSCTLS:
            found.append(f"{name}: sysctl {sysctl['name']}")
    for kind, container in _containers(pod):
        where = f"{name} {kind} {container['name']}"
        context = container.get("securityContext") or {}
        if context.get("privileged"):
            found.append(f"{where}: privileged")
        extra = set((context.get("capabilities") or {}).get("add") or []) - BASELINE_CAPABILITIES
        if extra:
            found.append(f"{where}: adds {sorted(extra)}")
        if context.get("procMount") not in (None, "Default"):
            found.append(f"{where}: procMount")
        for port in container.get("ports") or []:
            if port.get("hostPort"):
                found.append(f"{where}: hostPort {port['hostPort']}")
        for scope in (pod_context, context):
            if (scope.get("seccompProfile") or {}).get("type") == "Unconfined":
                found.append(f"{where}: seccomp Unconfined")
            if (scope.get("seLinuxOptions") or {}).get("type") not in (
                None, "", "container_t", "container_init_t", "container_kvm_t", "container_engine_t",
            ):
                found.append(f"{where}: SELinux type")
    return found


def test_the_namespace_enforces_the_baseline_standard() -> None:
    (namespace,) = [doc for doc in _render("base") if doc["kind"] == "Namespace"]
    labels = namespace["metadata"]["labels"]
    for mode in ("enforce", "warn", "audit"):
        assert labels.get(f"pod-security.kubernetes.io/{mode}") == "baseline", mode


@pytest.mark.parametrize("target", TARGETS)
def test_every_rendered_pod_passes_baseline(target: str) -> None:
    """The label refuses a pod that fails it, so a failing pod is an outage."""
    violations = [v for name, pod in _pods(target).items() for v in _baseline_violations(name, pod)]
    assert not violations, violations


def test_the_baseline_check_sees_a_violation() -> None:
    """Self-test of the checker: each kind of violation is reported, and a
    locked-down pod that adds an allowed capability is not."""
    bad = {
        "hostNetwork": True,
        "volumes": [{"name": "host", "hostPath": {"path": "/"}}],
        "containers": [{
            "name": "c", "ports": [{"containerPort": 80, "hostPort": 80}],
            "securityContext": {"privileged": True, "capabilities": {"add": ["SYS_ADMIN", "CHOWN"]},
                                "seccompProfile": {"type": "Unconfined"}},
        }],
    }
    found = _baseline_violations("bad", bad)
    assert len(found) == 6 and any("['SYS_ADMIN']" in item for item in found), found
    good = {"containers": [{"name": "c", "securityContext": {
        "capabilities": {"drop": ["ALL"], "add": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"]}}}]}
    assert _baseline_violations("good", good) == []
