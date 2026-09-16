"""Recording release-level gate verdicts, append-only (S6a-1).

The invariant: exactly one CURRENT verdict per release, and per phase, with
every superseded verdict kept. Two partial unique indexes enforce the "at most
one" half in the database; this module is what keeps the transition legal while
getting there.

Why the flush is load-bearing
-----------------------------
Promoting a new verdict means demoting the old one, and both are UPDATEs to the
same partial unique index. SQLAlchemy's unit of work sorts persistent UPDATEs by
PRIMARY KEY, not by the order they were assigned — and these ids are ``uuid4``,
so the order is a coin flip. Roughly half the time it emits the promote before
the demote and the index rejects it.

That is not hypothetical. It is the same defect that broke
``release_lifecycle_service.activate_release`` in S0, found by a reviewer who
ran it rather than read it, and it broke ``unlink_test_run`` a second time in
the same review. Third occurrence of the class in this epic. The flush between
demote and promote is the fix, and it must not be removed.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ReleaseGateDecision

#: The verdicts this table accepts. ``NOT_EVALUATED`` is a real answer, not an
#: error and not a default: below the evidence floor "we cannot say" is the only
#: honest verdict, and collapsing it into GO ("nothing failed") or NO_GO ("no
#: proof") is a confident lie in one direction or the other.
VERDICTS = ("GO", "CONDITIONAL_GO", "NO_GO", "NOT_EVALUATED")


async def current_decision(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    *,
    phase_id: uuid.UUID | str | None = None,
) -> Optional[ReleaseGateDecision]:
    """The standing verdict for a release, or for one of its phases."""
    stmt = select(ReleaseGateDecision).where(
        ReleaseGateDecision.release_id == release_id,
        ReleaseGateDecision.is_current.is_(True),
    )
    # ``phase_id IS NULL`` is the release-level row and must be matched with IS
    # NULL rather than ``== None``: SQL equality against NULL is never true, so
    # the plain comparison would silently return nothing and every caller would
    # read "no verdict yet" for a release that has one.
    if phase_id is None:
        stmt = stmt.where(ReleaseGateDecision.phase_id.is_(None))
    else:
        stmt = stmt.where(ReleaseGateDecision.phase_id == phase_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def record_decision(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    verdict: str,
    *,
    phase_id: uuid.UUID | str | None = None,
    denominator: int = 0,
    evidence_count: int = 0,
    run_ids: Optional[list] = None,
    status_rollup: Optional[dict] = None,
    attribution_mix: Optional[dict] = None,
    ingestion_complete: Optional[bool] = None,
    incomplete_runs: Optional[dict] = None,
    policy_id: uuid.UUID | str | None = None,
    policy_snapshot: Optional[dict] = None,
    baseline_release_id: uuid.UUID | str | None = None,
    blocking_reasons: Optional[list] = None,
    conditions_for_go: Optional[list] = None,
    created_by_id: uuid.UUID | str | None = None,
) -> ReleaseGateDecision:
    """Append a verdict and make it the current one.

    Never updates an existing verdict. The previous one is demoted and kept, so
    "what did we decide, on what evidence, under which policy" survives a later
    re-evaluation. A gate that rewrites its own past cannot be audited, and that
    is the question this table exists to answer months later.

    Does NOT commit. Per the repo's transaction-boundary rule the router owns
    the commit, so one unit of work covers the demote and the promote together —
    which also means a failure cannot leave a release with two current verdicts
    or none.
    """
    if verdict not in VERDICTS:
        # A verdict outside the vocabulary is a silent filter failure waiting to
        # happen: the column is String(20), so the database accepts anything,
        # and it would simply never match a status query again.
        raise ValueError(f"unknown verdict {verdict!r}; expected one of {VERDICTS}")

    if verdict == "CONDITIONAL_GO" and not conditions_for_go:
        # A conditional go with no conditions is the same as a GO, recorded
        # under a word that implies somebody still has work to do. Whoever
        # reads the scorecard cannot act on it and cannot tell it apart from a
        # pass, which is the worst of both.
        #
        # NOTE: nothing in this product currently EMITS this verdict —
        # `release_rollup_service.decide` returns only GO / NO_GO /
        # NOT_EVALUATED, and `conditions_for_go` has no writer. The verdict
        # stays in the vocabulary because the column and the AI council's
        # recommendation both use it, and narrowing it here would reject a row
        # a future caller is entitled to record. This guard makes sure that
        # when one does, it arrives meaning something.
        raise ValueError(
            "CONDITIONAL_GO requires conditions_for_go; a conditional verdict "
            "with no conditions is a GO wearing a different word"
        )

    previous = await current_decision(db, release_id, phase_id=phase_id)
    if previous is not None:
        previous.is_current = False
        # FLUSH HERE, and do not remove it.
        #
        # Both rows are UPDATEs against the same partial unique index, and
        # SQLAlchemy's unit of work orders persistent UPDATEs by PRIMARY KEY,
        # not by assignment order. These ids are uuid4, so without this the
        # promote is emitted before the demote roughly half the time and the
        # index rejects it. Same defect as S0's `activate_release`, found by a
        # reviewer who ran it rather than read it.
        await db.flush()

    decision = ReleaseGateDecision(
        release_id=release_id,
        phase_id=phase_id,
        is_current=True,
        verdict=verdict,
        denominator=denominator,
        evidence_count=evidence_count,
        run_ids=run_ids,
        status_rollup=status_rollup,
        attribution_mix=attribution_mix,
        ingestion_complete=ingestion_complete,
        incomplete_runs=incomplete_runs,
        policy_id=policy_id,
        policy_snapshot=policy_snapshot,
        baseline_release_id=baseline_release_id,
        blocking_reasons=blocking_reasons,
        conditions_for_go=conditions_for_go,
        created_by_id=created_by_id,
    )
    db.add(decision)
    await db.flush()
    return decision


async def decision_history(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    *,
    phase_id: uuid.UUID | str | None = None,
    limit: int = 50,
) -> list[ReleaseGateDecision]:
    """Every verdict for one release-level or phase-level scope, newest first.

    ``phase_id=None`` means the release-level history. It must explicitly match
    ``phase_id IS NULL``; otherwise an export silently mixes every phase's
    verdict into the release's own append-only decision trail.
    """
    stmt = select(ReleaseGateDecision).where(ReleaseGateDecision.release_id == release_id)
    if phase_id is None:
        stmt = stmt.where(ReleaseGateDecision.phase_id.is_(None))
    else:
        stmt = stmt.where(ReleaseGateDecision.phase_id == phase_id)
    stmt = stmt.order_by(ReleaseGateDecision.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


def snapshot_is_self_contained(decision: ReleaseGateDecision) -> dict[str, Any]:
    """What the verdict can still explain with every referenced row deleted.

    Retention deletes runs; policies get edited. A verdict that recomputed
    itself on read would quietly restate history under today's inputs, so the
    numbers are stored rather than derived — and this reports what survives, so
    a caller can tell a fully explicable verdict from one recorded before the
    snapshot columns were populated.
    """
    return {
        "verdict": decision.verdict,
        "denominator": decision.denominator,
        "evidence_count": decision.evidence_count,
        "has_run_set": bool(decision.run_ids),
        "has_policy_snapshot": bool(decision.policy_snapshot),
        "has_status_rollup": bool(decision.status_rollup),
        "has_attribution_mix": bool(decision.attribution_mix),
        "has_ingestion_completeness": decision.ingestion_complete is not None,
    }
