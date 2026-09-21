#!/usr/bin/env python3
"""
TestLookup — Visualization Demo Seed (VIZ-213)
=================================================
Adds, on top of seed_dev_data.py, the shapes the analytics charts have to
survive but the base seed never produces:
  - 8 more releases spread over ~3 months, every status in the vocabulary
  - runs across 3 environments and 5 branches (plus runs that carry neither)
  - failures that collapse into exactly 5 error signatures, plus a group of
    failures that carry no error message at all
  - an in-progress run dated today, UNKNOWN results, NULL durations
  - a suite that is skipped in every run (pass rate "not measured", not 0%)
  - a suite nobody has executed for over 30 days

Two halves, deliberately:
  build_viz_seed_plan()   pure and deterministic — no database, no globals.
                          Every guarantee above is asserted on it in
                          tests/test_seed_viz_data.py.
  apply_viz_seed()        thin writer. Links runs to releases and syncs the
                          canonical catalog through the same services
                          ingestion uses, so nothing denormalised is hand-set.
                          The in-progress run is left as the live drainer
                          leaves one: no history, no canonical sync yet.

Called once per project from seed_dev_data.py::main. It owns its own
random.Random: one extra draw from seed_dev_data.RNG would shift every value
the base seed generates after it.

Inspect the plan without a database:
    python scripts/seed_viz_data.py [project-slug]
"""

from __future__ import annotations

import os
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pass_rate import canonical_pass_rate, executed_count
from app.models.postgres import (
    FailureCategory,
    LaunchStatus,
    LinkSource,
    Project,
    Release,
    ReleasePhase,
    Severity,
    TestCase,
    TestCaseHistory,
    TestRun,
    TestStatus,
    User,
)
from app.services.run_status import terminal_run_status

# ─────────────────────────────────────────────────────────────────────────────
# Seed marker + catalogue
# ─────────────────────────────────────────────────────────────────────────────

VIZ_SEED_MARKER = "seed_viz_data_v1"
VIZ_SEED = 213  # fixed — same plan on every reset (timestamps move with `now`)

# Already in the form run_environment.normalize_environment() writes.
VIZ_ENVIRONMENTS = ("staging", "qa", "prod-mirror")

ALWAYS_SKIPPED_SUITE = "QuarantinedSuite"
STALE_SUITE = "LegacyBatchSuite"
STALE_AFTER_DAYS = 30  # "not executed recently" boundary the charts use
_STALE_SUITE_RETIRED_DAYS = 45  # margin, so the demo stays true for a fortnight
_REGRESSION_DAYS = 12  # InventorySuite case 01 fails in every run this recent


@dataclass(frozen=True)
class _SuiteDef:
    name: str
    package: str
    feature: str
    severity: str
    tests: int


# Distinct from seed_dev_data.SUITES on purpose: a different class name is a
# different fingerprint, so the base runs' history is left exactly as it was.
VIZ_SUITES = (
    _SuiteDef("CheckoutSuite", "com.qa.checkout", "Checkout", "BLOCKER", 6),
    _SuiteDef("InventorySuite", "com.qa.inventory", "Inventory", "CRITICAL", 5),
    _SuiteDef("NotificationSuite", "com.qa.notification", "Notifications", "MAJOR", 4),
    _SuiteDef("ReportingSuite", "com.qa.reporting", "Reporting", "MINOR", 4),
    _SuiteDef(ALWAYS_SKIPPED_SUITE, "com.qa.quarantine", "Quarantine", "MINOR", 3),
    _SuiteDef(STALE_SUITE, "com.qa.legacybatch", "LegacyBatch", "MAJOR", 3),
)

# A live run that has reported only its first suites so far.
_IN_PROGRESS_SUITES = ("CheckoutSuite", "InventorySuite")


@dataclass(frozen=True)
class _FailureGroup:
    template: str
    category: FailureCategory


