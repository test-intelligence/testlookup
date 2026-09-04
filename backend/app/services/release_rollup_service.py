"""Per-test-latest rollup over a release's runs, and the verdict it supports.

The question
------------
"Is 2.4.0 shippable?" is not "did the last run pass". A release accumulates many
runs, the same test appears in several of them, and a test that failed on Monday
and passed on Friday is a PASS for the release — the fix landed. Rolling up by
run, or by summing every result, answers a different question and answers it
loudly: a test re-run ten times contributes ten results and drowns out nine
tests that ran once.

So the unit is the TEST, and its value is its LATEST result within the release.

Where "latest" comes from, and the caveat on it
-----------------------------------------------
``TestRun.start_time``, which is MEANT to be execution time. Review found that
claim is only half true today, so it is written down rather than assumed:
``resolve_execution_time`` has exactly one caller,
``ingestion_pipeline.create_run_from_payload``. The sentinel path,
``ingestion._upsert_test_run``, still stamps ``datetime.now()`` at INGEST.

So on that path "latest" means "ingested last", and an archive uploaded late can
still supersede the fix that followed it — the very case S3a-1 was written to
prevent. Ordering here cannot fix that; the fix belongs on the write side and is
recorded as an open finding. What this module does is refuse to make the problem
worse: it never mixes the two clocks when both runs carry a ``start_time``, and
the tie-break below is deterministic so equal timestamps do not hand the verdict
to the query planner.

Five values, not four
---------------------
PASSED / FAILED / SKIPPED / BROKEN / UNKNOWN. The fifth exists for the reason
``TestRun.unknown_tests`` exists, recorded in that column's own comment: without
it the other four "did not add up to ``total_tests`` and an uninterpretable
result was invisible in every breakdown". A rollup that silently drops UNKNOWN
reports a denominator smaller than reality and a pass rate higher than the truth.

The evidence floor
------------------
Below it the verdict is ``NOT_EVALUATED``, which is a real answer rather than an
error or a default. Collapsing "we cannot say" into GO ("nothing failed") or
NO_GO ("no proof") is a confident lie in one direction or the other, and this
codebase has shipped the first of those before: an intelligence page called an
empty window "All clear".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Release, ReleaseTestRunLink, TestCase, TestRun, TestStatus

#: The five, in the order a scorecard reads them. Taken from ``TestStatus`` so a
#: value added there cannot silently go unreported here — a rollup that omits a
#: status under-counts the denominator and overstates the pass rate.
STATUSES: tuple[str, ...] = tuple(s.value for s in TestStatus)

#: Statuses that count as evidence a test was actually exercised.
#:
#: SKIPPED is deliberately excluded: a skipped test tells you nothing about the
#: code, and counting it as evidence is how a release with everything skipped
#: reports full coverage and a perfect pass rate.
EVIDENCE_STATUSES = frozenset({TestStatus.PASSED.value, TestStatus.FAILED.value,
                               TestStatus.BROKEN.value, TestStatus.UNKNOWN.value})

#: Statuses that block a release on their own.
#:
#: BROKEN counts with FAILED. A test that errored before asserting anything did
#: not prove the code works, and treating "the harness fell over" as anything
#: other than a failure is how an infrastructure outage reads as a green
#: release.
BLOCKING_STATUSES = frozenset({TestStatus.FAILED.value, TestStatus.BROKEN.value})

#: Minimum distinct tests carrying evidence before a verdict is meaningful.
#:
#: Shares the PRINCIPLE of ``flaky_score_service.MIN_OBSERVATIONS`` — below a
#: floor the honest output is "not enough data", not a number — but NOT its
#: unit, and the two are easy to confuse because both happen to be 5.
#: ``MIN_OBSERVATIONS`` counts executions of ONE fingerprint; this counts
#: DISTINCT TESTS across a whole release. Five tests run once each clears this
#: floor and would clear nothing there.
#:
#: Deliberately small: the floor exists to catch "almost nothing ran", not to
#: demand a large suite.
MIN_EVIDENCE = 5

#: Caps on what a single rollup will read.
#:
#: ``flaky_score_service`` caps the structurally identical join and explains
#: why: "a busy project's 30-day window can be hundreds of thousands of per-test
#: rows — enough to exhaust a worker". A six-week release at 20 runs a day and
#: 5k tests a run is millions of rows, and nothing here windows by time. Both
#: caps set ``truncated`` so a partial rollup announces itself rather than
#: quietly reporting a smaller release than exists.
MAX_RUNS = 2000
MAX_CASES = 500_000


@dataclass
class ReleaseRollup:
    """What the gate saw. Every field is snapshotted onto the decision row."""

    #: One entry per DISTINCT test, holding its latest status in the release.
    latest_by_test: dict[str, str] = field(default_factory=dict)
    #: Counts per status across those distinct tests.
    status_counts: dict[str, int] = field(default_factory=dict)
    #: The runs the rollup consumed, so the verdict stays explicable after
    #: retention deletes them.
    run_ids: list[str] = field(default_factory=list)
    #: How each run came to belong to the release — the attribution ladder rung.
    attribution_mix: dict[str, int] = field(default_factory=dict)
    #: True when a cap was hit, so the numbers describe part of the release.
    #: A truncated rollup that did not say so would report a smaller, and
    #: possibly cleaner, release than the one that exists.
    truncated: bool = False

    @property
    def denominator(self) -> int:
        """Distinct tests in scope — NOT the number of results.

        Counting results would let one test re-run ten times outweigh nine tests
        that ran once, which is the failure this whole rollup exists to avoid.
        """
        return len(self.latest_by_test)

    @property
    def evidence_count(self) -> int:
        """Distinct tests that actually ran. Skips are not evidence."""
        return sum(1 for s in self.latest_by_test.values() if s in EVIDENCE_STATUSES)

    @property
    def blocking_count(self) -> int:
        return sum(1 for s in self.latest_by_test.values() if s in BLOCKING_STATUSES)

    def pass_rate(self) -> Optional[float]:
        """Passed over EVIDENCE, not over the denominator.

        Dividing by the denominator counts skipped tests in the bottom half, so
        a release that skipped most of its suite reports a low pass rate and
        looks broken — when the truth is that it was barely tested. Returns
        ``None`` rather than 0.0 when nothing ran: a zero here is a measurement
        that happened, and none did.
        """
        evidence = self.evidence_count
        if evidence == 0:
            return None
        passed = sum(
            1 for s in self.latest_by_test.values() if s == TestStatus.PASSED.value
        )
        return round(passed / evidence * 100, 2)


def _identity(case: Any) -> Optional[str]:
    """The key that decides whether two results describe the SAME test.

    A fingerprint alone is not a test identity. It is
    ``sha256(f"{class_name or ''}::{test_name}")[:16]`` — no project, no suite —
    and this repo says so itself: ``flaky_score_service`` warns that
    "test_fingerprint is not globally unique, so an unscoped read would blend
    tenants", and ``CanonicalTestCase`` keys identity on
    ``(project_id, test_fingerprint)``.

    Two genuinely different tests both called ``test_login`` with no class name
    — one in a UI suite, one in an API suite — produce the same fingerprint.
    Keyed on the fingerprint alone they collapse into one entry, so the
    denominator is short by one and the later suite's PASS erases the earlier
    suite's FAIL. The verdict flips to GO on a release that has a failing test.

    The asymmetry is what makes this dangerous. WITHIN a run the collision is
    impossible (``uq_test_cases_run_fingerprint``) and ingestion resolves
    duplicates worst-outcome-wins; ACROSS runs this rollup resolves them
    latest-wins — the opposite direction. So the same collision that ingestion
    handles conservatively, the rollup would handle optimistically.

    The project is implied: ``build_rollup`` pins one project. The suite is not,
    so it is part of the key.
    """
    fingerprint = getattr(case, "test_fingerprint", None)
    name = getattr(case, "test_name", None)
    base = fingerprint or name
    if not base:
        # A row with neither is uninterpretable. Counting it under a shared
        # empty key would merge every such row into one phantom test.
        return None
    suite = getattr(case, "suite_name", None) or ""
    return f"{suite}::{base}"


def _order_key(run: TestRun) -> Any:
    """Newest-last ordering for a run.

    ``start_time`` is the execution time (S3a-1). It is nullable on rows that
    predate that slice, so fall back to ``created_at`` rather than letting a
    NULL sort unpredictably — an ordering that raises or silently reverses on
    legacy data would make the rollup wrong exactly where history is longest.
    """
    return run.start_time or run.created_at


async def build_rollup(
    db: AsyncSession,
    release_id: uuid.UUID | str,
) -> ReleaseRollup:
    """Roll a release's runs up to one latest status per distinct test."""
    rollup = ReleaseRollup()

    # Pin the project explicitly rather than trusting the release link.
    #
    # Nothing at the database level stops a link joining a run and a release in
    # different projects, and migration 0151 DELIBERATELY left historical
    # cross-project links in place, logging only a count.
    # ``sync_primary_release`` then copies ``release_id`` onto
    # ``TestRun.primary_release_id`` with no project check. So one bad link is
    # enough to pull another tenant's run into this release's verdict — and
    # chained with the identity key, a foreign project's passing ``test_login``
    # would overwrite this project's failing one. ``release_service`` guards the
    # same query shape for the same stated reason.
    project_id = (
        await db.execute(select(Release.project_id).where(Release.id == release_id))
    ).scalar_one_or_none()
    if project_id is None:
        # No such release: an empty rollup, which `decide` reports as
        # NOT_EVALUATED. Not an exception — "unknown release" is a caller error
        # the router's scope guard already answers with 404.
        return rollup

    runs = list(
        (
            await db.execute(
                select(TestRun)
                .where(
                    TestRun.primary_release_id == release_id,
                    TestRun.project_id == project_id,
                )
                # Deterministic order from the database as well as in Python.
                # The sort below is the authority, but leaving the query
                # unordered means the LIMIT beneath truncates an arbitrary
                # subset rather than the oldest.
                .order_by(TestRun.start_time.asc(), TestRun.id.asc())
                .limit(MAX_RUNS + 1)
            )
        )
        .scalars()
        .all()
    )
    if len(runs) > MAX_RUNS:
        # Truncated, and SAID SO. `flaky_score_service` caps the structurally
        # identical join for the same reason, noting a busy project's window is
        # "enough to exhaust a worker". Reporting the cap is the difference
        # between a partial verdict and a wrong one.
        runs = runs[:MAX_RUNS]
        rollup.truncated = True
    if not runs:
        return rollup

    # Oldest first, so a later run's result overwrites an earlier one and the
    # last write wins is the newest result.
    #
    # The id is part of the key, not decoration. `list.sort` is STABLE, and the
    # query above has no ORDER BY, so on equal timestamps the winner would be
    # whatever order Postgres happened to return rows in — and an UPDATE that
    # moves a row in the heap (``sync_primary_release`` writing
    # ``primary_release_id``, say) silently re-orders a seq scan. Two CI shards
    # triggered together share a ``start_time`` routinely, so this is the common
    # case, not a corner: the same data could yield GO on one call and NO_GO on
    # the next with no write in between.
    runs.sort(key=lambda r: (_order_key(r), str(r.id)))
    rollup.run_ids = [str(r.id) for r in runs]

    # The ladder rung comes from ``ReleaseTestRunLink.link_source``, NOT from
    # ``TestRun``. The first draft of this read ``run.release_link_source`` via
    # getattr with a default — a column that does not exist on that model — so
    # every run would have been reported as "unknown" forever, with no error
    # anywhere. A defaulted getattr against a misspelled attribute is invisible
    # precisely because it is designed not to fail.
    #
    # Read the PRIMARY link only: the run's release is its primary one, which
    # is what ``primary_release_id`` above selected on.
    links = (
        (
            await db.execute(
                select(ReleaseTestRunLink.test_run_id, ReleaseTestRunLink.link_source).where(
                    ReleaseTestRunLink.release_id == release_id,
                    ReleaseTestRunLink.is_primary.is_(True),
                )
            )
        )
        .all()
    )
    source_by_run = {str(run_id): (src or "unknown") for run_id, src in links}
    for run in runs:
        rung = source_by_run.get(str(run.id), "unattributed_link")
        rollup.attribution_mix[rung] = rollup.attribution_mix.get(rung, 0) + 1

    # Four columns, not the whole row. ``TestCase`` maps 44 columns including
    # ``error_message`` and ``stack_trace`` TEXT plus five JSON columns; loading
    # entities would pull all of it into memory for a rollup that reads four
    # fields.
    case_rows = (
        await db.execute(
            select(
                TestCase.test_run_id,
                TestCase.test_fingerprint,
                TestCase.test_name,
                TestCase.suite_name,
                TestCase.status,
            )
            .where(TestCase.test_run_id.in_([r.id for r in runs]))
            .limit(MAX_CASES + 1)
        )
    ).all()
    if len(case_rows) > MAX_CASES:
        case_rows = case_rows[:MAX_CASES]
        rollup.truncated = True
    cases = [
        SimpleNamespace(
            test_run_id=row[0],
            test_fingerprint=row[1],
            test_name=row[2],
            suite_name=row[3],
            status=row[4],
        )
        for row in case_rows
    ]
    order = {r.id: i for i, r in enumerate(runs)}
    # Sorting the CASES by their run's position means the assignment below is
    # oldest-to-newest regardless of what order the database returned them in.
    # Relying on row order would make the rollup depend on the planner.
    for case in sorted(cases, key=lambda c: order.get(c.test_run_id, -1)):
        key = _identity(case)
        if key is None:
            continue
        rollup.latest_by_test[key] = _normalise(case.status)

    rollup.status_counts = {s: 0 for s in STATUSES}
    for status in rollup.latest_by_test.values():
        rollup.status_counts[status] = rollup.status_counts.get(status, 0) + 1
    return rollup


