"""A project's default workflow per base (W14 AGT-08).

``project_workflow_defaults`` holds at most one row per ``(project_id, base)``:
the published fork a trigger that names no workflow runs instead of the
built-in ``base``. Four functions:

* :func:`resolve_run_workflow` -- what a run started NOW executes; called by
  ``agents/workflow._create_pipeline_run`` (on its own session) before it
  resolves the definition. It NEVER raises for a bad default: a default that no
  longer resolves (gone, rebased, corrupt) answers the built-in with source
  ``default_unavailable`` and the sentence the pipeline records.
* :func:`read_defaults` -- both bases as ``WorkflowDefaultRead`` bodies.
* :func:`set_default` / :func:`clear_default` -- the PUT / DELETE routes' work:
  validated, staged and flushed; the ROUTER commits (services flush, routers
  commit: no ``commit`` / ``rollback`` here).

A default is resolved ONLY inside its own project
(``workflow_definition_service.get_published_definition(db, project_id, ...)``),
so another project's workflow id is "not found" here. Nothing logs.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_ops_vocab import (
    DEFAULT_BASE_INVALID,
    DEFAULT_BASE_MISMATCH,
    DEFAULT_FALLBACK_SUFFIX,
    DEFAULT_NOT_PUBLISHED,
    DEFAULT_UNAVAILABLE_BASE,
    DEFAULT_UNAVAILABLE_GONE,
    DEFAULT_UNAVAILABLE_INVALID,
    WORKFLOW_DEFAULT_BASES,
)
from app.models.postgres import ProjectWorkflowDefault, User, WorkflowDefinition
from app.models.schemas import WorkflowDefaultUpdate
from app.services import agent_config_service as config_svc
from app.services import workflow_definition_service as definition_svc

SEMANTIC_INVALID = "Workflow definition failed semantic validation"   # routers/workflows.py's text, verbatim
DEFINITION_NOT_FOUND = "Workflow definition not found"                 # routers/workflows.py's text, verbatim


@dataclass(frozen=True, slots=True)
class DefaultResolution:
    workflow_id: str                 # what _create_pipeline_run passes to get_published_definition
    workflow_version: int | None
    source: str                      # "project_default" | "builtin" | "default_unavailable"
    reason: str | None               # the DEFAULT_UNAVAILABLE_* sentence + " The built-in {base} workflow ran instead."


class DefaultRejected(Exception):
    """A PUT / DELETE the routes turn into ``HTTPException(status_code, detail)``."""

    def __init__(self, status_code: int, detail: str | dict[str, Any]) -> None:
        super().__init__(detail if isinstance(detail, str) else detail.get("message", ""))
        self.status_code = status_code
        self.detail = detail


def default_ref(workflow_id: str, workflow_version: Optional[int]) -> str:
    """``wf.x@3`` for a pinned default, ``wf.x`` for "the latest published"."""
    return f"{workflow_id}@{workflow_version}" if workflow_version is not None else workflow_id


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "the stored definition is not valid"
    first = errors[0]
    where = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
    message = str(first.get("msg", "invalid"))
    return f"{where}: {message}" if where else message


def _unavailable_reason(
    row: ProjectWorkflowDefault, definition: Optional[WorkflowDefinition | dict[str, Any]], base: str,
) -> Optional[str]:
    """The DEFAULT_UNAVAILABLE_* sentence for a row's resolved definition; None when it is usable."""
    ref = default_ref(row.workflow_id, row.workflow_version)
    if definition is None:
        return DEFAULT_UNAVAILABLE_GONE.format(ref=ref)
    actual = definition["base"] if isinstance(definition, dict) else definition.base
    if actual != base:
        return DEFAULT_UNAVAILABLE_BASE.format(ref=ref, base=base)
    try:
        definition_svc.body_from_item(definition)
    except ValidationError as exc:
        return DEFAULT_UNAVAILABLE_INVALID.format(ref=ref, first_error=_first_error(exc))
    except (KeyError, TypeError, ValueError) as exc:   # a stored document missing its keys
        return DEFAULT_UNAVAILABLE_INVALID.format(ref=ref, first_error=type(exc).__name__)
    return None


async def _load_row(db: AsyncSession, project_id: uuid.UUID, base: str) -> Optional[ProjectWorkflowDefault]:
    result = await db.execute(
        select(ProjectWorkflowDefault).where(
            ProjectWorkflowDefault.project_id == project_id,
            ProjectWorkflowDefault.base == base,
        )
    )
    return result.scalar_one_or_none()


