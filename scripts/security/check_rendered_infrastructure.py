"""Read-only policy for normalized Compose and complete Kubernetes renders.

This complements the image/dependency gates and the detailed pod-security
regressions. It neither renders templates nor invokes a container/cluster API.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

import yaml

POLICY_VERSION = "2026-11-27.1"
MAX_BYTES = 20 * 1024 * 1024
RULES = {
    "OPS001": "Operational endpoints require private access.",
    "CTR001": "Host privileges and sensitive host resources are prohibited.",
    "CTR002": "Unconfined security profiles and excess capabilities are prohibited.",
    "APP001": "Application containers require the existing restricted controls.",
    "NET001": "Workload namespaces require default-deny ingress and egress.",
}
OPERATIONAL_PORTS = {3100, 4317, 4318, 5432, 5555, 6379, 8888, 9090, 9093, 9100,
                     11434, 13133, 14268, 14269, 16685, 16686, 27017}
OPERATIONAL_NAMES = {"prometheus", "alertmanager", "jaeger", "jaeger-query", "loki",
                     "grafana", "otel-collector", "opentelemetry-collector-contrib",
                     "flower", "postgres", "mongo", "redis", "valkey", "ollama",
                     "chromadb", "chroma", "minio", "versitygw"}
APP_IMAGES = {"backend", "frontend", "mcp", "testlookup-backend", "testlookup-frontend", "testlookup-mcp"}
PRIVATE_PATHS = ("/metrics", "/debug/pprof", "/pprof")
INTERNAL_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "fc00::/7", "fe80::/10", "::1/128",
))
BASELINE_CAPABILITIES = {"AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL",
                         "MKNOD", "NET_BIND_SERVICE", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"}
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,299}\Z")


class PolicyInputError(ValueError):
    """A bounded, value-free explanation suitable for CI output."""


def _mapping(value: Any) -> dict:
    if not isinstance(value, dict):
        raise PolicyInputError("Expected a rendered mapping.")
    return value


def _list(value: Any) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PolicyInputError("Expected a rendered list.")
    return value


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise PolicyInputError("Invalid resource identifier.")
    return value


def _bounded_tree(value: Any) -> None:
    pending = [(value, 0)]
    seen: set[int] = set()
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 200_000 or depth > 80:
            raise PolicyInputError("Rendered document exceeds structural limits.")
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                raise PolicyInputError("Rendered document must expand aliases.")
            seen.add(id(item))
            children = list(item.values()) if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in children)


def load_render(data: bytes, kind: str) -> Any:
    if not data or len(data) > MAX_BYTES:
        raise PolicyInputError("Rendered input is empty or exceeds 20 MiB.")
    try:
        if kind == "compose":
            value = json.loads(data)
        elif kind == "kubernetes":
            value = [doc for doc in yaml.safe_load_all(data) if doc is not None]
        else:
            raise PolicyInputError("Unsupported render format.")
    except (ValueError, yaml.YAMLError, UnicodeError, RecursionError) as error:
        raise PolicyInputError("Rendered input could not be parsed.") from error
    _bounded_tree(value)
    return value


def _add(found: list[dict], rule: str, resource: str, field: str) -> None:
    finding = {"rule": rule, "resource": resource, "field": field, "message": RULES[rule]}
    if finding not in found:
        found.append(finding)


def _image_name(image: Any) -> str:
    return str(image or "").split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]


def _operational(name: str, image: Any = "") -> bool:
    return _image_name(image) in OPERATIONAL_NAMES or name.removeprefix("testlookup-") in OPERATIONAL_NAMES


def _port(value: Any) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 65535:
        raise PolicyInputError("Invalid rendered port.")
    return int(value)


def _sensitive_host_path(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").lower()
    normalized = str(PurePosixPath(text))
    return (normalized in {"/", "/proc", "/sys", "/dev", "/run", "/var", "/var/run", "/var/lib", "/etc", "/root", "/home",
                            "/bin", "/sbin", "/usr", "/lib", "/lib64", "/boot"}
            or normalized.startswith(("/proc/", "/sys/", "/dev/", "/etc/", "/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/boot/",
                                      "/var/lib/docker", "/run/containerd", "/var/run/containerd", "/var/run/crio", "/run/crio"))
            or re.fullmatch(r"[a-z]:/?", normalized) is not None
            or any(part in normalized for part in ("docker.sock", "containerd.sock", "crio.sock", "pipe/docker_engine")))


def check_compose(document: Any) -> list[dict]:
    services = _mapping(_mapping(document).get("services"))
    if not services:
        raise PolicyInputError("Compose render contains no services.")
    found: list[dict] = []
    for name, volume in _mapping(document.get("volumes", {})).items():
        options = _mapping(_mapping(volume or {}).get("driver_opts", {}))
        if _sensitive_host_path(options.get("device")):
            _add(found, "CTR001", "compose/volume/" + _identifier(name), "driver_opts.device")
    for name, raw in services.items():
        name = _identifier(name)
        service = _mapping(raw)
        resource = f"compose/service/{name}"
        if not service.get("image") and not service.get("build"):
            raise PolicyInputError("Compose service has no image or build.")
        for field in ("privileged", "use_api_socket"):
            if service.get(field):
                _add(found, "CTR001", resource, field)
        for field in ("network_mode", "pid", "ipc", "userns_mode", "uts"):
            if str(service.get(field, "")).split(":", 1)[0] == "host":
                _add(found, "CTR001", resource, field)
        if service.get("devices") or service.get("device_cgroup_rules"):
            _add(found, "CTR001", resource, "devices")
        for option in _list(service.get("security_opt")):
            if "unconfined" in str(option).lower():
                _add(found, "CTR002", resource, "security_opt")
        capabilities = {str(cap).upper().removeprefix("CAP_") for cap in _list(service.get("cap_add"))}
        isolated_chown = (service.get("network_mode") == "none" and service.get("read_only") is True
                          and "ALL" in _list(service.get("cap_drop")) and not service.get("ports")
                          and all(_mapping(v).get("type") == "volume" for v in _list(service.get("volumes"))))
        for cap in sorted(capabilities - ({"CHOWN"} if isolated_chown else set())):
            _add(found, "CTR002", resource + "/capability/" + _identifier(cap), "cap_add")
        for index, raw_volume in enumerate(_list(service.get("volumes"))):
            volume = _mapping(raw_volume)
            target = str(volume.get("target", "")).replace("\\", "/").lower()
            socket_target = any(part in target for part in ("docker.sock", "containerd.sock", "crio.sock", "pipe/docker_engine"))
            if volume.get("type") == "bind" and (_sensitive_host_path(volume.get("source")) or socket_target):
                _add(found, "CTR001", resource + f"/volume/{index}", "volumes")
        for raw_port in _list(service.get("ports")):
            port = _mapping(raw_port)
            target = _port(port.get("target"))
            shared_api = name == "backend" or _image_name(service.get("image")) in {"backend", "testlookup-backend"}
            if not (_operational(name, service.get("image")) or shared_api or target in OPERATIONAL_PORTS):
                continue
            try:
                loopback = ipaddress.ip_address(str(port.get("host_ip", ""))).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                _add(found, "OPS001", resource + f"/port/{target}", "ports.host_ip")
    return found


def _pod(document: dict) -> dict | None:
    kind, spec = document.get("kind"), _mapping(document.get("spec", {}))
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        return _mapping(_mapping(_mapping(spec.get("jobTemplate")).get("spec")).get("template"))
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet", "ReplicationController"}:
        return _mapping(spec.get("template"))
    if isinstance(spec.get("template"), dict) and spec["template"].get("spec", {}).get("containers"):
        raise PolicyInputError("Unsupported pod workload kind in rendered profile.")
    return None


def _matches(selector: dict, labels: dict) -> bool:
    if any(labels.get(k) != v for k, v in _mapping(selector.get("matchLabels", {})).items()):
        return False
    for requirement in _list(selector.get("matchExpressions")):
        key, operator = requirement.get("key"), requirement.get("operator")
        values, actual = requirement.get("values", []), labels.get(requirement.get("key"))
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
            raise PolicyInputError("Unsupported network-policy selector.")
        if ((operator == "In" and actual not in values) or (operator == "NotIn" and actual in values)
                or (operator == "Exists" and key not in labels) or (operator == "DoesNotExist" and key in labels)):
            return False
    return True


def _selected_pods(service: dict, pods: list[tuple[str, str, dict, dict]]) -> list[dict]:
    selector = _mapping(service.get("spec", {}).get("selector", {}))
    if not selector:
        return []
    namespace = service["metadata"].get("namespace", "default")
    return [pod for _, ns, pod, labels in pods
            if ns == namespace and all(labels.get(k) == v for k, v in selector.items())]


def _backend_service(service: dict, pods: list[tuple[str, str, dict, dict]]) -> bool:
    name = service["metadata"]["name"]
    selector = service.get("spec", {}).get("selector", {})
    return (name in {"testlookup-backend", "testlookup-backend-diagnostics"} or selector.get("app") == "testlookup-backend"
            or any(_image_name(c.get("image")) in {"backend", "testlookup-backend"}
                   for pod in _selected_pods(service, pods) for c in pod.get("containers", [])))


def _service_operational(service: dict, pods: list[tuple[str, str, dict, dict]], port_ref: Any = None) -> bool:
    name = service["metadata"]["name"]
    selected = _selected_pods(service, pods)
    if _operational(name) or any(_operational("", c.get("image")) for pod in selected for c in pod.get("containers", [])):
        return True
    ports = [p for p in _list(service.get("spec", {}).get("ports"))
             if port_ref is None or port_ref in (p.get("name"), p.get("port"), p.get("targetPort"))]
    if not ports:
        raise PolicyInputError("Ingress/Route port is absent from the selected Service.")
    for port in ports:
        target = port.get("targetPort", port.get("port"))
        if port.get("port") in OPERATIONAL_PORTS or target in OPERATIONAL_PORTS:
            return True
        if isinstance(target, str):
            for pod in selected:
                for container in pod.get("containers", []):
                    if any(p.get("name") == target and p.get("containerPort") in OPERATIONAL_PORTS
                           for p in container.get("ports", [])):
                        return True
    return False


def _private_route(path: Any, path_type: str) -> bool:
    if not isinstance(path, str) or not path.startswith("/"):
        return True
    if path_type == "Exact":
        return any(path.rstrip("/") == private or path.startswith(private + "/") for private in PRIVATE_PATHS)
    base = path.rstrip("/")
    if path_type == "RoutePrefix":
        return any(private.startswith(base) or base.startswith(private + "/") for private in PRIVATE_PATHS)
    if path_type == "ImplementationSpecific":
        # Controller-specific regular-expression/rewrite semantics require an
        # exact site review rather than an optimistic prefix interpretation.
        return True
    return any(not base or private == base or private.startswith(base + "/") or base.startswith(private + "/") for private in PRIVATE_PATHS)


def _external_peer(peer: dict) -> bool:
    if not peer:
        return True
    if "ipBlock" not in peer:
        return False
    try:
        network = ipaddress.ip_network(peer["ipBlock"]["cidr"], strict=False)
    except (ValueError, KeyError, TypeError) as error:
        raise PolicyInputError("NetworkPolicy ipBlock is not a valid CIDR.") from error
    return not any(network.version == internal.version and network.subnet_of(internal) for internal in INTERNAL_NETWORKS)


def check_kubernetes(documents: Any) -> list[dict]:
    if not isinstance(documents, list) or not documents:
        raise PolicyInputError("Kubernetes render contains no resources.")
    docs: list[dict] = []
    for item in documents:
        item = _mapping(item)
        if item.get("kind") == "List":
            docs.extend(_mapping(child) for child in _list(item.get("items")))
        else:
            docs.append(item)
    found: list[dict] = []
    pods: list[tuple[str, str, dict, dict]] = []
    services: dict[tuple[str, str], dict] = {}
    policies: list[dict] = []
    for doc in docs:
        metadata = _mapping(doc.get("metadata"))
        name = _identifier(metadata.get("name"))
        namespace = _identifier(metadata.get("namespace", "default"))
        kind = _identifier(doc.get("kind"))
        resource = f"kubernetes/{kind}/{namespace}/{name}"
        template = _pod(doc)
        if template is not None:
            pod = template if kind == "Pod" else _mapping(template.get("spec"))
            labels = metadata.get("labels", {}) if kind == "Pod" else template.get("metadata", {}).get("labels", {})
            pods.append((resource, namespace, pod, labels))
        if kind == "Service":
            services[(namespace, name)] = doc
            spec = _mapping(doc.get("spec"))
        if kind == "NetworkPolicy":
            policies.append(doc)
    if not pods:
        raise PolicyInputError("Complete Kubernetes application render contains no pod workloads.")
    for (_, name), service in services.items():
        spec, metadata = service["spec"], service["metadata"]
        if spec.get("type") in {"NodePort", "LoadBalancer"} or spec.get("externalIPs"):
            for port in _list(spec.get("ports")):
                if _backend_service(service, pods) or _service_operational(service, pods, port.get("port")):
                    resource = f"kubernetes/Service/{metadata.get('namespace', 'default')}/{name}/port/{_port(port.get('port'))}"
                    _add(found, "OPS001", resource, "spec.type/externalIPs")
    for resource, namespace, pod, _ in pods:
        for field in ("hostNetwork", "hostPID", "hostIPC"):
            if pod.get(field):
                _add(found, "CTR001", resource, "spec." + field)
        for volume in _list(pod.get("volumes")):
            if "hostPath" in volume:
                _add(found, "CTR001", resource + "/volume/" + _identifier(volume.get("name")), "hostPath")
        containers = [c for key in ("containers", "initContainers", "ephemeralContainers") for c in _list(pod.get(key))]
        if not pod.get("containers"):
            raise PolicyInputError("Pod workload contains no containers.")
        application = any(_image_name(c.get("image")) in APP_IMAGES for c in containers)
        for raw in containers:
            container = _mapping(raw)
            child = resource + "/container/" + _identifier(container.get("name"))
            context = _mapping(container.get("securityContext", {}))
            effective = {**_mapping(pod.get("securityContext", {})), **context}
            if context.get("privileged") or effective.get("runAsUser") == 0:
                _add(found, "CTR001", child, "securityContext.privileged/runAsUser")
            if any(port.get("hostPort") for port in _list(container.get("ports"))):
                _add(found, "CTR001", child, "ports.hostPort")
            capabilities = _mapping(context.get("capabilities", {}))
            extra = set(capabilities.get("add", [])) - (set() if application else BASELINE_CAPABILITIES)
            for cap in sorted(extra):
                _add(found, "CTR002", child + "/capability/" + _identifier(cap), "capabilities.add")
            if effective.get("seccompProfile", {}).get("type") == "Unconfined" or context.get("procMount") not in {None, "Default"}:
                _add(found, "CTR002", child, "securityContext.seccompProfile/procMount")
            runtime_filesystem = "/Job/" not in resource and "/CronJob/" not in resource
            if application and (context.get("allowPrivilegeEscalation") is not False or (runtime_filesystem and context.get("readOnlyRootFilesystem") is not True)
                                or "ALL" not in capabilities.get("drop", []) or effective.get("runAsNonRoot") is not True
                                or effective.get("seccompProfile", {}).get("type") not in {"RuntimeDefault", "Localhost"}):
                _add(found, "APP001", child, "securityContext")
    for namespace in {ns for _, ns, _, _ in pods}:
        denied: set[str] = set()
        for policy in policies:
            spec = policy["spec"]
            if policy["metadata"].get("namespace", "default") == namespace and spec.get("podSelector") == {}:
                for direction in ("Ingress", "Egress"):
                    defaults = ["Ingress"] + (["Egress"] if "egress" in spec else [])
                    if direction in spec.get("policyTypes", defaults) and not spec.get(direction.lower()):
                        denied.add(direction)
        if denied != {"Ingress", "Egress"}:
            _add(found, "NET001", f"kubernetes/Namespace/{namespace}", "default-deny")
    for network_policy in policies:
        metadata, spec = network_policy["metadata"], network_policy["spec"]
        namespace = metadata.get("namespace", "default")
        selected = [pod for _, ns, pod, labels in pods
                    if ns == namespace and _matches(_mapping(spec.get("podSelector", {})), labels)]
        for index, ingress in enumerate(_list(spec.get("ingress"))):
            peers = _list(ingress.get("from"))
            unrestricted = not peers or any(_external_peer(_mapping(peer)) for peer in peers)
            if not unrestricted:
                continue
            for pod in selected:
                for container in pod.get("containers", []):
                    ports = _list(ingress.get("ports"))
                    operational = _operational("", container.get("image"))
                    operational_ports = {p.get("containerPort") for p in container.get("ports", []) if p.get("containerPort") in OPERATIONAL_PORTS}
                    if (not ports and (operational or operational_ports)) or any(
                        operational or p.get("port") in operational_ports
                        or any(c.get("name") == p.get("port") and c.get("containerPort") in OPERATIONAL_PORTS for c in container.get("ports", []))
                        for p in ports
                    ):
                        resource = f"kubernetes/NetworkPolicy/{namespace}/{metadata['name']}/ingress/{index}"
                        _add(found, "OPS001", resource, "spec.ingress.from")
    for doc in docs:
        kind, metadata = doc["kind"], doc["metadata"]
        namespace = metadata.get("namespace", "default")
        resource = f"kubernetes/{kind}/{namespace}/{metadata['name']}"
        routes = []
        if kind == "Ingress":
            spec = _mapping(doc.get("spec"))
            if spec.get("defaultBackend"):
                routes.append(("/", "Prefix", spec["defaultBackend"].get("service", {})))
            for rule in _list(spec.get("rules")):
                for path in _list(rule.get("http", {}).get("paths")):
                    routes.append((path.get("path", "/"), path.get("pathType", "Prefix"), path.get("backend", {}).get("service", {})))
        elif kind == "Route":
            spec = _mapping(doc.get("spec"))
            routes.append((spec.get("path", "/"), "RoutePrefix", {"name": spec.get("to", {}).get("name"),
                                                                      "port": {"name": spec.get("port", {}).get("targetPort")}}))
        for path, path_type, backend in routes:
            name = _identifier(backend.get("name"))
            service = services.get((namespace, name))
            if service is None:
                raise PolicyInputError("Ingress/Route target is absent from the complete render.")
            port = backend.get("port", {})
            port_ref = port.get("number", port.get("name"))
            if _service_operational(service, pods, port_ref) or (_backend_service(service, pods) and _private_route(path, path_type)):
                signature = hashlib.sha256(json.dumps([path, path_type, port_ref]).encode()).hexdigest()[:16]
                _add(found, "OPS001", resource + "/service/" + name + "/route/" + signature, "spec.backend/path")
    return found


def apply_exceptions(findings: list[dict], document: Any, profile: str, today: date | None = None) -> tuple[list[dict], list[dict]]:
    today = today or datetime.now(timezone.utc).date()
    document = _mapping(document)
    if set(document) != {"version", "exceptions"} or document["version"] != 1:
        raise PolicyInputError("Unsupported exception schema.")
    accepted: dict[tuple[str, str, str], dict] = {}
    seen: set[tuple[str, str, str, str]] = set()
    ids: set[str] = set()
    for entry in _list(document["exceptions"]):
        entry = _mapping(entry)
        if set(entry) != {"id", "rule", "profile", "resource", "field", "owner", "reason", "expires"}:
            raise PolicyInputError("Exception requires exact id/rule/profile/resource/owner/reason/expiry.")
        for key in ("id", "profile", "resource", "field", "owner"):
            _identifier(entry[key])
        if entry["id"] in ids:
            raise PolicyInputError("Duplicate exception identifier.")
        ids.add(entry["id"])
        if entry["rule"] not in RULES or entry["owner"].upper() in {"UNASSIGNED", "TODO", "REPLACE_OWNER"}:
            raise PolicyInputError("Exception rule or accountable owner is invalid.")
        if not isinstance(entry["reason"], str) or not 20 <= len(entry["reason"]) <= 500 or any(c in entry["reason"] for c in "\r\n"):
            raise PolicyInputError("Exception requires a bounded single-line rationale.")
        try:
            expires = date.fromisoformat(str(entry["expires"]))
        except ValueError as error:
            raise PolicyInputError("Exception expiry must be an ISO date.") from error
        if not today <= expires <= today + timedelta(days=90):
            raise PolicyInputError("Exception is expired or exceeds the 90-day review window.")
        key = (entry["profile"], entry["rule"], entry["resource"], entry["field"])
        if key in seen:
            raise PolicyInputError("Duplicate exception scope.")
        seen.add(key)
        if entry["profile"] == profile:
            accepted[(entry["rule"], entry["resource"], entry["field"])] = entry
    remaining, waived = [], []
    for finding in findings:
        entry = accepted.pop((finding["rule"], finding["resource"], finding["field"]), None)
        if entry:
            waived.append({"id": entry["id"], "owner": entry["owner"], "expires": str(entry["expires"]), **finding})
        else:
            remaining.append(finding)
    if accepted:
        raise PolicyInputError("Exception no longer matches a finding in its exact profile.")
    return remaining, waived


def evaluate(data: bytes, kind: str, profile: str, *, exceptions: Any = None, source_sha: str | None = None) -> dict:
    _identifier(profile)
    if source_sha is not None and re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise PolicyInputError("Source SHA must be a complete lowercase Git object ID.")
    document = load_render(data, kind)
    findings = check_compose(document) if kind == "compose" else check_kubernetes(document)
    waived = []
    if exceptions is not None:
        findings, waived = apply_exceptions(findings, exceptions, profile)
    return {"schema_version": 1, "policy_version": POLICY_VERSION, "policy_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "profile": profile, "format": kind, "source_sha": source_sha, "render_sha256": hashlib.sha256(data).hexdigest(),
            "passed": not findings, "findings": findings, "exceptions": waived}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("compose", "kubernetes"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source-sha")
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.input.stat().st_size > MAX_BYTES or (args.exceptions and args.exceptions.stat().st_size > MAX_BYTES):
            raise PolicyInputError("Input exceeds 20 MiB.")
        data = args.input.read_bytes()
        exceptions = json.loads(args.exceptions.read_text(encoding="utf-8")) if args.exceptions else None
        report = evaluate(data, args.format, args.profile, exceptions=exceptions, source_sha=args.source_sha)
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0 if report["passed"] else 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        message = str(error) if isinstance(error, PolicyInputError) else "Input or output could not be processed."
        failure = json.dumps({"schema_version": 1, "passed": False, "error": message})
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(failure + "\n", encoding="utf-8")
            except OSError:
                pass
        print(failure, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