def _normalise(status: Any) -> str:
    """Map any stored status onto the five-value vocabulary.

    Anything unrecognised becomes UNKNOWN rather than being dropped. Dropping it
    would shrink the denominator and inflate the pass rate — the product would
    report a better release than it measured, which is the one direction an
    honest gate must never fail in.
    """
    raw = getattr(status, "value", status)
    text = str(raw).strip().upper() if raw is not None else ""
    return text if text in STATUSES else TestStatus.UNKNOWN.value


def decide(rollup: ReleaseRollup, *, min_evidence: int = MIN_EVIDENCE) -> tuple[str, list[str]]:
    """The verdict, and the reasons behind it.

    Returns ``NOT_EVALUATED`` below the evidence floor. That is the honest
    answer: with almost nothing exercised, GO would claim the release is sound
    on no evidence, and NO_GO would claim it is broken on the same absence.
    """
    if rollup.evidence_count < min_evidence:
        return "NOT_EVALUATED", [
            f"only {rollup.evidence_count} of {rollup.denominator} tests carry "
            f"evidence; {min_evidence} required before a verdict means anything"
        ]

    blocking = [
        f"{key} is {status}"
        for key, status in sorted(rollup.latest_by_test.items())
        if status in BLOCKING_STATUSES
    ]
    if not blocking:
        return "GO", []
    return "NO_GO", blocking


