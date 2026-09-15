"""Agent catalog: the capability registry as API clients see it (architecture E1.1, section 3).

Clients discover agents here instead of hard-coding stage names. The catalog is
a projection of ``agent_capability_registry``, so adding a capability publishes
it.

Each capability also gets a generated input wrapper, ``<StageName>InvokeInput``::

    agent_id: Literal["agent.summary.v1"]
    payload:  <input model> | SubjectRef

The registry's input contracts are shared by several capabilities, so one union
discriminated on a field inside them would not be injective. One wrapper per
capability, chosen by the path's ``agent_id``, avoids that (section 3.3).

Every registry input name resolves to a concrete, closed Pydantic model. The
wrapper still accepts ``SubjectRef`` so the invoke route can preserve its
stored-subject authorization boundary.
"""
from __future__ import annotations

import importlib
import re
import uuid
from functools import lru_cache
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.models.agentic_runtime import CapabilitySpecV1
from app.services.agent_capability_registry import (
    CAPABILITY_REGISTRY,
    DEFAULT_TIERS,
    ESCALATION_TRIGGERS,
    is_report_producing,
    is_sync_eligible,
)

#: Modules whose Pydantic models back registry schema names. The
#: ``agents.catalog-schema-complete`` guard reads this tuple, so a model defined
#: anywhere else does not count as resolved.
CATALOG_SCHEMA_MODULES = (
    "app.models.agent_contracts",
    "app.models.agent_input_contracts",
    "app.models.agentic_runtime",
    "app.models.evidence_contracts",
    "app.services.decision_report_service",
)

#: ``agent.<stage>.v<version>`` -- validated for a clear error; route ORDER, not
#: this pattern, is what keeps ``/agents/{agent_id}`` off the literal routes.
AGENT_ID_PATTERN = re.compile(r"^agent\.[a-z_]+\.v\d+$")
_VERSION = re.compile(r"\.v(\d+)$")


class SubjectRef(BaseModel):
    """Invoke an agent on a stored subject; the server assembles the input from it."""

    model_config = ConfigDict(extra="forbid")

    test_run_id: uuid.UUID


class AgentCatalogEntry(BaseModel):
    agent_id: str
    version: int
    stage_name: str
    permission: str
    execution: str
    default_tier: Literal["deterministic", "slm", "llm"]
    escalation: list[
        Literal[
            "validation_failure",
            "low_confidence",
            "not_enough_evidence",
            "contradictions",
            "multi_artifact_evidence",
        ]
    ]
    sync_eligible: bool = Field(
        description="May be invoked synchronously: deterministic and expected to finish in 5 s or less.",
    )
    produces_report: bool = Field(
        description="Its output is a report contract, so a run of it needs human review (E8).",
    )
    input_schema: str
    output_schema: str
    input_schema_resolved: bool = Field(
        description="False when the input schema is a label with no model yet; the wrapper then accepts only a SubjectRef.",
    )
    output_schema_resolved: bool
    dependencies: list[str]
    required_evidence: list[str]
    expected_latency_ms: int
    expected_cost_usd: float
    timeout_seconds: int
    max_retries: int
    fallback: str
    concurrency_class: str


class AgentCatalogDetail(AgentCatalogEntry):
    input_wrapper: str
    input_json_schema: dict[str, Any]
    output_json_schema: Optional[dict[str, Any]] = None


def _model_name(schema: str) -> tuple[str, bool]:
    """``("AgentFindingV1", True)`` for ``"AgentFindingV1[]"``."""
    return (schema[:-2], True) if schema.endswith("[]") else (schema, False)


@lru_cache(maxsize=None)
def resolve_schema_model(schema: str) -> Optional[type[BaseModel]]:
    """The Pydantic model behind a registry schema name, or None for a bare label."""
    name, _ = _model_name(schema)
    for module_name in CATALOG_SCHEMA_MODULES:
        candidate = getattr(importlib.import_module(module_name), name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _spec(agent_id: str) -> Optional[CapabilitySpecV1]:
    return next((s for s in CAPABILITY_REGISTRY.values() if s.capability_id == agent_id), None)


def capability_for(agent_id: str) -> Optional[CapabilitySpecV1]:
    """The registry spec behind a catalog agent id, or None when unknown."""
    return _spec(agent_id)


def _wrapper_name(stage_name: str) -> str:
    return "".join(part.capitalize() for part in stage_name.split("_")) + "InvokeInput"


@lru_cache(maxsize=None)
def input_wrapper(agent_id: str) -> type[BaseModel]:
    """The generated ``<StageName>InvokeInput`` model for one capability."""
    spec = _spec(agent_id)
    if spec is None:
        raise KeyError(agent_id)
    model = resolve_schema_model(spec.input_schema)
    _, is_list = _model_name(spec.input_schema)
    payload_type: Any = SubjectRef
    if model is not None:
        payload_type = Union[list[model], SubjectRef] if is_list else Union[model, SubjectRef]  # type: ignore[valid-type]
    return create_model(
        _wrapper_name(spec.stage_name),
        __config__=ConfigDict(extra="forbid"),
        agent_id=(Literal[agent_id], ...),
        payload=(payload_type, ...),
    )


def _output_json_schema(schema: str) -> Optional[dict[str, Any]]:
    model = resolve_schema_model(schema)
    if model is None:
        return None
    _, is_list = _model_name(schema)
    body = model.model_json_schema()
    return {"type": "array", "items": body} if is_list else body


def _entry_fields(spec: CapabilitySpecV1) -> dict[str, Any]:
    match = _VERSION.search(spec.capability_id)
    return {
        "agent_id": spec.capability_id,
        "version": int(match.group(1)) if match else 1,
        "stage_name": spec.stage_name,
        "permission": spec.permission,
        "execution": spec.execution,
        "default_tier": DEFAULT_TIERS[spec.stage_name],
        "escalation": sorted(ESCALATION_TRIGGERS[spec.stage_name]),
        "sync_eligible": is_sync_eligible(spec.stage_name),
        "produces_report": is_report_producing(spec.stage_name),
        "input_schema": spec.input_schema,
        "output_schema": spec.output_schema,
        "input_schema_resolved": resolve_schema_model(spec.input_schema) is not None,
        "output_schema_resolved": resolve_schema_model(spec.output_schema) is not None,
        "dependencies": list(spec.dependencies),
        "required_evidence": list(spec.required_evidence),
        "expected_latency_ms": spec.expected_latency_ms,
        "expected_cost_usd": spec.expected_cost_usd,
        "timeout_seconds": spec.timeout_seconds,
        "max_retries": spec.max_retries,
        "fallback": spec.fallback,
        "concurrency_class": spec.concurrency_class,
    }


def list_catalog() -> list[AgentCatalogEntry]:
    """Every registered capability, ordered by agent id."""
    specs = sorted(CAPABILITY_REGISTRY.values(), key=lambda s: s.capability_id)
    return [AgentCatalogEntry(**_entry_fields(spec)) for spec in specs]


def get_catalog_detail(agent_id: str) -> Optional[AgentCatalogDetail]:
    """One capability with its input wrapper and JSON Schemas, or None if unknown."""
    spec = _spec(agent_id)
    if spec is None:
        return None
    wrapper = input_wrapper(agent_id)
    return AgentCatalogDetail(
        **_entry_fields(spec),
        input_wrapper=wrapper.__name__,
        input_json_schema=wrapper.model_json_schema(),
        output_json_schema=_output_json_schema(spec.output_schema),
    )
