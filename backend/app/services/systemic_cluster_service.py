"""Find tests that fail TOGETHER across runs, and name the shared cause.

Phase 3 of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

## The finding this implements

Roughly 75% of flaky tests fail in co-occurring clusters rather than in
isolation, and the shared root causes skew heavily to networking and unstable
external dependencies. That makes per-test triage a mismatch for the dominant
failure mode: *"these 14 tests flip together and it smells like an external
dependency"* is one investigation, where 14 individual flaky flags are 14.

It also reframes what an automated verdict can honestly claim. A cluster-level
environmental cause is checkable — you can go look at the dependency. A
per-test code-defect explanation usually is not.

## Why this is not the existing clusterer

``failure_clusters`` groups **within one run** by **semantic similarity of error
messages**. This groups **across runs** by **literal co-failure**: the same set
of runs turning red. Two tests can co-fail on every network blip while emitting
completely different messages, so message similarity would never join them, and
one run's grouping cannot express a pattern that only exists over time.

## Method

Deliberately the published method rather than something invented here:

1. Represent each test by the **set of run IDs in which it failed**.
2. Distance is **Jaccard** on those sets — 0 when two tests fail in exactly the
   same runs, 1 when they never co-fail.
3. **Agglomerative** clustering with average linkage, merging while the closest
   pair is under the distance ceiling.
4. Accept a cluster only when its **mean silhouette ≥ 0.6**. Everything else is
   discarded, because a weak cluster shown as a cluster is worse than no
   cluster: it invites an investigation into a pattern that is not there.

Implemented in plain Python — no scipy/sklearn in this service's dependency
set, the inputs are small (fingerprints per project, capped), and a
dependency-free implementation is exhaustively testable.

## "No clusters here" is the common answer

In the source study only 10 of 22 projects containing flaky tests contained any
cluster at all. Returning an empty list is a normal, frequent, correct outcome —
never a reason to lower the bar until something appears.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("services.systemic_cluster")

# Merge while the closest pair is nearer than this. 0.5 means two tests must
# co-fail in at least half the runs where either fails before they can join.
MAX_MERGE_DISTANCE = 0.5

# Mean silhouette a cluster must reach to be reported at all.
MIN_SILHOUETTE = 0.6

# A "cluster" of one is just a test. Two is the minimum that says anything
# about co-occurrence.
MIN_CLUSTER_SIZE = 2

# A test that failed once cannot evidence a co-failure pattern.
MIN_FAILURE_RUNS = 2

# Hard cap on how many fingerprints enter the clustering.
#
# Agglomerative clustering with average linkage recomputes every pairwise
# cluster distance on each merge, so the cost grows ~cubically once merges are
# admissible. Measured on this implementation: 200 fingerprints that all
# co-fail take 0.4s, 400 take 6s, and 800 take 85s.
#
# The blow-up case is not hypothetical — it is precisely the scenario this
# feature exists for. A wide outage makes hundreds of tests co-fail, every pair
# becomes mergeable, and the nightly sweep would stall on the project that most
# needed the answer. Cap the input, keep the tests with the MOST failures
# (those carry the pattern), and say so when the cap bites.
MAX_CLUSTERED_FINGERPRINTS = 400

# Cause families, ordered by how specific the evidence is. First match wins, so
# the most diagnostic signature is checked before the generic ones.
CAUSE_NETWORKING = "networking"
CAUSE_EXTERNAL_DEPENDENCY = "external_dependency"
CAUSE_FILESYSTEM = "filesystem"
CAUSE_TIMEOUT = "timeout"
CAUSE_CLOCK = "clock"
CAUSE_UNKNOWN = "unknown"

_CAUSE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (CAUSE_NETWORKING, (
        "unknownhost", "connection refused", "connectionerror", "econnrefused",
        "econnreset", "network is unreachable", "no route to host", "dns",
        "sockettimeout", "socket hang up", "ssl", "tls handshake",
    )),
    (CAUSE_EXTERNAL_DEPENDENCY, (
        "503 service unavailable", "502 bad gateway", "504 gateway",
        "upstream", "rate limit", "429", "circuit breaker", "dependency",
        "third-party", "api returned",
    )),
    (CAUSE_FILESYSTEM, (
        "no such file", "filenotfound", "permission denied", "disk", "enospc",
        "read-only file system", "ioerror", "file lock",
    )),
    (CAUSE_TIMEOUT, (
        "timeout", "timed out", "deadline exceeded", "did not complete within",
    )),
    (CAUSE_CLOCK, (
        "clock", "system time", "timezone", "skew", "expired token",
    )),
)


@dataclass(frozen=True)
class SystemicCluster:
    """A group of tests that fail together, with the evidence for it."""

    cluster_key: str
    members: tuple[str, ...]
    cohesion: float
    co_failure_runs: int
    cause_family: str = CAUSE_UNKNOWN
    label: str = ""
    member_names: dict[str, str] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.members)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_key": self.cluster_key,
            "label": self.label,
            "cause_family": self.cause_family,
            "size": self.size,
            "cohesion": self.cohesion,
            "co_failure_runs": self.co_failure_runs,
            "members": list(self.members),
        }


def jaccard_distance(left: set, right: set) -> float:
    """1 − |A∩B| / |A∪B|. Two empty sets are maximally distant, not identical.

    Treating "never failed" as "failed in all the same places" would cluster
    every healthy test into one enormous fictional group.
    """
    if not left or not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return 1.0 - (len(left & right) / len(union))


def _average_linkage(
    a: list[str], b: list[str], distances: Mapping[tuple[str, str], float]
) -> float:
    total = 0.0
    for x in a:
        for y in b:
            total += distances[(x, y)] if (x, y) in distances else distances[(y, x)]
    return total / (len(a) * len(b))


def _silhouette(
    cluster: list[str],
    others: list[list[str]],
    distances: Mapping[tuple[str, str], float],
) -> float:
    """Mean silhouette for one cluster.

    A singleton scores 0.0 — undefined, and 0 keeps it below any sane
    acceptance bar rather than letting it through on a technicality.
    """
    if len(cluster) < 2:
        return 0.0

    def dist(x: str, y: str) -> float:
        return distances[(x, y)] if (x, y) in distances else distances[(y, x)]

    scores: list[float] = []
    for point in cluster:
        intra = [dist(point, other) for other in cluster if other != point]
        a = sum(intra) / len(intra) if intra else 0.0
        b = float("inf")
        for other_cluster in others:
            if not other_cluster:
                continue
            mean_to_other = sum(dist(point, o) for o in other_cluster) / len(other_cluster)
            b = min(b, mean_to_other)
        if b == float("inf"):
            # Only one cluster exists — there is nothing to be separated FROM,
            # so cohesion is unevidenced. Report 0, not 1.
            return 0.0
        denominator = max(a, b)
        scores.append(0.0 if denominator == 0 else (b - a) / denominator)
    return sum(scores) / len(scores)


def classify_cause(error_texts: Iterable[Any]) -> str:
    """Name the shared cause family from members' failure text.

    First marker wins, checked most-diagnostic first. Returns ``unknown`` when
    nothing matches — a cluster is still useful without a named cause, and
    inventing one would be worse than admitting we cannot tell.
    """
    blob = " ".join(str(text or "").lower() for text in (error_texts or []))
    if not blob.strip():
        return CAUSE_UNKNOWN
    for family, markers in _CAUSE_MARKERS:
        if any(marker in blob for marker in markers):
            return family
    return CAUSE_UNKNOWN


def build_clusters(
    failures_by_fingerprint: Mapping[str, set],
    *,
    errors_by_fingerprint: Optional[Mapping[str, Iterable[Any]]] = None,
    names_by_fingerprint: Optional[Mapping[str, str]] = None,
    max_merge_distance: float = MAX_MERGE_DISTANCE,
    min_silhouette: float = MIN_SILHOUETTE,
    max_fingerprints: int = MAX_CLUSTERED_FINGERPRINTS,
) -> list[SystemicCluster]:
    """Cluster fingerprints by co-failure. Returns only clusters that qualify.

    Pure and never raises. An empty result is the expected outcome for most
    projects — see the module docstring.
    """
    # Only tests with enough failures to evidence a pattern take part.
    points = {
        fingerprint: set(runs)
        for fingerprint, runs in (failures_by_fingerprint or {}).items()
        if isinstance(fingerprint, str) and fingerprint and len(set(runs or ())) >= MIN_FAILURE_RUNS
    }
    if len(points) < MIN_CLUSTER_SIZE:
        return []

    if len(points) > max_fingerprints:
        # Keep the most-failing tests: a co-failure pattern lives among tests
        # that actually fail often, and dropping the long tail of one- or
        # two-failure tests costs the least signal.
        ranked = sorted(points, key=lambda fp: (-len(points[fp]), fp))
        kept = set(ranked[:max_fingerprints])
        logger.warning(
            "systemic_cluster_input_truncated",
            kept=len(kept),
            dropped=len(points) - len(kept),
            max_fingerprints=max_fingerprints,
        )
        points = {fp: runs for fp, runs in points.items() if fp in kept}

    keys = sorted(points)
    distances: dict[tuple[str, str], float] = {}
    for i, left in enumerate(keys):
        for right in keys[i + 1:]:
            distances[(left, right)] = jaccard_distance(points[left], points[right])

    # Agglomerative, average linkage: merge the closest pair until nothing is
    # closer than the ceiling.
    clusters: list[list[str]] = [[key] for key in keys]
    while len(clusters) > 1:
        best: Optional[tuple[float, int, int]] = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                distance = _average_linkage(clusters[i], clusters[j], distances)
                if best is None or distance < best[0]:
                    best = (distance, i, j)
        if best is None or best[0] > max_merge_distance:
            break
        _distance, i, j = best
        clusters[i] = clusters[i] + clusters[j]
        clusters.pop(j)

    candidates = [c for c in clusters if len(c) >= MIN_CLUSTER_SIZE]
    if not candidates:
        return []

    accepted: list[SystemicCluster] = []
    for cluster in candidates:
        others = [c for c in clusters if c is not cluster]
        cohesion = _silhouette(cluster, others, distances)
        if cohesion < min_silhouette:
            # Discarded on purpose. A weak cluster presented as a cluster sends
            # someone hunting a pattern that is not there.
            continue
        members = tuple(sorted(cluster))
        co_failures = set(points[members[0]])
        for member in members[1:]:
            co_failures &= points[member]
        cause = classify_cause(
            [
                text
                for member in members
                for text in ((errors_by_fingerprint or {}).get(member) or [])
            ]
        )
        accepted.append(
            SystemicCluster(
                cluster_key="",           # assigned below, after ordering
                members=members,
                cohesion=round(cohesion, 4),
                co_failure_runs=len(co_failures),
                cause_family=cause,
                label=f"{len(members)} tests failing together ({cause.replace('_', ' ')})",
                member_names={
                    m: (names_by_fingerprint or {}).get(m, "") for m in members
                },
            )
        )

    # Largest first, then by cohesion — stable keys for a stable UI.
    accepted.sort(key=lambda c: (-c.size, -c.cohesion, c.members[0]))
    return [
        SystemicCluster(
            cluster_key=f"sfc_{index + 1:03d}",
            members=cluster.members,
            cohesion=cluster.cohesion,
            co_failure_runs=cluster.co_failure_runs,
            cause_family=cluster.cause_family,
            label=cluster.label,
            member_names=cluster.member_names,
        )
        for index, cluster in enumerate(accepted)
    ]


# ── Persistence ─────────────────────────────────────────────────────────────


async def cluster_project(
    db: "AsyncSession",
    project_id: Any,
    *,
    window_days: int = 60,
    max_rows: int = 200_000,
) -> list[SystemicCluster]:
    """Build this project's systemic clusters from its recent failures.

    Project-scoped by construction — ``test_fingerprint`` is not globally
    unique. Reads only FAILED/BROKEN rows: a co-failure pattern is made of
    failures, and pulling passes would multiply the read for no signal.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models.postgres import TestCase, TestRun

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    # Bounded like the scorer: this runs nightly across every active project.
    rows = (
        await db.execute(
            select(
                TestCase.test_fingerprint,
                TestCase.test_name,
                TestCase.test_run_id,
                TestCase.error_message,
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestRun.created_at >= since,
                TestCase.status.in_(["FAILED", "BROKEN"]),
            )
            .order_by(TestRun.created_at.desc())
            .limit(max_rows + 1)
        )
    ).all()
    if len(rows) > max_rows:
        rows = rows[:max_rows]
        logger.warning(
            "systemic_cluster_window_truncated",
            project_id=str(project_id),
            max_rows=max_rows,
            window_days=window_days,
        )

    failures: dict[str, set] = {}
    errors: dict[str, list] = {}
    names: dict[str, str] = {}
    for fingerprint, test_name, run_id, error_message in rows:
        if not fingerprint:
            continue
        failures.setdefault(fingerprint, set()).add(run_id)
        if error_message:
            errors.setdefault(fingerprint, []).append(error_message)
        if test_name and fingerprint not in names:
            names[fingerprint] = test_name

    return build_clusters(
        failures, errors_by_fingerprint=errors, names_by_fingerprint=names
    )


