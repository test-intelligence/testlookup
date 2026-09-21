#!/usr/bin/env python3
"""
TestLookup — Large Synthetic Dataset (VIZ-213)
=================================================
Creates ONE clearly-marked synthetic project for performance measurement of
the analytics endpoints, at a chosen scale:

    small    ~2 thousand test cases    (20 runs · 4 releases · 10 suites)
    medium   ~100 thousand             (500 runs · 10 releases · 50 suites)
    large    ~1 million                (5,000 runs · 20 releases · 200 suites)

Realistic skew: a few suites hold most of the tests, ~3% of results fail
and those failures concentrate in ~30 error signatures, durations are
log-normal with a few NULLs. Rows go in with asyncpg COPY, one transaction
per batch, from generators that never hold more than a batch in memory.

Only the tables the charts read are populated: projects, releases,
test_suites, canonical_test_cases, test_runs, test_cases and
release_test_run_links. No history, AI analysis or pipeline rows.

Usage (inside the backend container):
    python /app/scripts/seed_large_dataset.py --scale small            # dry run
    python /app/scripts/seed_large_dataset.py --scale large --yes      # write
    python /app/scripts/seed_large_dataset.py --wipe --yes             # remove

Safety: refuses to run when APP_ENV=production, prints the target host and
database before doing anything, and writes nothing without --yes. --wipe
removes only the marked project (slug + marker) and its rows, in FK order.
The project's description carries DATASET_MARKER so it is never mistaken
for real data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, NamedTuple, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.pass_rate import canonical_pass_rate, executed_count
from app.models.postgres import FailureCategory, TestStatus
from app.services.ingestion import make_test_fingerprint
from app.services.release_sort_key import compute_sort_key
from app.services.run_status import terminal_run_status

# ─────────────────────────────────────────────────────────────────────────────
# Marker + scales
# ─────────────────────────────────────────────────────────────────────────────

DATASET_MARKER = "seed_large_dataset_v1"
DEFAULT_SLUG = "synthetic-perf-dataset"
DEFAULT_SEED = 213

ENVIRONMENTS = ("staging", "staging", "qa", "prod-mirror")
BRANCHES = (
    "main",
    "main",
    "main",
    "develop",
    "develop",
    "release/2.4",
    "feature/perf-harness",
)
INGESTION_SOURCES = ("sdk", "sdk", "file", "live", "upload")
SEVERITIES = ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "TRIVIAL")

# One test in fifty is "hot" and carries most of the failures: the overall
# failure share works out at 0.02 × 0.40 + 0.98 × 0.0225 ≈ 3%. Counted by
# position INSIDE a pipeline's slice, not by catalogue index: pipelines are
# index mod 2, 5 or 10, all of which divide 50, so "index % 50 == 0" put
# every hot test — every retry, every flaky flag — in pipeline 0.
_HOT_TEST_EVERY = 50
_HOT_FAIL_P = 0.40
_COLD_FAIL_P = 0.0225
_BROKEN_SHARE = 0.20  # of failures
_SKIP_P = 0.02
_UNKNOWN_P = 0.002
_NULL_DURATION_P = 0.03  # executed cases whose parser reported no timing
_DURATION_MU, _DURATION_SIGMA = 6.2, 1.1  # log-normal, median ≈ 0.5s
_MAX_DURATION_MS = 600_000
_SUITE_SKEW = 1.3  # Zipf exponent over suite sizes
_UNATTRIBUTED_EVERY, _UNATTRIBUTED_RESIDUE = 25, 7  # one run in 25 has no release
_LINK_SOURCE = "explicit_client"


@dataclass(frozen=True)
class ScaleSpec:
    name: str
    runs: int
    releases: int
    suites: int
    tests_per_run: int
    pipelines: int  # each run executes one pipeline's slice of the catalogue
    history_days: int


SCALES = {
    "small": ScaleSpec(
        "small",
        runs=20,
        releases=4,
        suites=10,
        tests_per_run=100,
        pipelines=2,
        history_days=30,
    ),
    "medium": ScaleSpec(
        "medium",
        runs=500,
        releases=10,
        suites=50,
        tests_per_run=200,
        pipelines=5,
        history_days=180,
    ),
    "large": ScaleSpec(
        "large",
        runs=5000,
        releases=20,
        suites=200,
        tests_per_run=200,
        pipelines=10,
        history_days=365,
    ),
}


@dataclass(frozen=True)
class PlanCounts:
    scale: str
    releases: int
    suites: int
    canonical_tests: int
    runs: int
    test_cases: int
    linked_runs: int
    unattributed_runs: int


def plan_counts(scale: str | ScaleSpec) -> PlanCounts:
    """Row counts for a scale, by arithmetic — nothing is generated."""
    spec = SCALES[scale] if isinstance(scale, str) else scale
    unattributed = len(range(_UNATTRIBUTED_RESIDUE, spec.runs, _UNATTRIBUTED_EVERY))
    return PlanCounts(
        scale=spec.name,
        releases=spec.releases,
        suites=spec.suites,
        canonical_tests=spec.tests_per_run * spec.pipelines,
        runs=spec.runs,
        test_cases=spec.runs * spec.tests_per_run,
        linked_runs=spec.runs - unattributed,
        unattributed_runs=unattributed,
    )


def _is_unattributed(run_index: int) -> bool:
    return run_index % _UNATTRIBUTED_EVERY == _UNATTRIBUTED_RESIDUE


# ─────────────────────────────────────────────────────────────────────────────
# Identity — every id is a uuid5 of (marker, slug, seed, kind, ordinal)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DatasetIdentity:
    slug: str = DEFAULT_SLUG
    seed: int = DEFAULT_SEED

    @property
    def namespace(self) -> uuid.UUID:
        return uuid.uuid5(
            uuid.NAMESPACE_URL, f"testlookup:{DATASET_MARKER}:{self.slug}:{self.seed}"
        )

    def id(self, kind: str, *parts: object) -> uuid.UUID:
        return uuid.uuid5(self.namespace, ":".join((kind, *map(str, parts))))

    @property
    def project_id(self) -> uuid.UUID:
        return self.id("project")

    @property
    def project_name(self) -> str:
        return f"Synthetic Perf Dataset ({self.slug})"

    @property
    def description(self) -> str:
        return f"Synthetic rows for performance measurement · {DATASET_MARKER} · remove with --wipe"


# ─────────────────────────────────────────────────────────────────────────────
# Row shapes — field order IS the COPY column order
# ─────────────────────────────────────────────────────────────────────────────


class ReleaseRow(NamedTuple):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: str
    description: str
    status: str
    release_type: str
    sort_key: str
    planned_date: datetime
    released_at: Optional[datetime]
    created_at: datetime


class SuiteRow(NamedTuple):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str


class CanonicalRow(NamedTuple):
    id: uuid.UUID
    project_id: uuid.UUID
    test_suite_id: uuid.UUID
    test_fingerprint: str
    test_name: str
    class_name: str


class RunRow(NamedTuple):
    id: uuid.UUID
    project_id: uuid.UUID
    build_number: str
    jenkins_job: str
    trigger_source: str
    branch: str
    commit_hash: str
    status: str
    ingestion_source: str
    environment: str
    primary_release_id: Optional[uuid.UUID]
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    broken_tests: int
    unknown_tests: int
    pass_rate: float
    duration_ms: Optional[int]
    primary_suite_name: str
    suite_names: str  # JSON text — asyncpg's json codec takes str
    start_time: datetime
    end_time: datetime
    created_at: datetime


class CaseRow(NamedTuple):
    id: uuid.UUID
    test_run_id: uuid.UUID
    canonical_test_case_id: uuid.UUID
    test_fingerprint: str
    test_name: str
    full_name: str
    suite_name: str
    class_name: str
    package_name: str
    status: str
    duration_ms: Optional[int]
    severity: str
    feature: str
    failure_category: Optional[str]
    error_message: Optional[str]
    retry_count: Optional[int]
    is_flaky_run: Optional[bool]
    has_attachments: bool
    created_at: datetime


class LinkRow(NamedTuple):
    id: uuid.UUID
    release_id: uuid.UUID
    test_run_id: uuid.UUID
    link_source: str
    is_primary: bool
    project_id: uuid.UUID
    linked_at: datetime


# (table, row shape) — tests check every field is a real column and every
# NOT NULL column without a server default is a field.
COPY_TABLES = (
    ("releases", ReleaseRow),
    ("test_suites", SuiteRow),
    ("canonical_test_cases", CanonicalRow),
    ("test_runs", RunRow),
    ("test_cases", CaseRow),
    ("release_test_run_links", LinkRow),
)


class CatalogueTest(NamedTuple):
    index: int
    suite_index: int
    suite_name: str
    test_name: str
    class_name: str
    package_name: str
    test_fingerprint: str
    hot: bool


@dataclass(frozen=True)
class RunBatch:
    runs: list[RunRow]
    cases: list[CaseRow]
    links: list[LinkRow]


# ─────────────────────────────────────────────────────────────────────────────
# Failure signatures — 30 templates whose placeholders are all noise that
# flaky_signals.error_signature() strips, so each is exactly one signature.
# ─────────────────────────────────────────────────────────────────────────────

_EXCEPTIONS = (
    (
        "AssertionError",
        "returned {n} rows, expected {m} (request {ref})",
        FailureCategory.PRODUCT_BUG,
    ),
    (
        "TimeoutException",
        "did not respond within {n}ms (request {ref})",
        FailureCategory.INFRASTRUCTURE,
    ),
    (
        "ConnectionRefusedException",
        "refused connection on port {n} (attempt {m}, request {ref})",
        FailureCategory.INFRASTRUCTURE,
    ),
    (
        "NullPointerException",
        "handle@0x{ref} was null for record {n}",
        FailureCategory.PRODUCT_BUG,
    ),
    (
        "IllegalStateException",
        "circuit breaker open after {n} failures (request {ref})",
        FailureCategory.AUTOMATION_DEFECT,
    ),
    (
        "PSQLException",
        "deadlock detected on row {n} at {ts}",
        FailureCategory.TEST_DATA,
    ),
)
_COMPONENTS = (
    "checkout-service",
    "inventory-service",
    "notification-gateway",
    "search-index",
    "billing-worker",
)

SIGNATURE_TEMPLATES: tuple[tuple[str, FailureCategory], ...] = tuple(
    (f"{exc}: {component} {phrase}", category)
    for exc, phrase, category in _EXCEPTIONS
    for component in _COMPONENTS
)
SIGNATURE_COUNT = len(SIGNATURE_TEMPLATES)  # 30


def render_signature(
    index: int, rng: random.Random, at: datetime
) -> tuple[str, FailureCategory]:
    template, category = SIGNATURE_TEMPLATES[index]
    return template.format(
        n=rng.randint(1, 99_999),
        m=rng.randint(1, 999),
        ref=f"{rng.getrandbits(48):012x}",
        ts=at.strftime("%Y-%m-%dT%H:%M:%S"),
    ), category


# ─────────────────────────────────────────────────────────────────────────────
# Generators (pure, deterministic)
# ─────────────────────────────────────────────────────────────────────────────


def _fingerprint(test_name: str, class_name: str) -> str:
    # File ingestion's own recipe, so a real report ingested into this project
    # resolves to the same canonical tests instead of a parallel catalogue.
    return make_test_fingerprint(test_name, class_name)


def is_hot(spec: ScaleSpec, index: int) -> bool:
    """Hot by position within the test's pipeline slice — every pipeline gets
    the same number of hot tests, so the same expected failure rate."""
    return (index // spec.pipelines) % _HOT_TEST_EVERY == 0


def suite_sizes(spec: ScaleSpec) -> list[int]:
    """How many catalogue tests each suite holds: Zipf-skewed, every suite ≥ 1."""
    total = spec.tests_per_run * spec.pipelines
    weights = [1 / (i + 1) ** _SUITE_SKEW for i in range(spec.suites)]
    scale = total / sum(weights)
    sizes = [max(1, int(w * scale)) for w in weights]
    sizes[0] += total - sum(sizes)  # rounding remainder goes to the largest suite
    assert (
        sizes[0] >= 1 and sum(sizes) == total
    ), "suite sizes must partition the catalogue"
    return sizes


def build_catalogue(spec: ScaleSpec) -> tuple[CatalogueTest, ...]:
    """Every logical test, in suite order. Run r executes the tests whose
    index ≡ r (mod pipelines), so each pipeline is a fixed slice."""
    tests: list[CatalogueTest] = []
    for s, size in enumerate(suite_sizes(spec)):
        suite_name = f"PerfSuite{s:03d}"
        package = f"com.perf.s{s:03d}"
        class_name = f"{package}.PerfSuite{s:03d}Test"
        for k in range(size):
            g = len(tests)
            test_name = f"testCase{k:04d}"
            tests.append(
                CatalogueTest(
                    index=g,
                    suite_index=s,
                    suite_name=suite_name,
                    test_name=test_name,
                    class_name=class_name,
                    package_name=package,
                    test_fingerprint=_fingerprint(test_name, class_name),
                    hot=is_hot(spec, g),
                )
            )
    return tuple(tests)


def iter_releases(
    spec: ScaleSpec, identity: DatasetIdentity, now: datetime
) -> Iterator[ReleaseRow]:
    span = spec.history_days / spec.releases
    for k in range(spec.releases):
        starts = now - timedelta(days=spec.history_days - k * span)
        ends = starts + timedelta(days=span)
        version = f"{1 + k // 10}.{k % 10}.0"
        if k == spec.releases - 1:
            status = "in_progress"
        elif k % 7 == 3:
            status = "archived"
        elif k % 11 == 5:
            status = "cancelled"
        else:
            status = "released"
        yield ReleaseRow(
            id=identity.id("release", k),
            project_id=identity.project_id,
            name=f"v{version} — Synthetic release {k + 1}",
            version=version,
            description=identity.description,
            status=status,
            release_type="major" if k % 10 == 0 else "minor",
            sort_key=compute_sort_key(version, None),
            planned_date=ends,
            released_at=ends if status in ("released", "archived") else None,
            created_at=starts,
        )


def iter_suites(spec: ScaleSpec, identity: DatasetIdentity) -> Iterator[SuiteRow]:
    for s, size in enumerate(suite_sizes(spec)):
        yield SuiteRow(
            id=identity.id("suite", s),
            project_id=identity.project_id,
            name=f"PerfSuite{s:03d}",
            description=f"{size} synthetic tests · {DATASET_MARKER}",
        )


def iter_canonical(
    spec: ScaleSpec, identity: DatasetIdentity, catalogue=None
) -> Iterator[CanonicalRow]:
    for t in catalogue or build_catalogue(spec):
        yield CanonicalRow(
            id=identity.id("canonical", t.index),
            project_id=identity.project_id,
            test_suite_id=identity.id("suite", t.suite_index),
            test_fingerprint=t.test_fingerprint,
            test_name=t.test_name,
            class_name=t.class_name,
        )


def _release_index(spec: ScaleSpec, run_index: int) -> Optional[int]:
    if _is_unattributed(run_index):
        return None
    return run_index * spec.releases // spec.runs


def pipeline_bounds(spec: ScaleSpec, pipeline: int) -> tuple[int, int]:
    """(first, last) run index executing `pipeline` — the canonical seen pointers."""
    first = pipeline
    last = spec.runs - 1 - ((spec.runs - 1 - pipeline) % spec.pipelines)
    return first, last


def build_run(
    spec: ScaleSpec,
    identity: DatasetIdentity,
    now: datetime,
    catalogue: tuple[CatalogueTest, ...],
    run_index: int,
) -> tuple[RunRow, list[CaseRow], Optional[LinkRow]]:
    """One run and its cases. Seeded per run, so any run can be rebuilt alone."""
    rng = random.Random(f"{identity.namespace}:run:{run_index}")
    pipeline = run_index % spec.pipelines
    run_id = identity.id("run", run_index)

    # Evenly spread over the history, oldest first, with sub-slot jitter.
    slot = spec.history_days / spec.runs
    start = now - timedelta(
        days=spec.history_days - (run_index + rng.random() * 0.9) * slot
    )

    cases: list[CaseRow] = []
    counts = {status: 0 for status in TestStatus}
    duration_total = 0
    any_timed = False
    suites: set[str] = set()
    for t in catalogue[pipeline :: spec.pipelines]:
        u = rng.random()
        fail_p = _HOT_FAIL_P if t.hot else _COLD_FAIL_P
        if u < fail_p:
            status = (
                TestStatus.BROKEN if rng.random() < _BROKEN_SHARE else TestStatus.FAILED
            )
        elif u < fail_p + _SKIP_P:
            status = TestStatus.SKIPPED
        elif u < fail_p + _SKIP_P + _UNKNOWN_P:
            status = TestStatus.UNKNOWN
        else:
            status = TestStatus.PASSED
        counts[status] += 1
        suites.add(t.suite_name)

        duration: Optional[int] = None
        if (
            status not in (TestStatus.SKIPPED, TestStatus.UNKNOWN)
            and rng.random() >= _NULL_DURATION_P
        ):
            duration = min(
                _MAX_DURATION_MS,
                max(1, int(rng.lognormvariate(_DURATION_MU, _DURATION_SIGMA))),
            )
            duration_total += duration
            any_timed = True

        error_message = category = None
        if status in (TestStatus.FAILED, TestStatus.BROKEN):
            # Pareto-skewed pick: the first few signatures take most failures.
            signature = min(int(rng.paretovariate(1.2)) - 1, SIGNATURE_COUNT - 1)
            error_message, category = render_signature(signature, rng, start)

        retry_count = is_flaky_run = None
        if t.hot and status == TestStatus.PASSED and rng.random() < 0.3:
            retry_count, is_flaky_run = 1, True

        cases.append(
            CaseRow(
                id=identity.id("case", run_index, t.index),
                test_run_id=run_id,
                canonical_test_case_id=identity.id("canonical", t.index),
                test_fingerprint=t.test_fingerprint,
                test_name=t.test_name,
                full_name=f"{t.class_name}.{t.test_name}",
                suite_name=t.suite_name,
                class_name=t.class_name,
                package_name=t.package_name,
                status=status.value,
                duration_ms=duration,
                severity=SEVERITIES[t.suite_index % len(SEVERITIES)],
                feature=t.suite_name,
                failure_category=category.value if category else None,
                error_message=error_message,
                retry_count=retry_count,
                is_flaky_run=is_flaky_run,
                has_attachments=status in (TestStatus.FAILED, TestStatus.BROKEN),
                created_at=start,
            )
        )

    passed, failed, broken = (
        counts[TestStatus.PASSED],
        counts[TestStatus.FAILED],
        counts[TestStatus.BROKEN],
    )
    unknown = counts[TestStatus.UNKNOWN]
    duration_ms = duration_total if any_timed else None
    # Primary suite: most cases, alphabetical tiebreak — ingestion's rule.
    by_suite: dict[str, int] = {}
    for case in cases:
        by_suite[case.suite_name] = by_suite.get(case.suite_name, 0) + 1
    primary_suite = sorted(by_suite.items(), key=lambda item: (-item[1], item[0]))[0][0]

    release_index = _release_index(spec, run_index)
    release_id = (
        identity.id("release", release_index) if release_index is not None else None
    )
    run = RunRow(
        id=run_id,
        project_id=identity.project_id,
        build_number=f"perf-{run_index:06d}",
        jenkins_job=f"perf-pipeline-{pipeline:02d}",
        trigger_source=rng.choice(("push", "push", "push", "schedule", "manual")),
        branch=rng.choice(BRANCHES),
        commit_hash=f"{rng.getrandbits(48):012x}",
        status=terminal_run_status(
            executed_count(passed, failed, broken), failed, broken, unknown
        ).value,
        ingestion_source=rng.choice(INGESTION_SOURCES),
        environment=rng.choice(ENVIRONMENTS),
        primary_release_id=release_id,
        total_tests=len(cases),
        passed_tests=passed,
        failed_tests=failed,
        skipped_tests=counts[TestStatus.SKIPPED],
        broken_tests=broken,
        unknown_tests=unknown,
        pass_rate=canonical_pass_rate(passed, failed, broken),
        duration_ms=duration_ms,
        primary_suite_name=primary_suite,
        suite_names=json.dumps(sorted(suites)),
        start_time=start,
        end_time=start
        + timedelta(
            milliseconds=duration_ms if duration_ms is not None else 10 * 60 * 1000
        ),
        created_at=start,
    )
    link = None
    if release_id is not None:
        # Set directly, in step with primary_release_id above: the linker's
        # per-row round trips are not what a million-row load wants.
        link = LinkRow(
            id=identity.id("link", run_index),
            release_id=release_id,
            test_run_id=run_id,
            link_source=_LINK_SOURCE,
            is_primary=True,
            project_id=identity.project_id,
            linked_at=start,
        )
    return run, cases, link


def default_batch_runs(spec: ScaleSpec) -> int:
    return max(1, 10_000 // spec.tests_per_run)


def iter_run_batches(
    spec: ScaleSpec,
    identity: DatasetIdentity,
    now: datetime,
    batch_runs: Optional[int] = None,
) -> Iterator[RunBatch]:
    """Runs in batches of ~10k cases — the writer's unit of transaction."""
    catalogue = build_catalogue(spec)
    size = batch_runs or default_batch_runs(spec)
    for offset in range(0, spec.runs, size):
        batch = RunBatch(runs=[], cases=[], links=[])
        for run_index in range(offset, min(offset + size, spec.runs)):
            run, cases, link = build_run(spec, identity, now, catalogue, run_index)
            batch.runs.append(run)
            batch.cases.extend(cases)
            if link is not None:
                batch.links.append(link)
        yield batch


