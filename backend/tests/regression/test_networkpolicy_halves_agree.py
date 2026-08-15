"""A NetworkPolicy egress grant with no matching ingress grant does nothing.

Found on the homelab. Every MCP tool call failed with::

    Error executing tool list_projects: All connection attempts failed

…while the pod reported ``Running 1/1``. From inside it::

    DNS testlookup-backend                        -> 10.43.31.138  (resolves)
    http://testlookup-backend:8000/openapi.json   -> [Errno 111] Connection refused
    (same for the ClusterIP and both pod IPs directly)

``allow-mcp`` already granted the MCP server **egress** to the backend on 8000.
``allow-backend`` listed its permitted **ingress** sources as ingress-nginx,
``app=testlookup-frontend`` and ``app.kubernetes.io/component=worker`` — and the
MCP pod carries none of those labels. Under ``default-deny-all`` a connection
needs *both* halves, so the traffic died at the destination while the source
policy said it was allowed.

Proven with two throwaway pods built from the same image, differing only by one
label:

===========================================  ====================
``np-probe-unlabelled``                      Connection refused
``np-probe-worker`` (component=worker)       200
===========================================  ====================

Nothing caught it: the MCP probes are ``tcpSocket`` on its own port (correct for
an SSE server — an HTTP GET on ``/sse`` would hang), and the SSE handshake and
``tools/list`` are both served without ever calling the backend. So the pod
looks healthy and the tool catalogue looks right, while every tool is dead.

The file itself already records this class happening once before — the beat
pod's ``component: scheduler`` was omitted where only ``worker`` was matched,
and its ``wait-for-redis`` initContainer hung forever. Two omissions, same
shape, four months apart. Hence a ratchet rather than a third comment.

The guard is the CLASS: **for every egress rule that names a pod selector, the
policy governing those pods must allow ingress back from the source on that
port.** A rule permitting traffic the other end drops is not a tighter policy,
it is a broken one.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
POLICY_FILE = REPO / "k8s" / "base" / "networkpolicy.yaml"


def _policies():
    docs = [
        d for d in yaml.safe_load_all(POLICY_FILE.read_text(encoding="utf-8"))
        if d and d.get("kind") == "NetworkPolicy"
    ]
    assert docs, "no NetworkPolicy documents parsed — has the file moved?"
    return docs


def _labels(selector) -> dict | None:
    """matchLabels of a podSelector, or None if this isn't a pod selector."""
    if not isinstance(selector, dict):
        return None
    return selector.get("matchLabels")


def _selects(selector_labels: dict, pod_labels: dict) -> bool:
    """Does a selector with these matchLabels select a pod carrying these?"""
    return all(pod_labels.get(k) == v for k, v in selector_labels.items())


def _ports(rule) -> set:
    return {p.get("port") for p in (rule.get("ports") or [])}


# Pods are identified by the labels their own governing policy selects on.
# That is enough to reason about this file, which is written entirely in
# terms of `app:` / `app.kubernetes.io/component:` labels.
def _governing(policies, target_labels: dict):
    """Policies whose podSelector selects pods carrying target_labels."""
    out = []
    for p in policies:
        sel = _labels(p["spec"].get("podSelector"))
        if sel is None:
            continue  # {} == all pods; the default-deny baseline
        if _selects(sel, target_labels):
            out.append(p)
    return out