# Exactly five. Every placeholder is noise flaky_signals.error_signature()
# strips — bare numbers, long hex ids, 0x addresses, ISO timestamps — so each
# template is ONE signature however many raw messages it renders to. A sixth
# entry here is a sixth failure group on every chart that keys on signature.
FAILURE_GROUPS = {
    "gateway_timeout": _FailureGroup(
        "TimeoutException: checkout-service did not respond within {ms}ms (request {request_id})",
        FailureCategory.INFRASTRUCTURE,
    ),
    "status_assertion": _FailureGroup(
        "AssertionError: expected HTTP 200 but got {code} for order {order_id}",
        FailureCategory.PRODUCT_BUG,
    ),
    "null_inventory_item": _FailureGroup(
        "NullPointerException: InventoryItem@{address} was null while reserving sku {sku}",
        FailureCategory.PRODUCT_BUG,
    ),
    "stock_deadlock": _FailureGroup(
        "PSQLException: deadlock detected while updating stock row {row} at {timestamp}",
        FailureCategory.TEST_DATA,
    ),
    "notification_refused": _FailureGroup(
        "ConnectionRefusedException: notification-gateway:{port} unreachable (attempt {attempt} of 5)",
        FailureCategory.INFRASTRUCTURE,
    ),
}

# (key, name, version, status, release_type, starts_days_ago, ends_days_ago, target_environment)
# ends_days_ago < 0 is a planned date in the future. Names and versions must
# stay clear of seed_dev_data.RELEASES_TEMPLATE: uq_releases_project_lower_name
# rejects a duplicate. Statuses are the declared vocabulary only — see the
# comment on Release.status about the drifted values live rows also carry.
_RELEASE_DEFS = (
    ("0.6.0", "v0.6.0 — Private Beta", "0.6.0", "released", "minor", 96, 84, "staging"),
    (
        "0.7.0",
        "v0.7.0 — Checkout Revamp",
        "0.7.0",
        "released",
        "minor",
        84,
        70,
        "staging",
    ),
    ("0.8.0", "v0.8.0 — Inventory Sync", "0.8.0", "archived", "minor", 70, 58, "qa"),
    (
        "0.8.1",
        "v0.8.1 — Stock Hotfix",
        "0.8.1",
        "released",
        "hotfix",
        58,
        52,
        "prod-mirror",
    ),
    (
        "0.9.0",
        "v0.9.0 — Notifications",
        "0.9.0",
        "released",
        "minor",
        52,
        38,
        "staging",
    ),
    (
        "0.9.5",
        "v0.9.5 — Reporting Preview",
        "0.9.5",
        "cancelled",
        "minor",
        38,
        30,
        "qa",
    ),
    (
        "1.0.0-rc1",
        "v1.0.0-rc1 — Release Candidate",
        "1.0.0-rc1",
        "in_progress",
        "rc",
        30,
        -5,
        "prod-mirror",
    ),
    ("2.0.0", "v2.0.0 — Platform Rewrite", "2.0.0", "planning", "major", 30, -60, None),
)

# The rewrite is validated from its own branch, not from a date window.
_PLATFORM_BRANCH = "feature/platform-v2"
_PLATFORM_RELEASE_KEY = "2.0.0"

_PHASES_BY_STATUS = {
    "released": (
        ("Feature Freeze", "code_freeze", "completed"),
        ("QA Regression", "qa_testing", "completed"),
        ("Production Deploy", "production", "completed"),
    ),
    "cancelled": (
        ("Feature Freeze", "code_freeze", "completed"),
        ("QA Regression", "qa_testing", "skipped"),
    ),
    "in_progress": (
        ("Feature Freeze", "code_freeze", "completed"),
        ("QA Regression", "qa_testing", "in_progress"),
        ("Production Deploy", "production", "pending"),
    ),
    "planning": (
        ("Development Complete", "development", "pending"),
        ("QA Regression", "qa_testing", "pending"),
    ),
}
_PHASES_BY_STATUS["archived"] = _PHASES_BY_STATUS["released"]

