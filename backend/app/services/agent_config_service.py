"""Per-project agent configuration (architecture E4.1, sections 4.1-4.3).

Three pieces:

* ``AgentConfigV1`` -- the project-layer document. Every sub-object is
  ``extra="forbid"``; the ``model.slm`` / ``model.llm`` blocks carry only
  ``provider``, ``model``, ``temperature`` and ``max_tokens``, never an endpoint
  or a key. Its validators hold the environment ceilings, the composition rule
  (``max_attempts x timeout_seconds <= AI_PIPELINE_DEADLINE_SECONDS``) and the
  mode checks for tools and for the capability itself.
* ``AgentConfigPatch`` + ``apply_patch`` -- the request layer. It holds only the
  fields the monotonicity table lets a request override, and ``apply_patch``
  refuses any value that loosens the project layer.
* The store: one ``agent_configs`` row per project and agent. A missing row
  means ``default_config``. Every PUT bumps ``config_version``.

Resolution against the global ``ai_config`` and the resolve-time offline clamp
are E4.2; this module does not decide which model a run uses.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import AGENT_CONFIG_MODES, AgentConfig, Project
from app.agents.fixer.state import (
    DEFAULT_FIXER_BUDGETS,
    DEFAULT_TEST_GLOBS,
    FIXER_AGENT_ID,
    VALID_RUNNER_TYPES,
    VALID_SCHEDULES,
    is_valid_runner_image,
)
from app.services.agent_capability_registry import CAPABILITY_REGISTRY
from app.services.agent_investigation_service import DEFAULT_BUDGETS
from app.services.llm_policy_service import (
    LOCAL_PROVIDERS,
    PROVIDER_PROFILES,
    configured_provider_allowlist,
)
from app.services.retry_policy import DEFAULT_RETRYABLE
from app.services.agent_authority_lock import (
    lock_project_agent_authority,
    project_agent_authority_lock_key,
)

Mode = Literal["shadow", "suggest", "act"]
Tier = Literal["auto", "deterministic", "slm", "llm"]
Permission = Literal["read_only", "propose_action", "mutating"]

#: ``shadow`` < ``suggest`` < ``act`` (section 4.1). Held equal to the model's
#: ``AGENT_CONFIG_MODES`` by tests/test_agent_configs.py.
MODE_ORDER: tuple[str, ...] = ("shadow", "suggest", "act")

#: The most a capability of each permission needs: ``shadow`` permits read-only
#: work, ``suggest`` adds proposals, ``act`` adds mutation (section 4.3).
_PERMISSION_MIN_MODE: dict[str, str] = {
    "read_only": "shadow",
    "propose_action": "suggest",
    "mutating": "act",
}

#: Tier tightness, tightest first. ``auto`` starts at the capability default and
#: may escalate all the way to ``llm``, so it is the loosest.
TIER_ORDER: tuple[str, ...] = ("deterministic", "slm", "llm", "auto")

#: Every LangChain tool under ``app/tools`` and the permission it needs. All of
#: them only read today. tests/test_agent_configs.py fails when a ``@tool`` is
#: added without an entry here, so a new mutating tool cannot default to
#: read_only by omission.
AGENT_TOOL_PERMISSIONS: dict[str, str] = {
    "analyze_openshift_pod_events": "read_only",
    "check_quarantine_status": "read_only",
    "check_test_flakiness": "read_only",
    "count_failure_kinds": "read_only",
    "detect_log_rate_anomaly": "read_only",
    "embed_and_cluster": "read_only",
    "fetch_allure_stacktrace": "read_only",
    "fetch_app_metrics": "read_only",
    "fetch_build_changes": "read_only",
    "fetch_rest_api_payload": "read_only",
    "get_failure_clusters": "read_only",
    "get_release_gate_verdict": "read_only",
    "list_recent_runs": "read_only",
    "list_run_failures": "read_only",
    "query_splunk_logs": "read_only",
    "recall_failure_history": "read_only",
    "recall_similar_failures": "read_only",
    "reconstruct_distributed_trace": "read_only",
    "validate_api_contract": "read_only",
}

REVIEW_POLICIES: tuple[str, ...] = ("human_required", "human_required_plus_auto_reviewer")


def mode_permits(mode: str, permission: str) -> bool:
    """True when ``mode`` is at least the mode ``permission`` needs."""
    needed = _PERMISSION_MIN_MODE.get(permission)
    if needed is None or mode not in MODE_ORDER:
        return False
    return MODE_ORDER.index(mode) >= MODE_ORDER.index(needed)


def tools_permitted_by(mode: str) -> list[str]:
    return sorted(name for name, perm in AGENT_TOOL_PERMISSIONS.items() if mode_permits(mode, perm))


def configurable_capabilities() -> dict[str, str]:
    """``{agent_id: stage_name}`` for every capability a project can configure.

    The ``runtime`` pseudo-capability (``agent.workflow.v1``) is bookkeeping, not
    an agent, so it has no configuration.
    """
    return {
        spec.capability_id: stage
        for stage, spec in CAPABILITY_REGISTRY.items()
        if spec.execution != "runtime"
    }


COMPATIBILITY_AGENT_IDS: tuple[str, ...] = ("fixer", "investigator")


def configurable_agents() -> tuple[str, ...]:
    """Registry capabilities plus the two pre-registry governance agents."""
    return tuple(sorted((*configurable_capabilities(), *COMPATIBILITY_AGENT_IDS)))


def _capability_for(agent_id: str) -> Any:
    stage = configurable_capabilities().get(agent_id)
    return CAPABILITY_REGISTRY[stage] if stage else None


# -- the project-layer document (section 4.2) -------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelEndpointConfig(_Strict):
    """One tier's model. Provider endpoints come only from the environment."""

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=200_000)

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in PROVIDER_PROFILES:
            raise ValueError(f"unknown provider {value!r}; known providers: {sorted(PROVIDER_PROFILES)}")
        return normalized


