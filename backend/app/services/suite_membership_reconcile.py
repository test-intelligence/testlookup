"""Reconcile the legacy suite memberships with the canonical catalog (W25a Day-0 follow-up; contract §7.1 risk 1).

The first parity comparison on a real deployment found BLOCKING keys that are
not writer disagreements: canonical ``execution`` rows the legacy table never
received (seeded history older than the table, a one-off logged-and-swallowed
legacy sync miss, and every project the W24 import creates — it builds
``canonical_test_cases`` but ``suite_memberships`` is a ``derived`` table, never
exported), and ``first_seen_differs`` pairs where two runs were created in the
same second (legacy kept the earlier run, canonical the later). This module
repairs exactly those two shapes so the matrix in ``suite_membership_parity``
classifies the keys as MATCHES. It never changes the matrix, never relabels a
reason and never touches an EXPECTED key.

``reconcile_project(db, project_id, apply=...)`` reads ONE project's rows and
runs the SAME pure ``classify`` the parity harness runs (with an empty window:
neither repaired reason depends on it), then:

(a) ``missing_in_legacy`` (canonical source ``execution``, a non-default suite,
    no legacy row for the fingerprint ANYWHERE in the project) → INSERT one
    ``suite_memberships`` row exactly as ``suite_sync_service`` writes it:
    ``source = "linked"`` + ``managed_test_case_id`` when a non-deprecated
    managed case carries the fingerprint, else ``"execution"``; names and
    ``first_seen_run_id`` / ``last_seen_run_id`` copied from the canonical row
    (so neither ``name_drift`` nor ``first_seen_differs`` can come from the
    insert). A canonical ``active`` row → ``status="active"`` in suite ``S``. A
    canonical ``deleted`` / ``needs_review`` row → the legacy writer's REMOVAL
    shape: suite ``S-deleted``, ``status = review_tag = "needs_review"``,
    ``deleted_at_run_id`` copied; the key is then R / R (a match) — it can
    become neither ``retired_but_seen`` (legacy A) nor ``retire_pending``
    (canonical A). The legacy writer never writes ``status="deleted"``.
(b) ``first_seen_differs`` → both sides' ``first_seen_run_id`` = the run with
    the EARLIER ``test_runs.created_at`` (tie: the smaller run id string); only
    the side that differs is updated. The matrix's "legacy holds the
    fingerprint in exactly ONE base suite" means every legacy row of that
    fingerprint belongs to this key; each one with a non-null, different
    pointer is aligned.

Left alone (counted): ``default_suite`` and ``catalog_only`` keys (EXPECTED),
and a key the legacy writer could not have written faithfully (``left_unfit``:
a ``<suite>-deleted`` name longer than the column, an unknown canonical
source, a run pointer without a run).

``apply=False`` issues the SAME reads and writes NOTHING. Stage-only: the
writes are statements on the CALLER's session — no flush, no commit, no
rollback, no audit row, no ``suite_membership_events`` row (a reconcile is not
a membership change), no mail, no outbound call; nothing logged names a test,
a suite or a fingerprint. Idempotent: a second ``apply=True`` changes nothing.
Callers: ``project_import_service.run_import`` (the new project, inside the
import's one transaction) and ``scripts/reconcile_legacy_suite_memberships.py``
(in the pod, one project per session).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, NamedTuple, Optional, Sequence

import structlog
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import CanonicalTestCase, ManagedTestCase, SuiteMembership, TestRun, TestSuite
from app.services.suite_membership_parity import DELETED_BUCKET_SUFFIX, CanonicalRow, LegacyRow, classify

_slog = structlog.get_logger(__name__)

MIRRORED_SOURCE = "execution"        # the one canonical source mirrored (managed / linked are EXPECTED catalog_only)
CANONICAL_ACTIVE = "active"          # the matrix: canonical "active" is A, every other status is R
LEGACY_ACTIVE = "active"            # suite_sync_service: a new member
LEGACY_REMOVED = "needs_review"      # suite_sync_service: a removed member's status AND review_tag, in <suite>-deleted
LEGACY_LINKED = "linked"             # suite_sync_service: a new member whose fingerprint a managed case carries
MANAGED_RETIRED = "deprecated"       # suite_sync_service links every managed case but these
SUITE_NAME_MAX = 500                 # == SuiteMembership.suite_name String(500)
_ID_CHUNK = 1000                     # bind parameters per IN (...) list


@dataclass(frozen=True)
class ReconcileResult:
    """What one project's reconcile wrote (``applied``) or would write (dry run)."""

    project_id: uuid.UUID
    applied: bool
    legacy_inserted: int = 0                # suite_memberships rows inserted (active + removed)
    legacy_inserted_removed: int = 0        # … of which mirrored a canonical deleted / needs_review row into <S>-deleted
    first_seen_aligned_legacy: int = 0      # suite_memberships rows whose first_seen_run_id moved to the earlier run
    first_seen_aligned_canonical: int = 0   # canonical_test_cases rows whose first_seen_run_id moved to the earlier run
    left_default_suite: int = 0             # EXPECTED default_suite keys — never touched
    left_catalog_only: int = 0              # EXPECTED catalog_only keys (managed / linked) — never touched
    left_unfit: int = 0                     # keys the legacy writer could not have written faithfully — never touched

    @property
    def changes(self) -> int:
        return self.legacy_inserted + self.first_seen_aligned_legacy + self.first_seen_aligned_canonical

    def counts(self) -> dict[str, int]:
        """The integer fields only (no id, no flag): what a log line or the script prints."""
        return {key: value for key, value in asdict(self).items() if key not in ("project_id", "applied")}