async def resolve_run_workflow(db: AsyncSession, project_id: uuid.UUID, base: str) -> DefaultResolution:
    """The workflow a run of ``base`` that names none executes in ``project_id``.

    No row -> the built-in (``builtin``); a row whose published definition is
    built on ``base`` and parses -> that fork at its resolved version
    (``project_default``); anything else -> the built-in with
    ``default_unavailable`` and the sentence. Never raises for a bad default.
    """
    builtin = DefaultResolution(workflow_id=base, workflow_version=None, source="builtin", reason=None)
    if base not in WORKFLOW_DEFAULT_BASES:
        return builtin
    row = await _load_row(db, project_id, base)
    if row is None:
        return builtin
    definition: Optional[WorkflowDefinition | dict[str, Any]]
    try:
        definition = await definition_svc.get_published_definition(
            db, project_id, row.workflow_id, row.workflow_version,
        )
    except (definition_svc.WorkflowNotFound, definition_svc.WorkflowNotPublished):
        definition = None
    reason = _unavailable_reason(row, definition, base)
    if reason is not None or definition is None:
        return DefaultResolution(
            workflow_id=base,
            workflow_version=None,
            source="default_unavailable",
            reason=(reason or "") + DEFAULT_FALLBACK_SUFFIX.format(base=base),
        )
    version = definition["version"] if isinstance(definition, dict) else definition.version
    return DefaultResolution(
        workflow_id=row.workflow_id, workflow_version=int(version), source="project_default", reason=None,
    )


# ── read ─────────────────────────────────────────────────────────────────────


def _effective_tier(agent_id: str, config: dict[str, Any]) -> str:
    """The project config's tier for one agent; ``auto`` read as the stage's default tier."""
    from app.services.agent_capability_registry import DEFAULT_TIERS  # noqa: PLC0415 (read only)

    model = config.get("model") if isinstance(config, dict) else None
    tier = model.get("tier") if isinstance(model, dict) else None
    if tier in (None, "auto"):
        stage = config_svc.configurable_capabilities().get(agent_id)
        return DEFAULT_TIERS.get(stage, "deterministic") if stage else "deterministic"
    return str(tier)


def model_stages(
    definition: WorkflowDefinition | dict[str, Any], config_rows: dict[str, Any],
) -> list[str]:
    """The definition's step ids whose project-config tier is not ``deterministic`` (the cost warning).

    A step whose project config is disabled is planned off at run start and calls no model, so it is not
    listed (``defect_commander`` starts disabled: mutating capabilities default to shadow + off).
    """
    try:
        body = definition_svc.body_from_item(definition)
    except (ValidationError, KeyError, TypeError, ValueError):
        return []
    configurable = set(config_svc.configurable_agents())
    stages: list[str] = []
    for step in body.steps:
        if step.agent_id not in configurable:
            continue
        config = config_svc.serialize(step.agent_id, config_rows.get(step.agent_id))["config"]
        if config.get("enabled") is False:
            continue
        if _effective_tier(step.agent_id, config) != "deterministic":
            stages.append(step.id)
    return stages


def _pick_definition(
    row: ProjectWorkflowDefault, published: list[WorkflowDefinition],
) -> Optional[WorkflowDefinition]:
    """The row's pinned version, else its newest published version, from one project's published rows."""
    candidates = [d for d in published if d.workflow_id == row.workflow_id]
    if row.workflow_version is not None:
        return next((d for d in candidates if d.version == row.workflow_version), None)
    return max(candidates, key=lambda d: d.version, default=None)


def _person(user: Optional[Any]) -> Optional[str]:
    if user is None:
        return None
    return (getattr(user, "full_name", None) or "").strip() or getattr(user, "username", None)


async def read_defaults(db: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    """``WorkflowDefaultRead`` bodies for both bases, in ``WORKFLOW_DEFAULT_BASES`` order.

    At most four statements: the rows, their published definitions (skipped
    with no row), the project's agent config rows (for ``model_stages``) and
    the updaters' names (skipped when nobody is named).
    """
    rows = {
        row.base: row
        for row in (await db.execute(
            select(ProjectWorkflowDefault).where(ProjectWorkflowDefault.project_id == project_id)
        )).scalars().all()
    }
    published: list[WorkflowDefinition] = []
    if rows:
        published = list((await db.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.project_id == project_id,
                WorkflowDefinition.workflow_id.in_(sorted({row.workflow_id for row in rows.values()})),
                WorkflowDefinition.status == "published",
            )
        )).scalars().all())
    config_rows = await config_svc.list_config_rows(db, project_id)
    updater_ids = sorted({row.updated_by for row in rows.values() if row.updated_by is not None}, key=str)
    people: dict[uuid.UUID, Any] = {}
    if updater_ids:
        people = {
            user.id: user
            for user in (await db.execute(select(User).where(User.id.in_(updater_ids)))).scalars().all()
        }
    out: list[dict[str, Any]] = []
    for base in WORKFLOW_DEFAULT_BASES:
        row = rows.get(base)
        if row is None:
            builtin = definition_svc.builtin(base)
            out.append({
                "base": base, "source": "builtin", "workflow_id": base, "workflow_version": None,
                "resolved_ref": f"{base}@{builtin['version']}", "resolved_name": builtin["name"],
                "state": "ok", "reason": None, "model_stages": model_stages(builtin, config_rows),
                "updated_by": None, "updated_at": None,
            })
            continue
        definition = _pick_definition(row, published)
        reason = _unavailable_reason(row, definition, base)
        usable = definition if reason is None else None
        out.append({
            "base": base, "source": "project_default", "workflow_id": row.workflow_id,
            "workflow_version": row.workflow_version,
            "resolved_ref": f"{row.workflow_id}@{usable.version}" if usable is not None else None,
            "resolved_name": usable.name if usable is not None else None,
            "state": "ok" if usable is not None else "unavailable",
            "reason": reason,
            "model_stages": model_stages(usable, config_rows) if usable is not None else [],
            "updated_by": _person(people.get(row.updated_by)) if row.updated_by else None,
            "updated_at": row.updated_at,
        })
    return out


