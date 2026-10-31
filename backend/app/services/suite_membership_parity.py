"""The suite-membership parity comparison (W25a HYG-04 phase 1; contract §2.1, §1.3).

The legacy model (``suite_memberships``, keyed by ``(project, suite_name,
fingerprint)``, removes a test on ONE absence into a ``<suite>-deleted``
bucket) and the canonical model (``canonical_test_cases`` ⋈ ``test_suites``,
keyed by ``(project, fingerprint)``, one suite per test, NULL-suite cases under
the default suite, retired only after the reconciler's window) are NOT designed
to agree row for row. This module compares their KNOWLEDGE per key
``(base suite, fingerprint)`` and labels every disagreement with exactly ONE
reason of ``suite_parity_vocab``: the documented design differences are
EXPECTED (reported, never counted), everything else is BLOCKING. D26's "zero
diffs" is ``blocking_diffs == 0``; a reason is never relabelled to reach zero.

Reads only. ``compare_project`` issues four SELECTs (C-1..C-4) on the CALLER's
session, one project at a time, and never writes, flushes, commits or rolls
back (``STAGE_ONLY_SERVICES``); ``worker/suite_parity_runner.py`` owns the
session and the run row. Nothing here sends mail, calls an integration or
logs a name, a fingerprint or a URL.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, NamedTuple, Optional, Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CanonicalTestCase,
    SuiteMembership,
    SuiteMembershipEvent,
    TestCase,
    TestRun,
    TestSuite,
)
from app.services import suite_parity_vocab as vocab

_slog = structlog.get_logger(__name__)

DELETED_BUCKET_SUFFIX = "-deleted"          # suite_sync_service's ``<suite>-deleted`` bucket
_BUCKET_STATUSES = frozenset({"needs_review", "deleted"})
_CATALOG_SOURCES = frozenset({"managed", "linked"})

# Per-key states (single letters in the contract's matrix → the wire words).
_A, _R, _X, _NONE = "A", "R", "X", "-"
_LEGACY_WORD = {_A: "active", _R: "removed", _NONE: "absent"}
_CANONICAL_WORD = {_A: "active", _R: "retired", _X: "other_suite", _NONE: "absent"}
_BLOCKING = frozenset(vocab.BLOCKING_REASONS)
_REASON_ORDER = {reason: i for i, reason in enumerate(vocab.BLOCKING_REASONS + vocab.EXPECTED_REASONS)}


class LegacyRow(NamedTuple):
    """One ``suite_memberships`` row (C-1)."""
    suite_name: str
    test_fingerprint: str
    status: str
    test_name: Optional[str] = None
    class_name: Optional[str] = None
    first_seen_run_id: Optional[uuid.UUID] = None
    last_seen_run_id: Optional[uuid.UUID] = None


class CanonicalRow(NamedTuple):
    """One ``canonical_test_cases`` row with its suite (C-2)."""
    test_fingerprint: str
    status: str
    source: str
    suite_name: str
    is_default: bool = False
    test_name: Optional[str] = None
    class_name: Optional[str] = None
    first_seen_run_id: Optional[uuid.UUID] = None
    last_seen_run_id: Optional[uuid.UUID] = None


@dataclass
class Divergence:
    reason: str
    project_id: Optional[uuid.UUID]
    suite: str
    fingerprint: str
    test_name: Optional[str]
    legacy: str          # "active" | "removed" | "absent"
    canonical: str       # "active" | "retired" | "other_suite" | "absent"

    @property
    def blocking(self) -> bool:
        return self.reason in _BLOCKING


@dataclass
class ProjectParity:
    project_id: Optional[uuid.UUID]
    keys: int
    divergences: list[Divergence] = field(default_factory=list)
    info_counts: dict[str, int] = field(default_factory=dict)


def _legacy_key(row: LegacyRow) -> tuple[str, str]:
    """``(base suite, state)`` of a legacy row: a ``-deleted`` bucket row is R in its base suite."""
    name = row.suite_name
    if name.endswith(DELETED_BUCKET_SUFFIX) and row.status in _BUCKET_STATUSES:
        return name[: -len(DELETED_BUCKET_SUFFIX)], _R
    return name, (_A if row.status == "active" else _R)


def _fold_legacy(rows: Iterable[LegacyRow]) -> dict[tuple[str, str], tuple[str, LegacyRow]]:
    """``(base suite, fp) → (state, row)``; an S row and an S-deleted row for one key → A wins."""
    keyed: dict[tuple[str, str], tuple[str, LegacyRow]] = {}
    for row in rows:
        base, state = _legacy_key(row)
        key = (base, row.test_fingerprint)
        held = keyed.get(key)
        if held is None:
            keyed[key] = (state, row)
        elif state == _A and held[0] != _A:
            keyed[key] = (state, row)
        elif state == held[0] and row.suite_name == base and held[1].suite_name != base:
            keyed[key] = (state, row)       # a tie keeps the suite's own row, never the bucket's (deterministic)
    return keyed


def _differs(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "") != (b or "")           # NULL and "" are the same absent class name


def _pick(candidates: list[str]) -> str:
    """The first BLOCKING reason in BLOCKING_REASONS order, else the first EXPECTED one."""
    return min(candidates, key=_REASON_ORDER.__getitem__)


def _matched_reason(legacy: LegacyRow, canonical: CanonicalRow, legacy_suite_count: int) -> Optional[str]:
    """A matched key (A/A or R/R): the name and first-sighting checks. A NULL run pointer never differs."""
    candidates: list[str] = []
    if _differs(legacy.test_name, canonical.test_name) or _differs(legacy.class_name, canonical.class_name):
        same_sighting = (
            legacy.last_seen_run_id is not None
            and canonical.last_seen_run_id is not None
            and legacy.last_seen_run_id == canonical.last_seen_run_id
        )
        candidates.append("name_drift" if same_sighting else "name_stale")
    if (
        legacy.first_seen_run_id is not None
        and canonical.first_seen_run_id is not None
        and legacy.first_seen_run_id != canonical.first_seen_run_id
    ):
        candidates.append("first_seen_differs" if legacy_suite_count == 1 else "first_seen_multi_suite")
    return _pick(candidates) if candidates else None


def classify(
    legacy_rows: Iterable[LegacyRow],
    canonical_rows: Iterable[CanonicalRow],
    seen_fps: set[str],
    default_suite_names: set[str],
    *,
    project_id: Optional[uuid.UUID] = None,
) -> tuple[int, list[Divergence]]:
    """PURE: the §2.1 matrix over ONE project's rows.

    Returns ``(keys compared, one Divergence per non-matching key)``, the
    divergences in key order (suite, fingerprint). The key universe is the
    legacy keys ∪ the canonical keys (``S`` = the canonical row's suite name);
    a canonical key whose fingerprint legacy holds in ANOTHER suite is counted
    but not reported (the move is reported once, on the legacy key).
    """
    legacy = _fold_legacy(legacy_rows)
    canonical: dict[str, CanonicalRow] = {row.test_fingerprint: row for row in canonical_rows}
    legacy_suites: dict[str, set[str]] = {}
    for base, fp in legacy:
        legacy_suites.setdefault(fp, set()).add(base)
    keys = set(legacy) | {(row.suite_name, fp) for fp, row in canonical.items()}

    divergences: list[Divergence] = []
    for suite, fp in sorted(keys):
        l_state, l_row = legacy.get((suite, fp), (_NONE, None))
        c_row = canonical.get(fp)
        if c_row is None:
            c_state = _NONE
        elif c_row.suite_name != suite:
            c_state = _X
        else:
            c_state = _A if c_row.status == "active" else _R

        reason: Optional[str]
        if l_state == _NONE:
            if c_row is None:                                  # unreachable: a key exists only if one side holds it
                continue
            if legacy_suites.get(fp):
                reason = None                                  # counted, NOT reported: the legacy key reports the move
            elif suite in default_suite_names or c_row.is_default:
                reason = "default_suite"
            elif c_row.source in _CATALOG_SOURCES:
                reason = "catalog_only"
            else:
                reason = "missing_in_legacy"
        elif c_state == _NONE:
            reason = "missing_in_canonical"
        elif c_state == _X:
            reason = "moved_suite"
        elif l_state == _A and c_state == _R:
            reason = "retired_but_seen" if fp in seen_fps else "legacy_stale"
        elif l_state == _R and c_state == _A:
            reason = "deletion_window" if fp in seen_fps else "retire_pending"
        elif l_row is None or c_row is None:                   # unreachable: A / R states carry their rows
            continue
        else:
            reason = _matched_reason(l_row, c_row, len(legacy_suites.get(fp, ())))

        if reason is None:
            continue
        name = l_row.test_name if l_row is not None else (c_row.test_name if c_row is not None else None)
        divergences.append(Divergence(
            reason=reason, project_id=project_id, suite=suite, fingerprint=fp, test_name=name,
            legacy=_LEGACY_WORD[l_state], canonical=_CANONICAL_WORD[c_state],
        ))
    return len(keys), divergences


async def compare_project(db: AsyncSession, project_id: uuid.UUID, *, window_runs: int) -> ProjectParity:
    """C-1..C-4 on the caller's session for ONE project, then ``classify``. No write, no commit."""
    legacy_q = await db.execute(                                                      # C-1 legacy
        select(
            SuiteMembership.suite_name, SuiteMembership.test_fingerprint, SuiteMembership.status,
            SuiteMembership.test_name, SuiteMembership.class_name,
            SuiteMembership.first_seen_run_id, SuiteMembership.last_seen_run_id,
        ).where(SuiteMembership.project_id == project_id)
    )
    legacy_rows = [LegacyRow(*row) for row in legacy_q.all()]

    canonical_q = await db.execute(                                                   # C-2 canonical
        select(
            CanonicalTestCase.test_fingerprint, CanonicalTestCase.status, CanonicalTestCase.source,
            TestSuite.name, TestSuite.is_default, CanonicalTestCase.test_name, CanonicalTestCase.class_name,
            CanonicalTestCase.first_seen_run_id, CanonicalTestCase.last_seen_run_id,
        )
        .join(TestSuite, TestSuite.id == CanonicalTestCase.test_suite_id)
        .where(CanonicalTestCase.project_id == project_id)
    )
    canonical_rows = [CanonicalRow(*row) for row in canonical_q.all()]

    seen: set[str] = set()                                                            # C-3 window
    if window_runs > 0:
        recent_runs = (
            select(TestRun.id)
            .where(TestRun.project_id == project_id)
            .order_by(TestRun.created_at.desc())
            .limit(window_runs)
            .scalar_subquery()
        )
        seen_q = await db.execute(
            select(TestCase.test_fingerprint)
            .distinct()
            .where(TestCase.test_run_id.in_(recent_runs), TestCase.test_fingerprint.is_not(None))
        )
        seen = {row[0] for row in seen_q.all() if row[0]}

    events_q = await db.execute(                                                      # C-4 info
        select(SuiteMembershipEvent.event_type, func.count())
        .where(
            SuiteMembershipEvent.project_id == project_id,
            SuiteMembershipEvent.event_type.in_(("modified", "restored")),
        )
        .group_by(SuiteMembershipEvent.event_type)
    )
    events = {str(kind): int(n) for kind, n in events_q.all()}

    defaults = {row.suite_name for row in canonical_rows if row.is_default}
    keys, divergences = classify(legacy_rows, canonical_rows, seen, defaults, project_id=project_id)
    return ProjectParity(
        project_id=project_id,
        keys=keys,
        divergences=divergences,
        info_counts={
            "legacy_modified_events": events.get("modified", 0),
            "legacy_restored_events": events.get("restored", 0),
        },
    )


def _field(run: Any, name: str) -> Any:
    return run[name] if isinstance(run, Mapping) else getattr(run, name)


def _utc_date(value: datetime) -> date:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).date()


