"""A Celery worker's CPU limit must not be below its ``--concurrency``.

``--concurrency=N`` forks N worker processes. A cgroup ``limits.cpu`` below N
cores means those N processes cannot exceed the cap *between them*, so under any
burst they collectively blow the quota inside each CFS period, get throttled,
and idle until the next one. The concurrency flag then promises parallelism the
cgroup will not allow.

Three of the four workers shipped that way::

    worker        --concurrency   limits.cpu     ratio
    critical            2           1000m         2:1
    ingestion           4           1000m         4:1
    default             4            500m         8:1
    ai                  2           2000m         1:1   <- the only correct one

Measured on the ingestion worker with an 80-run burst, reading
``/sys/fs/cgroup/cpu.stat`` directly (metrics-server's ~60s scrape is far too
coarse for a burst)::

    limit 1 core .... throttled 36-74x per pod, every run ....  5.0 runs/s
    limit 4 cores ... throttled 0x .............................  23.5 runs/s

Back to back at the same corpus size. Observed demand peaked at 1.46 cores in a
single pod, i.e. genuinely above the old cap.

**The CPU average does not reveal this.** It read 0.64 of 1.00 cores during a
throttled run -- apparent headroom -- because averaging over the window includes
the idle tail that throttling itself produces. ``nr_throttled`` is the field that
settles it, and this test exists because the arithmetic is checkable without
running anything at all.

A limit is a ceiling, not a reservation: raising it costs nothing while idle,
and ``requests`` are deliberately left alone.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKERS = REPO_ROOT / "k8s" / "base" / "worker-deployments.yaml"


def _cores(value: str) -> float:
    """'4000m' -> 4.0, '2' -> 2.0."""
    value = value.strip().strip('"').strip("'")
    if value.endswith("m"):
        return int(value[:-1]) / 1000.0
    return float(value)


def _strip_comments(text: str) -> str:
    """Drop YAML comments.

    The comments in this file quote the very numbers the assertions look for
    ("cpu limit 1000m -> 4000m"), so a naive regex over raw text would happily
    match the prose describing the defect instead of the setting.
    """
    return re.sub(r"(?m)\s#.*$", "", text)


def _worker_specs() -> list[tuple[str, int, float]]:
    """(deployment name, concurrency, cpu limit in cores) for celery workers."""
    raw = WORKERS.read_text(encoding="utf-8")
    specs = []
    for doc in _strip_comments(raw).split("\n---\n"):
        name_m = re.search(r"^  name: (testlookup-\S+)", doc, re.M)
        conc_m = re.search(r"--concurrency=(\d+)", doc)
        lim_m = re.search(r"limits:\s*\n\s*cpu: ([0-9.]+m?)", doc)
        if not (name_m and conc_m and lim_m):
            continue  # not a celery worker (beat has no --concurrency)
        specs.append((name_m.group(1), int(conc_m.group(1)), _cores(lim_m.group(1))))
    return specs


def test_the_fixture_actually_found_the_workers():
    """Guards the test. If the parse silently matched nothing, every
    assertion below would pass vacuously — which is the failure mode that
    makes a gate worthless."""
    specs = _worker_specs()
    assert len(specs) >= 4, f"expected at least 4 celery workers, parsed {specs}"
    names = {s[0] for s in specs}
    assert "testlookup-worker-ingestion" in names


@pytest.mark.parametrize("name,concurrency,limit", _worker_specs())
def test_cpu_limit_is_not_below_concurrency(name, concurrency, limit):
    assert limit >= concurrency, (
        f"{name} runs --concurrency={concurrency} under a {limit}-core limit, so "
        f"{concurrency} worker processes share {limit} cores and the cgroup "
        f"throttles under any burst. Measured on the ingestion worker: 5.0 runs/s "
        f"throttled vs 23.5 runs/s unthrottled. Raise limits.cpu to at least "
        f"{concurrency} cores, or lower --concurrency to {math.floor(limit)}."
    )


def test_requests_stay_below_limits():
    """Raising a ceiling must not turn into a reservation — that would make the
    pods unschedulable on a small node for no benefit."""
    raw = _strip_comments(WORKERS.read_text(encoding="utf-8"))
    for doc in raw.split("\n---\n"):
        name_m = re.search(r"^  name: (testlookup-\S+)", doc, re.M)
        req_m = re.search(r"requests:\s*\n\s*cpu: ([0-9.]+m?)", doc)
        lim_m = re.search(r"limits:\s*\n\s*cpu: ([0-9.]+m?)", doc)
        if not (name_m and req_m and lim_m):
            continue
        req, lim = _cores(req_m.group(1)), _cores(lim_m.group(1))
        assert req <= lim, f"{name_m.group(1)}: cpu request {req} exceeds limit {lim}"
        assert req <= 1.0, (
            f"{name_m.group(1)}: cpu request {req} — these are bursty queue "
            f"consumers, not steady draw; a large request only costs scheduling "
            f"flexibility"
        )
