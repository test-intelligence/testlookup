"""Phase gating — a verdict per phase of a release (S6b).

Depends on three earlier slices, and the dependency is real in each case:
S6a for the decision table and its phase-scoped partial unique index, S3a-2 for
the ``phase_id`` stamped on each link, and S7b for a policy resolver that can
take a phase layer without discarding the project's document.

The failure this slice is most at risk of
------------------------------------------
Passing vacuously. ``match_phase`` returns None freely — phases are optional and
their planned windows need not cover the whole release — so a NULL ``phase_id``
is a common, correct state and MANY phases legitimately have no runs at all.

A gate that reads "no failures" off an empty phase says GO to every phase nobody
tested. That is worse than no gate: the product would hand out approvals for
work that never happened, and each one looks exactly like a real pass. The
evidence floor is what stops it, and most of the tests for this module are about
the empty and near-empty cases rather than the populated one.

Why a phase gate is not just the release gate with a filter
------------------------------------------------------------
Phases run in order, and an exit criterion is about leaving one phase for the
next. So the verdict for a phase has to be readable on its own — a phase that
cannot be evaluated must not be reported the same way as one that failed, or a
release manager cannot tell "this phase is not ready" from "we have no idea
whether this phase is ready".
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ReleasePhase
from app.services import release_gate_decision_service as decisions
from app.services import release_rollup_service as rollup_svc
from app.services import policy_resolution


async def evaluate_phase(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    phase_id: uuid.UUID | str,
    *,
    record: bool = True,
    created_by_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """Roll one phase up and decide whether it may be exited."""
    phase = (
        await db.execute(select(ReleasePhase).where(ReleasePhase.id == phase_id))
    ).scalar_one_or_none()
    if phase is None:
        return {"phase_id": str(phase_id), "verdict": "NOT_EVALUATED",
                "blocking_reasons": ["no such phase"], "scorecard": None, "recorded": False}

    rollup = await rollup_svc.build_rollup(db, release_id, phase_id=phase_id)
    verdict, blocking = rollup_svc.decide(rollup)
    scorecard = rollup_svc.summarise(rollup)

    # The phase's own exit criteria join the policy stack as the NARROWEST
    # layer, merged key-wise over the project's. Before S7b this would have
    # replaced the project document wholesale, so a phase setting one criterion
    # would have silently discarded every threshold the project configured.
    policy = await policy_resolution.resolve_effective_document(
        db, getattr(phase, "project_id", None) or await _project_of(db, release_id)
    )
    if phase.exit_criteria:
        document, sources = policy_resolution.merge_documents(
            [(level, layer) for level, layer in _as_layers(policy)]
            + [("phase", phase.exit_criteria)]
        )
        policy = {**policy, "document": document, "sources": sources,
                  "layers": policy["layers"] + ["phase"], "effective_level": "phase"}

    decision = None
    if record:
        decision = await decisions.record_decision(
            db,
            release_id,
            verdict,
            phase_id=phase_id,
            denominator=rollup.denominator,
            evidence_count=rollup.evidence_count,
            run_ids=rollup.run_ids,
            status_rollup=rollup.status_counts,
            attribution_mix=rollup.attribution_mix,
            policy_snapshot=policy,
            blocking_reasons=blocking,
            created_by_id=created_by_id,
        )

    return {
        "release_id": str(release_id),
        "phase_id": str(phase_id),
        "phase_name": phase.name,
        "verdict": verdict,
        "blocking_reasons": blocking,
        "scorecard": scorecard,
        "policy": policy,
        "decision_id": str(decision.id) if decision is not None else None,
        "recorded": record,
        # Stated rather than inferred. A phase with no runs is the common case,
        # and "may_exit: false" for an unevaluated phase reads identically to
        # one that failed unless the reason travels with it.
        "may_exit": verdict == "GO",
    }


async def evaluate_all_phases(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    *,
    record: bool = False,
) -> list[dict[str, Any]]:
    """Every phase of a release, in order.

    Defaults to ``record=False``: reading the state of a release's phases is a
    routine act, and appending a row per phase per look would bury the real
    decisions in noise. ``evaluate_phase`` is where recording is deliberate.
    """
    phases = list(
        (
            await db.execute(
                select(ReleasePhase)
                .where(ReleasePhase.release_id == release_id)
                .order_by(ReleasePhase.order_index.asc(), ReleasePhase.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        await evaluate_phase(db, release_id, p.id, record=record)
        for p in phases
    ]


def summarise_gate(phase_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the release may advance, and precisely why not.

    Deliberately three-valued rather than a boolean. A release blocked by a
    FAILING phase and one blocked by an UNEVALUATED phase need different actions
    — fix the tests, or go run some — and collapsing both into "cannot advance"
    tells a release manager to do the wrong one half the time.
    """
    failing = [p["phase_name"] for p in phase_results if p["verdict"] == "NO_GO"]
    unevaluated = [
        p["phase_name"] for p in phase_results if p["verdict"] == "NOT_EVALUATED"
    ]
    if failing:
        status = "BLOCKED"
    elif unevaluated:
        # NOT the same as blocked, and emphatically not GO. An unevaluated phase
        # is an absence of evidence, and treating absence as approval is how a
        # gate hands out passes for work nobody did.
        status = "INCOMPLETE"
    elif phase_results:
        status = "READY"
    else:
        # A release with no phases at all. Phases are optional, so this is not
        # a failure — but it is also not a pass, because nothing was gated.
        status = "NO_PHASES"
    return {
        "status": status,
        "failing_phases": failing,
        "unevaluated_phases": unevaluated,
        "phase_count": len(phase_results),
    }


async def _project_of(db: AsyncSession, release_id: uuid.UUID | str):
    from app.models.postgres import Release

    return (
        await db.execute(select(Release.project_id).where(Release.id == release_id))
    ).scalar_one_or_none()


def _as_layers(resolved: dict) -> list[tuple[str, dict]]:
    """Rebuild the layer list from a resolved document.

    ``resolve_effective_document`` returns the merged result rather than the
    layers, so re-merging with a phase on top needs the merged document as a
    single base layer. That is sound because merging is associative for the
    key-wise rule: ((a<-b)<-c) equals (a<-b<-c).
    """
    return [(resolved.get("effective_level", "project"), resolved.get("document", {}))]
