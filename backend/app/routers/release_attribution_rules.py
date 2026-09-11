"""Attribution rules — the bridge for teams that cannot send a release name.

Rung 3 of the attribution ladder matches a run's own metadata against
per-project rules and names the release it belongs to. The model, the four-value
match vocabulary, the evaluator and the linker's call site all shipped with S3a
and migration 0154.

**Nothing could create a rule.** There was no router, so the table was always
empty and rung 3 could never fire on any deployment — such projects fell
straight past it to the active release, which cannot tell a hotfix branch from
a release candidate from trunk CI. That is the gap this closes; the matching
logic itself is untouched.

Why a preview endpoint earns its place
--------------------------------------
The documented failure mode of a rule is not that it fails to match — it is
that it matches EVERYTHING. ``release_attribution`` says so directly: attributing
on a field a run does not carry "is how a rule quietly captures every run in the
project", and the damage is invisible because every run still gets attributed,
just to the wrong release. A rule is also retroactive in effect: it changes
where future runs land, and nobody re-reads it afterwards.

So a rule can be tried against recent runs before it is saved. Counting matches
is cheap and it is the one number that distinguishes a useful rule from one that
captures the whole project.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    require_project_access,
    require_role,
)
from app.db.postgres import get_db
from app.services.activity.service import ActorRef, record as record_activity
from app.models.postgres import (
    AttributionMatchField,
    ReleaseAttributionRule,
    TestRun,
    User,
    UserRole,
)
from app.services import release_attribution

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/attribution-rules",
    tags=["Release Attribution Rules"],
)

#: How many recent runs a preview evaluates against.
#:
#: Bounded because this runs synchronously in a request. Recent rather than
#: random: a rule is written for how the project builds NOW, and a sample from
#: last year would answer a question nobody asked.
PREVIEW_RUN_LIMIT = 200


class AttributionRuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    #: A closed set, taken from the ORM enum rather than restated.
    #:
    #: Every value must be a column that exists on the run AT INGEST TIME. A
    #: rule matching something derived later evaluates against NULL and
    #: silently never fires — which looks identical to a rule nobody triggered.
    match_field: str
    #: A glob, not a regex. User-authored input: a regex is easy to get subtly
    #: wrong and is a denial-of-service surface.
    match_pattern: str = Field(..., min_length=1, max_length=255)
    #: By NAME, not id — a rule usually predates the release it names, and the
    #: name resolves through the same auto-create path every other rung uses.
    target_release_name: str = Field(..., min_length=1, max_length=255)
    priority: int = Field(100, ge=0, le=10_000)
    is_enabled: bool = True


class AttributionRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    match_field: Optional[str] = None
    match_pattern: Optional[str] = Field(None, min_length=1, max_length=255)
    target_release_name: Optional[str] = Field(None, min_length=1, max_length=255)
    priority: Optional[int] = Field(None, ge=0, le=10_000)
    is_enabled: Optional[bool] = None


def _validate_match_field(value: Optional[str]) -> None:
    """Reject a field the evaluator cannot read.

    Stored as ``String(30)``, so the database accepts anything. A typo would
    save cleanly and then never match — the rule would sit in the list looking
    configured while the ladder fell past it, which is the silent-drift class
    this codebase guards against elsewhere with enum-vocabulary checks.
    """
    if value is None:
        return
    allowed = {f.value for f in AttributionMatchField}
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"match_field must be one of {sorted(allowed)} — a value the "
                "evaluator cannot read would store cleanly and never match."
            ),
        )


def _serialize(rule: ReleaseAttributionRule) -> dict:
    return {
        "id": str(rule.id),
        "project_id": str(rule.project_id),
        "name": rule.name,
        "match_field": rule.match_field,
        "match_pattern": rule.match_pattern,
        "target_release_name": rule.target_release_name,
        "priority": rule.priority,
        "is_enabled": rule.is_enabled,
        "created_at": rule.created_at,
    }


async def _get_rule_or_404(
    db: AsyncSession, project_id: uuid.UUID, rule_id: str
) -> ReleaseAttributionRule:
    try:
        wanted = uuid.UUID(rule_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="Rule not found") from exc

    rule = (
        await db.execute(
            select(ReleaseAttributionRule).where(
                ReleaseAttributionRule.id == wanted,
                # Scoped to the project in the PATH, not merely fetched by id.
                # Without this a rule id from another project would be editable
                # by anyone holding access to this one.
                ReleaseAttributionRule.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.get("")
async def list_attribution_rules(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
    __: User = Depends(require_project_access()),
):
    """Rules for this project, in the order the ladder evaluates them.

    Listed in evaluation order rather than by creation, because which rule wins
    is the whole question when two could match — sorting by anything else would
    show a list that does not explain the outcome.
    """
    rows = (
        (
            await db.execute(
                select(ReleaseAttributionRule)
                .where(ReleaseAttributionRule.project_id == project_id)
                .order_by(
                    ReleaseAttributionRule.priority.asc(),
                    ReleaseAttributionRule.created_at.asc(),
                    ReleaseAttributionRule.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_serialize(r) for r in rows], "total": len(rows)}


@router.post("", status_code=201)
async def create_attribution_rule(
    project_id: uuid.UUID,
    body: AttributionRuleIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_project_access()),
):
    """Create a rule. Rung 3 can fire for this project from the next run on."""
    _validate_match_field(body.match_field)

    rule = ReleaseAttributionRule(
        # Identity assigned here rather than obtained from a flush: the ledger
        # row is staged in the SAME transaction and needs a stable entity_id,
        # and an extra flush would be a transaction-shape change made purely to
        # read back a value we can just as well decide.
        id=uuid.uuid4(),
        project_id=project_id,
        name=body.name,
        match_field=body.match_field,
        match_pattern=body.match_pattern,
        target_release_name=body.target_release_name,
        priority=body.priority,
        is_enabled=body.is_enabled,
        created_by_id=current_user.id,
    )
    db.add(rule)

    # Epic ACT. An enabled catch-all rule was once left on a real project by an
    # e2e run and nobody could see who created it, because this router wrote no
    # record of any kind. Now it does.
    await record_activity(
        db,
        project_id=project_id,
        event_type="attribution_rule.created",
        actor=ActorRef.from_user(current_user),
        entity_id=rule.id,
        entity_label=rule.name,
        context={
            "match_field": rule.match_field,
            "match_pattern": rule.match_pattern,
            "target_release_name": rule.target_release_name,
            "is_enabled": rule.is_enabled,
        },
    )

    await db.commit()
    await db.refresh(rule)
    return _serialize(rule)


@router.put("/{rule_id}")
async def update_attribution_rule(
    project_id: uuid.UUID,
    rule_id: str,
    body: AttributionRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_project_access()),
):
    rule = await _get_rule_or_404(db, project_id, rule_id)
    updates = body.model_dump(exclude_none=True)
    _validate_match_field(updates.get("match_field"))

    before = {field: getattr(rule, field, None) for field in updates}
    # Only what differs — see the note in routers/projects.py: a re-sent value
    # is not a change, and recording it as one makes the feed report edits
    # nobody made.
    changed = {f: v for f, v in updates.items() if before.get(f) != v}

    for field, value in updates.items():
        setattr(rule, field, value)

    if changed:
        # Enabling/disabling is the change an operator most needs to see, so it
        # gets its own event name rather than hiding inside a field diff.
        if set(changed) == {"is_enabled"}:
            event = (
                "attribution_rule.enabled"
                if changed["is_enabled"]
                else "attribution_rule.disabled"
            )
        else:
            event = "attribution_rule.updated"
        await record_activity(
            db,
            project_id=project_id,
            event_type=event,
            actor=ActorRef.from_user(current_user),
            entity_id=rule.id,
            entity_label=rule.name,
            before={f: before[f] for f in changed},
            after=changed,
            context={"changed": ", ".join(sorted(changed))},
        )

    await db.commit()
    await db.refresh(rule)
    return _serialize(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_attribution_rule(
    project_id: uuid.UUID,
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_project_access()),
):
    """Delete a rule.

    Deliberately does NOT re-attribute the runs it already matched. A link
    carries ``link_source=rule_match`` and the release it produced; rewriting
    history because a rule was retired would change what a past gate decision
    was based on, silently.
    """
    rule = await _get_rule_or_404(db, project_id, rule_id)
    # Staged BEFORE the delete so the label and pattern are still readable —
    # after db.delete the attributes are gone and the row would say only that
    # "something" was removed.
    await record_activity(
        db,
        project_id=project_id,
        event_type="attribution_rule.deleted",
        actor=ActorRef.from_user(current_user),
        entity_id=rule.id,
        entity_label=rule.name,
        context={
            "match_field": rule.match_field,
            "match_pattern": rule.match_pattern,
        },
    )
    await db.delete(rule)
    await db.commit()


@router.post("/preview")
async def preview_attribution_rule(
    project_id: uuid.UUID,
    body: AttributionRuleIn,
    limit: int = Query(PREVIEW_RUN_LIMIT, ge=1, le=PREVIEW_RUN_LIMIT),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
    __: User = Depends(require_project_access()),
):
    """Try a rule against recent runs WITHOUT saving it.

    The failure mode of an attribution rule is not that it misses — it is that
    it captures everything, and every run still gets attributed so nothing looks
    wrong. ``matched`` against ``considered`` is the one number that tells those
    apart before the rule starts deciding where real runs land.

    Evaluated with ``rule_matches`` — the same predicate the ladder uses — on an
    UNSAVED instance, so the preview cannot disagree with the rule it previews.
    """
    _validate_match_field(body.match_field)

    runs = (
        (
            await db.execute(
                select(TestRun)
                .where(TestRun.project_id == project_id)
                .order_by(TestRun.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    candidate = ReleaseAttributionRule(
        project_id=project_id,
        name=body.name,
        match_field=body.match_field,
        match_pattern=body.match_pattern,
        target_release_name=body.target_release_name,
        priority=body.priority,
        is_enabled=body.is_enabled,
    )
    matched = [r for r in runs if release_attribution.rule_matches(candidate, r)]

    return {
        "considered": len(runs),
        "matched": len(matched),
        # A field most runs do not carry produces a rule that fires rarely and
        # unpredictably, which reads as "the rule is broken" long after it was
        # written. Reported so that is visible before saving.
        "field_present": sum(
            1
            for r in runs
            if release_attribution._run_value(r, body.match_field)
        ),
        "sample": [
            {
                "run_id": str(r.id),
                "build_number": r.build_number,
                "branch": r.branch,
                "environment": r.environment,
            }
            for r in matched[:10]
        ],
    }