async def store_clusters(
    db: "AsyncSession",
    project_id: Any,
    clusters: Iterable[SystemicCluster],
    *,
    window_days: int = 60,
) -> int:
    """Replace this project's clusters with a freshly computed set.

    Full replace rather than upsert: cluster membership is a property of the
    window, and a test that left a cluster must actually leave it. A merge
    would let a stale cluster outlive the evidence for it.

    Staged only — the caller owns the commit.
    """
    from sqlalchemy import delete, select

    from app.models.postgres import SystemicFlakeCluster, SystemicFlakeClusterMember

    existing_ids = list(
        (
            await db.execute(
                select(SystemicFlakeCluster.id).where(
                    SystemicFlakeCluster.project_id == project_id
                )
            )
        ).scalars().all()
    )
    if existing_ids:
        await db.execute(
            delete(SystemicFlakeClusterMember).where(
                SystemicFlakeClusterMember.cluster_id.in_(existing_ids)
            )
        )
        await db.execute(
            delete(SystemicFlakeCluster).where(
                SystemicFlakeCluster.project_id == project_id
            )
        )

    written = 0
    for cluster in clusters:
        row = SystemicFlakeCluster(
            project_id=project_id,
            cluster_key=cluster.cluster_key,
            label=cluster.label,
            cause_family=cluster.cause_family,
            size=cluster.size,
            cohesion=cluster.cohesion,
            co_failure_runs=cluster.co_failure_runs,
            window_days=window_days,
        )
        db.add(row)
        await db.flush()
        for fingerprint in cluster.members:
            db.add(
                SystemicFlakeClusterMember(
                    cluster_id=row.id,
                    test_fingerprint=fingerprint,
                    test_name=cluster.member_names.get(fingerprint) or None,
                    failure_runs=cluster.co_failure_runs,
                )
            )
        written += 1
    await db.flush()
    return written