# ── write (staged + flushed; the router commits) ─────────────────────────────


def _require_base(base: str) -> None:
    if base not in WORKFLOW_DEFAULT_BASES:
        raise DefaultRejected(422, DEFAULT_BASE_INVALID)


async def _semantic_errors(
    db: AsyncSession, project_id: uuid.UUID, definition: WorkflowDefinition,
) -> list[str]:
    """The publish route's own semantic check (``routers/workflows._semantic_result``) with the project's configs."""
    try:
        body = definition_svc.body_from_item(definition)
    except ValidationError as exc:
        return [_first_error(exc)]
    rows = await config_svc.list_config_rows(db, project_id)
    configs = {
        step.agent_id: config_svc.serialize(step.agent_id, rows.get(step.agent_id))["config"]
        for step in body.steps
        if step.agent_id in config_svc.configurable_agents()
    }
    result = definition_svc.validation_result(definition, agent_configs=configs)
    return [] if result["valid"] else list(result["errors"])


async def set_default(
    db: AsyncSession,
    project_id: uuid.UUID,
    base: str,
    body: WorkflowDefaultUpdate,
    actor: Optional[Any],
) -> tuple[ProjectWorkflowDefault, dict[str, Any]]:
    """Make ``body``'s published fork the project's ``base`` default (one row per base, upserted).

    Refusals (``DefaultRejected``): a bad base (422); a workflow this project
    does not have (404, also for another project's id); a workflow with no
    published version / a draft pin (422); a fork built on another base (422);
    a definition the publish route's semantic check refuses (422 with
    ``errors``). Returns the staged row and the serialized definition, plus
    ``previous_default`` (the replaced ref, or ``builtin``) for the ledger row.
    """
    _require_base(base)
    try:
        definition = await definition_svc.get_published_definition(
            db, project_id, body.workflow_id, body.workflow_version,
        )
    except definition_svc.WorkflowNotFound:
        raise DefaultRejected(404, DEFINITION_NOT_FOUND) from None
    except definition_svc.WorkflowNotPublished:
        raise DefaultRejected(422, DEFAULT_NOT_PUBLISHED.format(workflow_id=body.workflow_id)) from None
    if not isinstance(definition, WorkflowDefinition):   # a built-in: the pattern refuses it before here
        raise DefaultRejected(404, DEFINITION_NOT_FOUND)
    if definition.base != base:
        raise DefaultRejected(
            422, DEFAULT_BASE_MISMATCH.format(workflow_id=body.workflow_id, actual=definition.base),
        )
    errors = await _semantic_errors(db, project_id, definition)
    if errors:
        raise DefaultRejected(422, {"message": SEMANTIC_INVALID, "errors": errors})
    previous = await _load_row(db, project_id, base)
    previous_ref = default_ref(previous.workflow_id, previous.workflow_version) if previous else "builtin"
    actor_id = getattr(actor, "id", None)
    insert_statement = pg_insert(ProjectWorkflowDefault).values(
        project_id=project_id,
        base=base,
        workflow_id=body.workflow_id,
        workflow_version=body.workflow_version,
        updated_by=actor_id,
    )
    upsert = insert_statement.on_conflict_do_update(
        constraint="pk_project_workflow_defaults",
        set_={
            "workflow_id": insert_statement.excluded.workflow_id,
            "workflow_version": insert_statement.excluded.workflow_version,
            "updated_by": insert_statement.excluded.updated_by,
            "updated_at": text("now()"),
        },
    ).returning(ProjectWorkflowDefault)
    result = await db.execute(upsert, execution_options={"populate_existing": True})
    row: ProjectWorkflowDefault = result.scalar_one()
    await db.flush()
    return row, {**definition_svc.serialize(definition), "previous_default": previous_ref}


async def clear_default(
    db: AsyncSession, project_id: uuid.UUID, base: str,
) -> Optional[ProjectWorkflowDefault]:
    """Delete the project's ``base`` default (staged + flushed); None when there was none."""
    _require_base(base)
    row = await _load_row(db, project_id, base)
    if row is None:
        return None
    await db.delete(row)
    await db.flush()
    return row


__all__ = [
    "DefaultRejected", "DefaultResolution", "clear_default", "default_ref", "model_stages",
    "read_defaults", "resolve_run_workflow", "set_default",
]