class _Legacy(NamedTuple):
    id: uuid.UUID
    row: LegacyRow


class _Canonical(NamedTuple):
    id: uuid.UUID
    row: CanonicalRow
    deleted_at_run_id: Optional[uuid.UUID]


def _chunks(values: Sequence[Any]) -> list[Sequence[Any]]:
    return [values[start:start + _ID_CHUNK] for start in range(0, len(values), _ID_CHUNK)]


async def _legacy_rows(db: AsyncSession, project_id: uuid.UUID) -> list[_Legacy]:
    result = await db.execute(
        select(
            SuiteMembership.id, SuiteMembership.suite_name, SuiteMembership.test_fingerprint, SuiteMembership.status,
            SuiteMembership.test_name, SuiteMembership.class_name,
            SuiteMembership.first_seen_run_id, SuiteMembership.last_seen_run_id,
        ).where(SuiteMembership.project_id == project_id)
    )
    return [_Legacy(row[0], LegacyRow(*row[1:])) for row in result.all()]


async def _canonical_rows(db: AsyncSession, project_id: uuid.UUID) -> dict[str, _Canonical]:
    result = await db.execute(
        select(
            CanonicalTestCase.id, CanonicalTestCase.test_fingerprint, CanonicalTestCase.status,
            CanonicalTestCase.source, TestSuite.name, TestSuite.is_default, CanonicalTestCase.test_name,
            CanonicalTestCase.class_name, CanonicalTestCase.first_seen_run_id, CanonicalTestCase.last_seen_run_id,
            CanonicalTestCase.deleted_at_run_id,
        )
        .join(TestSuite, TestSuite.id == CanonicalTestCase.test_suite_id)
        .where(CanonicalTestCase.project_id == project_id)
    )
    rows: dict[str, _Canonical] = {}
    for row in result.all():
        canonical = CanonicalRow(*row[1:10])
        rows[canonical.test_fingerprint] = _Canonical(row[0], canonical, row[10])
    return rows