def test_every_egress_grant_has_a_matching_ingress_grant():
    """The regression, generalised. This fails on the MCP omission and would
    have failed on the beat/scheduler omission before it."""
    policies = _policies()
    broken = []

    for src in policies:
        spec = src["spec"]
        src_labels = _labels(spec.get("podSelector"))
        if not src_labels:
            continue  # all-pods policies (default-deny, DNS egress)
        for rule in spec.get("egress") or []:
            if not rule or not rule.get("to"):
                continue  # `- {}` allow-all, or DNS-style namespace egress
            for dest in rule["to"]:
                dest_labels = _labels(dest.get("podSelector"))
                if dest_labels is None:
                    continue  # namespaceSelector / ipBlock — out of scope here
                wanted = _ports(rule)
                allowed = False
                for gov in _governing(policies, dest_labels):
                    if "Ingress" not in (gov["spec"].get("policyTypes") or []):
                        continue
                    for ing in gov["spec"].get("ingress") or []:
                        if not ing:
                            continue
                        froms = ing.get("from") or []
                        port_ok = not wanted or (wanted & _ports(ing)) == wanted
                        if not port_ok:
                            continue
                        for f in froms:
                            f_labels = _labels(f.get("podSelector"))
                            if f_labels and _selects(f_labels, src_labels):
                                allowed = True
                                break
                        if allowed:
                            break
                    if allowed:
                        break
                if not allowed:
                    broken.append(
                        f"{src['metadata']['name']} ({src_labels}) is granted egress to "
                        f"{dest_labels} on {sorted(wanted) or 'any port'}, but no policy "
                        f"lets those pods accept ingress from it"
                    )

    assert not broken, "one-sided NetworkPolicy grants:\n  " + "\n  ".join(broken)


def test_the_mcp_server_may_reach_the_backend():
    """Named explicitly. The generic check above is the ratchet; this is the
    specific regression, so a refactor of the checker cannot quietly drop it."""
    policies = _policies()
    backend = next(
        p for p in policies
        if _labels(p["spec"].get("podSelector")) == {"app": "testlookup-backend"}
    )
    sources = [
        _labels(f.get("podSelector"))
        for ing in backend["spec"]["ingress"]
        for f in (ing.get("from") or [])
    ]
    assert {"app": "testlookup-mcp"} in sources, (
        "allow-backend does not accept ingress from the MCP server, so every "
        "MCP tool call fails while the pod still reports Ready"
    )


def test_the_checker_actually_fails_on_a_one_sided_grant(tmp_path, monkeypatch):
    """Without this, a checker that silently found nothing to inspect — a
    parse change, a renamed key — would report the file clean forever."""
    doc = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: talker}
spec:
  podSelector: {matchLabels: {app: talker}}
  policyTypes: [Egress]
  egress:
    - to: [{podSelector: {matchLabels: {app: listener}}}]
      ports: [{protocol: TCP, port: 8000}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: listener}
spec:
  podSelector: {matchLabels: {app: listener}}
  policyTypes: [Ingress]
  ingress:
    - from: [{podSelector: {matchLabels: {app: someone-else}}}]
      ports: [{protocol: TCP, port: 8000}]
"""
    f = tmp_path / "np.yaml"
    f.write_text(doc, encoding="utf-8")
    monkeypatch.setattr(
        "tests.regression.test_networkpolicy_halves_agree.POLICY_FILE", f
    )
    with pytest.raises(AssertionError, match="one-sided"):
        test_every_egress_grant_has_a_matching_ingress_grant()


def test_the_checker_accepts_a_correct_pair(tmp_path, monkeypatch):
    """And does not simply always fail."""
    doc = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: talker}
spec:
  podSelector: {matchLabels: {app: talker}}
  policyTypes: [Egress]
  egress:
    - to: [{podSelector: {matchLabels: {app: listener}}}]
      ports: [{protocol: TCP, port: 8000}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: listener}
spec:
  podSelector: {matchLabels: {app: listener}}
  policyTypes: [Ingress]
  ingress:
    - from: [{podSelector: {matchLabels: {app: talker}}}]
      ports: [{protocol: TCP, port: 8000}]
"""
    f = tmp_path / "np.yaml"
    f.write_text(doc, encoding="utf-8")
    monkeypatch.setattr(
        "tests.regression.test_networkpolicy_halves_agree.POLICY_FILE", f
    )
    test_every_egress_grant_has_a_matching_ingress_grant()