def streak_days(runs: Iterable[Any], today: date) -> int:
    """Consecutive clean UTC days ending today (or yesterday, if today has no finished run yet).

    FINISHED runs only (status ≠ running), grouped by the UTC date of
    ``started_at``; the LAST run of a date decides it (seven clicks in one
    afternoon are one day). A date with no finished run, a ``diffs`` day or a
    ``failed`` day stops the count: absence is not health.
    """
    last_of_day: dict[date, tuple[datetime, str]] = {}
    for run in runs:
        status = _field(run, "status")
        if status == "running":
            continue
        started = _field(run, "started_at")
        day = _utc_date(started)
        held = last_of_day.get(day)
        aware = started if started.tzinfo is not None else started.replace(tzinfo=timezone.utc)
        if held is None or aware > held[0]:
            last_of_day[day] = (aware, status)
    day = today if today in last_of_day else today - timedelta(days=1)
    streak = 0
    while day in last_of_day and last_of_day[day][1] == "clean":
        streak += 1
        day -= timedelta(days=1)
    return streak


def zero_counts() -> dict[str, int]:
    """Every reason of both tuples + INFO_COUNTS at 0 (a run's ``counts`` always names them all)."""
    return {name: 0 for name in vocab.BLOCKING_REASONS + vocab.EXPECTED_REASONS + vocab.INFO_COUNTS}


