"""Readable release-gate blocking reasons (W05 REL-01).

The rollup names a blocking test by its IDENTITY, ``"{suite}::{key} is
{STATUS}"`` (``release_rollup_service._identity`` / ``decide``), where ``key``
is the test's fingerprint whenever ingestion computed one: an opaque 16-hex
string. A release card reading ``checkout-e2e::9f2c41d07ab3e815 is FAILED``
tells a release manager nothing, so every surface that SHOWS a reason
translates it to ``"test_pay (checkout-e2e) is FAILED"``. The stored decision
keeps the raw text (it is the audit record); only the display changes.

Promoted from ``notification/run_report.py`` (``_reason_key`` /
``_readable_reasons``, which remain there as aliases), because the release list,
the release detail and the run email must print the same words.

Rules, all inherited byte for byte from the email:

* the output is ``f"{name} ({suite or 'no suite'}) is {status}"``;
* a reason whose key does not resolve to a test of the release's project (a
  test name rather than a fingerprint, a fingerprint whose rows retention
  deleted) passes through unchanged, as does every defect reason;
* a fingerprint is resolved within ONE project only: fingerprints are not
  globally unique (``sha256(class::name)[:16]``), so an unscoped read would
  name another tenant's test.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, select, tuple_

from app.models.postgres import Release, TestCase, TestRun


def reason_key(reason: str) -> tuple[Optional[str], Optional[str], str]:
    """``"<suite>::<fingerprint> is FAILED"`` -> (suite, fingerprint, status).

    Anything else (a defect reason, an incomplete-ingestion sentence) returns
    ``(None, None, reason)``.
    """
    head, sep, status = reason.rpartition(" is ")
    if not sep or "::" not in head:
        return None, None, reason
    suite, _, ident = head.rpartition("::")
    return suite, ident, status


def _render(reasons: Sequence[str], names: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    for reason in reasons:
        suite, fp, status = reason_key(reason)
        if fp and fp in names:
            out.append(f"{names[fp]} ({suite or 'no suite'}) is {status}")
        else:
            out.append(reason)
    return out


def _fingerprints(reasons: Sequence[str]) -> set[str]:
    return {fp for _, fp, _ in map(reason_key, reasons) if fp}


async def readable_reasons(db: Any, project_id: Any, reasons: Sequence[str]) -> list[str]:
    """One project's reasons, translated with at most ONE query (none when no
    reason names a test)."""
    reasons = [str(r) for r in reasons]
    fps = _fingerprints(reasons)
    names: dict[str, str] = {}
    if fps:
        rows = await db.execute(
            select(TestCase.test_fingerprint, func.max(TestCase.test_name))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestRun.project_id == project_id, TestCase.test_fingerprint.in_(fps))
            .group_by(TestCase.test_fingerprint)
        )
        names = {fp: name for fp, name in rows.all() if name}
    return _render(reasons, names)


async def display_reasons_by_project(
    db: Any, per_project: Mapping[uuid.UUID, Sequence[str]]
) -> dict[uuid.UUID, list[str]]:
    """Every project's reasons translated in ONE query for the whole batch.

    The query is keyed on ``(project_id, test_fingerprint)`` pairs, so a
    fingerprint is only ever resolved inside the project that asked for it.
    No query runs when no reason in the batch names a test.
    """
    batch = {pid: [str(r) for r in reasons] for pid, reasons in per_project.items()}
    pairs = sorted(
        {(pid, fp) for pid, reasons in batch.items() for fp in _fingerprints(reasons)},
        key=lambda pair: (str(pair[0]), pair[1]),
    )
    names: dict[tuple[str, str], str] = {}
    if pairs:
        rows = await db.execute(
            select(TestRun.project_id, TestCase.test_fingerprint, func.max(TestCase.test_name))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(tuple_(TestRun.project_id, TestCase.test_fingerprint).in_(pairs))
            .group_by(TestRun.project_id, TestCase.test_fingerprint)
        )
        names = {(str(pid), fp): name for pid, fp, name in rows.all() if name}
    out: dict[uuid.UUID, list[str]] = {}
    for pid, reasons in batch.items():
        scoped = {fp: name for (p, fp), name in names.items() if p == str(pid)}
        out[pid] = _render(reasons, scoped)
    return out


async def display_reasons_for_release(
    db: Any, release_id: Any, reasons: Sequence[str]
) -> list[str]:
    """One release's reasons; its project is resolved inside the same query
    (ONE query, none when no reason names a test)."""
    reasons = [str(r) for r in reasons]
    fps = _fingerprints(reasons)
    names: dict[str, str] = {}
    if fps:
        project_of_release = (
            select(Release.project_id).where(Release.id == release_id).scalar_subquery()
        )
        rows = await db.execute(
            select(TestCase.test_fingerprint, func.max(TestCase.test_name))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_of_release,
                TestCase.test_fingerprint.in_(fps),
            )
            .group_by(TestCase.test_fingerprint)
        )
        names = {fp: name for fp, name in rows.all() if name}
    return _render(reasons, names)


__all__ = [
    "display_reasons_by_project",
    "display_reasons_for_release",
    "readable_reasons",
    "reason_key",
]
