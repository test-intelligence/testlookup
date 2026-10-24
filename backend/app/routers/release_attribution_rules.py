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

W11 REL-09: "which release would this run land in"
--------------------------------------------------
Matching alone over-promises: the candidate is evaluated ALONE, while at ingest
an earlier enabled rule that matches the same run wins (rung 3 is a total order),
and a run that named its release (rung 1) or was matched to a milestone (rung 2)
never reaches the rules at all. ``POST /preview?detail=true`` therefore answers
where each matched run would really land — ``rule`` (here), ``earlier_rule``
(``shadowed``) or ``explicit`` (rungs 1-2 keep it) — whether the target release
exists yet, and how many of the considered runs have no release at all
(Unattributed). Without ``detail`` the preview is exactly what it always was:
one statement and four keys. The ladder itself is not changed (D13).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    require_project_access,
    require_role,
)
from app.db.postgres import get_db
from app.services.activity.service import ActorRef, record as record_activity
from app.models.postgres import (
    ASSERTED_LINK_SOURCES,
    AttributionMatchField,
    Release,
    ReleaseAttributionRule,
    ReleaseTestRunLink,
    TestRun,
    User,
    UserRole,
)
from app.models.schemas import (
    AttributionPreviewResponse,
    AttributionRuleField,
    AttributionRuleFieldsResponse,
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

#: Where a matched run would land if the previewed rule were saved (W11 REL-09,
#: ``AttributionPreviewSample.would_land``), in the ladder's precedence:
#: ``explicit`` — its primary link is an assertion (rung 1 / 2), a rule never moves it;
#: ``earlier_rule`` — an enabled saved rule ahead of the candidate matches it first;
#: ``rule`` — the candidate would claim it.
PREVIEW_LANDINGS = ("rule", "earlier_rule", "explicit")

#: The page's field labels and help, one entry per ``AttributionMatchField``
#: value (``GET /fields``; ``frontend/src/types/attributionRules.ts`` keeps a
#: fallback copy, pinned by ``tests/test_attribution_field_vocab.py``).
ATTRIBUTION_FIELD_LABELS = {
    "branch": "Branch",
    "build_number": "Build number",
    "environment": "Environment",
    "tag": "Tag",
}
ATTRIBUTION_FIELD_HELP = {
    "branch": "The run's git branch. Wildcards: * and ?, e.g. release/2.5*.",
    "build_number": "The run's build label. Wildcards: * and ?, e.g. nightly-*.",
    "environment": "The run's environment, matched exactly (case does not matter), e.g. staging.",
    "tag": "Any of the run's tags. Wildcards: * and ?, e.g. rc-*.",
}

#: A preview sample's tags: the first ten, each clipped to 100 characters.
_SAMPLE_TAGS = 10
_SAMPLE_TAG_CHARS = 100
_SAMPLE_SIZE = 10


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


def _field_is_glob(value: str) -> bool:
    """Whether ``rule_matches`` reads this field with a glob (``*`` / ``?``).

    Read from the evaluator's own set (plus ``tag``, whose list is globbed
    member by member), so the page's help cannot drift from the matcher.
    """
    return value in release_attribution._GLOB_FIELDS or value == AttributionMatchField.TAG.value


@router.get("/fields", response_model=AttributionRuleFieldsResponse)
async def list_attribution_rule_fields(
    project_id: uuid.UUID,
    _: User = Depends(get_current_active_user),
    __: User = Depends(require_project_access()),
) -> AttributionRuleFieldsResponse:
    """The fields a rule can match on, with how each one matches (W11 REL-09).

    In ``AttributionMatchField`` declaration order. Static: the vocabulary is
    the ORM enum, the glob flag the evaluator's own set, so the handler runs
    no statement (the dependency checks the caller's access to the project).
    """
    return AttributionRuleFieldsResponse(
        fields=[
            AttributionRuleField(
                value=f.value,
                label=ATTRIBUTION_FIELD_LABELS[f.value],
                glob=_field_is_glob(f.value),
                help=ATTRIBUTION_FIELD_HELP[f.value],
            )
            for f in AttributionMatchField
        ]
    )


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


# activity: none - dry-run of a rule against recent runs; persists nothing
@router.post(
    "/preview",
    response_model=AttributionPreviewResponse,
    # Without ``detail`` the answer is exactly the pre-W11 four keys (and a
    # sample of four fields): the new fields' defaults are not claims, so they
    # are left out rather than reported as measured zeros.
    response_model_exclude_unset=True,
)
async def preview_attribution_rule(
    project_id: uuid.UUID,
    body: AttributionRuleIn,
    limit: int = Query(PREVIEW_RUN_LIMIT, ge=1, le=PREVIEW_RUN_LIMIT),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
    __: User = Depends(require_project_access()),
    detail: bool = False,
    rule_id: Optional[uuid.UUID] = None,
):
    """Try a rule against recent runs WITHOUT saving it.

    The failure mode of an attribution rule is not that it misses — it is that
    it captures everything, and every run still gets attributed so nothing looks
    wrong. ``matched`` against ``considered`` is the one number that tells those
    apart before the rule starts deciding where real runs land.

    Evaluated with ``rule_matches`` — the same predicate the ladder uses — on an
    UNSAVED instance, so the preview cannot disagree with the rule it previews.

    ``detail`` (W11 REL-09) adds where each matched run would really land
    (``_preview_detail``). ``rule_id`` names the rule being edited, so it is not
    counted as an earlier rule shadowing itself; it is read only inside the
    PATH project (the access check above), so another project's rule id is
    simply never found — the answer is the same as without it, no oracle.
    Plain defaults, not ``Query(...)``: tests call this handler directly.
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

    out: dict[str, Any] = {
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
            for r in matched[:_SAMPLE_SIZE]
        ],
    }
    if not detail:
        return out
    return await _preview_detail(
        db, project_id=project_id, body=body, rule_id=rule_id, runs=runs, matched=matched, out=out
    )


def _sample_tags(run: TestRun) -> list[str]:
    """The run's tags as the sample shows them: strings only, ten, each ≤ 100 chars."""
    tags = run.tags if isinstance(run.tags, list) else []
    return [t[:_SAMPLE_TAG_CHARS] for t in tags if isinstance(t, str)][:_SAMPLE_TAGS]


async def _preview_detail(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    body: AttributionRuleIn,
    rule_id: Optional[uuid.UUID],
    runs: Sequence[TestRun],
    matched: Sequence[TestRun],
    out: dict[str, Any],
) -> dict[str, Any]:
    """Where each matched run would land if the candidate were saved (W11 REL-09).

    Three statements after the runs, in order, each confined to the PATH
    project; nothing is written:

    (a) the project's enabled rules in the ladder's own order (``priority``,
        ``created_at``, ``id`` — ``match_attribution_rule``'s ORDER BY), plus
        the edited rule (``rule_id``) whatever its state, so its own position
        is known. A NEW candidate is placed by ``(priority, created_at = now)``:
        after every saved rule of the same priority, exactly where saving it
        would put it. An EDITED one keeps its ``created_at`` and ``id`` (an
        update moves only its priority) and is not an earlier rule for itself.
        A run is ``shadowed`` when a saved rule ahead of the candidate matches
        it, with the ladder's one predicate ``rule_matches``.
    (b) the matched runs' PRIMARY links with their release names: a link
        asserted by rung 1 / 2 (``ASSERTED_LINK_SOURCES``) means a rule would
        never have moved the run (``explicit``); the name is the run's release
        today (``None`` = Unattributed).
    (c) whether a release of the project already carries the target name, by
        the linker's own match (stripped, ``lower(name) = lower(:target)``):
        when none does, the first matching run creates it.

    Each matched run lands in exactly ONE place, in the ladder's precedence
    (explicit, then an earlier rule, then the candidate), so ``explicit`` +
    ``shadowed`` + the runs that land "here" add up to ``matched``.
    ``unattributed_recent`` counts the considered runs with no primary release
    (no statement: the runs are loaded).
    """
    saved = (
        await db.execute(
            select(ReleaseAttributionRule)
            .where(
                ReleaseAttributionRule.project_id == project_id,
                or_(
                    ReleaseAttributionRule.is_enabled.is_(True),
                    ReleaseAttributionRule.id == rule_id,
                )
                if rule_id is not None
                else ReleaseAttributionRule.is_enabled.is_(True),
            )
            .order_by(
                ReleaseAttributionRule.priority.asc(),
                ReleaseAttributionRule.created_at.asc(),
                ReleaseAttributionRule.id.asc(),
            )
        )
    ).scalars().all()

    editing = next((r for r in saved if rule_id is not None and r.id == rule_id), None)
    others = [r for r in saved if r is not editing and r.is_enabled]
    if editing is not None:
        position = (body.priority, editing.created_at, editing.id)
        earlier = [r for r in others if (r.priority, r.created_at, r.id) < position]
    else:
        # created_at = now: every saved rule of the same priority is older.
        earlier = [r for r in others if r.priority <= body.priority]

    links: dict[uuid.UUID, tuple[Optional[str], Optional[str]]] = {}
    if matched:
        rows = (
            await db.execute(
                select(
                    ReleaseTestRunLink.test_run_id,
                    ReleaseTestRunLink.link_source,
                    Release.name,
                )
                .join(Release, Release.id == ReleaseTestRunLink.release_id)
                .where(
                    ReleaseTestRunLink.test_run_id.in_([r.id for r in matched]),
                    ReleaseTestRunLink.is_primary.is_(True),
                    Release.project_id == project_id,
                )
            )
        ).all()
        links = {row[0]: (row[1], row[2]) for row in rows}

    target = body.target_release_name.strip()
    target_exists = (
        await db.execute(
            select(literal(1))
            .select_from(Release)
            .where(
                Release.project_id == project_id,
                func.lower(Release.name) == target.lower(),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None

    def landing(run: TestRun) -> str:
        source = links.get(run.id, (None, None))[0]
        if source in ASSERTED_LINK_SOURCES:
            return "explicit"
        if any(release_attribution.rule_matches(rule, run) for rule in earlier):
            return "earlier_rule"
        return "rule"

    landings = [landing(r) for r in matched]
    out["shadowed"] = landings.count("earlier_rule")
    out["explicit"] = landings.count("explicit")
    out["target_exists"] = target_exists
    out["unattributed_recent"] = sum(1 for r in runs if r.primary_release_id is None)
    out["sample"] = [
        {
            **row,
            "tags": _sample_tags(run),
            "created_at": run.created_at,
            "current_release_name": links.get(run.id, (None, None))[1],
            "would_land": where,
        }
        for row, run, where in zip(out["sample"], matched, landings)
    ]
    return out