_ENV_CYCLE = ("staging", "qa", "staging", "prod-mirror", "qa", "staging")
_OLD_BRANCHES = ("main", "develop", "main", "feature/checkout-v2", "main")
_RECENT_BRANCHES = ("release/1.0", "main", "release/1.0", "develop", _PLATFORM_BRANCH)
_INGESTION_SOURCES = ("sdk", "file", "sdk", "live")
# How the attribution was decided — a mix, so the release scorecard's
# asserted-versus-inferred split has something on both sides.
_LINK_SOURCES = (
    LinkSource.EXPLICIT_CLIENT.value,
    LinkSource.EXPLICIT_CLIENT.value,
    LinkSource.ACTIVE_RELEASE.value,
    LinkSource.EXPLICIT_CLIENT.value,
    LinkSource.MANUAL_UI.value,
)


# ─────────────────────────────────────────────────────────────────────────────
# Plan model
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhasePlan:
    name: str
    phase_type: str
    status: str
    order_index: int


@dataclass(frozen=True)
class ReleasePlan:
    key: str
    name: str
    version: str
    status: str
    release_type: str
    description: str
    created_at: datetime
    planned_date: datetime
    released_at: Optional[datetime]
    target_environment: Optional[str]
    phases: tuple[PhasePlan, ...]


@dataclass(frozen=True)
class CasePlan:
    test_name: str
    class_name: str
    package_name: str
    full_name: str
    suite_name: str
    feature: str
    severity: str
    test_fingerprint: str
    status: TestStatus
    duration_ms: Optional[int]
    error_message: Optional[str]
    stack_trace: Optional[str]
    failure_category: Optional[FailureCategory]
    retry_count: Optional[int]
    is_flaky_run: Optional[bool]


@dataclass(frozen=True)
class RunPlan:
    build_number: str
    jenkins_job: str
    trigger_source: str
    branch: Optional[str]
    environment: Optional[str]
    commit_hash: Optional[str]
    ingestion_source: str
    status: LaunchStatus
    start_time: datetime
    end_time: Optional[datetime]
    release_key: Optional[str]  # None = deliberately unattributed
    link_source: Optional[str]
    cases: tuple[CasePlan, ...]
    # Aggregates — always derived from `cases` by _aggregate(), never typed in.
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    broken_tests: int
    unknown_tests: int
    pass_rate: Optional[float]  # None while the run is in progress
    duration_ms: Optional[int]
    primary_suite_name: Optional[str]
    suite_names: tuple[str, ...]


@dataclass(frozen=True)
class VizSeedPlan:
    project_slug: str
    generated_at: datetime
    seed: int
    releases: tuple[ReleasePlan, ...]
    runs: tuple[RunPlan, ...]  # oldest → newest


@dataclass(frozen=True)
class VizSeedSummary:
    releases: int
    runs: int
    test_cases: int
    linked_runs: int
    unattributed_runs: int
    skipped: bool = False  # True when the project already had this seed


@dataclass(frozen=True)
class _Slot:
    """One run-to-be: where it sits in time and which edge case it carries."""

    index: int
    age_days: float  # nominal, before jitter
    bare_upload: bool = False  # no branch, no environment, no release
    has_unknown: bool = False  # two results the server could not interpret
    untimed: bool = False  # a report format that carries no durations
    unattributed: bool = False
    in_progress: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fingerprint(test_name: str, class_name: str) -> str:
    # File ingestion's own recipe, so a real report ingested into a demo
    # project links to the same canonical tests. Imported on call: the
    # ingestion module pulls in the service layer, and this planner must
    # stay importable without it (see apply_viz_seed).
    from app.services.ingestion import make_test_fingerprint

    return make_test_fingerprint(test_name, class_name)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _run_slots() -> list[_Slot]:
    slots: list[_Slot] = []
    # Older than the base seed's 30-day window: one run every three days.
    for k, age in enumerate(range(95, 31, -3)):
        slots.append(
            _Slot(
                index=len(slots),
                age_days=float(age),
                bare_upload=(k == 5),
                has_unknown=(k == 8),
            )
        )
    # Inside it, and denser — the default 30-day charts are where most of the
    # edge cases have to show up.
    for k in range(22):
        slots.append(
            _Slot(
                index=len(slots),
                age_days=round(29 - k * 1.3, 1),
                bare_upload=(k == 6),
                has_unknown=k in (3, 14),
                untimed=(k == 10),
                unattributed=(k == 17),
            )
        )
    slots.append(_Slot(index=len(slots), age_days=0.0, in_progress=True))
    return slots


