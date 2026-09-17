"""Storage and wire contracts for versioned workflow definitions.

E3.1 owns persistence and structural parsing. E3.2 delegates semantic
validation to ``agents.workflow_compiler`` before evaluation or publication.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import WorkflowDefinition


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowStepV1(_Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    agent_id: str = Field(min_length=1, max_length=80)
    config_ref: Optional[str] = Field(default=None, max_length=80)
    tools: list[str] = Field(default_factory=list, max_length=64)
    reviews: list[str] = Field(default_factory=list, max_length=64)
    model: Optional[dict[str, Any]] = None


class WorkflowEdgeV1(_Strict):
    source: str | list[str] = Field(alias="from")
    to: str = Field(min_length=1, max_length=80)
    when: Optional[dict[str, Any]] = None
    join: Optional[Literal["all", "any"]] = None


class WorkflowLoopV1(_Strict):
    source: str = Field(alias="from", min_length=1, max_length=80)
    to: str = Field(min_length=1, max_length=80)
    when: dict[str, Any]
    max_iterations: int = Field(ge=1, le=100)


class WorkflowRetryPolicyV1(_Strict):
    max_attempts: int = Field(default=5, ge=1, le=10)
    base_seconds: int = Field(default=30, ge=0, le=3600)
    cap_seconds: int = Field(default=600, ge=1, le=86400)

    @model_validator(mode="after")
    def cap_not_below_base(self) -> "WorkflowRetryPolicyV1":
        if self.cap_seconds < self.base_seconds:
            raise ValueError("cap_seconds must be greater than or equal to base_seconds")
        return self


class WorkflowBodyV1(_Strict):
    workflow_id: str = Field(
        min_length=3,
        max_length=80,
        pattern=r"^(?:wf\.[a-z0-9_.-]+|offline|deep|live)$",
    )
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=4000)
    base: Literal["offline", "deep", "live"]
    steps: list[WorkflowStepV1] = Field(min_length=1, max_length=100)
    edges: list[WorkflowEdgeV1] = Field(default_factory=list, max_length=300)
    loops: list[WorkflowLoopV1] = Field(default_factory=list, max_length=50)
    retry_policy: WorkflowRetryPolicyV1 = Field(default_factory=WorkflowRetryPolicyV1)
    review_policy: Literal["human_required", "human_required_plus_auto_reviewer"] = "human_required"
    deadline_seconds: int = Field(default=1500, ge=1, le=86400)

    @field_validator("steps")
    @classmethod
    def unique_step_ids(cls, steps: list[WorkflowStepV1]) -> list[WorkflowStepV1]:
        ids = [step.id for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return steps


class WorkflowForkV1(_Strict):
    workflow_id: str = Field(min_length=3, max_length=80, pattern=r"^wf\.[a-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=4000)


class WorkflowEvaluateV1(_Strict):
    version: Optional[int] = Field(default=None, ge=1)
    sample_limit: int = Field(default=20, ge=20, le=100)


class WorkflowPublishV1(_Strict):
    version: int = Field(ge=1)
    definition_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    accept_regression: bool = False
    reason: Optional[str] = Field(default=None, max_length=2000)
    eval_manifest_checksum: Optional[str] = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def regression_reason_required(self) -> "WorkflowPublishV1":
        if self.accept_regression and not (self.reason or "").strip():
            raise ValueError("accept_regression requires a non-empty reason")
        if self.accept_regression and self.eval_manifest_checksum is None:
            raise ValueError("accept_regression requires eval_manifest_checksum")
        return self


BUILTIN_STAGE_NAMES: dict[str, tuple[str, ...]] = {
    "offline": ("ingestion", "anomaly_detection", "root_cause_analysis", "summary", "triage"),
    "live": ("ingestion", "summary"),
    "deep": (
        "ingestion", "anomaly_detection", "failure_clustering",
        "cluster_investigation_dispatch", "root_cause_analysis",
        "cluster_investigation_join", "summary", "triage",
        "contract_validation", "log_intelligence", "regression_watchman",
        "change_ownership", "defect_commander", "gap_detection",
        "report_refinement", "flaky_sentinel", "test_health", "release_risk",
        "decision_report", "decision_report_critic",
    ),
}
BUILTIN_WORKFLOW_IDS = frozenset(BUILTIN_STAGE_NAMES)


class WorkflowNotFound(LookupError):
    pass


class WorkflowConflict(ValueError):
    pass


class WorkflowNotPublished(ValueError):
    pass


def is_builtin(workflow_id: str) -> bool:
    return workflow_id in BUILTIN_WORKFLOW_IDS


def definition_checksum(definition: dict[str, Any]) -> str:
    """Return the stable digest clients use for compare-and-publish."""
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _step(stage: str) -> dict[str, Any]:
    return {"id": stage, "agent_id": f"agent.{stage}.v1", "tools": [], "reviews": []}


def _builtin_edges(name: str) -> list[dict[str, Any]]:
    failed: dict[str, Any] = {"field": "failed_test_ids", "op": "count_gt", "value": 0}
    triageable: dict[str, Any] = {
        "field": "triageable_analysis_count", "op": "gt", "value": 0,
    }
    otherwise: dict[str, Any] = {"op": "else"}
    if name == "live":
        return [
            {"from": "ingestion", "to": "summary"},
            {"from": "summary", "to": "__end__"},
        ]
    shared: list[dict[str, Any]] = [
        {"from": "ingestion", "to": "root_cause_analysis"},
        {"from": "ingestion", "to": "anomaly_detection", "when": failed},
        {"from": "ingestion", "to": "root_cause_analysis", "when": otherwise},
        {"from": "anomaly_detection", "to": "summary"},
        {"from": "root_cause_analysis", "to": "summary"},
        {"from": "summary", "to": "triage", "when": triageable},
    ]
    if name == "offline":
        return [
            *shared,
            {"from": "summary", "to": "__end__", "when": otherwise},
            {"from": "triage", "to": "__end__"},
        ]
    specialists = [
        "contract_validation",
        "log_intelligence",
        "regression_watchman",
        "change_ownership",
        "defect_commander",
    ]
    specialist_edges: list[dict[str, Any]] = [
        edge
        for stage in specialists
        for edge in (
            {"from": "cluster_investigation_join", "to": stage},
            {"from": stage, "to": "gap_detection"},
        )
    ]
    return [
        *shared,
        {"from": "summary", "to": "failure_clustering", "when": otherwise},
        {"from": "triage", "to": "failure_clustering"},
        {"from": "failure_clustering", "to": "cluster_investigation_dispatch"},
        {"from": "cluster_investigation_dispatch", "to": "cluster_investigation_join"},
        *specialist_edges,
        {"from": "gap_detection", "to": "report_refinement"},
        {"from": "report_refinement", "to": "flaky_sentinel"},
        {"from": "flaky_sentinel", "to": "test_health"},
        {"from": "test_health", "to": "release_risk"},
        {"from": "release_risk", "to": "decision_report"},
        {"from": "decision_report", "to": "decision_report_critic"},
        {"from": "decision_report_critic", "to": "__end__"},
    ]


def _builtin_definition(name: str) -> dict[str, Any]:
    stages = BUILTIN_STAGE_NAMES[name]
    return {
        "workflow_id": name,
        "version": 1,
        "project_id": None,
        "base": name,
        "steps": [_step(stage) for stage in stages],
        "edges": _builtin_edges(name),
        "loops": [],
        "retry_policy": {"max_attempts": 5, "base_seconds": 30, "cap_seconds": 600},
        "review_policy": "human_required",
        "deadline_seconds": 1500,
    }


def builtin(workflow_id: str) -> dict[str, Any]:
    if not is_builtin(workflow_id):
        raise WorkflowNotFound(workflow_id)
    definition = _builtin_definition(workflow_id)
    return {
        "id": workflow_id,
        "workflow_id": workflow_id,
        "version": 1,
        "project_id": None,
        "name": f"{workflow_id.title()} workflow",
        "description": "Built-in TestLookup workflow template.",
        "base": workflow_id,
        "definition": definition,
        "definition_sha256": definition_checksum(definition),
        "status": "published",
        "published_at": None,
        "eval_verdict": None,
        "eval_coverage": None,
        "eval_gate_run_id": None,
        "evaluated_at": None,
        "eval_regression_accepted": False,
        "eval_regression_reason": None,
        "eval_regression_accepted_at": None,
        "read_only": True,
        "built_in": True,
    }


def serialize(row: WorkflowDefinition) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "workflow_id": row.workflow_id,
        "version": row.version,
        "project_id": str(row.project_id),
        "name": row.name,
        "description": row.description,
        "base": row.base,
        "definition": row.definition,
        "definition_sha256": definition_checksum(row.definition),
        "status": row.status,
        "published_at": row.published_at,
        "eval_verdict": row.eval_verdict,
        "eval_coverage": row.eval_coverage,
        "eval_gate_run_id": str(row.eval_gate_run_id) if row.eval_gate_run_id else None,
        "evaluated_at": row.evaluated_at,
        "eval_regression_accepted": row.eval_regression_accepted,
        "eval_regression_reason": row.eval_regression_reason,
        "eval_regression_accepted_at": row.eval_regression_accepted_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "read_only": row.status == "published",
        "built_in": False,
    }


async def list_definitions(db: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(WorkflowDefinition)
        .where(WorkflowDefinition.project_id == project_id)
        .order_by(WorkflowDefinition.workflow_id, WorkflowDefinition.version.desc())
    )).scalars().all()
    return [builtin(name) for name in sorted(BUILTIN_WORKFLOW_IDS)] + [serialize(row) for row in rows]


async def get_definition(
    db: AsyncSession,
    project_id: uuid.UUID,
    workflow_id: str,
    version: Optional[int] = None,
) -> WorkflowDefinition | dict[str, Any]:
    if is_builtin(workflow_id):
        if version not in (None, 1):
            raise WorkflowNotFound(workflow_id)
        return builtin(workflow_id)
    query = select(WorkflowDefinition).where(
        WorkflowDefinition.project_id == project_id,
        WorkflowDefinition.workflow_id == workflow_id,
    )
    query = query.where(WorkflowDefinition.version == version) if version else query.order_by(WorkflowDefinition.version.desc()).limit(1)
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise WorkflowNotFound(workflow_id)
    return row


async def get_published_definition(
    db: AsyncSession,
    project_id: uuid.UUID,
    workflow_id: str,
    version: Optional[int] = None,
) -> WorkflowDefinition | dict[str, Any]:
    """Resolve the immutable workflow version that a run may execute.

    Drafts are editable and therefore can never be execution authority.  When
    the caller omits a version, select the newest *published* version instead
    of ``get_definition``'s newest version (which may be a later draft).
    """
    if is_builtin(workflow_id):
        return await get_definition(db, project_id, workflow_id, version)
    query = select(WorkflowDefinition).where(
        WorkflowDefinition.project_id == project_id,
        WorkflowDefinition.workflow_id == workflow_id,
        WorkflowDefinition.status == "published",
    )
    query = (
        query.where(WorkflowDefinition.version == version)
        if version is not None
        else query.order_by(WorkflowDefinition.version.desc()).limit(1)
    )
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        exists = (await db.execute(
            select(WorkflowDefinition.id).where(
                WorkflowDefinition.project_id == project_id,
                WorkflowDefinition.workflow_id == workflow_id,
            ).limit(1)
        )).scalar_one_or_none()
        if exists is not None:
            raise WorkflowNotPublished(workflow_id)
        raise WorkflowNotFound(workflow_id)
    return row


async def _lock_version(db: AsyncSession, project_id: uuid.UUID, workflow_id: str) -> None:
    key = f"workflow-definition:{project_id}:{workflow_id}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


async def lock_definition(
    db: AsyncSession, project_id: uuid.UUID, workflow_id: str
) -> None:
    """Serialize every mutation of one project's version chain.

    Evaluation, publication, deletion, and forking a mutable source must use
    the same transaction-scoped lock as create/update.  Otherwise a request can
    validate or evaluate one draft while a concurrent request commits different
    bytes into that version, then publish evidence for the stale document.
    """
    if not is_builtin(workflow_id):
        await _lock_version(db, project_id, workflow_id)


def _definition(body: WorkflowBodyV1, project_id: uuid.UUID, version: int) -> dict[str, Any]:
    data = body.model_dump(mode="json", by_alias=True, exclude={"name", "description"})
    data.update({"project_id": str(project_id), "version": version})
    return data


def _clear_evaluation(row: WorkflowDefinition) -> None:
    """A definition edit invalidates evidence keyed to its prior checksum."""
    row.eval_verdict = None
    row.eval_coverage = None
    row.eval_gate_run_id = None
    row.evaluated_at = None
    row.eval_regression_accepted = False
    row.eval_regression_reason = None
    row.eval_regression_accepted_by = None
    row.eval_regression_accepted_at = None


async def create_definition(
    db: AsyncSession, project_id: uuid.UUID, body: WorkflowBodyV1, *, actor_id: Optional[uuid.UUID]
) -> WorkflowDefinition:
    if is_builtin(body.workflow_id):
        raise WorkflowConflict("built-in workflow ids are reserved")
    await _lock_version(db, project_id, body.workflow_id)
    exists = (await db.execute(select(WorkflowDefinition.id).where(
        WorkflowDefinition.project_id == project_id,
        WorkflowDefinition.workflow_id == body.workflow_id,
    ).limit(1))).scalar_one_or_none()
    if exists is not None:
        raise WorkflowConflict("workflow_id already exists; update its latest version")
    row = WorkflowDefinition(
        project_id=project_id, workflow_id=body.workflow_id, version=1,
        name=body.name, description=body.description, base=body.base,
        definition=_definition(body, project_id, 1), status="draft",
        created_by=actor_id, updated_by=actor_id,
    )
    db.add(row)
    await db.flush()
    return row


async def update_definition(
    db: AsyncSession, project_id: uuid.UUID, workflow_id: str,
    body: WorkflowBodyV1, *, actor_id: Optional[uuid.UUID]
) -> tuple[WorkflowDefinition, bool]:
    if is_builtin(workflow_id):
        raise WorkflowConflict("built-in workflows are read-only")
    if body.workflow_id != workflow_id:
        raise WorkflowConflict("body workflow_id must match the path")
    await _lock_version(db, project_id, workflow_id)
    current = await get_definition(db, project_id, workflow_id)
    assert isinstance(current, WorkflowDefinition)
    if current.status == "published":
        version = current.version + 1
        row = WorkflowDefinition(
            project_id=project_id, workflow_id=workflow_id, version=version,
            name=body.name, description=body.description, base=body.base,
            definition=_definition(body, project_id, version), status="draft",
            created_by=actor_id, updated_by=actor_id,
        )
        db.add(row)
        await db.flush()
        return row, True
    current.name = body.name
    current.description = body.description
    current.base = body.base
    current.definition = _definition(body, project_id, current.version)
    current.updated_by = actor_id
    _clear_evaluation(current)
    await db.flush()
    return current, False


async def delete_definition(db: AsyncSession, row: WorkflowDefinition) -> None:
    if row.status == "published":
        raise WorkflowConflict("published workflow versions are immutable")
    await db.delete(row)
    await db.flush()


async def publish_definition(db: AsyncSession, row: WorkflowDefinition) -> WorkflowDefinition:
    if row.status == "published":
        return row
    row.status = "published"
    row.published_at = datetime.now(timezone.utc)
    await db.flush()
    return row


async def fork_definition(
    db: AsyncSession, project_id: uuid.UUID, source: WorkflowDefinition | dict[str, Any],
    body: WorkflowForkV1, *, actor_id: Optional[uuid.UUID]
) -> WorkflowDefinition:
    source_data = source if isinstance(source, dict) else serialize(source)
    definition = dict(source_data["definition"])
    definition.update({"workflow_id": body.workflow_id, "version": 1, "project_id": str(project_id)})
    create = WorkflowBodyV1.model_validate({
        **{key: value for key, value in definition.items() if key not in {"version", "project_id"}},
        "name": body.name,
        "description": body.description,
    })
    return await create_definition(db, project_id, create, actor_id=actor_id)


def body_from_item(item: WorkflowDefinition | dict[str, Any]) -> WorkflowBodyV1:
    payload = item if isinstance(item, dict) else serialize(item)
    definition = payload["definition"]
    # Re-parse stored JSON so corruption is reported rather than compiled.
    return WorkflowBodyV1.model_validate({
        **{key: value for key, value in definition.items() if key not in {"version", "project_id"}},
        "name": payload["name"],
        "description": payload["description"],
    })


def validation_result(
    item: WorkflowDefinition | dict[str, Any],
    *,
    agent_configs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = item if isinstance(item, dict) else serialize(item)
    body = body_from_item(item)
    from app.agents.workflow_compiler import validate_workflow  # noqa: PLC0415

    result = validate_workflow(body, agent_configs=agent_configs)
    return {
        "valid": result.valid,
        "workflow_id": body.workflow_id,
        "version": payload["version"],
        "errors": list(result.errors),
        "validation_scope": "semantic",
        "compiler_validation": "passed" if result.valid else "failed",
    }


__all__ = [
    "BUILTIN_WORKFLOW_IDS", "WorkflowBodyV1", "WorkflowConflict", "WorkflowNotPublished",
    "WorkflowEvaluateV1", "WorkflowForkV1", "WorkflowNotFound", "WorkflowPublishV1",
    "body_from_item", "create_definition", "definition_checksum",
    "delete_definition", "fork_definition", "get_definition", "get_published_definition", "is_builtin",
    "lock_definition",
    "list_definitions", "publish_definition", "serialize", "update_definition",
    "validation_result",
]
