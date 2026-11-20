"""On K3s only the Traefik pods reach the frontend and the backend, on their ports.

The rule meant for Traefik used to admit EVERY pod in ``kube-system`` to the
frontend, the backend and the MCP server on every port (security audit
2026-11-18). That included the backend's unauthenticated ``/metrics`` and the
MCP port, which the same overlay keeps off the network by deleting its Ingress.

The rule now: the ``from`` side names the Traefik pods by label, the ports are
listed (the frontend's 8080, the backend's 8000) and the MCP pods are not selected. Because a
wrong label would cut the whole site off, the deploy script checks that the
cluster's Traefik pods carry the label before it builds or applies anything.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.shell_utils import bash_environment

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
POLICY_FILE = REPO / "k8s" / "overlays" / "k3s" / "netpol-k3s.yaml"
DEPLOY = REPO / "k3ssetup" / "deploy-k3s.sh"
SERVICES = REPO / "k8s" / "base" / "services.yaml"
TRAEFIK_LABEL = {"app.kubernetes.io/name": "traefik"}


def _policy() -> dict:
    docs = [d for d in yaml.safe_load_all(POLICY_FILE.read_text(encoding="utf-8")) if d]
    # One policy, under the name the cluster already has: `kubectl apply` does
    # not delete, so a renamed policy would leave the old wide one in place.
    [policy] = [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "allow-traefik-ingress"]
    return policy


def _selected_apps(policy: dict) -> set[str]:
    selector = policy["spec"]["podSelector"]
    apps = set()
    if "app" in (selector.get("matchLabels") or {}):
        apps.add(selector["matchLabels"]["app"])
    for expression in selector.get("matchExpressions") or []:
        assert expression["key"] == "app" and expression["operator"] == "In", expression
        apps.update(expression["values"])
    assert apps, "an empty podSelector selects every pod in the namespace"
    return apps


def test_only_traefik_pods_are_admitted():
    policy = _policy()
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    rules = policy["spec"]["ingress"]
    assert rules
    for rule in rules:
        assert rule.get("from"), "a rule without `from` admits every source"
        for peer in rule["from"]:
            assert peer.get("namespaceSelector") == {
                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
            }, peer
            # Both selectors in ONE peer: pods with this label IN that namespace.
            assert (peer.get("podSelector") or {}).get("matchLabels") == TRAEFIK_LABEL, (
                "without a podSelector every pod in kube-system is admitted"
            )
            assert "ipBlock" not in peer


def test_the_ports_are_listed_and_the_mcp_server_is_not_selected():
    policy = _policy()
    assert "testlookup-mcp" not in _selected_apps(policy), (
        "the overlay deletes the MCP Ingress to keep it off the network; "
        "the policy must not open it to kube-system either"
    )
    for rule in policy["spec"]["ingress"]:
        assert rule.get("ports"), "a rule without `ports` admits every port (the backend serves /metrics)"
        for port in rule["ports"]:
            assert port.get("protocol", "TCP") == "TCP" and isinstance(port["port"], int), port


def _container_ports() -> dict[str, dict]:
    """app label -> {port name or number: container port}, from the base Deployments."""
    found: dict[str, dict] = {}
    for path in sorted((REPO / "k8s" / "base").glob("*deployment*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not doc or doc.get("kind") != "Deployment":
                continue
            template = doc["spec"]["template"]
            ports = found.setdefault(template["metadata"]["labels"]["app"], {})
            for container in template["spec"]["containers"]:
                for port in container.get("ports") or []:
                    ports[port["containerPort"]] = port["containerPort"]
                    if "name" in port:
                        ports[port["name"]] = port["containerPort"]
    return found


def test_the_site_still_gets_through():
    """Positive case: every Service the K3s Ingress routes to stays reachable,
    on the port its pods listen on (a NetworkPolicy sees the pod's port, not
    the Service's), and no other port is opened."""
    policy = _policy()
    assert _selected_apps(policy) == {"testlookup-frontend", "testlookup-backend"}
    ports = {port["port"] for rule in policy["spec"]["ingress"] for port in rule["ports"]}
    services = {
        d["metadata"]["name"]: d
        for d in yaml.safe_load_all(SERVICES.read_text(encoding="utf-8"))
        if d and d["kind"] == "Service"
    }
    routed = set()
    ingress = REPO / "k8s" / "overlays" / "k3s" / "ingress-traefik.yaml"
    for doc in yaml.safe_load_all(ingress.read_text(encoding="utf-8")):
        for rule in ((doc or {}).get("spec") or {}).get("rules") or []:
            routed.update(path["backend"]["service"]["name"] for path in rule["http"]["paths"])
    assert routed, "no routes found: this check would prove nothing"
    container_ports = _container_ports()
    needed = set()
    for name in routed:
        spec = services[name]["spec"]
        assert spec["selector"]["app"] in _selected_apps(policy), name
        for port in spec["ports"]:
            needed.add(container_ports[spec["selector"]["app"]][port["targetPort"]])
    assert len(needed) == 2, needed
    assert ports == needed, (
        f"the Ingress needs pod ports {sorted(needed)}, the policy opens {sorted(ports)}"
    )


def _find_bash() -> str | None:
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    return None if found and "system32" in found.lower() else found


BASH = _find_bash()


def _function(name: str) -> str:
    source = DEPLOY.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{$", source, re.M)
    assert match, f"{name}() is no longer in deploy-k3s.sh"
    lines = source[match.start():].splitlines()
    for index, line in enumerate(lines[1:], 1):
        if line == "}":
            return "\n".join(lines[: index + 1])
    raise AssertionError(f"no closing brace for {name}()")


def test_the_deploy_checks_the_same_label_before_it_builds_anything():
    source = DEPLOY.read_text(encoding="utf-8")
    [(key, value)] = TRAEFIK_LABEL.items()
    assert f'TRAEFIK_POD_SELECTOR="{key}={value}"' in source
    check = source.index("check_traefik_pod_label \\\n  || error ")
    assert check < source.index("Step 1 — Deploy Local Container Registry")
    assert check < source.index('kubectl apply -k "$RENDER_DIR"')


@pytest.mark.skipif(BASH is None, reason="requires POSIX bash")
def test_a_traefik_without_the_label_stops_the_deploy(tmp_path: Path):
    body = (
        "set -euo pipefail\n"
        'TRAEFIK_POD_SELECTOR="app.kubernetes.io/name=traefik"\n'
        'kubectl() { printf "%s\\n" "$*" >> kubectl.log; [ "$FAKE" = fail ] && return 1; '
        '[ "$FAKE" = labelled ] && echo pod/traefik-7d5f6474df-abcde; return 0; }\n'
        + _function("check_traefik_pod_label")
        + "\nfor FAKE in labelled unlabelled fail; do\n"
        '  if check_traefik_pod_label 2>err.$FAKE; then echo "$FAKE=continue"; else echo "$FAKE=stop"; fi\n'
        "done\n"
        'FAKE=unlabelled; SKIP_TRAEFIK_LABEL_CHECK=true\n'
        'if check_traefik_pod_label; then echo "skipped=continue"; fi\n'
        "cat kubectl.log\n"
    )
    runner = tmp_path / "runner.sh"
    runner.write_text(body, encoding="utf-8", newline="\n")
    result = subprocess.run(
        [BASH, str(runner)], cwd=tmp_path, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=bash_environment(BASH), timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:4] == [
        "labelled=continue", "unlabelled=stop", "fail=stop", "skipped=continue",
    ]
    assert "-n kube-system get pods -l app.kubernetes.io/name=traefik" in result.stdout
    # The refusal says what was looked for and what to do instead.
    message = (tmp_path / "err.unlabelled").read_text(encoding="utf-8")
    assert "app.kubernetes.io/name=traefik" in message and "--show-labels" in message