def _suites_for(slot: _Slot) -> list[_SuiteDef]:
    if slot.in_progress:
        return [s for s in VIZ_SUITES if s.name in _IN_PROGRESS_SUITES]
    return [
        s
        for s in VIZ_SUITES
        if s.name != STALE_SUITE or slot.age_days >= _STALE_SUITE_RETIRED_DAYS
    ]


def _release_key_for(age: timedelta, branch: Optional[str]) -> Optional[str]:
    """The release whose window holds a run started `age` before now.

    `age` is the run's ACTUAL age, jitter included: attributing on the
    nominal slot age let a run jittered past a window's start land in a
    release created after it.
    """
    for key, _name, _version, _status, _type, starts, ends, _env in _RELEASE_DEFS:
        if key == _PLATFORM_RELEASE_KEY:
            # By branch, not by window — but never before the release existed.
            if branch == _PLATFORM_BRANCH and age <= timedelta(days=starts):
                return key
        elif branch != _PLATFORM_BRANCH and timedelta(days=starts) >= age > timedelta(
            days=ends
        ):
            return key
    return None


def _outcome(
    suite_name: str,
    n: int,
    slot: _Slot,
    environment: Optional[str],
) -> tuple[TestStatus, Optional[str], Optional[int], Optional[bool]]:
    """(status, failure group, retry_count, is_flaky_run) for one test in one run.

    Scripted on the run index rather than rolled: a guarantee that holds "with
    high probability" is a test that fails one reseed in a thousand. The RNG
    only supplies what no guarantee depends on — durations, jitter, noise.
    """
    i = slot.index
    key = (suite_name, n)
    if suite_name == ALWAYS_SKIPPED_SUITE:
        return TestStatus.SKIPPED, None, None, None
    if slot.has_unknown and key in (("NotificationSuite", 3), ("ReportingSuite", 4)):
        return TestStatus.UNKNOWN, None, None, None
    # Environment-sensitive: only ever times out against prod-mirror.
    if key == ("CheckoutSuite", 2) and environment == "prod-mirror" and i % 12 != 9:
        return TestStatus.FAILED, "gateway_timeout", None, None
    # A classic flake: fails after two retries, passes on the first retry, passes clean.
    if key == ("CheckoutSuite", 3):
        if i % 3 == 1:
            return TestStatus.FAILED, "status_assertion", 2, True
        if i % 3 == 2:
            return TestStatus.PASSED, None, 1, True
    # A persistent regression: one signature, every recent run.
    if key == ("InventorySuite", 1) and (
        slot.in_progress or slot.age_days <= _REGRESSION_DAYS
    ):
        return TestStatus.FAILED, "null_inventory_item", None, None
    if key == ("InventorySuite", 2) and i % 5 == 2:
        return TestStatus.FAILED, "stock_deadlock", None, None
    # Fails without saying why — the group that has no signature at all.
    if key == ("InventorySuite", 4) and i % 6 == 4:
        return TestStatus.FAILED, None, None, None
    if key == ("NotificationSuite", 1) and i % 4 == 1:
        return TestStatus.BROKEN, "notification_refused", None, None
    if key == ("NotificationSuite", 2) and i % 7 == 0:
        return TestStatus.PASSED, None, 1, True
    if key == ("ReportingSuite", 1) and i % 5 == 0:
        return TestStatus.SKIPPED, None, None, None
    # Same signature as the checkout flake: a failure group spans suites.
    if key == ("ReportingSuite", 2) and i % 8 == 4:
        return TestStatus.FAILED, "status_assertion", None, None
    if key == (STALE_SUITE, 2) and i % 4 == 2:
        return TestStatus.FAILED, "stock_deadlock", None, None
    return TestStatus.PASSED, None, None, None


