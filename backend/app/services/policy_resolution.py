"""Key-wise policy resolution across scopes (S7b).

The defect this replaces
------------------------
``resolve_effective_policy`` picks ONE policy document and discards the rest:
project if there is one, else system, else hardcoded defaults. Winner takes all.

That is wrong the moment a project wants to change one thing. A project policy
that sets a single threshold silently discards every other key the system
default carried, and those keys do not fall back to the system default — they
fall through to the HARDCODED constants, because the system document was never
consulted again. A team tightening one number gets a policy they never wrote for
everything else, with nothing anywhere reporting the substitution.

S6b makes it worse by adding a third scope. A phase policy setting one criterion
would discard the project's entire document the same way.

So resolution merges KEY BY KEY, narrowest scope winning per key, and records
which scope supplied each one.

Why provenance per key, not just per document
----------------------------------------------
"Why did this release fail the gate?" is answerable only if you can say which
threshold applied and where it came from. With whole-document resolution the
answer is a single level name that is true of the document and false of most of
its contents. Recording the source per key is what makes a gate decision
explicable rather than merely reproducible — and the S6a snapshot stores the
merged result, so the explanation survives a later policy edit.

Deliberately NOT a deep merge of everything
--------------------------------------------
Lists are replaced, not concatenated. ``rules`` is an ordered list of
conditions, and appending a narrower scope's rules to a broader scope's would
silently apply BOTH — a project that removed a rule would find it still firing.
Replacement is the honest semantic for an ordered list: you either inherit the
list or you state your own.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ReleaseGatePolicy

#: Narrowest last. Each scope overrides the ones before it, key by key.
#:
#: ``phase`` is declared now and unused until S6b. Declaring it here means that
#: slice adds a ROW to this list rather than a fourth branch to a chain of
#: if-statements, which is how the current resolver became winner-takes-all.
SCOPES: tuple[str, ...] = ("hardcoded", "system", "project", "phase")

#: Keys taken wholesale from the narrowest scope that sets them, never merged.
#:
#: Only meaningful for DICT-valued keys: a list is replaced anyway, because the
#: nested merge below only engages for dicts. ``rules`` is listed for intent —
#: it is an ordered list of conditions and concatenating scopes would leave a
#: project unable to remove a rule — but mutation testing showed the membership
#: is doing no work for it. Naming that here rather than leaving a constant that
#: appears to enforce something it does not.
#:
#: A dict-valued key belongs here when its keys are a SET rather than
#: independent settings, so inheriting half of one scope and half of another
#: would produce a combination nobody configured.
REPLACE_WHOLESALE = frozenset({"rules", "kind_rules"})


def merge_documents(layers: list[tuple[str, dict[str, Any]]]) -> tuple[dict, dict[str, str]]:
    """Merge policy documents broadest-first, returning the result and provenance.

    ``layers`` is ordered broadest to narrowest. Returns ``(merged, sources)``
    where ``sources`` maps each top-level key to the scope that supplied its
    final value — the record that makes a verdict explicable.

    Nested dicts merge one level down (``thresholds``, ``hard_caps`` and friends
    are flat maps of named numbers, and a project overriding one threshold must
    not drop its siblings). Anything in ``REPLACE_WHOLESALE`` is taken whole.
    """
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for scope, doc in layers:
        # No empty-document guard: iterating an empty dict already does
        # nothing. Mutation testing found one here that could not be killed,
        # because there was no behaviour behind it — a guard that reads as
        # protection and protects nothing.
        for key, value in doc.items():
            nestable = key not in REPLACE_WHOLESALE and isinstance(value, dict)
            if nestable and isinstance(merged.get(key), dict):
                # One level down, so overriding `thresholds.go_threshold` does
                # not delete `thresholds.no_go_threshold`. That silent sibling
                # loss is the whole defect being fixed.
                nested = dict(merged[key])
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
            if nestable:
                # Recorded on EVERY layer, including the first. Recording it
                # only when merging into an existing dict left the broadest
                # layer's keys with no provenance at all — so the one scope
                # whose values are most often inherited was the one nobody
                # could trace.
                for nested_key in value:
                    sources[f"{key}.{nested_key}"] = scope
            sources[key] = scope

    return merged, sources


async def resolve_policy_layers(
    db: AsyncSession,
    project_id: uuid.UUID | str | None,
    *,
    hardcoded: Optional[dict] = None,
) -> list[tuple[str, dict[str, Any]]]:
    """The documents in force, broadest first.

    Returns every layer rather than the winner, so the caller can merge and
    report provenance. A resolver that returned only the narrowest match is
    exactly what made the current behaviour lossy.
    """
    layers: list[tuple[str, dict[str, Any]]] = []
    if hardcoded:
        layers.append(("hardcoded", hardcoded))

    system = await _active_policy(db, None)
    if system is not None and system.rules:
        layers.append(("system", system.rules))

    if project_id:
        project = await _active_policy(db, project_id)
        if project is not None and project.rules:
            layers.append(("project", project.rules))

    return layers


async def _active_policy(
    db: AsyncSession, project_id: uuid.UUID | str | None
) -> Optional[ReleaseGatePolicy]:
    """The active policy for one scope, deterministically.

    ``is_active`` has no database-level single-active guarantee — the only
    unique constraint is ``(project_id, version)`` and "one active per scope" is
    enforced in application code, so a concurrent publish can leave two active
    rows. Ordering by version descending makes resolution deterministic anyway,
    and matches what the history endpoints show, instead of returning an
    arbitrary row from an unordered LIMIT 1.
    """
    stmt = select(ReleaseGatePolicy).where(ReleaseGatePolicy.is_active.is_(True))
    if project_id is None:
        # IS NULL, not `== None`: SQL equality against NULL is never true, so
        # the plain comparison silently returns no system default and every
        # project falls through to hardcoded.
        stmt = stmt.where(ReleaseGatePolicy.project_id.is_(None))
    else:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        stmt = stmt.where(ReleaseGatePolicy.project_id == pid)
    stmt = stmt.order_by(ReleaseGatePolicy.version.desc()).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def resolve_effective_document(
    db: AsyncSession,
    project_id: uuid.UUID | str | None,
    *,
    hardcoded: Optional[dict] = None,
) -> dict[str, Any]:
    """The merged policy plus the provenance of every key.

    The shape a gate decision snapshots: `document` is what was applied,
    `sources` says where each part came from, and `layers` names the scopes
    consulted. Recording all three is what lets somebody months later answer
    "which threshold applied, and who set it" without re-deriving anything.
    """
    layers = await resolve_policy_layers(db, project_id, hardcoded=hardcoded)
    document, sources = merge_documents(layers)
    return {
        "document": document,
        "sources": sources,
        "layers": [scope for scope, _ in layers],
        # The narrowest scope that contributed anything. Kept for callers that
        # only want the old one-word answer — but it is a summary of the merge,
        # not a substitute for it.
        "effective_level": layers[-1][0] if layers else "hardcoded",
    }