class EscalationConfig(_Strict):
    on_validation_failure: bool = True
    on_confidence_below: int = Field(default=70, ge=0, le=100)
    max_escalations: int = Field(default=1, ge=0, le=3)


class ModelConfig(_Strict):
    tier: Tier = "auto"
    slm: Optional[ModelEndpointConfig] = None
    llm: Optional[ModelEndpointConfig] = None
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)


class ThresholdsConfig(_Strict):
    confidence_min: int = Field(default=80, ge=0, le=100)
    max_failures_analyzed: int = Field(default=50, ge=1, le=1000)
    degraded_ratio: float = Field(default=0.3, ge=0.0, le=1.0)


class RetryConfig(_Strict):
    max_attempts: int = Field(default_factory=lambda: settings.AGENT_PIPELINE_MAX_ATTEMPTS, ge=1)
    base_seconds: int = Field(default_factory=lambda: settings.AGENT_RETRY_BASE_SECONDS, ge=1)
    cap_seconds: int = Field(default_factory=lambda: settings.AGENT_RETRY_CAP_SECONDS, ge=1)
    jitter: float = Field(default=0.2, ge=0.0, lt=1.0)
    retry_on: list[str] = Field(default_factory=lambda: ["model_unavailable", "timeout", "tool_error"])

    @field_validator("retry_on")
    @classmethod
    def _retryable_codes(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - DEFAULT_RETRYABLE)
        if unknown:
            raise ValueError(f"retry_on may only name retryable codes {sorted(DEFAULT_RETRYABLE)}; got {unknown}")
        return sorted(set(value))

    @model_validator(mode="after")
    def _ceiling_and_cap(self) -> "RetryConfig":
        ceiling = int(settings.AGENT_MAX_ATTEMPTS_CEILING)
        if self.max_attempts > ceiling:
            raise ValueError(
                f"retry.max_attempts={self.max_attempts} exceeds this deployment's ceiling "
                f"AGENT_MAX_ATTEMPTS_CEILING={ceiling}"
            )
        if self.cap_seconds < self.base_seconds:
            raise ValueError(
                f"retry.cap_seconds={self.cap_seconds} is below retry.base_seconds={self.base_seconds}"
            )
        return self


class ToolsConfig(_Strict):
    allowlist: list[str] = Field(default_factory=list)

    @field_validator("allowlist")
    @classmethod
    def _known_tools(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(AGENT_TOOL_PERMISSIONS))
        if unknown:
            raise ValueError(f"unknown tools {unknown}; known tools: {sorted(AGENT_TOOL_PERMISSIONS)}")
        return sorted(set(value))


