"""Evaluating a release gate, and comparing it against its baseline (S6a-3).

Ties the two halves together: ``release_rollup_service`` computes what the
release looks like, ``release_gate_decision_service`` records the verdict
append-only, and this module is what a caller actually invokes.

Why the scorecard carries its own caveats
------------------------------------------
A verdict on its own is unusable. "NO_GO" over a release where two tests ran
means something entirely different from "NO_GO" over one where four thousand
did, and a reader given only the word cannot tell them apart. So every response
carries the denominator, the evidence count, whether it was measured at all,
and how the runs came to belong to the release — a verdict built mostly from the
active-release fallback is weaker evidence than one built from explicit client
names, and the reader is the one who has to weigh that.

The baseline comparison, and what it deliberately does not do
--------------------------------------------------------------
A release is compared against ``Release.baseline_release_id`` — explicitly its
predecessor, overridable because a hotfix's baseline is its parent release, not
whatever shipped most recently.

The comparison reports DELTAS and refuses to editorialise beyond them. In
particular a movement in pass rate is only reported as meaningful when BOTH
sides were measured: comparing a thoroughly-tested release against one where
nothing ran produces a large, confident, meaningless number, and that number
would be read as a regression.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Release
from app.services import release_defect_service as defect_svc
from app.services import release_gate_decision_service as decisions
from app.services import release_rollup_service as rollup_svc

#: Worst-first. A known blocker is decisive, so NO_GO outranks "cannot tell";
#: and NOT_EVALUATED outranks GO because an unassessed criterion must never
#: read as a pass. Both sides speak this same three-valued vocabulary, which
#: is what makes combining them meaningful rather than a cast.
_VERDICT_RANK = {"NO_GO": 2, "NOT_EVALUATED": 1, "GO": 0}


def _worse_of(a: str, b: str) -> str:
    return a if _VERDICT_RANK.get(a, 0) >= _VERDICT_RANK.get(b, 0) else b


async def evaluate_release(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    *,
    record: bool = True,
    created_by_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """Roll the release up, decide, and (by default) record the verdict.

    ``record=False`` exists for a preview: a caller can ask "what would the gate
    say?" without appending to the audit history. The history is the point of
    that table, so writing to it must be a deliberate act rather than a
    side effect of looking.
    """
    rollup = await rollup_svc.build_rollup(db, release_id)
    verdict, blocking = rollup_svc.decide(rollup)
    scorecard = rollup_svc.summarise(rollup)

    release = (
        await db.execute(select(Release).where(Release.id == release_id))
    ).scalar_one_or_none()
    baseline_id = getattr(release, "baseline_release_id", None) if release else None

    # Open defects are the other half of "is this release shippable", and the
    # rollup cannot see them: it answers from test results alone, so a release
    # whose whole suite passes over a known open CRITICAL rolled up to GO.
    #
    # ``defects_for_release`` is three-valued for the reason the rollup is:
    # "blocked by a known critical" and "cannot tell, these defects have no
    # severity" need different actions, and a boolean sends somebody to fix
    # the wrong thing. Unrated defects are reported rather than dropped, so
    # the gate cannot be passed by leaving a field blank.
    defects = None
    if release is not None:
        defects = await defect_svc.defects_for_release(
            db, release_id, release.project_id
        )
        verdict = _worse_of(verdict, defects["verdict"])
        blocking = list(blocking) + list(defects["reasons"])

    decision = None
    if record:
        decision = await decisions.record_decision(
            db,
            release_id,
            verdict,
            denominator=rollup.denominator,
            evidence_count=rollup.evidence_count,
            run_ids=rollup.run_ids,
            status_rollup=rollup.status_counts,
            attribution_mix=rollup.attribution_mix,
            baseline_release_id=baseline_id,
            blocking_reasons=blocking,
            created_by_id=created_by_id,
        )

    return {
        "release_id": str(release_id),
        "verdict": verdict,
        "blocking_reasons": blocking,
        "scorecard": scorecard,
        # Attached whole, not reduced to its verdict: a reader deciding whether
        # to override needs to see WHICH defects, and how many were unrated.
        "defects": defects,
        "decision_id": str(decision.id) if decision is not None else None,
        "recorded": record,
    }


async def compare_to_baseline(
    db: AsyncSession,
    release_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Release-over-release deltas, with the honesty conditions attached.

    Returns ``comparable: False`` and a reason rather than numbers whenever the
    comparison would be misleading. A delta computed against a release nothing
    ran in is arithmetically fine and completely meaningless, and once it is a
    number on a scorecard nobody re-derives whether it was meaningful.
    """
    release = (
        await db.execute(select(Release).where(Release.id == release_id))
    ).scalar_one_or_none()
    if release is None:
        return {"comparable": False, "reason": "release not found"}

    baseline_id = release.baseline_release_id
    if baseline_id is None:
        # Not an error. The first release of a project has no predecessor, and
        # saying so is more useful than a zero delta that implies one.
        return {"comparable": False, "reason": "this release has no baseline"}

    current = await rollup_svc.build_rollup(db, release_id)
    baseline = await rollup_svc.build_rollup(db, baseline_id)

    current_card = rollup_svc.summarise(current)
    baseline_card = rollup_svc.summarise(baseline)

    # BOTH sides must be measured. Comparing a thoroughly-tested release against
    # one where almost nothing ran yields a large, confident number that reads
    # as a regression and is an artefact of the denominator.
    if not current_card["measured"] or not baseline_card["measured"]:
        unmeasured = "this release" if not current_card["measured"] else "the baseline"
        return {
            "comparable": False,
            "reason": f"{unmeasured} is below the evidence floor",
            "current": current_card,
            "baseline": baseline_card,
        }

    current_rate = current.pass_rate()
    baseline_rate = baseline.pass_rate()
    return {
        "comparable": True,
        "baseline_release_id": str(baseline_id),
        "current": current_card,
        "baseline": baseline_card,
        "pass_rate_delta": (
            None
            if current_rate is None or baseline_rate is None
            else round(current_rate - baseline_rate, 2)
        ),
        # Reported alongside the rate delta because a pass-rate improvement on a
        # much smaller suite is not an improvement.
        "denominator_delta": current.denominator - baseline.denominator,
        "evidence_delta": current.evidence_count - baseline.evidence_count,
    }


async def current_gate(
    db: AsyncSession,
    release_id: uuid.UUID | str,
) -> Optional[dict[str, Any]]:
    """The standing verdict, read from the snapshot rather than recomputed.

    Recomputing here would quietly restate history under today's runs and
    today's policy, which is exactly what the snapshot columns exist to prevent.
    Returns ``None`` when the release has never been evaluated — distinct from a
    verdict of NOT_EVALUATED, which means it WAS evaluated and there was not
    enough evidence.
    """
    decision = await decisions.current_decision(db, release_id)
    if decision is None:
        return None
    return {
        "release_id": str(release_id),
        "verdict": decision.verdict,
        "blocking_reasons": decision.blocking_reasons or [],
        "decided_at": decision.created_at.isoformat() if decision.created_at else None,
        "scorecard": {
            "denominator": decision.denominator,
            "evidence_count": decision.evidence_count,
            "status_counts": decision.status_rollup or {},
            "attribution_mix": decision.attribution_mix or {},
            "run_count": len(decision.run_ids or []),
            "measured": decision.evidence_count >= rollup_svc.MIN_EVIDENCE,
            "evidence_floor": rollup_svc.MIN_EVIDENCE,
        },
        # Says plainly that this is a stored verdict, not a live read. Without
        # it a reader cannot tell whether the numbers describe now or the moment
        # the gate ran.
        "from_snapshot": True,
    }