class ParityTotals:
    """Running totals over projects: the runner folds each project in and drops its rows.

    Peak memory = the totals + ≤ SAMPLE_PER_REASON rows per reason + three
    ints per project with a divergence, never the instance's rows.
    """

    def __init__(self) -> None:
        self.projects_checked = 0
        self.keys_compared = 0
        self.blocking_diffs = 0
        self.expected_divergences = 0
        self.counts = zero_counts()
        self._per_project: list[tuple[str, int, int]] = []
        self._sample: dict[str, list[dict[str, Any]]] = {}

    def add(self, result: ProjectParity) -> None:
        self.projects_checked += 1
        self.keys_compared += result.keys
        blocking = expected = 0
        for d in result.divergences:
            self.counts[d.reason] = self.counts.get(d.reason, 0) + 1
            if d.blocking:
                blocking += 1
            else:
                expected += 1
            kept = self._sample.setdefault(d.reason, [])
            if len(kept) < vocab.SAMPLE_PER_REASON:
                kept.append({
                    "reason": d.reason,
                    "project_id": str(d.project_id) if d.project_id is not None else None,
                    "suite": d.suite,
                    "fingerprint": d.fingerprint,
                    "test_name": d.test_name,
                    "legacy": d.legacy,
                    "canonical": d.canonical,
                })
        for name in vocab.INFO_COUNTS:
            self.counts[name] += int(result.info_counts.get(name, 0))
        self.blocking_diffs += blocking
        self.expected_divergences += expected
        if (blocking or expected) and result.project_id is not None:
            self._per_project.append((str(result.project_id), blocking, expected))

    def as_dict(self) -> dict[str, Any]:
        ranked = sorted(self._per_project, key=lambda p: (-p[1], -p[2], p[0]))[: vocab.PER_PROJECT_MAX]
        sample = [row for reason in vocab.BLOCKING_REASONS + vocab.EXPECTED_REASONS
                  for row in self._sample.get(reason, [])]
        return {
            "projects_checked": self.projects_checked,
            "keys_compared": self.keys_compared,
            "blocking_diffs": self.blocking_diffs,
            "expected_divergences": self.expected_divergences,
            "counts": dict(self.counts),
            "per_project": [{"project_id": pid, "blocking": b, "expected": e} for pid, b, e in ranked],
            "sample": sample,
        }


def summarise(results: Sequence[ProjectParity]) -> dict[str, Any]:
    """counts / per_project / sample over the projects, capped per §1.1 (≤ 20 per reason, ≤ 200 projects)."""
    totals = ParityTotals()
    for result in results:
        totals.add(result)
    return totals.as_dict()
