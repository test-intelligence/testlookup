"""Regression: the readiness probe deadlocked a rollout under load.

Observed on the homelab (2026-08-17) while deploying the worker memory fix.
The probe ran ``celery -A app.worker.celery_app inspect ping --timeout=10``
inside a 15 s exec. That is a control-channel round-trip, so it measures how
*responsive* the worker is, not whether it is healthy — and with four prefork
children pinned at the CPU limit the reply missed the deadline::

    Warning  Unhealthy  28s (x13 over 5m32s)  kubelet
      Readiness probe failed: command timed out:
      "celery -A app.worker.celery_app inspect ping --timeout=10"

Meanwhile the worker's own log showed it consuming tasks normally throughout,
with ``restarts=0`` and no OOM. It was busy, not broken.

For a queue consumer with **no Service in front of it**, readiness gates only
the rollout. So marking a busy worker NotReady is worse than useless:

    rs testlookup-worker-default-5f546fc8b7   desired=2  ready=1   <- new, 4Gi
    rs testlookup-worker-default-6bb47455bc   desired=1  ready=0   <- old, 2Gi

The Deployment could not scale the old ReplicaSet down, so a pod from the
previous revision — the one OOM-looping at 2 GiB, 6 restarts — stayed alive
serving the queue while its replacement sat NotReady beside it. The deploy
script reported DEGRADED for the same reason.

Fix: the ``worker_ready`` signal writes a sentinel file and ``worker_shutdown``
removes it; the probe checks for the file. ``worker_ready`` fires once the
consumer has connected to the broker and started consuming, which is exactly
what readiness should mean here, and a file check costs nothing under load.
Liveness stays on ``pgrep`` and remains what catches a dead worker.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("yaml")

import yaml  # noqa: E402

pytestmark = pytest.mark.regression

_K8S = pathlib.Path(__file__).resolve().parents[3] / "k8s" / "base"
_MANIFESTS = ("worker-deployments.yaml", "worker-children-deployment.yaml")
_SENTINEL = "/tmp/celery-worker-ready"


def _worker_containers():
    for name in _MANIFESTS:
        for doc in yaml.safe_load_all((_K8S / name).read_text(encoding="utf-8")):
            if not doc or doc.get("kind") != "Deployment":
                continue
            container = doc["spec"]["template"]["spec"]["containers"][0]
            # beat has no readiness probe and consumes no queue.
            if not container.get("readinessProbe"):
                continue
            yield doc["metadata"]["name"], container


def test_no_worker_gates_readiness_on_a_control_channel_round_trip():
    """`inspect ping` under load is the deadlock. It must not come back."""
    for name, container in _worker_containers():
        cmd = " ".join(container["readinessProbe"]["exec"]["command"])
        assert "inspect" not in cmd, (
            f"{name}: readiness runs `inspect ping` again — a saturated pool "
            f"misses the deadline and the rollout cannot complete"
        )


def test_every_worker_reports_readiness_from_the_sentinel():
    checked = 0
    for name, container in _worker_containers():
        cmd = container["readinessProbe"]["exec"]["command"]
        assert _SENTINEL in cmd, f"{name}: readiness does not check {_SENTINEL}"
        checked += 1
    assert checked >= 5, f"expected every queue worker covered, saw {checked}"


def test_liveness_still_catches_a_dead_worker():
    """Readiness got cheaper; liveness must not have been weakened with it."""
    for name, container in _worker_containers():
        live = " ".join(container["livenessProbe"]["exec"]["command"])
        assert "pgrep" in live, f"{name}: liveness no longer proves the process exists"


def test_the_signal_handlers_write_and_clear_the_same_path(tmp_path, monkeypatch):
    """The probe's path and the app's path must not drift apart."""
    from app.worker import celery_app

    assert celery_app.READY_SENTINEL == _SENTINEL

    sentinel = tmp_path / "celery-worker-ready"
    monkeypatch.setattr(celery_app, "READY_SENTINEL", str(sentinel))

    celery_app._mark_worker_ready()
    assert sentinel.is_file(), "worker_ready did not write the sentinel"

    celery_app._clear_worker_ready()
    assert not sentinel.exists(), "worker_shutdown left the worker reporting Ready"


def test_readiness_never_blocks_a_worker_from_booting(tmp_path, monkeypatch):
    """An unwritable path must not raise out of the signal handler.

    A readiness mechanism that can crash the boot it reports on would be a
    worse failure than the one it replaces.
    """
    from app.worker import celery_app

    monkeypatch.setattr(celery_app, "READY_SENTINEL", str(tmp_path / "no" / "such" / "dir" / "s"))
    celery_app._mark_worker_ready()   # must not raise
    celery_app._clear_worker_ready()  # must not raise
