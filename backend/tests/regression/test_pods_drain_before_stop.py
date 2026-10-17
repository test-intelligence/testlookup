"""The API and frontend pods drain before they stop, so a deploy does not answer 504.

2026-10-17: /chat showed "Request failed with status code 504" on the homelab while a deploy replaced the backend
pod (events: "Killing pod/testlookup-backend-... Stopping container backend" at the minute of the report; every
chat call on the new pod answered 200). The rollout itself was safe (maxUnavailable 0, maxSurge 1: the new pod is
Ready before the old one goes), but a deleted pod keeps receiving requests until the ingress drops it from its
endpoints, and nothing held it up: SIGTERM stopped gunicorn's listener at once, so a request routed in that window
failed. And gunicorn's 30 s graceful stop raced the kubelet's 30 s SIGKILL, so a finishing request (a chat answer)
could be cut.

The fix: a preStop sleep (the pod keeps serving while the endpoint removal propagates) and a termination grace
period long enough for the sleep plus the in-flight requests.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
GUNICORN_GRACEFUL_S = 30  # gunicorn's default graceful_timeout; gunicorn_conf.py does not change it


def _deployment(name: str) -> dict:
    path = ROOT / "k8s" / "base" / f"{name}-deployment.yaml"
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    return next(d for d in docs if d.get("kind") == "Deployment")


def _pre_stop_sleep_s(container: dict) -> int:
    command = container["lifecycle"]["preStop"]["exec"]["command"]
    match = re.fullmatch(r"sleep (\d+)", command[-1].strip())
    assert match, f"preStop must be a plain sleep, got {command!r}"
    return int(match.group(1))


@pytest.mark.parametrize("name, in_flight_s", [("backend", GUNICORN_GRACEFUL_S), ("frontend", 10)])
def test_the_pod_keeps_serving_while_the_ingress_drops_it(name, in_flight_s):
    spec = _deployment(name)["spec"]["template"]["spec"]
    container = next(c for c in spec["containers"] if c["name"] == name)
    sleep_s = _pre_stop_sleep_s(container)
    assert sleep_s >= 5, "the endpoint removal takes seconds to reach the ingress"
    grace = spec.get("terminationGracePeriodSeconds", 30)
    assert grace >= sleep_s + in_flight_s, (
        f"{name}: {grace} s of grace cannot hold a {sleep_s} s preStop sleep plus {in_flight_s} s of in-flight requests"
    )


def test_the_rollout_still_brings_the_new_pod_up_first():
    for name in ("backend", "frontend"):
        rolling = _deployment(name)["spec"]["strategy"]["rollingUpdate"]
        assert rolling["maxUnavailable"] == 0 and rolling["maxSurge"] >= 1, name