def _duration(
    status: TestStatus,
    suite_name: str,
    n: int,
    slot: _Slot,
    rng: random.Random,
) -> Optional[int]:
    # Absence is not a measurement of zero: a test that never ran, a result
    # nobody could read and a report without timings all stay NULL.
    if (
        slot.untimed
        or status == TestStatus.UNKNOWN
        or suite_name == ALWAYS_SKIPPED_SUITE
    ):
        return None
    if (suite_name, n) == ("CheckoutSuite", 5) and slot.index % 4 == 0:
        return None
    if (suite_name, n) == ("ReportingSuite", 3) and slot.index % 3 == 0:
        return None
    if status == TestStatus.SKIPPED:
        return rng.randint(5, 40)
    if status == TestStatus.BROKEN:
        return rng.randint(30000, 31500)
    if status == TestStatus.FAILED:
        return rng.randint(2500, 16000)
    return rng.randint(150, 4000)


def _render_message(
    group: str, slot: _Slot, n: int, start: datetime, rng: random.Random
) -> str:
    # The run index is in every numeric field, so no two failures of a group
    # render to the same raw message — and they still share one signature.
    i = slot.index
    return FAILURE_GROUPS[group].template.format(
        ms=30000 + 17 * i,
        request_id=f"{rng.getrandbits(48):012x}",
        code=(500, 502, 503)[i % 3],
        order_id=48000 + 37 * i + n,
        address=f"0x{rng.getrandbits(32):08x}",
        sku=7000 + i,
        row=100 + i,
        timestamp=start.strftime("%Y-%m-%dT%H:%M:%S"),
        port=8443,
        attempt=1 + i % 5,
    )


def _suite_attribution(
    cases: tuple[CasePlan, ...],
) -> tuple[Optional[str], tuple[str, ...]]:
    # Mirrors ingestion.compute_suite_attribution (highest count, alphabetical
    # tiebreak) without importing the ingestion stack into a pure planner;
    # tests/test_seed_viz_data.py pins the two together.
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.suite_name] = counts.get(case.suite_name, 0) + 1
    if not counts:
        return None, ()
    primary = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return primary, tuple(sorted(counts))


