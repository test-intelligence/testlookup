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

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Release
from app.services import release_defect_service as defect_svc
from app.services import release_gate_decision_service as decisions
from app.services import release_gate_reasons
from app.services import release_rollup_service as rollup_svc
from app.services.notification import gate_notifications

#: Blocking reasons embedded in a release list / detail item (W05 REL-01). The
#: card shows three and the count; the rest live on the release's gate panel.
GATE_LIST_REASON_CAP = 20
#: ``blocking_reasons_display`` on ``GET /releases/{id}/gate``.
GATE_DETAIL_REASON_CAP = 200

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
        # W09 NTF-01: the verdict recorded immediately before this one, read only
        # when recording, so "Quality gate failed" is staged on a transition INTO
        # NO_GO and never on a repeat (or on a preview, record=False).
        previous = await decisions.current_decision(db, release_id)
        previous_verdict = getattr(previous, "verdict", None)
        decision = await decisions.record_decision(
            db,
            release_id,
            verdict,
            denominator=rollup.denominator,
            evidence_count=rollup.evidence_count,
            run_ids=rollup.run_ids,
            status_rollup=rollup.status_counts,
            attribution_mix=rollup.attribution_mix,
            ingestion_complete=not bool(rollup.incomplete_runs),
            incomplete_runs=rollup.incomplete_runs,
            baseline_release_id=baseline_id,
            blocking_reasons=blocking,
            created_by_id=created_by_id,
        )
        if release is not None and gate_notifications.is_transition_into_no_go(previous_verdict, verdict):
            # SAVEPOINT + swallow inside: a notification fault never fails the gate.
            await gate_notifications.stage_gate_no_go(
                db, release_id=release_id, decision=decision, previous_verdict=previous_verdict,
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
    status_counts = decision.status_rollup or {}
    ingestion_complete = getattr(decision, "ingestion_complete", None)
    incomplete_runs = getattr(decision, "incomplete_runs", None) or {}
    blocking_reasons = decision.blocking_reasons or []
    measured = _is_measured(decision.evidence_count, ingestion_complete)
    return {
        "release_id": str(release_id),
        "verdict": decision.verdict,
        "blocking_reasons": blocking_reasons,
        # W05 REL-01 (additive): the full count, so a reader of a truncated
        # display list knows how many there are.
        "blocking_total": len(blocking_reasons),
        "decided_at": decision.created_at.isoformat() if decision.created_at else None,
        "scorecard": {
            "denominator": decision.denominator,
            "evidence_count": decision.evidence_count,
            "status_counts": status_counts,
            "attribution_mix": decision.attribution_mix or {},
            "run_count": len(decision.run_ids or []),
            "measured": measured,
            "ingestion_complete": ingestion_complete,
            "incomplete_runs": incomplete_runs,
            "evidence_floor": rollup_svc.MIN_EVIDENCE,
            # W05 REL-01 (additive): derived from the row already held -- no
            # query; the same arithmetic and sentence as the live scorecard.
            "pass_rate": _stored_pass_rate(status_counts, decision.evidence_count),
            "insufficient_reason": _stored_insufficient_reason(
                measured,
                decision.evidence_count,
                decision.denominator,
                ingestion_complete,
                incomplete_runs,
            ),
        },
        # Says plainly that this is a stored verdict, not a live read. Without
        # it a reader cannot tell whether the numbers describe now or the moment
        # the gate ran.
        "from_snapshot": True,
    }


# ── The verdict embedded in a release (W05 REL-01) ───────────────────────────


def _is_measured(evidence_count: Any, ingestion_complete: Optional[bool]) -> bool:
    """The formula ``current_gate`` has always used for a stored row."""
    return (
        int(evidence_count or 0) >= rollup_svc.MIN_EVIDENCE
        and ingestion_complete is not False
    )


def _stored_pass_rate(
    status_counts: Mapping[str, Any] | None, evidence_count: Any
) -> Optional[float]:
    """``None`` for a legacy row whose rollup carries no ``PASSED`` count."""
    passed = (status_counts or {}).get("PASSED")
    return rollup_svc.pass_rate_from_counts(
        int(passed) if passed is not None else None, int(evidence_count or 0)
    )


def _stored_insufficient_reason(
    measured: bool,
    evidence_count: Any,
    denominator: Any,
    ingestion_complete: Optional[bool],
    incomplete_runs: Mapping[str, Any] | None,
) -> Optional[str]:
    if measured:
        return None
    reason = rollup_svc.insufficient_reason(
        int(evidence_count or 0), int(denominator or 0), len(incomplete_runs or {})
    )
    # Unmeasured only because ingestion was incomplete, with no per-run counts
    # stored: still say why, rather than ``None`` for an unmeasured verdict.
    return reason or "source results were rejected; release evidence is incomplete"


def _iso_utc(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment: datetime = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    return str(value)


def shape_gate_summary(
    *,
    verdict: str,
    decided_at: datetime | None,
    denominator: int,
    evidence_count: int,
    run_count: int,
    status_counts: Mapping[str, int] | None,
    ingestion_complete: bool | None,
    incomplete_runs: Mapping[str, int] | None,
    blocking_reasons: Sequence[str],
    blocking_total: int,
) -> dict[str, Any]:
    """The verdict a release card carries, from one stored decision row. Pure.

    ``blocking_reasons`` are DISPLAY strings (already translated by
    ``release_gate_reasons``); at most ``GATE_LIST_REASON_CAP`` are kept, and
    ``blocking_total`` says how many the decision really holds.
    """
    counts = {str(k): int(v or 0) for k, v in (status_counts or {}).items()}
    evidence = int(evidence_count or 0)
    measured = _is_measured(evidence, ingestion_complete)
    return {
        "verdict": str(verdict),
        "decided_at": _iso_utc(decided_at),
        "pass_rate": _stored_pass_rate(counts, evidence),
        "denominator": int(denominator or 0),
        "evidence_count": evidence,
        "run_count": int(run_count or 0),
        "status_counts": counts,
        "measured": measured,
        "insufficient_reason": _stored_insufficient_reason(
            measured, evidence, denominator, ingestion_complete, incomplete_runs
        ),
        "blocking_reasons": [str(r) for r in list(blocking_reasons)[:GATE_LIST_REASON_CAP]],
        "blocking_total": int(blocking_total or 0),
    }


#: ONE statement for any number of releases. ``run_ids`` and the whole
#: ``blocking_reasons`` array are never loaded: their lengths and the first
#: ``GATE_LIST_REASON_CAP`` reasons are computed in the database. Both columns
#: are ``json`` (migration 0156), and SQLAlchemy's ``JSON`` type stores a Python
#: ``None`` as the JSON scalar ``null``, which ``json_array_length`` refuses:
#: hence the ``json_typeof`` guards. ``ix_rgd_release_current`` (partial,
#: ``is_current IS TRUE AND phase_id IS NULL``) serves the predicate; the join
#: to ``releases`` is a primary-key lookup naming each row's project, so the
#: reasons are translated inside the right tenant.
CURRENT_GATE_SUMMARIES_SQL = (
    "SELECT d.release_id::text AS release_id, r.project_id::text AS project_id, "
    "d.verdict, d.created_at, d.denominator, d.evidence_count, d.status_rollup, "
    "d.ingestion_complete, d.incomplete_runs, "
    "CASE WHEN json_typeof(d.run_ids) = 'array' THEN json_array_length(d.run_ids) ELSE 0 END "
    "AS run_count, "
    "CASE WHEN json_typeof(d.blocking_reasons) = 'array' "
    "THEN json_array_length(d.blocking_reasons) ELSE 0 END AS blocking_total, "
    "CASE WHEN json_typeof(d.blocking_reasons) = 'array' "
    "THEN jsonb_path_query_array(d.blocking_reasons::jsonb, "
    f"'$[0 to {GATE_LIST_REASON_CAP - 1}]') "
    "ELSE '[]'::jsonb END AS blocking_head "
    "FROM release_gate_decisions d JOIN releases r ON r.id = d.release_id "
    "WHERE d.release_id = ANY(:ids) AND d.is_current IS TRUE AND d.phase_id IS NULL"
)


def _json(value: Any) -> Any:
    """A ``json`` column read through ``text()``: decoded by the driver's codec
    normally, a raw string on a driver without one."""
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


async def current_gate_summaries(
    db: AsyncSession, release_ids: Sequence[uuid.UUID | str]
) -> dict[str, dict[str, Any]]:
    """``{str(release_id): summary}`` for each release's CURRENT release-level
    decision. A release that was never evaluated is ABSENT (the caller embeds
    ``gate: null``); history rows and phase rows are ignored.

    At most two statements whatever the number of releases: the summary read
    and, only when a reason names a test, one name lookup for the whole batch.
    """
    ids: list[uuid.UUID] = []
    for rid in release_ids:
        try:
            ids.append(rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid)))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    result = await db.execute(text(CURRENT_GATE_SUMMARIES_SQL), {"ids": ids})
    rows = list(result.mappings().all())
    if not rows:
        return {}

    heads: dict[str, list[str]] = {}
    per_project: dict[uuid.UUID, list[str]] = {}
    for row in rows:
        head = _json(row["blocking_head"])
        head = [str(r) for r in head] if isinstance(head, list) else []
        heads[row["release_id"]] = head
        if head:
            per_project.setdefault(uuid.UUID(str(row["project_id"])), []).extend(head)

    translated: dict[uuid.UUID, list[str]] = {}
    if per_project:
        translated = await release_gate_reasons.display_reasons_by_project(db, per_project)

    # Each project's list comes back in input order: walk it out release by
    # release.
    cursors: dict[uuid.UUID, int] = {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        head = heads[row["release_id"]]
        display = head
        if head:
            pid = uuid.UUID(str(row["project_id"]))
            start = cursors.get(pid, 0)
            display = translated.get(pid, [])[start:start + len(head)] or head
            cursors[pid] = start + len(head)
        status_counts = _json(row["status_rollup"])
        incomplete = _json(row["incomplete_runs"])
        out[str(row["release_id"])] = shape_gate_summary(
            verdict=row["verdict"],
            decided_at=row["created_at"],
            denominator=row["denominator"],
            evidence_count=row["evidence_count"],
            run_count=row["run_count"],
            status_counts=status_counts if isinstance(status_counts, dict) else None,
            ingestion_complete=row["ingestion_complete"],
            incomplete_runs=incomplete if isinstance(incomplete, dict) else None,
            blocking_reasons=display,
            blocking_total=row["blocking_total"],
        )
    return out