async def _run_times(db: AsyncSession, run_ids: set[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    times: dict[uuid.UUID, datetime] = {}
    for chunk in _chunks(sorted(run_ids, key=str)):
        result = await db.execute(select(TestRun.id, TestRun.created_at).where(TestRun.id.in_(chunk)))
        times.update({run_id: created for run_id, created in result.all() if created is not None})
    return times


async def _managed_by_fingerprint(db: AsyncSession, project_id: uuid.UUID, fingerprints: list[str]) -> dict[str, uuid.UUID]:
    """suite_sync_service's auto-link lookup (lowest id first, so a duplicate fingerprint resolves deterministically)."""
    linked: dict[str, uuid.UUID] = {}
    for chunk in _chunks(sorted(set(fingerprints))):
        result = await db.execute(
            select(ManagedTestCase.id, ManagedTestCase.test_fingerprint)
            .where(
                ManagedTestCase.project_id == project_id,
                ManagedTestCase.test_fingerprint.in_(chunk),
                ManagedTestCase.status != MANAGED_RETIRED,
            )
            .order_by(ManagedTestCase.id)
        )
        for managed_id, fingerprint in result.all():
            linked.setdefault(fingerprint, managed_id)
    return linked


def _earliest(candidates: set[uuid.UUID], times: dict[uuid.UUID, datetime]) -> Optional[uuid.UUID]:
    """The earliest sighting (``created_at``, then the smaller id string); None when a run has no time."""
    if any(run_id not in times for run_id in candidates):
        return None
    return min(candidates, key=lambda run_id: (times[run_id], str(run_id)))


async def reconcile_project(db: AsyncSession, project_id: uuid.UUID, *, apply: bool) -> ReconcileResult:
    """Repair ONE project's ``missing_in_legacy`` and ``first_seen_differs`` keys (module docstring).

    Reads on ``db``; with ``apply`` also INSERT / UPDATE statements on it. Never commits.
    """
    legacy = await _legacy_rows(db, project_id)
    canonical = await _canonical_rows(db, project_id)
    defaults = {entry.row.suite_name for entry in canonical.values() if entry.row.is_default}
    _keys, divergences = classify(
        [entry.row for entry in legacy], [entry.row for entry in canonical.values()], set(), defaults,
        project_id=project_id,
    )
    legacy_by_fp: dict[str, list[_Legacy]] = {}
    for entry in legacy:
        legacy_by_fp.setdefault(entry.row.test_fingerprint, []).append(entry)

    left_default = sum(d.reason == "default_suite" for d in divergences)
    left_catalog = sum(d.reason == "catalog_only" for d in divergences)
    left_unfit = 0

    # (a) missing_in_legacy → the legacy writer's row.
    inserts: list[dict[str, Any]] = []
    removed = 0
    for d in divergences:
        if d.reason != "missing_in_legacy":
            continue
        source = canonical[d.fingerprint]
        row = source.row
        is_removed = row.status != CANONICAL_ACTIVE
        suite_name = f"{row.suite_name}{DELETED_BUCKET_SUFFIX}" if is_removed else row.suite_name
        if row.source != MIRRORED_SOURCE or len(suite_name) > SUITE_NAME_MAX:
            left_unfit += 1
            continue
        removed += is_removed
        inserts.append({
            "id": uuid.uuid4(),
            "project_id": project_id,
            "suite_name": suite_name,
            "test_fingerprint": row.test_fingerprint,
            "test_name": row.test_name,
            "class_name": row.class_name,
            "status": LEGACY_REMOVED if is_removed else LEGACY_ACTIVE,
            "review_tag": LEGACY_REMOVED if is_removed else None,
            "deleted_at_run_id": source.deleted_at_run_id if is_removed else None,
            "first_seen_run_id": row.first_seen_run_id,
            "last_seen_run_id": row.last_seen_run_id,
        })

    # (b) first_seen_differs → both sides on the earlier sighting.
    differs = [d for d in divergences if d.reason == "first_seen_differs"]
    candidates: dict[str, set[uuid.UUID]] = {}
    for d in differs:
        runs = {r.row.first_seen_run_id for r in legacy_by_fp.get(d.fingerprint, []) if r.row.first_seen_run_id}
        first = canonical[d.fingerprint].row.first_seen_run_id
        if first is not None:
            runs.add(first)
        candidates[d.fingerprint] = runs
    wanted: set[uuid.UUID] = set()
    for runs in candidates.values():
        wanted |= runs
    times = await _run_times(db, wanted) if wanted else {}
    legacy_updates: list[tuple[uuid.UUID, uuid.UUID]] = []
    canonical_updates: list[tuple[uuid.UUID, uuid.UUID]] = []
    for d in differs:
        target = _earliest(candidates[d.fingerprint], times)
        if target is None:
            left_unfit += 1
            continue
        held = canonical[d.fingerprint]
        if held.row.first_seen_run_id is not None and held.row.first_seen_run_id != target:
            canonical_updates.append((held.id, target))
        for entry in legacy_by_fp.get(d.fingerprint, []):
            if entry.row.first_seen_run_id is not None and entry.row.first_seen_run_id != target:
                legacy_updates.append((entry.id, target))

    if apply:
        if inserts:
            linked = await _managed_by_fingerprint(db, project_id, [values["test_fingerprint"] for values in inserts])
            for values in inserts:
                managed_id = linked.get(values["test_fingerprint"])
                values["managed_test_case_id"] = managed_id
                values["source"] = LEGACY_LINKED if managed_id else MIRRORED_SOURCE
            await db.execute(insert(SuiteMembership), inserts)
        for row_id, run_id in legacy_updates:
            await db.execute(update(SuiteMembership).where(SuiteMembership.id == row_id).values(first_seen_run_id=run_id))
        for row_id, run_id in canonical_updates:
            await db.execute(
                update(CanonicalTestCase).where(CanonicalTestCase.id == row_id).values(first_seen_run_id=run_id)
            )

    result = ReconcileResult(
        project_id=project_id,
        applied=apply,
        legacy_inserted=len(inserts),
        legacy_inserted_removed=removed,
        first_seen_aligned_legacy=len(legacy_updates),
        first_seen_aligned_canonical=len(canonical_updates),
        left_default_suite=left_default,
        left_catalog_only=left_catalog,
        left_unfit=left_unfit,
    )
    if apply and result.changes:
        _slog.info("suite_membership_reconciled", project_id=str(project_id), **result.counts())
    return result
