"""Defensive policy fixtures plus source-preserving render contracts."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("check_rendered_infrastructure", DIRECTORY / "check_rendered_infrastructure.py")
policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)
import render_reference_profiles as renderer  # noqa: E402


def compose():
    return {"services": {
        "postgres": {"image": "postgres:16-alpine", "ports": [{"target": 5432, "published": "5433", "host_ip": "127.0.0.1"}]},
        "backend": {"image": "registry.invalid/testlookup/backend:fixture", "environment": {"PASSWORD": "private-fixture-text"}},
        "telemetry-volume-init": {"image": "busybox:1.36", "network_mode": "none", "read_only": True,
                                  "cap_drop": ["ALL"], "cap_add": ["CHOWN"],
                                  "volumes": [{"type": "volume", "source": "data", "target": "/data"}]},
    }}


def kubernetes():
    return [
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "testlookup-backend", "namespace": "app"},
         "spec": {"template": {"metadata": {"labels": {"app": "testlookup-backend"}}, "spec": {
             "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
             "containers": [{"name": "api", "image": "registry.invalid/testlookup/backend:fixture",
                             "ports": [{"containerPort": 8000, "name": "http"}, {"containerPort": 9100, "name": "metrics"}],
                             "securityContext": {"runAsUser": 1000, "allowPrivilegeEscalation": False,
                                                 "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}}]}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "testlookup-backend", "namespace": "app"},
         "spec": {"selector": {"app": "testlookup-backend"}, "ports": [{"name": "http", "port": 8000, "targetPort": "http"}]}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "renamed-scraper", "namespace": "app"},
         "spec": {"selector": {"app": "testlookup-backend"}, "ports": [{"name": "observation", "port": 80, "targetPort": "metrics"}]}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "deny", "namespace": "app"},
         "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}},
    ]


def test_healthy_normalized_inputs_and_in_memory_controls():
    assert policy.check_compose(compose()) == []
    assert policy.check_kubernetes(kubernetes()) == []
    assert all(renderer.defensive_controls(json.dumps(compose()).encode(), "compose").values())
    assert all(renderer.defensive_controls(yaml.safe_dump_all(kubernetes()).encode(), "kubernetes").values())


@pytest.mark.parametrize("host", [None, "0.0.0.0", "::", "192.0.2.10"])
def test_operational_publication_requires_exact_loopback(host):
    document = compose()
    port = document["services"]["postgres"]["ports"][0]
    port.pop("host_ip")
    if host is not None:
        port["host_ip"] = host
    found = policy.check_compose(document)
    assert [f["rule"] for f in found] == ["OPS001"]
    port["host_ip"] = "::1"
    assert policy.check_compose(document) == []


@pytest.mark.parametrize("field,value,rule", [
    ("privileged", True, "CTR001"), ("network_mode", "host", "CTR001"), ("pid", "host", "CTR001"),
    ("ipc", "host", "CTR001"), ("use_api_socket", True, "CTR001"),
    ("devices", ["/dev/kvm:/dev/kvm"], "CTR001"), ("security_opt", ["seccomp=unconfined"], "CTR002"),
    ("cap_add", ["SYS_ADMIN"], "CTR002"),
])
def test_compose_privileges_are_detected(field, value, rule):
    document = compose()
    document["services"]["backend"][field] = value
    assert any(f["rule"] == rule for f in policy.check_compose(document))


@pytest.mark.parametrize("source", ["/", "/proc", "/sys/fs", "/etc/sudoers.d", "/usr/local/bin", "/var/run", "/run", "/var/lib/docker/volumes", "/var/run/docker.sock", "C:\\", "//./pipe/docker_engine"])
def test_sensitive_bind_mounts_are_detected(source):
    document = compose()
    document["services"]["backend"]["volumes"] = [{"type": "bind", "source": source, "target": "/host"}]
    assert any(f["rule"] == "CTR001" for f in policy.check_compose(document))


def test_chown_initializer_is_only_allowed_while_isolated():
    document = compose()
    document["services"]["telemetry-volume-init"].pop("network_mode")
    assert any(f["rule"] == "CTR002" for f in policy.check_compose(document))


def test_named_volume_cannot_hide_a_sensitive_host_bind():
    document = compose()
    document["volumes"] = {"host": {"driver": "local", "driver_opts": {"type": "none", "o": "bind", "device": "/etc/sudoers.d"}}}
    assert any(f["rule"] == "CTR001" and f["resource"] == "compose/volume/host" for f in policy.check_compose(document))


def test_container_configuration_target_does_not_become_a_host_system_mount():
    document = compose()
    document["services"]["backend"]["volumes"] = [{"type": "bind", "source": "/workspace/config.yml", "target": "/etc/application/config.yml", "read_only": True}]
    assert policy.check_compose(document) == []
    document["services"]["backend"]["volumes"][0]["target"] = "/var/run/docker.sock"
    assert any(f["rule"] == "CTR001" for f in policy.check_compose(document))


def test_direct_public_api_listener_exposes_its_shared_operational_paths():
    document = compose()
    document["services"]["backend"]["ports"] = [{"target": 8000, "published": "8000", "host_ip": "0.0.0.0"}]
    assert any(f["rule"] == "OPS001" and "/backend/" in f["resource"] for f in policy.check_compose(document))


@pytest.mark.parametrize("mutation,rule", [
    (lambda pod, c: pod.update(hostNetwork=True), "CTR001"),
    (lambda pod, c: pod.update(volumes=[{"name": "host", "hostPath": {"path": "/"}}]), "CTR001"),
    (lambda pod, c: c["securityContext"].update(privileged=True), "CTR001"),
    (lambda pod, c: c["securityContext"].update(runAsUser=0), "CTR001"),
    (lambda pod, c: c["ports"][0].update(hostPort=8000), "CTR001"),
    (lambda pod, c: c["securityContext"]["capabilities"].update(add=["SYS_ADMIN"]), "CTR002"),
    (lambda pod, c: c["securityContext"].update(seccompProfile={"type": "Unconfined"}), "CTR002"),
    (lambda pod, c: c["securityContext"].pop("readOnlyRootFilesystem"), "APP001"),
    (lambda pod, c: c["securityContext"].pop("allowPrivilegeEscalation"), "APP001"),
])
def test_kubernetes_unsafe_or_missing_controls_fail(mutation, rule):
    document = kubernetes()
    pod = document[0]["spec"]["template"]["spec"]
    mutation(pod, pod["containers"][0])
    assert any(f["rule"] == rule for f in policy.check_kubernetes(document))


def test_open_shift_assigned_uid_preserves_nonroot_contract():
    document = kubernetes()
    document[0]["spec"]["template"]["spec"]["containers"][0]["securityContext"].pop("runAsUser")
    assert policy.check_kubernetes(document) == []


@pytest.mark.parametrize("field,value", [("type", "NodePort"), ("type", "LoadBalancer"), ("externalIPs", ["192.0.2.10"])])
def test_public_service_follows_named_metrics_port_after_service_rename(field, value):
    document = kubernetes()
    document[2]["spec"][field] = value
    assert any(f["rule"] == "OPS001" and "renamed-scraper" in f["resource"] for f in policy.check_kubernetes(document))


@pytest.mark.parametrize("kind,path,target,expected", [
    ("Ingress", "/metrics", "testlookup-backend", True), ("Ingress", "/", "testlookup-backend", True),
    ("Ingress", "/api/", "testlookup-backend", False), ("Ingress", "/", "renamed-scraper", True),
    ("Route", "/met", "testlookup-backend", True), ("Route", "/api/", "testlookup-backend", False),
])
def test_routing_resolves_actual_service_and_prefix_semantics(kind, path, target, expected):
    document = kubernetes()
    spec = ({"rules": [{"http": {"paths": [{"path": path, "pathType": "Prefix", "backend": {"service": {"name": target, "port": {"number": 8000 if target == "testlookup-backend" else 80}}}}]}}]}
            if kind == "Ingress" else {"path": path, "to": {"kind": "Service", "name": target}, "port": {"targetPort": "http"}})
    document.append({"apiVersion": "networking.k8s.io/v1", "kind": kind, "metadata": {"name": "public", "namespace": "app"}, "spec": spec})
    assert any(f["rule"] == "OPS001" for f in policy.check_kubernetes(document)) is expected


def test_deleted_default_deny_and_additive_allow_all_are_detected():
    document = kubernetes()
    document.pop()
    assert any(f["rule"] == "NET001" for f in policy.check_kubernetes(document))
    document = kubernetes()
    document.append({"kind": "NetworkPolicy", "metadata": {"name": "too-wide", "namespace": "app"},
                     "spec": {"podSelector": {"matchLabels": {"app": "testlookup-backend"}},
                              "ingress": [{"ports": [{"port": "metrics"}], "from": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}]}})
    assert any(f["rule"] == "OPS001" and "too-wide" in f["resource"] for f in policy.check_kubernetes(document))


@pytest.mark.parametrize("path_type,path", [("Exact", "/debug/pprof/profile"), ("Prefix", "/debug/pprof/profile"),
                                          ("Exact", "/pprof/heap"), ("Prefix", "/pprof/heap")])
def test_private_debug_subtrees_remain_private(path_type, path):
    assert policy._private_route(path, path_type)


@pytest.mark.parametrize("cidrs,private", [(["8.8.8.0/24"], False), (["0.0.0.0/1", "128.0.0.0/1"], False),
                                         (["2001:4860::/32"], False), (["10.12.0.0/16", "fc00::/8"], True)])
def test_operational_ingress_cannot_hide_public_access_in_narrower_cidrs(cidrs, private):
    document = kubernetes()
    document.append({"kind": "NetworkPolicy", "metadata": {"name": "source-ranges", "namespace": "app"},
                     "spec": {"podSelector": {"matchLabels": {"app": "testlookup-backend"}},
                              "ingress": [{"ports": [{"port": "metrics"}], "from": [{"ipBlock": {"cidr": c}} for c in cidrs]}]}})
    assert any(f["rule"] == "OPS001" for f in policy.check_kubernetes(document)) is not private


def exception(finding):
    return {"version": 1, "exceptions": [{"id": "site-private-lan", "profile": "site-release", "rule": finding["rule"],
            "resource": finding["resource"], "field": finding["field"], "owner": "platform-team",
            "reason": "Controlled private listener behind the reviewed site access boundary.", "expires": "2030-01-08"}]}


def test_exception_is_exact_and_cannot_waive_another_field_or_profile():
    finding = {"rule": "CTR001", "resource": "compose/service/backend", "field": "privileged", "message": policy.RULES["CTR001"]}
    waiver = exception(finding)
    remaining, waived = policy.apply_exceptions([finding], waiver, "site-release", date(2030, 1, 1))
    assert remaining == [] and waived[0]["owner"] == "platform-team"
    other = {**finding, "field": "network_mode"}
    remaining, _ = policy.apply_exceptions([finding, other], waiver, "site-release", date(2030, 1, 1))
    assert remaining == [other]
    remaining, waived = policy.apply_exceptions([finding], waiver, "another-profile", date(2030, 1, 1))
    assert remaining == [finding] and waived == []


@pytest.mark.parametrize("key,value", [("owner", ""), ("owner", "UNASSIGNED"), ("profile", "*"), ("resource", "compose/*"),
                                      ("reason", ""), ("expires", "2029-12-31"), ("expires", "2030-04-02")])
def test_invalid_or_expired_exceptions_fail_closed(key, value):
    finding = {"rule": "OPS001", "resource": "compose/service/postgres/port/5432", "field": "ports.host_ip"}
    waiver = exception(finding)
    waiver["exceptions"][0][key] = value
    with pytest.raises(policy.PolicyInputError):
        policy.apply_exceptions([finding], waiver, "site-release", date(2030, 1, 1))


def test_stale_duplicate_and_wrong_schema_exceptions_fail_closed():
    finding = {"rule": "OPS001", "resource": "compose/service/postgres/port/5432", "field": "ports.host_ip"}
    waiver = exception(finding)
    with pytest.raises(policy.PolicyInputError):
        policy.apply_exceptions([], waiver, "site-release", date(2030, 1, 1))
    waiver["exceptions"].append(deepcopy(waiver["exceptions"][0]))
    with pytest.raises(policy.PolicyInputError):
        policy.apply_exceptions([finding], waiver, "site-release", date(2030, 1, 1))


@pytest.mark.parametrize("data,kind", [(b"{}", "compose"), (b"[]", "kubernetes"), (b"not-json private-fixture-text", "compose"),
                                      (b"a: &a {b: *a}", "kubernetes")])
def test_empty_malformed_or_recursive_input_cannot_pass(data, kind):
    with pytest.raises(policy.PolicyInputError):
        policy.evaluate(data, kind, "fixture")


def test_evidence_binds_input_and_policy_without_disclosing_environment_values(tmp_path, capsys):
    path = tmp_path / "render.json"
    path.write_text(json.dumps(compose()), encoding="utf-8")
    output = tmp_path / "evidence.json"
    assert policy.main(["--format", "compose", "--input", str(path), "--profile", "fixture", "--source-sha", "a" * 40, "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["source_sha"] == "a" * 40 and len(report["render_sha256"]) == 64 and len(report["policy_sha256"]) == 64
    assert "private-fixture-text" not in output.read_text() + capsys.readouterr().out
    path.write_text("private-fixture-text", encoding="utf-8")
    assert policy.main(["--format", "compose", "--input", str(path), "--profile", "fixture", "--output", str(output)]) == 2
    assert json.loads(output.read_text())["passed"] is False
    assert "private-fixture-text" not in capsys.readouterr().err


def test_reference_renderer_uses_only_local_config_commands_and_synthetic_environment(monkeypatch):
    calls = []
    monkeypatch.setenv("COMPOSE_FILE", "untrusted-file.yml")
    monkeypatch.setenv("APP_SECRET_KEY", "private-fixture-text")
    monkeypatch.setattr(renderer.shutil, "which", lambda name: name)
    def run(command, root, environment):
        calls.append(command)
        assert "APP_SECRET_KEY" not in environment and "COMPOSE_FILE" not in environment
        if command[0] == "docker":
            assert "config" in command and "--no-env-resolution" in command
            envfile = Path(command[command.index("--env-file") + 1])
            assert envfile.name == "synthetic.env" and "private-fixture-text" not in envfile.read_text()
            return json.dumps(compose()).encode()
        assert command[:2] == ["kustomize", "build"]
        return yaml.safe_dump_all(kubernetes()).encode()
    monkeypatch.setattr(renderer, "_run", run)
    report = renderer.run_matrix(renderer.ROOT, profiles=["compose-release", "kubernetes-prod"], source_sha="b" * 40)
    assert report["passed"] and len(calls) == 2
    assert all(all(p["defensive_controls"].values()) for p in report["profiles"])


def test_absent_renderer_never_turns_into_a_skipped_gate(monkeypatch):
    monkeypatch.setattr(renderer.shutil, "which", lambda name: None)
    with pytest.raises(policy.PolicyInputError):
        renderer.run_matrix(renderer.ROOT, profiles=["compose-release"])


def test_seeded_controls_detect_a_disabled_detector(monkeypatch):
    monkeypatch.setattr(renderer.policy, "check_compose", lambda document: [])
    monkeypatch.setattr(renderer.shutil, "which", lambda name: name)
    monkeypatch.setattr(renderer, "_run", lambda *args: json.dumps(compose()).encode())
    report = renderer.run_matrix(renderer.ROOT, profiles=["compose-release"])
    assert report["passed"] is False
    assert not any(report["profiles"][0]["defensive_controls"].values())
