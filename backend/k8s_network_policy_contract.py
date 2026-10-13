"""Small Kubernetes NetworkPolicy evaluator used by deployment contract tests.

It answers "would a pod with these labels reach that pod on this port?" from
the manifests an overlay ships (k8s/base, the overlay's own files and its
components), without rendering kustomize, so the tests run on any machine.
It implements only what the manifests use: same-namespace pod selectors,
matchLabels / matchExpressions, numeric ports, and the union of the allows.
"""
from __future__ import annotations

import pathlib
from collections.abc import Iterable
from typing import Any

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
BASE = REPO / "k8s" / "base"
OVERLAYS = REPO / "k8s" / "overlays"


def yaml_docs(path: pathlib.Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(d, dict)]


def selects(selector: dict[str, Any] | None, labels: dict[str, str]) -> bool:
    """Kubernetes label-selector semantics (an empty selector selects all)."""
    if selector is None:
        return False
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr["key"], expr["operator"], expr.get("values") or []
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and labels.get(key) in values:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
    return True


def template_labels(paths: Iterable[pathlib.Path]) -> dict[str, dict[str, str]]:
    """Deployment / StatefulSet name -> its pod-template labels."""
    out: dict[str, dict[str, str]] = {}
    for path in paths:
        for doc in yaml_docs(path):
            if doc.get("kind") in ("Deployment", "StatefulSet"):
                out[doc["metadata"]["name"]] = doc["spec"]["template"]["metadata"]["labels"]
    return out


def overlay_files(overlay: str) -> list[pathlib.Path]:
    """The overlay's own manifests (not its kustomization.yaml)."""
    overlay_dir = OVERLAYS / overlay
    return [p for p in sorted(overlay_dir.glob("*.yaml")) if p.name != "kustomization.yaml"]


def policies_for(overlay: str) -> list[dict[str, Any]]:
    """Every NetworkPolicy the overlay ships: base, its own files, its components."""
    overlay_dir = OVERLAYS / overlay
    kustomization = yaml.safe_load((overlay_dir / "kustomization.yaml").read_text(encoding="utf-8"))
    files = sorted(BASE.glob("*.yaml")) + overlay_files(overlay)
    for component in kustomization.get("components") or []:
        files += sorted((overlay_dir / component).resolve().glob("*.yaml"))
    return [
        d for path in files for d in yaml_docs(path)
        if d.get("kind") == "NetworkPolicy"
    ]


def _governing(policies: list[dict[str, Any]], dest: dict[str, str]) -> list[dict[str, Any]]:
    """The policies that isolate ``dest`` for ingress."""
    return [
        p for p in policies
        if selects(p["spec"].get("podSelector"), dest)
        and "Ingress" in (p["spec"].get("policyTypes") or ["Ingress"])
    ]


def _rule_covers_port(rule: dict[str, Any], port: int) -> bool:
    ports = {p.get("port") for p in rule.get("ports") or []}
    return not ports or port in ports


def admits(
    policies: list[dict[str, Any]], dest: dict[str, str], src: dict[str, str], port: int
) -> bool:
    """Would a same-namespace pod labelled ``src`` reach ``dest`` on ``port``?"""
    governing = _governing(policies, dest)
    if not governing:
        return True  # not isolated at all
    for policy in governing:
        for rule in policy["spec"].get("ingress") or []:
            if not _rule_covers_port(rule, port):
                continue
            peers = rule.get("from")
            if not peers:
                return True  # no `from`: every source
            for peer in peers:
                ns = peer.get("namespaceSelector")
                if "ipBlock" in peer:
                    continue
                if ns is not None and ns != {}:
                    continue  # another namespace by label
                if selects(peer.get("podSelector", {}), src):
                    return True
    return False


def admits_from_namespace(
    policies: list[dict[str, Any]], dest: dict[str, str], namespace: dict[str, str], port: int
) -> bool:
    """Would traffic from a namespace labelled ``namespace`` reach ``dest`` on ``port``?

    Matches peers that name a whole namespace (a namespaceSelector with no, or
    an empty, podSelector), which is how the manifests admit ingress
    controllers and scrapers.
    """
    governing = _governing(policies, dest)
    if not governing:
        return True  # not isolated at all
    for policy in governing:
        for rule in policy["spec"].get("ingress") or []:
            if not _rule_covers_port(rule, port):
                continue
            peers = rule.get("from")
            if not peers:
                return True  # no `from`: every source
            for peer in peers:
                ns = peer.get("namespaceSelector")
                if ns is None or peer.get("podSelector") not in (None, {}):
                    continue  # a pod in this namespace, an ipBlock, or narrower than a namespace
                if selects(ns, namespace):
                    return True
    return False


def app_pods() -> dict[str, dict[str, str]]:
    """The application's own pods (k8s/base), name -> pod-template labels."""
    pods = template_labels(sorted(BASE.glob("*.yaml")))
    for name in ("testlookup-backend", "testlookup-mcp", "testlookup-beat", "testlookup-frontend"):
        assert name in pods, f"k8s/base no longer defines {name}; update this contract"
    workers = [n for n, labels in pods.items() if labels.get("app.kubernetes.io/component") == "worker"]
    assert workers, "no worker Deployments found in k8s/base"
    return pods
