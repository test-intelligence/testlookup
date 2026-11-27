"""Wait for fixed private stores, then run the archived candidate checker."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import time
from urllib.request import ProxyHandler, build_opener


def ready(opener):
    for origin, path in (
        ("http://backend:8000", "/health/ready"), ("http://prometheus:9090", "/-/ready"),
        ("http://loki:3100", "/ready"), ("http://jaeger:16686", "/api/v3/services"),
        ("http://otel-collector:13133", "/"),
    ):
        with opener.open(origin + path, timeout=5) as response:
            if response.status != 200:
                return False
    with opener.open("http://prometheus:9090/api/v1/targets", timeout=5) as response:
        targets = json.loads(response.read(1_048_576))["data"]["activeTargets"]
    app = [row for row in targets if row.get("scrapePool") in {"testlookup-backend", "testlookup-worker"}]
    return len(app) == 3 and all(row.get("health") == "up" for row in app)


def main():
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 180
    while True:
        try:
            if ready(opener):
                break
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if time.monotonic() >= deadline:
            print("Private monitoring startup proof timed out")
            return 1
        time.sleep(1)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/monitoring/release_signal_gate.py"), run_name="__main__")
    return 1  # The checker terminates with its own explicit gate status.


if __name__ == "__main__":
    raise SystemExit(main())
