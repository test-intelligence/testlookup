"""The deploy's node checks read the command's real exit code and remove their own debug pods.

``homelabsetup/deploy-homelab.sh`` checks each node's
``/etc/rancher/k3s/registries.yaml`` through ``kubectl debug node/<node>``.
Two defects lived there until 2026-10-18:

1. **The check could not fail.** Without ``-i``, ``kubectl debug`` returns as
   soon as the debug pod is *created*, with exit 0, whatever the command in it
   does — so every node "passed" the mirror precheck.
2. **Its debug pods were never removed.** The cleanup deleted
   ``-l created-by=kubectl-debug``, a label these pods do not carry: 735 dead
   ``node-debugger-*`` pods had piled up in namespace default (three per
   deploy).

``node_debug_run`` now finds the pod its own call created, waits for it to
finish, returns the container's exit code (125 when the check could not run)
and deletes exactly that pod. Verified live on the homelab 2026-10-18: the real
grep returned 0, a grep for an absent entry 1, a test for an absent file 1, and
no debug pod was left.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
DEPLOY = REPO / "homelabsetup" / "deploy-homelab.sh"

pytestmark = pytest.mark.skipif(
    not DEPLOY.exists(), reason="homelab deploy script not present in this checkout"
)


def _script() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _function() -> str:
    text = _script()
    start = text.index("node_debug_run() {")
    end = text.index("\n}\n", start)
    return text[start:end]


def test_kubectl_debug_runs_only_inside_node_debug_run():
    text = _script()
    calls = [m.start() for m in re.finditer(r"^\s*kubectl debug node/", text, flags=re.M)]
    start = text.index("node_debug_run() {")
    end = text.index("\n}\n", start)
    assert calls, "the helper's kubectl debug call is gone"
    assert all(start < c < end for c in calls), "a kubectl debug call outside node_debug_run cannot report its exit code"


def test_the_helper_returns_the_commands_exit_code_and_deletes_its_own_pod():
    fn = _function()
    assert "comm -13" in fn and "node-debugger-${node}-" in fn, "must find the pod THIS call created"
    assert "Succeeded|Failed" in fn, "must wait for the pod to finish"
    assert "state.terminated.exitCode" in fn, "must read the container's own exit code"
    assert re.search(r'kubectl -n default delete "\$pod"', fn), "must delete exactly that pod"
    assert 'return "$code"' in fn and "return 125" in fn


def test_the_mirror_precheck_uses_the_helper_and_no_label_cleanup_remains():
    text = _script()
    assert re.search(
        r'node_debug_run "\$node" sh -c \\\s*\n\s*"chroot /host grep -qF \'\$REGISTRY\' /etc/rancher/k3s/registries\.yaml"',
        text,
    ), "the precheck runs as one sh -c string"
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "created-by=kubectl-debug" not in code, "the label cleanup never matched a debug pod"
    # A check that could not run is a warning, not a missing mirror.
    assert re.search(r"125\)\s+warn ", text)


def test_no_node_debug_run_argument_starts_with_a_slash():
    """Git Bash rewrites an argument that starts with `/` into a Windows path
    before the native kubectl sees it: `chroot /host ...` became
    `chroot "C:/Program Files/Git/host" ...` and the precheck reported every
    node missing (2026-10-19). Every call passes its command as words that do
    not start with `/` (a single `sh -c` string)."""
    text = _script()
    calls = re.findall(r'node_debug_run "\$node"((?:[^\n]*\\\n)*[^\n]*)', text)
    assert calls, "no node_debug_run call found"
    for args in calls:
        words = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', args.replace("\\\n", " "))
        bad = [w for w in words if w.strip("\"'").startswith("/") and w not in ("||",)]
        assert not bad, f"node_debug_run argument(s) starting with '/': {bad}"