def summarise(rollup: ReleaseRollup) -> dict[str, Any]:
    """The scorecard payload — what a reader needs to judge the verdict itself.

    Includes the denominator ALONGSIDE the pass rate. A percentage without the
    count it was computed over is the shape that lets "90% passed" stand for
    both a thorough release and one where nine of ten tests never ran.
    """
    return {
        "denominator": rollup.denominator,
        "evidence_count": rollup.evidence_count,
        "pass_rate": rollup.pass_rate(),
        "status_counts": dict(rollup.status_counts),
        "attribution_mix": dict(rollup.attribution_mix),
        "run_count": len(rollup.run_ids),
        # States plainly whether the numbers above are worth reading, rather
        # than leaving a reader to infer it from a small denominator.
        "measured": rollup.evidence_count >= MIN_EVIDENCE,
        # The threshold travels with the answer. ``flaky_score`` publishes its
        # own so a reader can argue with it; a bare boolean asks to be trusted.
        "evidence_floor": MIN_EVIDENCE,
        # Why it is unmeasured, in the same shape as ``insufficient_reason``.
        # ``None`` when it IS measured, so the key is never a bare empty string
        # that reads as "no reason" rather than "no problem".
        "insufficient_reason": (
            None
            if rollup.evidence_count >= MIN_EVIDENCE
            else f"{rollup.evidence_count} of {rollup.denominator} tests carry evidence; "
            f"{MIN_EVIDENCE} required"
        ),
        # A partial rollup announces itself. Reported unconditionally so its
        # absence cannot be mistaken for a complete read.
        "truncated": rollup.truncated,
    }


def statuses_are_exhaustive(counts: Iterable[str]) -> bool:
    """Every status in the vocabulary is represented in a counts mapping.

    A breakdown missing a key does not read as missing — it reads as zero, which
    is the difference between "no broken tests" and "we did not look".
    """
    return set(counts) >= set(STATUSES)