# ─────────────────────────────────────────────────────────────────────────────
# Writer (asyncpg COPY)
# ─────────────────────────────────────────────────────────────────────────────


class DatasetExistsError(RuntimeError):
    pass


def asyncpg_dsn(url: str) -> str:
    """The SQLAlchemy URL from settings, as asyncpg wants it."""
    return (
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def describe_target(url: str) -> str:
    """Host, port, database and user — never the password."""
    parsed = make_url(url)
    return f"{parsed.host}:{parsed.port or 5432}/{parsed.database} as {parsed.username}"


async def _connect(dsn: str):
    import asyncpg

    return await asyncpg.connect(asyncpg_dsn(dsn))


async def write_dataset(
    dsn: str,
    spec: ScaleSpec,
    *,
    identity: DatasetIdentity = DatasetIdentity(),
    now: Optional[datetime] = None,
    batch_runs: Optional[int] = None,
    log=print,
) -> PlanCounts:
    """Bulk-load one synthetic project. Raises DatasetExistsError if the slug is taken."""
    now = now or datetime.now(timezone.utc)
    counts = plan_counts(spec)
    catalogue = build_catalogue(spec)

    conn = await _connect(dsn)
    try:
        existing = await conn.fetchval(
            "SELECT id FROM projects WHERE slug = $1", identity.slug
        )
        if existing is not None:
            raise DatasetExistsError(
                f"project {identity.slug!r} already exists ({existing}) — run --wipe --yes first"
            )

        async with conn.transaction():
            await conn.execute(
                "INSERT INTO projects (id, name, slug, description, is_active) VALUES ($1, $2, $3, $4, true)",
                identity.project_id,
                identity.project_name,
                identity.slug,
                identity.description,
            )
            await conn.copy_records_to_table(
                "releases",
                records=list(iter_releases(spec, identity, now)),
                columns=list(ReleaseRow._fields),
            )
            await conn.copy_records_to_table(
                "test_suites",
                records=list(iter_suites(spec, identity)),
                columns=list(SuiteRow._fields),
            )
            await conn.copy_records_to_table(
                "canonical_test_cases",
                records=list(iter_canonical(spec, identity, catalogue)),
                columns=list(CanonicalRow._fields),
            )
        log(
            f"  {counts.releases} releases · {counts.suites} suites · {counts.canonical_tests} canonical tests"
        )

        written = 0
        for batch in iter_run_batches(spec, identity, now, batch_runs):
            async with conn.transaction():
                await conn.copy_records_to_table(
                    "test_runs", records=batch.runs, columns=list(RunRow._fields)
                )
                await conn.copy_records_to_table(
                    "test_cases", records=batch.cases, columns=list(CaseRow._fields)
                )
                if batch.links:
                    await conn.copy_records_to_table(
                        "release_test_run_links",
                        records=batch.links,
                        columns=list(LinkRow._fields),
                    )
            written += len(batch.cases)
            log(f"  {written:,}/{counts.test_cases:,} test cases")

        # Seen pointers last: they reference runs, which did not exist when the
        # canonical rows went in. Each pipeline's slice saw its first and last run.
        async with conn.transaction():
            for pipeline in range(spec.pipelines):
                first, last = pipeline_bounds(spec, pipeline)
                await conn.execute(
                    "UPDATE canonical_test_cases SET first_seen_run_id = $1, last_seen_run_id = $2 "
                    "WHERE project_id = $3 AND id = ANY($4::uuid[])",
                    identity.id("run", first),
                    identity.id("run", last),
                    identity.project_id,
                    [
                        identity.id("canonical", t.index)
                        for t in catalogue[pipeline :: spec.pipelines]
                    ],
                )
    finally:
        await conn.close()
    return counts


def _chunks(values: list, size: int):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


async def wipe_dataset(
    dsn: str, *, identity: DatasetIdentity = DatasetIdentity(), log=print
) -> int:
    """Remove the marked project and every row under it. Returns projects removed (0 or 1)."""
    conn = await _connect(dsn)
    try:
        # Slug AND marker: a real project that happens to share the slug is left
        # alone. strpos, not LIKE: the marker's "_" is a LIKE wildcard.
        project_id = await conn.fetchval(
            "SELECT id FROM projects WHERE slug = $1 AND strpos(description, $2) > 0",
            identity.slug,
            DATASET_MARKER,
        )
        if project_id is None:
            log(
                f"  no project with slug {identity.slug!r} and marker {DATASET_MARKER!r} — nothing to wipe"
            )
            return 0

        run_ids = [
            row["id"]
            for row in await conn.fetch(
                "SELECT id FROM test_runs WHERE project_id = $1 ORDER BY created_at",
                project_id,
            )
        ]
        # Order matters, and every statement repeats the project scope:
        #   1. cases, before canonicals: test_cases.canonical_test_case_id is
        #      SET NULL, so the other order rewrites a million rows first;
        #   2. links;
        #   3. canonicals, before runs: first_seen/last_seen/deleted_at_run_id
        #      are SET NULL pointers to runs — deleting a run they still name
        #      rewrites the canonical row the run was pointed at. (Postgres
        #      still runs the per-row RI lookup for each deleted run; those
        #      columns are unindexed, so only an index makes that lookup cheap.
        #      This order removes the writes, not the lookups.)
        #   4. suites, after canonicals (that FK is RESTRICT);
        #   5. runs, then releases (primary_release_id), then the project.
        # Runs go in chunks so no single transaction holds the whole table.
        for chunk in _chunks(run_ids, 200):
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM test_cases tc USING test_runs tr "
                    "WHERE tc.test_run_id = tr.id AND tr.project_id = $1 AND tc.test_run_id = ANY($2::uuid[])",
                    project_id,
                    chunk,
                )
        for chunk in _chunks(run_ids, 200):
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM release_test_run_links l USING test_runs tr "
                    "WHERE l.test_run_id = tr.id AND tr.project_id = $1 AND l.test_run_id = ANY($2::uuid[])",
                    project_id,
                    chunk,
                )
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM canonical_test_cases WHERE project_id = $1", project_id
            )
            await conn.execute(
                "DELETE FROM test_suites WHERE project_id = $1", project_id
            )
        for chunk in _chunks(run_ids, 200):
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM test_runs WHERE project_id = $1 AND id = ANY($2::uuid[])",
                    project_id,
                    chunk,
                )
        async with conn.transaction():
            await conn.execute("DELETE FROM releases WHERE project_id = $1", project_id)
            await conn.execute("DELETE FROM projects WHERE id = $1", project_id)
        log(f"  removed project {identity.slug!r} ({len(run_ids)} runs)")
        return 1
    finally:
        await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load a marked synthetic project for performance measurement"
    )
    parser.add_argument(
        "--scale",
        choices=sorted(SCALES),
        default="small",
        help="dataset size (default: small)",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="remove the marked project and its rows instead of writing",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually write (or wipe); without it, only the plan is printed",
    )
    parser.add_argument(
        "--slug", default=DEFAULT_SLUG, help=f"project slug (default: {DEFAULT_SLUG})"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"generator seed (default: {DEFAULT_SEED})",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    if settings.is_production:
        print(
            "Refusing: APP_ENV=production. This generator is for non-production databases only.",
            file=sys.stderr,
        )
        return 2
    url = str(settings.DATABASE_URL or "")
    if not url:
        print("Refusing: DATABASE_URL is not set.", file=sys.stderr)
        return 2
    identity = DatasetIdentity(slug=args.slug, seed=args.seed)
    print(f"Target database: {describe_target(url)}")
    print(f"Project: {identity.slug} (marker {DATASET_MARKER})")

    if args.wipe:
        if not args.yes:
            print("Dry run — re-run with --yes to wipe.")
            return 2
        print("Wiping...")
        await wipe_dataset(url, identity=identity)
        return 0

    spec = SCALES[args.scale]
    counts = plan_counts(spec)
    print(
        f"Scale {spec.name}: {counts.runs:,} runs · {counts.test_cases:,} test cases · "
        f"{counts.releases} releases · {counts.suites} suites · {counts.canonical_tests:,} canonical tests · "
        f"{counts.unattributed_runs} runs unattributed"
    )
    if not args.yes:
        print("Dry run — re-run with --yes to write.")
        return 2
    print("Writing...")
    await write_dataset(url, spec, identity=identity)
    print("Done.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