class BudgetConfig(_Strict):
    """Budget names are the existing ``DEFAULT_BUDGETS`` keys (section 4.2)."""

    max_llm_calls_per_run: int = Field(default=int(DEFAULT_BUDGETS["max_llm_calls_per_run"]), ge=0)
    max_tokens_per_run: int = Field(default=int(DEFAULT_BUDGETS["max_tokens_per_run"]), ge=0)
    max_cost_usd_per_run: float = Field(default=float(DEFAULT_BUDGETS["max_cost_usd_per_run"]), ge=0.0)
    max_runs_per_day: int = Field(default=int(DEFAULT_BUDGETS["max_runs_per_day"]), ge=0)


class ShadowConfig(_Strict):
    sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    daily_token_budget: int = Field(default=200_000, ge=0)


class ReviewConfig(_Strict):
    policy: Literal["human_required", "human_required_plus_auto_reviewer"] = "human_required"
    auto_reviewer: bool = False
    second_model_check: bool = False

    @model_validator(mode="after")
    def _policy_names_the_reviewer(self) -> "ReviewConfig":
        if self.policy == "human_required_plus_auto_reviewer" and not self.auto_reviewer:
            raise ValueError("review.policy=human_required_plus_auto_reviewer requires review.auto_reviewer=true")
        return self


class OverridePolicyConfig(_Strict):
    allow_tier_downgrade: bool = True
    allow_retry_decrease: bool = True
    allow_tool_narrowing: bool = True


class InvestigatorPolicyBudgets(_Strict):
    """The complete legacy Investigator budget contract preserved by E4.4."""

    max_runs_per_day: int = Field(default=10, ge=0, le=10_000)
    max_llm_calls_per_run: int = Field(default=30, ge=0, le=100_000)
    max_tokens_per_run: int = Field(default=60_000, ge=0, le=100_000_000)
    max_cost_usd_per_run: float = Field(default=5.0, ge=0, le=1_000_000)
    max_seconds_per_run: int = Field(default=300, ge=0, le=86_400)
    max_cluster_children_per_run: int = Field(default=1, ge=0, le=20)
    max_cluster_members_per_child: int = Field(default=50, ge=1, le=500)
    max_cluster_child_llm_calls_per_parent: int = Field(default=6, ge=0, le=1_000)
    max_cluster_child_tokens_per_parent: int = Field(default=12_000, ge=0, le=10_000_000)
    max_cluster_child_cost_usd_per_parent: float = Field(default=2.0, ge=0, le=1_000_000)
    max_cluster_child_seconds_per_parent: int = Field(default=180, ge=0, le=86_400)
    max_active_cluster_children_per_project: int = Field(default=2, ge=0, le=20)
    max_cluster_children_per_day: int = Field(default=20, ge=0, le=1_000)


class InvestigatorConfigExtension(_Strict):
    budgets: InvestigatorPolicyBudgets = Field(default_factory=InvestigatorPolicyBudgets)
    shadow_runs_completed: int = Field(default=0, ge=0)
    promotion_note: Optional[str] = Field(default=None, max_length=2_000)