def _aggregate(cases: tuple[CasePlan, ...]) -> dict:
    """Run-level aggregates, by the rules ingestion._update_run_aggregates applies."""

    def count(status: TestStatus) -> int:
        return sum(1 for case in cases if case.status == status)

    passed, failed, broken = (
        count(TestStatus.PASSED),
        count(TestStatus.FAILED),
        count(TestStatus.BROKEN),
    )
    timed = [case.duration_ms for case in cases if case.duration_ms is not None]
    primary_suite, suite_names = _suite_attribution(cases)
    return {
        "total_tests": len(cases),
        "passed_tests": passed,
        "failed_tests": failed,
        "skipped_tests": count(TestStatus.SKIPPED),
        "broken_tests": broken,
        "unknown_tests": count(TestStatus.UNKNOWN),
        # Canonical rule: skipped and unknown are outside the denominator.
        "pass_rate": canonical_pass_rate(passed, failed, broken),
        "duration_ms": sum(timed) if timed else None,
        "primary_suite_name": primary_suite,
        "suite_names": suite_names,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Plan (pure)
# ─────────────────────────────────────────────────────────────────────────────


def _plan_release(defn: tuple, now: datetime) -> ReleasePlan:
    key, name, version, status, release_type, starts, ends, target_env = defn
    planned = now - timedelta(days=ends)
    shipped = status in ("released", "archived")
    return ReleasePlan(
        key=key,
        name=name,
        version=version,
        status=status,
        release_type=release_type,
        description=f"Demo release for the analytics charts · {VIZ_SEED_MARKER}",
        created_at=now - timedelta(days=starts),
        planned_date=planned,
        released_at=planned if shipped else None,
        target_environment=target_env,
        phases=tuple(
            PhasePlan(
                name=p_name, phase_type=p_type, status=p_status, order_index=idx + 1
            )
            for idx, (p_name, p_type, p_status) in enumerate(_PHASES_BY_STATUS[status])
        ),
    )


def _plan_case(
    suite: _SuiteDef,
    n: int,
    slot: _Slot,
    environment: Optional[str],
    start: datetime,
    rng: random.Random,
) -> CasePlan:
    test_name = f"test{suite.feature}Case{n:02d}"
    class_name = f"{suite.package}.{suite.name}Test"
    status, group, retry_count, is_flaky_run = _outcome(
        suite.name, n, slot, environment
    )

    error_message = stack_trace = failure_category = None
    if status in (TestStatus.FAILED, TestStatus.BROKEN):
        if group is None:
            failure_category = FailureCategory.UNKNOWN
        else:
            error_message = _render_message(group, slot, n, start, rng)
            failure_category = FAILURE_GROUPS[group].category
            stack_trace = (
                f"{error_message}\n\tat {class_name}.{test_name}({suite.name}Test.java:{40 + n})"
                "\n\tat com.qa.runner.TestRunner.run(TestRunner.java:120)"
            )

    return CasePlan(
        test_name=test_name,
        class_name=class_name,
        package_name=suite.package,
        full_name=f"{class_name}.{test_name}",
        suite_name=suite.name,
        feature=suite.feature,
        severity=suite.severity,
        test_fingerprint=_fingerprint(test_name, class_name),
        status=status,
        duration_ms=_duration(status, suite.name, n, slot, rng),
        error_message=error_message,
        stack_trace=stack_trace,
        failure_category=failure_category,
        retry_count=retry_count,
        is_flaky_run=is_flaky_run,
    )


def _plan_run(
    slot: _Slot, project_slug: str, now: datetime, rng: random.Random
) -> RunPlan:
    i = slot.index
    if slot.in_progress:
        # Today (UTC) even when `now` is seconds past midnight, and never in the future.
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = max(midnight, now - timedelta(minutes=25))
    else:
        start = now - timedelta(days=slot.age_days + rng.uniform(0, 0.2))

    if slot.bare_upload:
        environment = branch = commit_hash = None
    else:
        environment = _ENV_CYCLE[i % len(_ENV_CYCLE)]
        branches = (
            _RECENT_BRANCHES if slot.age_days < STALE_AFTER_DAYS else _OLD_BRANCHES
        )
        branch = branches[i % len(branches)]
        commit_hash = f"{rng.getrandbits(48):012x}"

    cases = tuple(
        _plan_case(suite, n, slot, environment, start, rng)
        for suite in _suites_for(slot)
        for n in range(1, suite.tests + 1)
    )
    aggregates = _aggregate(cases)

    if slot.in_progress:
        # The shape the live path writes (stream_service.create_session's stub,
        # then live_session_drainer._project on every drain): trigger_source
        # "live_stream", counts as reported so far, no pass_rate and no
        # duration until the session completes, and end_time stamped with
        # the moment of the latest drain — here, `now`.
        status, ingestion_source, trigger_source = (
            LaunchStatus.IN_PROGRESS,
            "live",
            "live_stream",
        )
        end_time = now
        aggregates["duration_ms"] = None
        aggregates["pass_rate"] = None
    else:
        status = terminal_run_status(
            executed_count(
                aggregates["passed_tests"],
                aggregates["failed_tests"],
                aggregates["broken_tests"],
            ),
            aggregates["failed_tests"],
            aggregates["broken_tests"],
            aggregates["unknown_tests"],
        )
        end_time = start + timedelta(
            milliseconds=aggregates["duration_ms"] or 12 * 60 * 1000
        )
        if slot.bare_upload:
            ingestion_source, trigger_source = "upload", "manual"
        else:
            ingestion_source = (
                "file"
                if slot.untimed
                else _INGESTION_SOURCES[i % len(_INGESTION_SOURCES)]
            )
            trigger_source = rng.choice(["push", "push", "schedule", "manual"])

    attributed = not (slot.bare_upload or slot.unattributed or slot.in_progress)
    release_key = _release_key_for(now - start, branch) if attributed else None

    return RunPlan(
        build_number=f"viz-{3000 + i}",
        jenkins_job=f"{project_slug.replace('-', '_')}-{environment or 'adhoc'}-pipeline",
        trigger_source=trigger_source,
        branch=branch,
        environment=environment,
        commit_hash=commit_hash,
        ingestion_source=ingestion_source,
        status=status,
        start_time=start,
        end_time=end_time,
        release_key=release_key,
        link_source=_LINK_SOURCES[i % len(_LINK_SOURCES)] if release_key else None,
        cases=cases,
        **aggregates,
    )


def build_viz_seed_plan(
    project_slug: str, now: datetime, seed: int = VIZ_SEED
) -> VizSeedPlan:
    """Everything apply_viz_seed() will write for one project, as plain data.

    Deterministic for fixed inputs. `now` anchors every timestamp, so the
    in-progress run is dated today and the stale suite stays stale.
    """
    now = _as_utc(now)
    # A str seed is hashed with SHA-512, so this is stable across processes
    # and platforms — unlike hash(), which PYTHONHASHSEED randomises.
    rng = random.Random(f"{VIZ_SEED_MARKER}:{seed}:{project_slug}")
    return VizSeedPlan(
        project_slug=project_slug,
        generated_at=now,
        seed=seed,
        releases=tuple(_plan_release(defn, now) for defn in _RELEASE_DEFS),
        runs=tuple(_plan_run(slot, project_slug, now, rng) for slot in _run_slots()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Writer
# ─────────────────────────────────────────────────────────────────────────────


async def apply_viz_seed(
    db: AsyncSession,
    project: Project,
    created_by: Optional[User],
    plan: VizSeedPlan,
) -> VizSeedSummary:
    """Write `plan` for `project`. Flushes; the caller owns the commit."""
    # Imported here, not at the top: these pull in the service layer, and the
    # planner above has to stay importable (and testable) without it.
    from app.services.release_linker import link_run_to_release
    from app.services.release_sort_key import compute_sort_key
    from app.services.test_suite_service import sync_canonical_test_cases

    already = (
        await db.execute(
            select(Release.id)
            .where(
                Release.project_id == project.id,
                Release.name == plan.releases[0].name,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if already is not None:
        return VizSeedSummary(0, 0, 0, 0, 0, skipped=True)

    created_by_id = created_by.id if created_by is not None else None

    release_ids: dict[str, uuid.UUID] = {}
    for rel in plan.releases:
        release = Release(
            id=uuid.uuid4(),
            project_id=project.id,
            name=rel.name,
            version=rel.version,
            description=rel.description,
            status=rel.status,
            release_type=rel.release_type,
            sort_key=compute_sort_key(rel.version, rel.name),
            target_environment=rel.target_environment,
            planned_date=rel.planned_date,
            released_at=rel.released_at,
            created_by_id=created_by_id,
            created_at=rel.created_at,
        )
        db.add(release)
        await db.flush()
        for phase in rel.phases:
            db.add(
                ReleasePhase(
                    id=uuid.uuid4(),
                    release_id=release.id,
                    name=phase.name,
                    phase_type=phase.phase_type,
                    status=phase.status,
                    order_index=phase.order_index,
                )
            )
        release_ids[rel.key] = release.id
    await db.flush()

    test_cases = 0
    linked = 0
    # Oldest first: canonical sync stamps last_seen_run_id with whichever run
    # it processed last, so order is what makes the stale suite stale.
    for run_plan in plan.runs:
        run = TestRun(
            id=uuid.uuid4(),
            project_id=project.id,
            build_number=run_plan.build_number,
            jenkins_job=run_plan.jenkins_job,
            trigger_source=run_plan.trigger_source,
            branch=run_plan.branch,
            commit_hash=run_plan.commit_hash,
            environment=run_plan.environment,
            ingestion_source=run_plan.ingestion_source,
            status=run_plan.status,
            total_tests=run_plan.total_tests,
            passed_tests=run_plan.passed_tests,
            failed_tests=run_plan.failed_tests,
            skipped_tests=run_plan.skipped_tests,
            broken_tests=run_plan.broken_tests,
            unknown_tests=run_plan.unknown_tests,
            pass_rate=run_plan.pass_rate,
            duration_ms=run_plan.duration_ms,
            primary_suite_name=run_plan.primary_suite_name,
            suite_names=list(run_plan.suite_names),
            minio_prefix=f"{project.slug}/runs/{run_plan.build_number}",
            tags=[VIZ_SEED_MARKER],
            start_time=run_plan.start_time,
            end_time=run_plan.end_time,
            created_at=run_plan.start_time,
        )
        db.add(run)
        await db.flush()

        tcs: list[TestCase] = []
        for case in run_plan.cases:
            tc = TestCase(
                id=uuid.uuid4(),
                test_run_id=run.id,
                test_fingerprint=case.test_fingerprint,
                test_name=case.test_name,
                full_name=case.full_name,
                suite_name=case.suite_name,
                class_name=case.class_name,
                package_name=case.package_name,
                status=case.status,
                duration_ms=case.duration_ms,
                severity=Severity(case.severity),
                feature=case.feature,
                failure_category=case.failure_category,
                error_message=case.error_message,
                stack_trace=case.stack_trace,
                retry_count=case.retry_count,
                is_flaky_run=case.is_flaky_run,
                tags=[case.feature.lower()],
                has_attachments=case.status in (TestStatus.FAILED, TestStatus.BROKEN),
                created_at=run_plan.start_time,
            )
            db.add(tc)
            tcs.append(tc)
        await db.flush()
        test_cases += len(tcs)

        if run_plan.release_key is not None:
            # The real linker: it decides is_primary and keeps
            # test_runs.primary_release_id in step with the link it wrote.
            manual = run_plan.link_source == LinkSource.MANUAL_UI.value
            await link_run_to_release(
                db,
                release_ids[run_plan.release_key],
                run.id,
                link_source=run_plan.link_source,
                project_id=project.id,
                linked_by_id=created_by_id if manual else None,
            )
            linked += 1

        if run_plan.status == LaunchStatus.IN_PROGRESS:
            # What the live drainer leaves behind mid-session: projected cases
            # and nothing else. History and the canonical sync belong to the
            # finalize pipeline (sync_canonical_test_cases reconciles a
            # FINALISED run), so these cases keep canonical_test_case_id NULL
            # and no catalogue pointer names this run. Every one of its
            # fingerprints was already catalogued by an earlier finished run.
            continue

        # Test case history (flakiness tracking) — same shape the base seed writes.
        for tc in tcs:
            db.add(
                TestCaseHistory(
                    id=uuid.uuid4(),
                    test_case_id=tc.id,
                    test_run_id=run.id,
                    test_fingerprint=tc.test_fingerprint,
                    status=tc.status,
                    duration_ms=tc.duration_ms,
                    failure_category=tc.failure_category,
                    created_at=run_plan.start_time,
                )
            )
        await db.flush()

        # The real catalog path too: TestSuite rows, CanonicalTestCase rows
        # and their first/last-seen pointers, test_cases.canonical_test_case_id.
        await sync_canonical_test_cases(db, project.id, run.id)

    await db.flush()
    return VizSeedSummary(
        releases=len(plan.releases),
        runs=len(plan.runs),
        test_cases=test_cases,
        linked_runs=linked,
        unattributed_runs=len(plan.runs) - linked,
    )


if __name__ == "__main__":
    # No database involved — prints what would be written.
    slug = sys.argv[1] if len(sys.argv) > 1 else "ecommerce-platform"
    demo = build_viz_seed_plan(slug, datetime.now(timezone.utc))
    print(
        f"{slug}: {len(demo.releases)} releases · {len(demo.runs)} runs · "
        f"{sum(r.total_tests for r in demo.runs)} test cases"
    )
    for r in demo.runs:
        print(
            f"  {r.build_number}  {r.start_time:%Y-%m-%d}  {r.status.value:<11} "
            f"{str(r.environment):<12} {str(r.branch):<22} release={r.release_key}  "
            f"{r.passed_tests}/{r.failed_tests}/{r.skipped_tests}/{r.broken_tests}/{r.unknown_tests}"
        )