class FixerRunnerExtension(_Strict):
    type: str = "none"
    runner_image: Optional[str] = None
    command_template: Optional[str] = None
    workflow_ref: Optional[str] = None

    @field_validator("type")
    @classmethod
    def _known_runner_type(cls, value: str) -> str:
        if value not in VALID_RUNNER_TYPES:
            raise ValueError(f"runner.type must be one of {list(VALID_RUNNER_TYPES)}")
        return value

    @field_validator("runner_image")
    @classmethod
    def _safe_runner_image(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not is_valid_runner_image(value):
            raise ValueError("runner_image must be a valid container image reference")
        return value


class FixerPolicyBudgets(_Strict):
    max_tests_per_run: int = Field(default=int(DEFAULT_FIXER_BUDGETS["max_tests_per_run"]), ge=0, le=1_000)
    max_attempts_per_test: int = Field(default=int(DEFAULT_FIXER_BUDGETS["max_attempts_per_test"]), ge=0, le=100)
    validation_reruns: int = Field(default=int(DEFAULT_FIXER_BUDGETS["validation_reruns"]), ge=1, le=100)
    max_concurrent_open_prs: int = Field(
        default=int(DEFAULT_FIXER_BUDGETS["max_concurrent_open_prs"]), ge=0, le=100
    )


class FixerConfigExtension(_Strict):
    runner: FixerRunnerExtension = Field(default_factory=FixerRunnerExtension)
    test_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))
    budgets: FixerPolicyBudgets = Field(default_factory=FixerPolicyBudgets)
    schedule: str = "off"

    @field_validator("test_globs")
    @classmethod
    def _non_empty_globs(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("test_globs must contain at least one non-empty glob")
        return cleaned

    @field_validator("schedule")
    @classmethod
    def _known_schedule(cls, value: str) -> str:
        if value not in VALID_SCHEDULES:
            raise ValueError(f"schedule must be one of {list(VALID_SCHEDULES)}")
        return value


class AgentConfigExtensions(_Strict):
    """Typed homes for pre-registry contracts migrated from agent_policies."""

    investigator: Optional[InvestigatorConfigExtension] = None
    fixer: Optional[FixerConfigExtension] = None


class AgentConfigV1(_Strict):
    """A project's configuration of one agent (section 4.2)."""

    agent_id: str
    enabled: bool = True
    mode: Mode = "shadow"
    model: ModelConfig = Field(default_factory=ModelConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    timeout_seconds: int = Field(default=60, ge=1)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    shadow: ShadowConfig = Field(default_factory=ShadowConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    override_policy: OverridePolicyConfig = Field(default_factory=OverridePolicyConfig)
    extensions: AgentConfigExtensions = Field(default_factory=AgentConfigExtensions)

    @field_validator("agent_id")
    @classmethod
    def _registered(cls, value: str) -> str:
        if value not in configurable_agents():
            raise ValueError(f"unknown agent_id {value!r}; configurable agents: {list(configurable_agents())}")
        return value

    @model_validator(mode="after")
    def _section_4_3(self) -> "AgentConfigV1":
        errors: list[str] = []
        timeout_ceiling = int(settings.AGENT_MAX_TIMEOUT_CEILING)
        if self.timeout_seconds > timeout_ceiling:
            errors.append(
                f"timeout_seconds={self.timeout_seconds} exceeds this deployment's ceiling "
                f"AGENT_MAX_TIMEOUT_CEILING={timeout_ceiling}"
            )
        deadline = int(settings.AI_PIPELINE_DEADLINE_SECONDS)
        worst_case = self.retry.max_attempts * self.timeout_seconds
        if worst_case > deadline:
            errors.append(
                f"retry.max_attempts x timeout_seconds = {self.retry.max_attempts} x {self.timeout_seconds} "
                f"= {worst_case} s, which does not fit the {deadline} s pipeline deadline "
                f"(AI_PIPELINE_DEADLINE_SECONDS); lower one of them"
            )
        forbidden = [t for t in self.tools.allowlist if not mode_permits(self.mode, AGENT_TOOL_PERMISSIONS[t])]
        if forbidden:
            errors.append(f"tools {forbidden} need a higher mode than {self.mode!r}")
        capability = _capability_for(self.agent_id)
        if self.enabled and capability is not None and not mode_permits(self.mode, capability.permission):
            errors.append(
                f"{self.agent_id} is a {capability.permission} capability; mode {self.mode!r} cannot run it. "
                f"Raise mode to {_PERMISSION_MIN_MODE[capability.permission]!r} or set enabled=false"
            )
        if self.agent_id == "investigator" and self.extensions.fixer is not None:
            errors.append("investigator config cannot carry extensions.fixer")
        if self.agent_id == FIXER_AGENT_ID:
            if self.mode == "act":
                errors.append("fixer mode 'act' is reserved; use 'shadow' or 'suggest'")
            if self.extensions.investigator is not None:
                errors.append("fixer config cannot carry extensions.investigator")
        if self.agent_id not in COMPATIBILITY_AGENT_IDS and (
            self.extensions.investigator is not None or self.extensions.fixer is not None
        ):
            errors.append("registry capability configs cannot carry compatibility extensions")
        if errors:
            raise ValueError("; ".join(errors))
        return self


def default_config(agent_id: str) -> AgentConfigV1:
    """The configuration a project has before anyone writes one.

    The mode is the lowest one that lets the capability run, except that a
    ``mutating`` capability is never granted ``act`` by default: it starts in
    ``shadow`` and disabled.
    """
    if agent_id == "investigator":
        return AgentConfigV1(
            agent_id=agent_id,
            enabled=True,
            mode="shadow",
            extensions=AgentConfigExtensions(investigator=InvestigatorConfigExtension()),
        )
    if agent_id == FIXER_AGENT_ID:
        return AgentConfigV1(
            agent_id=agent_id,
            enabled=False,
            mode="shadow",
            extensions=AgentConfigExtensions(fixer=FixerConfigExtension()),
        )
    capability = _capability_for(agent_id)
    if capability is None:
        raise KeyError(agent_id)
    if capability.permission == "mutating":
        mode, enabled = "shadow", False
    else:
        mode, enabled = _PERMISSION_MIN_MODE[capability.permission], True
    # Clamped to the environment so defaults always validate: a lowered ceiling
    # must not turn "nobody configured this agent" into a 500.
    deadline = int(settings.AI_PIPELINE_DEADLINE_SECONDS)
    timeout = max(1, min(int(capability.timeout_seconds), int(settings.AGENT_MAX_TIMEOUT_CEILING), deadline))
    attempts = max(1, min(
        int(settings.AGENT_PIPELINE_MAX_ATTEMPTS),
        int(settings.AGENT_MAX_ATTEMPTS_CEILING),
        deadline // timeout,
    ))
    return AgentConfigV1(
        agent_id=agent_id,
        enabled=enabled,
        mode=cast(Mode, mode),
        timeout_seconds=timeout,
        retry=RetryConfig(max_attempts=attempts),
        tools=ToolsConfig(allowlist=tools_permitted_by(mode)),
    )


# -- the request layer (section 4.1, monotonicity table) ------------------------------------


class _TierPatch(_Strict):
    tier: Tier


class _RetryPatch(_Strict):
    max_attempts: int = Field(ge=1)


class _ThresholdsPatch(_Strict):
    max_failures_analyzed: int = Field(ge=1)


class _ToolsPatch(_Strict):
    allowlist: list[str]


class _BudgetPatch(_Strict):
    max_llm_calls_per_run: Optional[int] = Field(default=None, ge=0)
    max_tokens_per_run: Optional[int] = Field(default=None, ge=0)
    max_cost_usd_per_run: Optional[float] = Field(default=None, ge=0.0)
    max_runs_per_day: Optional[int] = Field(default=None, ge=0)


class _ReviewPatch(_Strict):
    add_auto_reviewer: bool = False


class AgentConfigPatch(_Strict):
    """What one request may override. Every field may only tighten the project layer.

    ``mode``, ``temperature``, ``max_tokens``, ``escalation``, most thresholds and
    any provider or endpoint are absent on purpose: ``extra="forbid"`` rejects them.
    """

    model: Optional[_TierPatch] = None
    retry: Optional[_RetryPatch] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    thresholds: Optional[_ThresholdsPatch] = None
    tools: Optional[_ToolsPatch] = None
    budget: Optional[_BudgetPatch] = None
    review: Optional[_ReviewPatch] = None


class OverrideRejected(ValueError):
    """A request override would loosen the project layer, or its override policy forbids it."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


class ConfigVersionConflict(RuntimeError):
    """A conditional config replacement lost a concurrent-write race."""

    def __init__(self, expected_version: int):
        self.expected_version = expected_version
        super().__init__(f"agent config version {expected_version} is stale")


def _lower_only(reasons: list[str], field: str, requested: Any, base: Any) -> None:
    if requested is not None and requested > base:
        reasons.append(f"{field}={requested} loosens the project value {base}; a request may only lower it")


def apply_patch(base: AgentConfigV1, patch: AgentConfigPatch) -> AgentConfigV1:
    """``base`` tightened by ``patch``. Raises ``OverrideRejected`` for any loosening."""
    reasons: list[str] = []
    doc = base.model_dump()
    policy = base.override_policy

    if patch.model is not None:
        requested, current = patch.model.tier, base.model.tier
        if TIER_ORDER.index(requested) > TIER_ORDER.index(current):
            reasons.append(f"model.tier={requested!r} is looser than the project tier {current!r}; tiers only step down")
        elif requested != current and not policy.allow_tier_downgrade:
            reasons.append("override_policy.allow_tier_downgrade is false for this agent")
        doc["model"]["tier"] = requested

    if patch.retry is not None:
        requested_attempts, current_attempts = patch.retry.max_attempts, base.retry.max_attempts
        _lower_only(reasons, "retry.max_attempts", requested_attempts, current_attempts)
        if requested_attempts < current_attempts and not policy.allow_retry_decrease:
            reasons.append("override_policy.allow_retry_decrease is false for this agent")
        doc["retry"]["max_attempts"] = requested_attempts

    if patch.timeout_seconds is not None:
        _lower_only(reasons, "timeout_seconds", patch.timeout_seconds, base.timeout_seconds)
        doc["timeout_seconds"] = patch.timeout_seconds

    if patch.thresholds is not None:
        requested_failures = patch.thresholds.max_failures_analyzed
        _lower_only(reasons, "thresholds.max_failures_analyzed", requested_failures, base.thresholds.max_failures_analyzed)
        doc["thresholds"]["max_failures_analyzed"] = requested_failures

    if patch.budget is not None:
        for key, value in patch.budget.model_dump(exclude_none=True).items():
            _lower_only(reasons, f"budget.{key}", value, getattr(base.budget, key))
            doc["budget"][key] = value

    if patch.tools is not None:
        requested_tools, current_tools = set(patch.tools.allowlist), set(base.tools.allowlist)
        widened = sorted(requested_tools - current_tools)
        if widened:
            reasons.append(f"tools {widened} are not in the project allowlist; a request may only narrow it")
        elif requested_tools != current_tools and not policy.allow_tool_narrowing:
            reasons.append("override_policy.allow_tool_narrowing is false for this agent")
        doc["tools"]["allowlist"] = sorted(requested_tools)

    if patch.review is not None and patch.review.add_auto_reviewer:
        doc["review"]["policy"] = "human_required_plus_auto_reviewer"
        doc["review"]["auto_reviewer"] = True

    if reasons:
        raise OverrideRejected(reasons)
    return AgentConfigV1.model_validate(doc)


# -- environment courtesy checks (section 4.3) ---------------------------------------------


def provider_environment_errors(config: AgentConfigV1, *, offline: bool) -> list[str]:
    """PUT-time refusals of providers this deployment will not use.

    A courtesy, not the control: the resolver clamps again at invoke time
    (E4.2), because the environment can change after the row is written.
    """
    errors: list[str] = []
    allowlist = configured_provider_allowlist()
    for tier_name in ("slm", "llm"):
        endpoint = getattr(config.model, tier_name)
        if endpoint is None:
            continue
        if offline and endpoint.provider not in LOCAL_PROVIDERS:
            errors.append(
                f"model.{tier_name}.provider={endpoint.provider!r} is not a local provider and offline mode is on; "
                f"local providers: {sorted(LOCAL_PROVIDERS)}"
            )
        if allowlist and endpoint.provider not in allowlist:
            errors.append(
                f"model.{tier_name}.provider={endpoint.provider!r} is not in AI_LLM_PROVIDER_ALLOWLIST {sorted(allowlist)}"
            )
    return errors


# -- the store ------------------------------------------------------------------------------------

#: Stored as columns, not inside the ``config`` document.
_COLUMN_FIELDS = frozenset({"agent_id", "enabled", "mode"})


def agent_config_authority_lock_key(project_id: uuid.UUID) -> str:
    """Return the transaction-lock domain shared by config writes and G4."""
    return project_agent_authority_lock_key(project_id)


async def lock_agent_config_authority(db: AsyncSession, project_id: uuid.UUID) -> None:
    """Keep a project config snapshot stable until the transaction finishes."""
    await lock_project_agent_authority(db, project_id)


async def get_config_row(db: AsyncSession, project_id: uuid.UUID, agent_id: str) -> Optional[AgentConfig]:
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.project_id == project_id, AgentConfig.agent_id == agent_id)
    )
    return result.scalar_one_or_none()


async def list_config_rows(db: AsyncSession, project_id: uuid.UUID) -> dict[str, AgentConfig]:
    result = await db.execute(select(AgentConfig).where(AgentConfig.project_id == project_id))
    return {row.agent_id: row for row in result.scalars().all()}


async def put_config(
    db: AsyncSession,
    project_id: uuid.UUID,
    config: AgentConfigV1,
    *,
    updated_by: Optional[uuid.UUID],
    expected_version: Optional[int] = None,
) -> AgentConfig:
    """Replace a project's configuration of one agent and bump its version.

    One ``INSERT ... ON CONFLICT DO UPDATE`` statement: two concurrent PUTs get
    distinct versions instead of racing a read-then-write.
    """
    await lock_agent_config_authority(db, project_id)
    document = config.model_dump(mode="json", exclude=set(_COLUMN_FIELDS))
    now = datetime.now(timezone.utc)
    insert_statement = pg_insert(AgentConfig).values(
        id=uuid.uuid4(),
        project_id=project_id,
        agent_id=config.agent_id,
        enabled=config.enabled,
        mode=config.mode,
        config=document,
        config_version=1,
        updated_by=updated_by,
        created_at=now,
        updated_at=now,
    )
    conflict_kwargs: dict[str, Any] = {}
    if expected_version is not None:
        conflict_kwargs["where"] = AgentConfig.config_version == expected_version
    upsert = insert_statement.on_conflict_do_update(
        constraint="uq_agent_configs_project_agent",
        set_={
            "enabled": insert_statement.excluded.enabled,
            "mode": insert_statement.excluded.mode,
            "config": insert_statement.excluded.config,
            "config_version": AgentConfig.config_version + 1,
            "updated_by": insert_statement.excluded.updated_by,
            "updated_at": insert_statement.excluded.updated_at,
        },
        **conflict_kwargs,
    ).returning(AgentConfig)
    result = await db.execute(upsert, execution_options={"populate_existing": True})
    row: Optional[AgentConfig] = result.scalar_one_or_none()
    if row is None:
        assert expected_version is not None
        raise ConfigVersionConflict(expected_version)
    return row


async def config_versions(db: AsyncSession, project_id: uuid.UUID) -> dict[str, int]:
    """``{agent_id: config_version}`` for the agents this project has configured.

    Frozen into a pipeline run's ``execution_metadata``. An agent with no row
    runs on defaults and is absent from the map.
    """
    result = await db.execute(
        select(AgentConfig.agent_id, AgentConfig.config_version).where(AgentConfig.project_id == project_id)
    )
    return {str(agent_id): int(version) for agent_id, version in result.all()}


async def increment_investigator_shadow_runs(db: AsyncSession, project_id: uuid.UUID) -> AgentConfig:
    """Atomically increment the server-owned Investigator promotion counter."""
    await db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    row = await get_config_row(db, project_id, "investigator")
    current = AgentConfigV1.model_validate(serialize("investigator", row)["config"])
    extension = current.extensions.investigator or InvestigatorConfigExtension()
    document = current.model_dump(mode="json")
    document["extensions"]["investigator"] = extension.model_copy(
        update={"shadow_runs_completed": extension.shadow_runs_completed + 1}
    ).model_dump(mode="json")
    return await put_config(
        db,
        project_id,
        AgentConfigV1.model_validate(document),
        updated_by=None,
    )


def serialize(agent_id: str, row: Optional[AgentConfig]) -> dict[str, Any]:
    """The wire shape. A stored row that no longer validates is returned as stored, marked invalid.

    A row can stop validating without being written, for example when an
    environment ceiling is lowered. Hiding it behind the defaults would report
    a configuration nobody chose.
    """
    if row is None:
        return {
            "agent_id": agent_id,
            "source": "default",
            "config_version": 0,
            "valid": True,
            "errors": [],
            "updated_at": None,
            "updated_by": None,
            "config": default_config(agent_id).model_dump(mode="json"),
        }
    document = {**dict(row.config or {}), "agent_id": agent_id, "enabled": bool(row.enabled), "mode": row.mode}
    errors: list[str] = []
    try:
        document = AgentConfigV1.model_validate(document).model_dump(mode="json")
    except ValidationError as exc:
        errors = [str(err.get("msg", "")) for err in exc.errors()]
    return {
        "agent_id": agent_id,
        "source": "project",
        "config_version": int(row.config_version),
        "valid": not errors,
        "errors": errors,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": str(row.updated_by) if row.updated_by else None,
        "config": document,
    }


__all__ = [
    "AGENT_CONFIG_MODES",
    "AGENT_TOOL_PERMISSIONS",
    "COMPATIBILITY_AGENT_IDS",
    "AgentConfigPatch",
    "AgentConfigV1",
    "ConfigVersionConflict",
    "MODE_ORDER",
    "OverrideRejected",
    "REVIEW_POLICIES",
    "TIER_ORDER",
    "apply_patch",
    "agent_config_authority_lock_key",
    "config_versions",
    "configurable_capabilities",
    "configurable_agents",
    "default_config",
    "get_config_row",
    "increment_investigator_shadow_runs",
    "list_config_rows",
    "lock_agent_config_authority",
    "mode_permits",
    "provider_environment_errors",
    "put_config",
    "serialize",
    "tools_permitted_by",
]
