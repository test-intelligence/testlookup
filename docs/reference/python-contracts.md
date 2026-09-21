# Python data contracts and enumerations

[Documentation home](../README.md) · [Regeneration](../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../handoff/verification.md).

Declared class fields and validators from all backend application modules, including inline router schemas, dataclasses, TypedDict state, enums and ORM models. Inherited fields are defined in linked base classes and public expanded schemas are in [HTTP schemas](schemas.md). Validator names are pointers to implementation, not an assertion that their logic is fully represented in JSON Schema.

## backend/app/agents/analysis_agent.py — AnalysisAgent

[backend/app/agents/analysis_agent.py:128](../../backend/app/agents/analysis_agent.py#L128)

Bases: `BaseAgent`.



```python
stage_name = 'root_cause_analysis'
UNKNOWN_CATEGORY = 'UNKNOWN'
```

## backend/app/agents/anomaly_agent.py — AnomalyDetectionAgent

[backend/app/agents/anomaly_agent.py:49](../../backend/app/agents/anomaly_agent.py#L49)

Bases: `BaseAgent`.



```python
stage_name = 'anomaly_detection'
```

## backend/app/agents/base.py — BaseAgent

[backend/app/agents/base.py:27](../../backend/app/agents/base.py#L27)

Bases: `ABC`.

Abstract base for all pipeline agents.
Provides DB session management, stage result persistence,
OpenTelemetry span tracking, and Prometheus metrics emission.

```python
stage_name: str = 'unknown'
```

## backend/app/agents/change_ownership_agent.py — ChangeOwnershipAgent

[backend/app/agents/change_ownership_agent.py:50](../../backend/app/agents/change_ownership_agent.py#L50)

Bases: `BaseAgent`.

Compose authoritative baseline and cluster ownership evidence.

```python
stage_name = 'change_ownership'
```

## backend/app/agents/cluster_agent.py — ClusterAgent

[backend/app/agents/cluster_agent.py:25](../../backend/app/agents/cluster_agent.py#L25)

Bases: `BaseAgent`.



```python
stage_name = 'failure_clustering'
```

## backend/app/agents/consistency.py — ConsistencyCheck

[backend/app/agents/consistency.py:93](../../backend/app/agents/consistency.py#L93)

Bases: `BaseModel`.

A single named consistency verification result.

```python
model_config = ConfigDict(extra='ignore')
name: str
passed: bool
severity: str = 'warning'
details: str = ''
offending_refs: list[str] = Field(default_factory=list)
```

- Validator/serializer `_clamp_severity`: [backend/app/agents/consistency.py:106](../../backend/app/agents/consistency.py#L106). Read source for the cross-field or conversion rule.
## backend/app/agents/consistency.py — ConsistencyReport

[backend/app/agents/consistency.py:110](../../backend/app/agents/consistency.py#L110)

Bases: `BaseModel`.

A collection of consistency checks for one agent's output.

```python
model_config = ConfigDict(extra='ignore')
agent: str
checks: list[ConsistencyCheck] = Field(default_factory=list)
```

## backend/app/agents/contract_agent.py — ContractAgent

[backend/app/agents/contract_agent.py:32](../../backend/app/agents/contract_agent.py#L32)

Bases: `BaseAgent`.

Validate REST response contracts using server-scoped evidence only.

Subclasses ``BaseAgent`` so the stage lands in the standard tables. It
did not until 2026-08-24, which is why ``contract_validation`` wrote no
``stage_started`` / ``stage_completed`` event at all: 0 of 6 on the
deployment, against 6 of 6 for the compliant ``regression_watchman``.
The row itself was backfilled by the wrapper added in #840, but a
backfilled row carries no span, no metrics and no decision trail.

```python
stage_name = 'contract_validation'
```

## backend/app/agents/conversation.py — QueryIntent

[backend/app/agents/conversation.py:59](../../backend/app/agents/conversation.py#L59)

Bases: `str, Enum`.



```python
TREND = 'trend'
FAILURE = 'failure'
FLAKINESS = 'flakiness'
COMPARISON = 'comparison'
SUMMARY = 'summary'
TRIAGE = 'triage'
PERFORMANCE = 'performance'
GENERAL = 'general'
```

## backend/app/agents/decision_report_agent.py — DecisionReportAgent

[backend/app/agents/decision_report_agent.py:508](../../backend/app/agents/decision_report_agent.py#L508)

Bases: `BaseAgent`.

Publish the final, specialist-complete report for a deep workflow.

```python
stage_name = 'decision_report'
```

## backend/app/agents/decision_report_critic_agent.py — DecisionReportCriticAgent

[backend/app/agents/decision_report_critic_agent.py:615](../../backend/app/agents/decision_report_critic_agent.py#L615)

Bases: `BaseAgent`.



```python
stage_name = 'decision_report_critic'
```

## backend/app/agents/defect_commander.py — DefectCommander

[backend/app/agents/defect_commander.py:47](../../backend/app/agents/defect_commander.py#L47)

Bases: `BaseAgent`.



```python
stage_name = 'defect_commander'
```

## backend/app/agents/evidence.py — EvidenceRef

[backend/app/agents/evidence.py:34](../../backend/app/agents/evidence.py#L34)

Bases: `BaseModel`.

A single, redaction-safe piece of evidence backing an agent decision.

```python
model_config = ConfigDict(extra='ignore')
source: str = ''
ref_id: str = ''
excerpt: str = ''
strength: Literal['weak', 'medium', 'strong'] = 'weak'
contribution: int = 0
```

- Validator/serializer `_coerce_str`: [backend/app/agents/evidence.py:47](../../backend/app/agents/evidence.py#L47). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_strength`: [backend/app/agents/evidence.py:54](../../backend/app/agents/evidence.py#L54). Read source for the cross-field or conversion rule.
- Validator/serializer `_clamp_contribution`: [backend/app/agents/evidence.py:63](../../backend/app/agents/evidence.py#L63). Read source for the cross-field or conversion rule.
- Validator/serializer `_redact_excerpt`: [backend/app/agents/evidence.py:74](../../backend/app/agents/evidence.py#L74). Read source for the cross-field or conversion rule.
## backend/app/agents/fixer/runners.py — ValidationRunner

[backend/app/agents/fixer/runners.py:83](../../backend/app/agents/fixer/runners.py#L83)

Bases: `ABC`.

Interface: rerun a test against a patch and report validated/failed/error.

```python
type: str = 'abstract'
```

## backend/app/agents/fixer/runners.py — NoRunner

[backend/app/agents/fixer/runners.py:199](../../backend/app/agents/fixer/runners.py#L199)

Bases: `ValidationRunner`.

The ``type:"none"`` default. Always ``error`` — a fix is NEVER validated
and therefore NEVER surfaced as a PR. This is the invariant's backstop.

```python
type = 'none'
```

## backend/app/agents/fixer/runners.py — DockerEphemeralRunner

[backend/app/agents/fixer/runners.py:216](../../backend/app/agents/fixer/runners.py#L216)

Bases: `ValidationRunner`.

Flagship runner: ephemeral Docker container on the worker.

```python
type = 'docker'
```

## backend/app/agents/fixer/runners.py — WorkflowDispatchRunner

[backend/app/agents/fixer/runners.py:386](../../backend/app/agents/fixer/runners.py#L386)

Bases: `ValidationRunner`.

GitHub ``workflow_dispatch`` v1: dispatch a reference validation workflow
and poll its run conclusion. Reuses the GitHub integration's PAT/SSRF/
error patterns. When the integration is missing/offline, returns
``error`` — never a spurious ``validated``.

```python
type = 'workflow_dispatch'
```

## backend/app/agents/fixer/runners.py — FakeRunner

[backend/app/agents/fixer/runners.py:553](../../backend/app/agents/fixer/runners.py#L553)

Bases: `ValidationRunner`.

Deterministic in-memory runner for the FakeRunner end-to-end tests.

``outcome`` is one of validated | failed | error. It records the spec it
received so tests can assert on selectors/patches without a container.

```python
type = 'fake'
```

## backend/app/agents/fixer/state.py — TestIdentity

[backend/app/agents/fixer/state.py:73](../../backend/app/agents/fixer/state.py#L73)

Bases: ``.

How to name + invoke the single test under validation.

```python
__test__ = False
framework: str
command_template: str
test_selector: str
```

## backend/app/agents/fixer/state.py — ValidationSpec

[backend/app/agents/fixer/state.py:84](../../backend/app/agents/fixer/state.py#L84)

Bases: ``.

Immutable request handed to a :class:`ValidationRunner`.

```python
repo_url: str
ref: str
patch: str
test_identity: TestIdentity
reruns: int = 5
timeout_s: int = 300
env_allowlist: tuple[str, ...] = ()
runner_image: Optional[str] = None
workflow_ref: Optional[str] = None
cpus: float = 1.0
memory: str = '1g'
allow_network_egress: bool = False
clone_token: Optional[str] = None
```

## backend/app/agents/fixer/state.py — RunAttempt

[backend/app/agents/fixer/state.py:107](../../backend/app/agents/fixer/state.py#L107)

Bases: ``.

One rerun of the test inside the sandbox.

```python
n: int
passed: bool
duration_ms: int
log_digest: str
```

## backend/app/agents/fixer/state.py — ValidationResult

[backend/app/agents/fixer/state.py:117](../../backend/app/agents/fixer/state.py#L117)

Bases: ``.

Runner outcome. ``validated`` iff EVERY rerun passed.

```python
status: str
runs: list[RunAttempt] = field(default_factory=list)
logs_ref: Optional[str] = None
error: Optional[str] = None
```

## backend/app/agents/fixer/state.py — FixCandidate

[backend/app/agents/fixer/state.py:135](../../backend/app/agents/fixer/state.py#L135)

Bases: ``.

A flaky/quarantined test the Fixer may attempt.

```python
test_fingerprint: str
test_name: Optional[str]
suite_name: Optional[str]
flip_rate: Optional[float]
flip_window_size: Optional[int]
prior_attempts: int = 0
```

## backend/app/agents/fixer/workflow.py — TerminalDecision

[backend/app/agents/fixer/workflow.py:62](../../backend/app/agents/fixer/workflow.py#L62)

Bases: ``.

Pure result of mapping a validation outcome to a terminal attempt state.

``open_pr`` is True ONLY for a validated fix in suggest mode with an open-PR
slot AND a usable GitHub integration. This encodes the load-bearing
invariant: ``error`` (infra), ``failed`` (fix didn't validate), shadow
mode, and the NoRunner default NEVER open a PR.

```python
__slots__ = ('status', 'open_pr', 'reason')
```

## backend/app/agents/flaky_sentinel_agent.py — FlakySentinelAgent

[backend/app/agents/flaky_sentinel_agent.py:89](../../backend/app/agents/flaky_sentinel_agent.py#L89)

Bases: `BaseAgent`.



```python
stage_name = 'flaky_sentinel'
```

## backend/app/agents/gap_detection_agent.py — GapDetectionAgent

[backend/app/agents/gap_detection_agent.py:36](../../backend/app/agents/gap_detection_agent.py#L36)

Bases: `BaseAgent`.



```python
stage_name = 'gap_detection'
```

## backend/app/agents/ingestion_agent.py — IngestionAgent

[backend/app/agents/ingestion_agent.py:22](../../backend/app/agents/ingestion_agent.py#L22)

Bases: `BaseAgent`.



```python
stage_name = 'ingestion'
```

## backend/app/agents/investigator/hypotheses.py — HypothesisAgent

[backend/app/agents/investigator/hypotheses.py:129](../../backend/app/agents/investigator/hypotheses.py#L129)

Bases: `BaseAgent`.

Shared run() skeleton for the five hypothesis sub-agents.

```python
hypothesis_id: str = 'unknown'
title: str = 'Unknown hypothesis'
```

## backend/app/agents/investigator/hypotheses.py — InfraHypothesisAgent

[backend/app/agents/investigator/hypotheses.py:463](../../backend/app/agents/investigator/hypotheses.py#L463)

Bases: `HypothesisAgent`.



```python
stage_name = 'hypothesis_infra'
hypothesis_id = 'infra'
title = 'Infrastructure failure (runners, services, platform)'
```

## backend/app/agents/investigator/hypotheses.py — CommitHypothesisAgent

[backend/app/agents/investigator/hypotheses.py:569](../../backend/app/agents/investigator/hypotheses.py#L569)

Bases: `HypothesisAgent`.



```python
stage_name = 'hypothesis_commit'
hypothesis_id = 'commit'
title = 'Code change onset (commit vs baseline)'
_NO_GIT_NOTE = 'commit-range contents unavailable; onset alignment only'
```

## backend/app/agents/investigator/hypotheses.py — EnvironmentHypothesisAgent

[backend/app/agents/investigator/hypotheses.py:678](../../backend/app/agents/investigator/hypotheses.py#L678)

Bases: `HypothesisAgent`.



```python
stage_name = 'hypothesis_environment'
hypothesis_id = 'environment'
title = 'Environment drift vs baseline'
_STRONG_FIELDS = ('environment', 'ocp_namespace', 'ocp_node')
```

## backend/app/agents/investigator/hypotheses.py — KnownFlakyHypothesisAgent

[backend/app/agents/investigator/hypotheses.py:780](../../backend/app/agents/investigator/hypotheses.py#L780)

Bases: `HypothesisAgent`.



```python
stage_name = 'hypothesis_known_flaky'
hypothesis_id = 'known_flaky'
title = 'Known-flaky recurrence'
```

## backend/app/agents/investigator/hypotheses.py — RegressionHypothesisAgent

[backend/app/agents/investigator/hypotheses.py:876](../../backend/app/agents/investigator/hypotheses.py#L876)

Bases: `HypothesisAgent`.



```python
stage_name = 'hypothesis_regression'
hypothesis_id = 'regression'
title = 'Genuine product regression'
```

## backend/app/agents/investigator/persistence.py — BudgetReservation

[backend/app/agents/investigator/persistence.py:52](../../backend/app/agents/investigator/persistence.py#L52)

Bases: ``.



```python
reservation_id: str
allowed: bool
reserved_llm_calls: int = 0
reserved_tokens: int = 0
stop_reason: BudgetStopReason | None = None
binding: tuple[tuple[str, str], ...] = ()
reserved_cost_usd: float = 0.0
cost_source: str = 'unknown'
```

## backend/app/agents/investigator/state.py — InvestigationState

[backend/app/agents/investigator/state.py:26](../../backend/app/agents/investigator/state.py#L26)

Bases: `TypedDict`.

Shared state across the Investigator's LangGraph nodes.

```python
investigation_id: str
pipeline_run_id: str
run_id: str
project_id: str
build_number: str
mode: str
triggered_by: str
budget: dict[str, int]
deadline_ts: float
bundle: dict[str, Any]
cancelled: bool
hypotheses: Annotated[list[dict[str, Any]], _concat_lists]
spend_llm_calls: Annotated[int, _add_int]
spend_tokens: Annotated[int, _add_int]
spend_cost_usd: Annotated[float, _add_float]
errors: Annotated[list[str], _concat_lists]
verdict: Optional[dict[str, Any]]
model_info: Optional[dict[str, str]]
resume_hypotheses: list[dict[str, Any]]
resume_completed_hypotheses: set[str]
resume_completed_stages: set[str]
resume_verdict: Optional[dict[str, Any]]
```

## backend/app/agents/investigator/synthesis.py — SynthesisAgent

[backend/app/agents/investigator/synthesis.py:162](../../backend/app/agents/investigator/synthesis.py#L162)

Bases: `BaseAgent`.

Fan-in node: hypotheses → verdict (+ ONE optional narrative LLM call).

```python
stage_name = 'investigator_synthesis'
```

## backend/app/agents/log_intelligence_agent.py — LogIntelligenceAgent

[backend/app/agents/log_intelligence_agent.py:69](../../backend/app/agents/log_intelligence_agent.py#L69)

Bases: `BaseAgent`.

Specialist for log-based evidence gathering.
Called per cluster with context about the failing service and timestamp.

Subclasses ``BaseAgent`` so the stage lands in the standard tables. It
did not until 2026-08-24, which is why ``log_intelligence`` wrote no
``stage_started`` / ``stage_completed`` event at all: 0 of 6 on the
deployment, against 6 of 6 for the compliant ``regression_watchman``.

```python
stage_name = 'log_intelligence'
```

## backend/app/agents/regression_watchman.py — RegressionWatchman

[backend/app/agents/regression_watchman.py:63](../../backend/app/agents/regression_watchman.py#L63)

Bases: `BaseAgent`.



```python
stage_name = 'regression_watchman'
```

## backend/app/agents/release_risk_agent.py — ReleaseRiskAgent

[backend/app/agents/release_risk_agent.py:73](../../backend/app/agents/release_risk_agent.py#L73)

Bases: `BaseAgent`.



```python
stage_name = 'release_risk'
```

## backend/app/agents/report_refinement_agent.py — ReportRefinementAgent

[backend/app/agents/report_refinement_agent.py:40](../../backend/app/agents/report_refinement_agent.py#L40)

Bases: `BaseAgent`.



```python
stage_name = 'report_refinement'
```

## backend/app/agents/reviewer_agent.py — _ClaimExtraction

[backend/app/agents/reviewer_agent.py:63](../../backend/app/agents/reviewer_agent.py#L63)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
reference_ids: list[str] = Field(default_factory=list, max_length=100)
numeric_facts: dict[str, float] = Field(default_factory=dict)
```

- Validator/serializer `_bounded_finite_claims`: [backend/app/agents/reviewer_agent.py:70](../../backend/app/agents/reviewer_agent.py#L70). Read source for the cross-field or conversion rule.
## backend/app/agents/reviewer_agent.py — _SecondModelAssessment

[backend/app/agents/reviewer_agent.py:83](../../backend/app/agents/reviewer_agent.py#L83)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
agreement_score: float = Field(ge=0.0, le=1.0)
unsupported_claims: list[str] = Field(default_factory=list, max_length=100)
blocking_unsupported_claims: list[str] = Field(default_factory=list, max_length=100)
```

- Validator/serializer `_bounded_claims`: [backend/app/agents/reviewer_agent.py:91](../../backend/app/agents/reviewer_agent.py#L91). Read source for the cross-field or conversion rule.
## backend/app/agents/reviewer_agent.py — ReviewerAgent

[backend/app/agents/reviewer_agent.py:551](../../backend/app/agents/reviewer_agent.py#L551)

Bases: `BaseAgent`.



```python
stage_name = 'reviewer'
```

## backend/app/agents/state.py — WorkflowState

[backend/app/agents/state.py:60](../../backend/app/agents/state.py#L60)

Bases: `TypedDict`.



```python
pipeline_run_id: str
test_run_id: str
project_id: str
build_number: str
workflow_type: str
eval_manifest_checksum: str
pipeline_deadline_ts: float
test_run_data: Optional[dict]
branch: Optional[str]
failed_test_ids: list[str]
total_tests: int
pass_rate: float
ingestion_enriched: bool
anomalies: list[dict]
is_regression: bool
regression_tests: list[str]
anomaly_summary: Optional[str]
analyses: Annotated[dict[str, dict], _merge_dicts]
executive_summary: Optional[str]
summary_markdown: Optional[str]
structured_summary: Optional[dict]
summary_provenance: Optional[dict]
decision_intelligence: Optional[dict]
decision_evidence_snapshot: Optional[dict]
decision_report_verification: Optional[dict]
authorized_evidence_artifacts: list[dict]
evidence_authorization_errors: list[str]
triage_results: list[dict]
failure_clusters: list[dict]
cluster_map: dict[str, str]
cluster_child_settings: dict
contract_agent_enabled: bool
contract_findings: Optional[dict]
log_intelligence_enabled: bool
log_findings: Optional[dict]
defect_commander_enabled: bool
defect_promotion: Optional[dict]
regression_watchman_enabled: bool
regression_classification: Optional[dict]
change_ownership_enabled: bool
change_ownership_findings: Optional[dict]
cluster_investigation_plan: Optional[dict]
cluster_investigation_results: Optional[dict]
deep_findings: Annotated[dict[str, dict], _merge_dicts]
flaky_findings: list[dict]
test_health_findings: list[dict]
gap_report: Optional[dict]
refined_report: Optional[dict]
release_decision: Optional[dict]
errors: Annotated[list[str], _concat_lists]
completed_stages: Annotated[list[str], _dedup_concat_lists]
current_stage: Annotated[str, _last_str]
stage_errors: Annotated[dict[str, list[str]], _merge_error_dicts]
stage_quality: Annotated[Optional[str], _last_optional_str]
low_confidence_count: Annotated[int, _last_int]
skipped_stages: Annotated[list[str], _concat_lists]
execution_path: Annotated[str, _last_str]
fallback_used: Annotated[bool, _last_bool]
tools_used: Annotated[list[str], _concat_lists]
analysis_mode_requested: str
analysis_mode_resolved: str
analysis_mode_resolution: dict
_cost_budget_prechecked: bool
_cost_budget_action: Optional[str]
_cost_budget_rationale: Optional[str]
_cost_budget_utilization_pct: Optional[float]
_cost_budget_mode_override: Optional[str]
_cost_budget_block: bool
_workflow_route_decisions: list[dict]
_workflow_loop_iterations: dict[str, int]
review_verdict: dict
supervisor: dict
step_llm_budget: dict
_workflow_step_llm_budgets: Annotated[dict[str, dict], _merge_dicts]
_workflow_tier_overrides: Annotated[dict[str, str], _merge_dicts]
review_retry_count: int
review_max_iterations: int
_workflow_step_outputs: Annotated[dict[str, dict], _merge_dicts]
_checkpoint_stages: list[str]
_checkpoint_replay_metadata: dict[str, dict]
workflow_plan: dict
initial_workflow_plan: dict
workflow_agent_configs: dict[str, dict]
resolved_agent_configs: dict[str, dict]
workflow_verification: dict
agent_contracts: Annotated[dict[str, dict], _merge_dicts]
schema_version: int
_fencing_token: Optional[str]
_attempt: int
stage_metrics: Annotated[dict[str, dict], _merge_dicts]
```

## backend/app/agents/summary_agent.py — SummaryAgent

[backend/app/agents/summary_agent.py:180](../../backend/app/agents/summary_agent.py#L180)

Bases: `BaseAgent`.



```python
stage_name = 'summary'
_LAYER_SCHEMAS: dict[str, type[BaseModel]] = {'incident_view': IncidentView, 'evidence_pack': EvidencePack, 'action_plan': ActionPlan}
```

## backend/app/agents/test_health_agent.py — TestHealthAgent

[backend/app/agents/test_health_agent.py:70](../../backend/app/agents/test_health_agent.py#L70)

Bases: `BaseAgent`.



```python
stage_name = 'test_health'
```

## backend/app/agents/triage_agent.py — DefectTriageAgent

[backend/app/agents/triage_agent.py:50](../../backend/app/agents/triage_agent.py#L50)

Bases: `BaseAgent`.



```python
stage_name = 'triage'
```

## backend/app/agents/workflow_compiler.py — WorkflowValidation

[backend/app/agents/workflow_compiler.py:37](../../backend/app/agents/workflow_compiler.py#L37)

Bases: ``.



```python
valid: bool
errors: tuple[str, ...]
```

## backend/app/agents/workflow_compiler.py — CompiledWorkflow

[backend/app/agents/workflow_compiler.py:43](../../backend/app/agents/workflow_compiler.py#L43)

Bases: ``.



```python
graph: StateGraph
entrypoint: str
validation: WorkflowValidation
```

## backend/app/agents/workflow_compiler.py — _FieldSpec

[backend/app/agents/workflow_compiler.py:56](../../backend/app/agents/workflow_compiler.py#L56)

Bases: ``.



```python
kind: str
enum: frozenset[str] = frozenset()
```

## backend/app/core/config.py — Settings

[backend/app/core/config.py:110](../../backend/app/core/config.py#L110)

Bases: `BaseSettings`.

Application settings — loaded from environment variables.

```python
APP_NAME: str = 'TestLookup'
APP_ENV: Literal['development', 'staging', 'production'] = 'development'
APP_SECRET_KEY: str = 'change-me-in-production'
APP_SECRET_KEY_PREVIOUS: Optional[str] = None
APP_DEBUG: bool = False
APP_VERSION: str = '0.0.1'
BUILD_REVISION: str = ''
BUILD_DATE: str = ''
CORS_ORIGINS_RAW: str = Field(default='http://localhost:3000,http://localhost:5173', validation_alias=AliasChoices('CORS_ORIGINS_RAW', 'CORS_ORIGINS'))
PUBLIC_BASE_URL: str = ''
POSTGRES_HOST: str = 'localhost'
POSTGRES_PORT: int = 5433
POSTGRES_DB: str = 'testlookup'
POSTGRES_USER: str = 'testlookup_user'
POSTGRES_PASSWORD: str = ''
DATABASE_URL: str = ''
MONGO_HOST: str = 'localhost'
MONGO_PORT: int = 27017
MONGO_DB: str = 'testlookup_logs'
MONGO_URI: str = 'mongodb://localhost:27017'
STORAGE_BACKEND: Literal['minio', 's3', 'local'] = 'minio'
LOCAL_STORAGE_PATH: str = '/tmp/testlookup_data'
MINIO_ENDPOINT: str = 'localhost:9000'
MINIO_ACCESS_KEY: str = 'testlookup_minio'
MINIO_SECRET_KEY: str = ''
MINIO_BUCKET_NAME: str = 'test-telemetry'
MINIO_USE_SSL: bool = False
REDIS_PASSWORD: str = ''
REDIS_URL: str = 'redis://localhost:6379/0'
CELERY_BROKER_URL: str = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND: str = 'redis://localhost:6379/1'
CELERY_WORKER_CONCURRENCY: int = 4
PG_POOL_SIZE: Optional[int] = Field(default=None, ge=1)
PG_MAX_OVERFLOW: Optional[int] = Field(default=None, ge=0)
PG_POOL_RECYCLE: int = Field(default=1800, ge=1)
PG_POOL_TIMEOUT: int = Field(default=30, ge=1)
PG_PROCESS_ROLE: Literal['api', 'worker', 'operation'] = 'operation'
PG_PROCESSES_PER_POD: int = Field(default=1, ge=1)
PG_FLEET_MAX_CONNECTIONS: int = Field(default=400, ge=1)
PG_FLEET_OPERATIONAL_RESERVE: int = Field(default=50, ge=1)
PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS: int = Field(default=3, ge=0)
PG_FLEET_RESERVED_CONNECTIONS: int = Field(default=0, ge=0)
PG_FLEET_MIGRATION_CONNECTIONS: int = Field(default=1, ge=1)
PG_FLEET_REQUIRED_CONNECTIONS: int = Field(default=273, ge=1)
MONGO_MAX_POOL_SIZE: int = 50
MONGO_MIN_POOL_SIZE: int = 5
MONGO_SOCKET_TIMEOUT_MS: int = 30000
MONGO_MAX_IDLE_TIME_MS: int = 60000
S3_MAX_POOL_CONNECTIONS: int = 50
INGESTION_S3_CONCURRENCY: int = 10
LLM_MAX_CONCURRENT_ANALYSES: int = 3
AGENT_INVOKE_SYNC_CONCURRENCY: int = 4
AGENT_INVOKE_SYNC_WAIT_SECONDS: int = 25
AGENT_INVOKE_STREAM_TICKET_SECONDS: int = 60
WS_MAX_CONNECTIONS_PER_PROJECT: int = 500
WS_MAX_TOTAL_CONNECTIONS: int = 5000
WS_BROADCAST_TIMEOUT: float = 5.0
INGEST_RATE_LIMIT_PER_MINUTE: int = 200
INGEST_EVENT_RATE_LIMIT_PER_MINUTE: int = 20000
MAX_ARCHIVE_UNCOMPRESSED_BYTES: int = 200 * 1024 * 1024
MAX_ARCHIVE_ENTRIES: int = 5000
MAX_ARCHIVE_ENTRY_BYTES: int = 50 * 1024 * 1024
MAX_ARCHIVE_RATIO: int = 100
FORM_URLENCODED_MAX_BYTES: int = 64 * 1024
FORM_URLENCODED_ACS_MAX_BYTES: int = 2 * 1024 * 1024
INGEST_MAX_RESULTS_PER_UPLOAD: int = MAX_RESULTS_PER_INGEST
INGEST_REDIS_MEMORY_THRESHOLD_PCT: float = 75.0
INGEST_REDIS_MEMORY_ABSOLUTE_BYTES: int = 0
LIVE_BUFFER_MAX_EVENTS_PER_RUN: int = 50000
PERSIST_LIVE_BULK_INSERT_CHUNK: int = 1000
LIVE_INGEST_SHARD_COUNT: int = 8
LIVE_SESSION_DRAIN_ENABLED: bool = True
LIVE_SESSION_DRAIN_BATCH_SIZE: int = 5000
AI_PIPELINE_DEADLINE_SECONDS: int = 1500
AGENT_PIPELINE_ALERT_GRACE_SECONDS: int = 300
HIGH_VOLUME_AUTO_DETECT_ENABLED: bool = True
HIGH_VOLUME_TESTS_PER_MINUTE: int = 1000
HIGH_VOLUME_CONSECUTIVE_MINUTES: int = 3
HIGH_VOLUME_SAMPLE_EVERY_N: int = 2
INGESTION_DLQ_ENABLED: bool = True
CANONICAL_DELETION_WINDOW_RUNS: int = 5
LLM_PROVIDER: Literal['ollama', 'lmstudio', 'localai', 'vllm', 'openai', 'gemini', 'anthropic', 'openrouter'] = 'ollama'
LLM_MODEL: str = 'qwen2.5:3b-instruct-q5_K_M'
LLM_TEMPERATURE: float = 0.1
LLM_MAX_TOKENS: int = 4096
OLLAMA_BASE_URL: str = 'http://localhost:11434'
OLLAMA_NUM_CTX: int = 8192
LMSTUDIO_BASE_URL: str = 'http://localhost:1234/v1'
LOCALAI_BASE_URL: str = 'http://localhost:8080/v1'
VLLM_BASE_URL: str = 'http://localhost:8000/v1'
OPENAI_API_KEY: Optional[str] = None
GOOGLE_API_KEY: Optional[str] = None
ANTHROPIC_API_KEY: Optional[str] = None
OPENROUTER_API_KEY: Optional[str] = None
OPENROUTER_BASE_URL: str = 'https://openrouter.ai/api/v1'
OPENROUTER_SITE_URL: str = 'https://github.com/anandtopu/testlookup'
OPENROUTER_APP_NAME: str = 'TestLookup'
LLM_PRICE_OVERRIDES: Optional[str] = None
EMBEDDING_PROVIDER: str = 'ollama'
EMBEDDING_MODEL: str = 'nomic-embed-text:v1.5'
CHROMA_HOST: str = 'localhost'
CHROMA_PORT: int = 8001
CHROMA_COLLECTION: str = 'testlookup_embeddings'
CHROMA_ONNX_MODEL_DIR: str = ''
AI_OFFLINE_MODE: bool = True
REVIEW_GATE_ENFORCED: bool = False
AI_LLM_PROVIDER_ALLOWLIST: str = ''
AI_LLM_ALLOWED_BASE_URLS: str = ''
OFFLINE_NOTIFICATION_ALLOWED_HOSTS: str = ''
OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS: str = ''
AGENT_MEMORY_RETENTION_DAYS: int = 365
AI_CONFIDENCE_THRESHOLD: int = 80
AIQ_GAP_REFINEMENT_ENABLED: bool = False
AIQ_CONTRACT_VALIDATION_ENABLED: bool = False
AIQ_CHANGE_OWNERSHIP_ENABLED: bool = False
AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED: bool = False
AI_REPORT_EVAL_ALLOW_CALLER_CORPUS: bool = False
AI_MAX_RETRIES: int = 3
AI_TIMEOUT_SECONDS: int = 300
AGENT_PIPELINE_MAX_ATTEMPTS: int = 5
AGENT_MAX_ATTEMPTS_CEILING: int = 10
AGENT_MAX_TIMEOUT_CEILING: int = 600
AGENT_RETRY_BASE_SECONDS: int = 30
AGENT_RETRY_CAP_SECONDS: int = 600
LLM_CLUSTER_MAX_CONCURRENT: int = 4
LLM_CLUSTER_SLOT_LEASE_SECONDS: int = 60
AI_ANALYSIS_CACHE_TTL: int = 3600
SEMANTIC_SIMILARITY_THRESHOLD: float = 0.85
PROMPT_OVERHEAD_TOKENS: int = 1500
SEMANTIC_CACHE_MAX_DOCUMENTS: int = 10000
JIRA_ENABLED: bool = False
JIRA_DOMAIN: Optional[str] = None
JIRA_EMAIL: Optional[str] = None
JIRA_API_TOKEN: Optional[str] = None
JIRA_DEFAULT_PROJECT_KEY: str = 'QA'
JIRA_WEBHOOK_SECRET: Optional[str] = None
CONFLUENCE_ENABLED: bool = False
CONFLUENCE_DOMAIN: Optional[str] = None
CONFLUENCE_EMAIL: Optional[str] = None
CONFLUENCE_API_TOKEN: Optional[str] = None
SPLUNK_ENABLED: bool = False
SPLUNK_BASE_URL: Optional[str] = None
SPLUNK_API_TOKEN: Optional[str] = None
SPLUNK_INDEX: str = 'main'
OCP_ENABLED: bool = False
OCP_API_URL: Optional[str] = None
OCP_SA_TOKEN: Optional[str] = None
OCP_DEFAULT_NAMESPACE: str = 'qa-testing'
SLACK_ENABLED: bool = False
SLACK_BOT_TOKEN: Optional[str] = None
SLACK_WEBHOOK_URL: Optional[str] = None
SLACK_DEFAULT_CHANNEL: str = '#qa-alerts'
TEAMS_ENABLED: bool = False
TEAMS_WEBHOOK_URL: Optional[str] = None
SMTP_ENABLED: bool = False
SMTP_HOST: str = 'localhost'
SMTP_PORT: int = 587
SMTP_USER: Optional[str] = None
SMTP_PASSWORD: Optional[str] = None
SMTP_FROM: str = 'noreply@testlookup.io'
SMTP_TLS: bool = True
SEARCH_INDEX_BATCH_SIZE: int = 200
SEARCH_INDEX_INCREMENTAL_LIMIT: int = 5000
SEARCH_QUERY_TIMEOUT_MS: int = 5000
SEARCH_MAX_RESULTS: int = 200
DEV_AUTO_LOGIN_ENABLED: bool = True
JWT_SECRET_KEY: str = 'change-me-jwt-secret'
JWT_ALGORITHM: str = 'HS256'
JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
AUTH_REVOCATION_FAIL_OPEN: bool = False
MFA_ISSUER_NAME: str = 'TestLookup'
MFA_CHALLENGE_TTL_SECONDS: int = 300
MFA_RECOVERY_CODE_COUNT: int = 10
MFA_BREAKGLASS_ENABLED: bool = False
SSO_ENABLED: bool = False
SCIM_ENABLED: bool = False
SAML_SP_ENTITY_ID: str = 'https://testlookup.io/saml/metadata'
SAML_BASE_URL: str = 'http://localhost:8000'
SSO_ADMIN_FALLBACK_ENABLED: bool = True
SSO_ALLOW_PRIVATE_IDP_ENDPOINTS: bool = False
SAML_ALLOW_IDP_INITIATED: bool = False
SAML_CLOCK_SKEW_SECONDS: int = 60
SSO_MAX_PROVISIONED_ROLE: str = 'ADMIN'
FINETUNE_ENABLED: bool = False
FINETUNE_CLASSIFIER_MIN_EXAMPLES: int = 500
FINETUNE_REASONING_MIN_EXAMPLES: int = 2000
FINETUNE_EMBED_MIN_PAIRS: int = 1000
FINETUNE_INCREMENTAL_TRIGGER: int = 200
FINETUNE_EVAL_HOLDOUT: float = 0.1
FINETUNE_MIN_ACCURACY_GAIN: float = 0.02
FINETUNE_EXPORT_BUCKET: str = 'training-data'
FINETUNE_OPENAI_SUFFIX: str = 'testlookup'
CLASSIFIER_CONFIDENCE_THRESHOLD: int = 85
CLASSIFIER_MODEL: Optional[str] = None
DEEP_INVESTIGATION_ENABLED: bool = True
RELEASE_PASS_RATE_THRESHOLD: float = 90.0
KNOWLEDGE_RAG_ENABLED: bool = False
KNOWLEDGE_SYNC_TIMEOUT_SECONDS: int = 60
KNOWLEDGE_MAX_SOURCES_PER_PROJECT: int = 100
KNOWLEDGE_DOCS_BUCKET: str = 'knowledge-docs'
KNOWLEDGE_STALE_THRESHOLD_JIRA_HOURS: int = 24
KNOWLEDGE_STALE_THRESHOLD_URL_HOURS: int = 168
KNOWLEDGE_CHUNK_TARGET_TOKENS: int = 400
KNOWLEDGE_CHUNK_MAX_TOKENS: int = 800
KNOWLEDGE_CHUNK_OVERLAP_TOKENS: int = 50
KNOWLEDGE_RESYNC_BATCH_CAP: int = 50
KNOWLEDGE_SYNC_STUCK_MINUTES: int = 45
ANALYSIS_MODE: str = 'auto'
ML_MODEL_DIR: str = 'models'
ML_MODEL_SYNC_ENABLED: bool = False
ML_MODEL_STORE_PREFIX: str = 'ml-models/'
ML_MIN_TRAINING_SAMPLES: int = 200
ML_RETRAIN_ENABLED: bool = True
ML_ACCURACY_THRESHOLD: float = 0.8
ML_PSEUDO_LABEL_CAP: float = 0.3
ML_PSEUDO_LABEL_WEIGHT: float = 0.3
ML_HUMAN_LABEL_FLOOR: int = 50
ANOMALY_REGRESSION_THRESHOLD: float = 10.0
ANOMALY_MIN_HISTORY_RUNS: int = 3
ANOMALY_FLAKY_WINDOW_RUNS: int = 10
ANOMALY_FLAKY_LOOKBACK_DAYS: int = 30
ANOMALY_PERF_HISTORY_DAYS: int = 30
ANOMALY_MIN_PERF_SAMPLES: int = 5
ANOMALY_PERF_SPIKE_MULTIPLIER: float = 2.0
ANOMALY_PERF_MIN_ABSOLUTE_DELTA_MS: int = 1000
ANOMALY_SUMMARY_MAX_ITEMS: int = 8
RISK_WEIGHT_USER_IMPACT: float = 0.25
RISK_WEIGHT_ENV_SENSITIVITY: float = 0.1
RISK_WEIGHT_REPRODUCIBILITY: float = 0.15
RISK_WEIGHT_REGRESSION_LIKELY: float = 0.2
RISK_WEIGHT_HIST_RECURRENCE: float = 0.1
RISK_WEIGHT_BLAST_RADIUS: float = 0.15
RISK_WEIGHT_DIAGNOSIS_CONF: float = 0.05
PROMETHEUS_URL: Optional[str] = None
GITHUB_TOKEN: Optional[str] = None
GITHUB_REPO: Optional[str] = None
COMMIT_RANGE_FILE_FETCH_LIMIT: int = 25
HTTP_VERIFY_TLS: bool = True
HTTP_CA_BUNDLE: Optional[str] = None
WEBHOOK_SECRET: str = 'change-me-webhook-secret'
TRUSTED_PROXY_IPS: str = ''
LIVE_EVENTS_REQUIRE_PROJECT_KEY: bool = True
OTEL_ENABLED: bool = True
OTEL_SERVICE_NAME: str = 'testlookup'
OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
METRICS_ENABLED: bool = True
LOG_LEVEL: str = 'INFO'
LOG_FORMAT: Literal['json', 'text'] = 'json'
model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', case_sensitive=True, extra='ignore', env_ignore_empty=True, populate_by_name=True)
```

- Validator/serializer `_validate_llm_cluster_slot_lease`: [backend/app/core/config.py:614](../../backend/app/core/config.py#L614). Read source for the cross-field or conversion rule.
- Validator/serializer `_validate_sso_max_provisioned_role`: [backend/app/core/config.py:629](../../backend/app/core/config.py#L629). Read source for the cross-field or conversion rule.
## backend/app/core/deps.py — AuthorizedTestCaseContext

[backend/app/core/deps.py:45](../../backend/app/core/deps.py#L45)

Bases: ``.

Server-resolved ownership for a test-case scoped operation.

```python
test_case: object
test_run: object
run_id: uuid.UUID
project_id: uuid.UUID
```

## backend/app/core/deps.py — ApiKeyGrant

[backend/app/core/deps.py:196](../../backend/app/core/deps.py#L196)

Bases: ``.

What the API key that authenticated this request was granted.

Stashed on the loaded ``User`` like the project binding, by
``_validate_api_key``; ``None`` for a JWT.

```python
key_id: uuid.UUID
scopes: tuple[str, ...]
expires_at: datetime | None
```

## backend/app/core/deps.py — ApiKeyContext

[backend/app/core/deps.py:451](../../backend/app/core/deps.py#L451)

Bases: ``.

Internal result of API key validation — carries project scope.

```python
user: User
project_id: uuid.UUID | None
```

## backend/app/core/deps.py — StreamingApiKeyContext

[backend/app/core/deps.py:606](../../backend/app/core/deps.py#L606)

Bases: ``.

Context for the API-key-only streaming ingest endpoint.

Distinct from ``ApiKeyContext`` because streaming has stricter requirements:
project-scoped key + ``stream:write`` scope. Carries the api_key id/name so
the server can label auto-created live sessions with a recognisable client.

```python
user: User
project_id: uuid.UUID
api_key_id: uuid.UUID
api_key_name: str
```

## backend/app/core/scim_errors.py — SCIMJSONResponse

[backend/app/core/scim_errors.py:18](../../backend/app/core/scim_errors.py#L18)

Bases: `JSONResponse`.

JSON response using the media type required by SCIM 2.0.

```python
media_type = SCIM_MEDIA_TYPE
```

## backend/app/db/mongo.py — Collections

[backend/app/db/mongo.py:131](../../backend/app/db/mongo.py#L131)

Bases: ``.



```python
RAW_ALLURE_JSON = 'raw_allure_json'
RAW_TESTNG_XML = 'raw_testng_xml'
REST_API_PAYLOADS = 'rest_api_payloads'
EXECUTION_LOGS = 'execution_logs'
AI_ANALYSIS_PAYLOADS = 'ai_analysis_payloads'
OCP_POD_EVENTS = 'ocp_pod_events'
RUN_SUMMARIES = 'run_summaries'
DECISION_EVIDENCE_SNAPSHOTS = 'decision_evidence_snapshots'
DECISION_REPORTS = 'decision_reports'
DECISION_REPORT_ATTEMPTS = 'decision_report_attempts'
LIVE_EXECUTION_EVENTS = 'live_execution_events'
```

## backend/app/db/postgres.py — _SessionFactoryProxy

[backend/app/db/postgres.py:187](../../backend/app/db/postgres.py#L187)

Bases: ``.

Callable that resolves the CURRENT session factory on every call.

Why this exists (F-027). ``__getattr__`` below runs **once** per importing
module, so ``from app.db.postgres import AsyncSessionLocal`` at MODULE level
binds whatever object it returned at first import — permanently, into that
module's namespace.

That is fine in the API process, whose engine is long-lived. It was a bug
in the Celery worker, whose task wrapper disposed the engine and cleared
both ``@lru_cache``es at the end of every task, so from the second
task onward a module-level binding still pointed at the factory of the
**disposed** engine. Modules importing inside a function (``tasks.py``)
re-resolved and got a fresh factory; modules importing at module level
(``ingestion_pipeline.py``, ``ingestion.py``, and ~38 others) did not.
Since re-audit M1 a worker keeps its engine across tasks, but it still
disposes it when an interrupted task discards the loop, and clears the
caches after fork, so the proxy still matters.

The observable result was a 100%-reproducible failure at ``finalize_run``'s
first query -- ``asyncpg InterfaceError: cannot perform operation: another
operation is in progress`` -- 162 times across 32 bulk-ingest tasks, while
the same tasks logged 32 clean engine builds and 32 successful disposes.
Teardown was never the problem; a stale *reference* surviving it was.

Returning a proxy instead of the factory makes the module-level binding
stable and the resolution late, fixing every call site without editing any
of them. Every usage in the codebase is a plain ``AsyncSessionLocal(...)``
call, which is all this needs to support.

```python
__slots__ = ()
```

## backend/app/models/agent_contracts.py — AgentContractMetadata

[backend/app/models/agent_contracts.py:32](../../backend/app/models/agent_contracts.py#L32)

Bases: `BaseModel`.



```python
schema_version: int = AGENT_CONTRACT_SCHEMA_VERSION
agent_name: str
agent_version: str = 'v1'
fallback_used: bool = False
confidence: Optional[int] = None
confidence_score: int = 0
evidence_count: int = 0
evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
decision_reason: str = ''
confidence_breakdown: Optional[dict[str, Any]] = None
output_keys: list[str] = Field(default_factory=list)
generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
```

- Validator/serializer `_clamp_confidence_score`: [backend/app/models/agent_contracts.py:50](../../backend/app/models/agent_contracts.py#L50). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — DecisionClaimV1

[backend/app/models/agent_contracts.py:55](../../backend/app/models/agent_contracts.py#L55)

Bases: `BaseModel`.

Typed, evidence-aware claim surfaced by the terminal decision report.

```python
model_config = ConfigDict(extra='forbid')
claim_id: str = Field(min_length=1, max_length=160)
kind: Literal['fact', 'inference', 'unknown', 'recommendation']
text: str = Field(min_length=1, max_length=500)
confidence: float = Field(ge=0.0, le=1.0)
confidence_basis: str = Field(min_length=1, max_length=500)
evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
counter_evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
source_stage: str = Field(min_length=1, max_length=80)
freshness: str | None = Field(default=None, max_length=80)
hypothesis: bool = False
```

## backend/app/models/agent_contracts.py — ProposedActionV1

[backend/app/models/agent_contracts.py:72](../../backend/app/models/agent_contracts.py#L72)

Bases: `BaseModel`.

Approval-aware action proposal; never an execution receipt.

```python
model_config = ConfigDict(extra='forbid')
action_id: str = Field(min_length=1, max_length=160)
title: str = Field(min_length=1, max_length=240)
owner: str = Field(min_length=1, max_length=120)
rationale: str = Field(min_length=1, max_length=500)
evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
risk: Literal['low', 'medium', 'high', 'unknown'] = 'unknown'
required_permission: str = Field(min_length=1, max_length=120)
idempotency_key: str = Field(min_length=1, max_length=160)
status: Literal['proposed'] = 'proposed'
```

## backend/app/models/agent_contracts.py — ContractedAgentOutput

[backend/app/models/agent_contracts.py:86](../../backend/app/models/agent_contracts.py#L86)

Bases: `BaseModel`.



```python
contract: AgentContractMetadata
```

## backend/app/models/agent_contracts.py — ReviewReferenceSetV1

[backend/app/models/agent_contracts.py:90](../../backend/app/models/agent_contracts.py#L90)

Bases: `BaseModel`.

Identifiers the reviewed output is allowed to cite.

```python
model_config = ConfigDict(extra='forbid')
test_case_ids: list[str] = Field(default_factory=list)
cluster_ids: list[str] = Field(default_factory=list)
artifact_ids: list[str] = Field(default_factory=list)
```

## backend/app/models/agent_contracts.py — ReviewedStepV1

[backend/app/models/agent_contracts.py:100](../../backend/app/models/agent_contracts.py#L100)

Bases: `BaseModel`.

One completed step and the policy context needed to review it.

```python
model_config = ConfigDict(extra='forbid')
step_name: str = Field(min_length=1, max_length=80)
output: dict[str, Any]
mode: Literal['shadow', 'suggest', 'act'] = 'shadow'
tools_used: list[str] = Field(default_factory=list)
tool_permissions: dict[str, Literal['read_only', 'propose_action', 'mutating']] = Field(default_factory=dict)
model_tier: Optional[Literal['deterministic', 'slm', 'llm']] = None
model_provider: Optional[str] = Field(default=None, min_length=1, max_length=80)
model_name: Optional[str] = Field(default=None, min_length=1, max_length=160)
```

## backend/app/models/agent_contracts.py — ReviewerInputV1

[backend/app/models/agent_contracts.py:117](../../backend/app/models/agent_contracts.py#L117)

Bases: `BaseModel`.

Deterministic evidence supplied to the generic reviewer.

```python
model_config = ConfigDict(extra='forbid')
reviewed_steps: list[ReviewedStepV1] = Field(min_length=1, max_length=20)
references: ReviewReferenceSetV1 = Field(default_factory=ReviewReferenceSetV1)
numeric_facts: dict[str, float] = Field(default_factory=dict)
numeric_tolerance: float = Field(default=0.01, ge=0.0, le=1.0)
run_data: dict[str, Any] = Field(default_factory=dict)
analyses: dict[str, dict[str, Any]] = Field(default_factory=dict)
```

- Validator/serializer `_step_names_are_unique`: [backend/app/models/agent_contracts.py:130](../../backend/app/models/agent_contracts.py#L130). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — ReviewCheckV1

[backend/app/models/agent_contracts.py:139](../../backend/app/models/agent_contracts.py#L139)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
family: Literal[1, 2, 3, 4, 5]
name: str = Field(min_length=1, max_length=120)
passed: bool
severity: Literal['info', 'warning', 'blocking']
detail: str = Field(default='', max_length=1000)
step_name: Optional[str] = Field(default=None, max_length=80)
offending_refs: list[str] = Field(default_factory=list, max_length=100)
```

## backend/app/models/agent_contracts.py — ReviewDisagreementV1

[backend/app/models/agent_contracts.py:151](../../backend/app/models/agent_contracts.py#L151)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
kind: str = Field(min_length=1, max_length=120)
agents: list[str] = Field(min_length=1, max_length=20)
resolution: str = Field(min_length=1, max_length=500)
severity: Literal['warning', 'blocking'] = 'warning'
```

## backend/app/models/agent_contracts.py — SecondModelCheckV1

[backend/app/models/agent_contracts.py:160](../../backend/app/models/agent_contracts.py#L160)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
provider: str = Field(min_length=1, max_length=80)
model: str = Field(min_length=1, max_length=160)
agreement_score: float = Field(ge=0.0, le=1.0)
```

## backend/app/models/agent_contracts.py — ReviewVerdictV1

[backend/app/models/agent_contracts.py:168](../../backend/app/models/agent_contracts.py#L168)

Bases: `BaseModel`.

Fail-closed result consumed by the workflow supervisor.

```python
model_config = ConfigDict(extra='forbid')
reviewed_steps: list[str] = Field(min_length=1, max_length=20)
checks: list[ReviewCheckV1]
verdict: Literal['pass', 'pass_with_flags', 'retry', 'reject']
disagreements: list[ReviewDisagreementV1] = Field(default_factory=list)
hallucination_risk: Literal['low', 'medium', 'high']
requires_human_review: bool
second_model: Optional[SecondModelCheckV1] = None
```

- Validator/serializer `_verdict_is_consistent`: [backend/app/models/agent_contracts.py:182](../../backend/app/models/agent_contracts.py#L182). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — ReviewerSupervisorDecisionV1

[backend/app/models/agent_contracts.py:224](../../backend/app/models/agent_contracts.py#L224)

Bases: `BaseModel`.

Deterministic routing instruction emitted for the workflow compiler.

```python
model_config = ConfigDict(extra='forbid')
route: Literal['continue', 'retry', 'finalize']
reviewed_steps: list[str] = Field(min_length=1, max_length=20)
tier_override: Optional[Literal['llm']] = None
status: Optional[Literal['failed']] = None
error_code: Optional[Literal['validation_failed']] = None
requires_human_review: bool = False
retry_count: int = Field(default=0, ge=0, le=1)
```

- Validator/serializer `_route_fields_are_consistent`: [backend/app/models/agent_contracts.py:238](../../backend/app/models/agent_contracts.py#L238). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — ReviewerAgentOutput

[backend/app/models/agent_contracts.py:254](../../backend/app/models/agent_contracts.py#L254)

Bases: `ContractedAgentOutput`.

Workflow-state envelope for a contracted reviewer verdict.

```python
review_verdict: ReviewVerdictV1
supervisor: ReviewerSupervisorDecisionV1
step_llm_budget: dict[str, Any]
```

## backend/app/models/agent_contracts.py — GapReason

[backend/app/models/agent_contracts.py:269](../../backend/app/models/agent_contracts.py#L269)

Bases: `str, Enum`.



```python
UNANALYZED = 'unanalyzed'
ERRORED = 'errored'
INCONCLUSIVE = 'inconclusive'
NO_EVIDENCE = 'no_evidence'
LOW_CONFIDENCE = 'low_confidence'
```

## backend/app/models/agent_contracts.py — GapItem

[backend/app/models/agent_contracts.py:277](../../backend/app/models/agent_contracts.py#L277)

Bases: `BaseModel`.

One coverage/quality gap for a single failed test. Structural only.

```python
model_config = ConfigDict(extra='ignore')
test_id: str = ''
reason: GapReason = GapReason.UNANALYZED
detail: str = ''
bucket: Literal['analyzed', 'skipped', 'errored'] = 'skipped'
```

- Validator/serializer `_coerce_test_id`: [backend/app/models/agent_contracts.py:289](../../backend/app/models/agent_contracts.py#L289). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_reason`: [backend/app/models/agent_contracts.py:296](../../backend/app/models/agent_contracts.py#L296). Read source for the cross-field or conversion rule.
- Validator/serializer `_truncate_detail`: [backend/app/models/agent_contracts.py:311](../../backend/app/models/agent_contracts.py#L311). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — ContradictionType

[backend/app/models/agent_contracts.py:318](../../backend/app/models/agent_contracts.py#L318)

Bases: `str, Enum`.



```python
FLAKY_VS_REGRESSION = 'flaky_vs_regression'
CATEGORY_DISAGREEMENT = 'category_disagreement'
CONFIDENCE_SPLIT = 'confidence_split'
```

## backend/app/models/agent_contracts.py — ResolutionStrategy

[backend/app/models/agent_contracts.py:324](../../backend/app/models/agent_contracts.py#L324)

Bases: `str, Enum`.



```python
PREFER_ANALYSIS = 'prefer_analysis'
PREFER_ANOMALY = 'prefer_anomaly'
MERGE = 'merge'
FLAG_FOR_REVIEW = 'flag_for_review'
```

## backend/app/models/agent_contracts.py — Contradiction

[backend/app/models/agent_contracts.py:331](../../backend/app/models/agent_contracts.py#L331)

Bases: `BaseModel`.

A cross-route disagreement for a single multi-route test. Structural only.

```python
model_config = ConfigDict(extra='ignore')
test_id: str = ''
type: ContradictionType = ContradictionType.CATEGORY_DISAGREEMENT
routes: list[str] = Field(default_factory=list)
resolution: ResolutionStrategy = ResolutionStrategy.FLAG_FOR_REVIEW
detail: str = ''
applied: bool = False
```

- Validator/serializer `_coerce_test_id`: [backend/app/models/agent_contracts.py:357](../../backend/app/models/agent_contracts.py#L357). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_type`: [backend/app/models/agent_contracts.py:364](../../backend/app/models/agent_contracts.py#L364). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_resolution`: [backend/app/models/agent_contracts.py:379](../../backend/app/models/agent_contracts.py#L379). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_routes`: [backend/app/models/agent_contracts.py:394](../../backend/app/models/agent_contracts.py#L394). Read source for the cross-field or conversion rule.
- Validator/serializer `_truncate_detail`: [backend/app/models/agent_contracts.py:402](../../backend/app/models/agent_contracts.py#L402). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — IngestionAgentOutput

[backend/app/models/agent_contracts.py:409](../../backend/app/models/agent_contracts.py#L409)

Bases: `ContractedAgentOutput`.



```python
test_run_data: Optional[dict[str, Any]] = None
branch: Optional[str] = None
failed_test_ids: list[str] = Field(default_factory=list)
run_input_fingerprint: Optional[str] = None
total_tests: int = 0
pass_rate: float = 0.0
ingestion_enriched: bool = False
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
current_stage: str = 'anomaly_detection'
```

## backend/app/models/agent_contracts.py — ClusterAgentOutput

[backend/app/models/agent_contracts.py:426](../../backend/app/models/agent_contracts.py#L426)

Bases: `ContractedAgentOutput`.



```python
failure_clusters: list[dict[str, Any]] = Field(default_factory=list)
cluster_map: dict[str, str] = Field(default_factory=dict)
```

## backend/app/models/agent_contracts.py — ContractAgentOutput

[backend/app/models/agent_contracts.py:431](../../backend/app/models/agent_contracts.py#L431)

Bases: `ContractedAgentOutput`.

Bounded API-contract specialist result (AIQ-P4).

```python
status: Literal['complete', 'not_enough_evidence', 'failed'] = 'not_enough_evidence'
violations: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
violation_count: int = Field(default=0, ge=0)
critical_count: int = Field(default=0, ge=0)
drift_count: int = Field(default=0, ge=0)
endpoints_checked: list[str] = Field(default_factory=list, max_length=200)
evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
summary: str = Field(default='', max_length=2000)
suggests_product_bug: bool = False
```

## backend/app/models/agent_contracts.py — AnomalyDetectionAgentOutput

[backend/app/models/agent_contracts.py:445](../../backend/app/models/agent_contracts.py#L445)

Bases: `ContractedAgentOutput`.



```python
anomalies: list[dict[str, Any]] = Field(default_factory=list)
is_regression: bool = False
regression_tests: list[str] = Field(default_factory=list)
anomaly_summary: Optional[str] = None
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
current_stage: str = 'root_cause_analysis'
```

## backend/app/models/agent_contracts.py — AnalysisAgentOutput

[backend/app/models/agent_contracts.py:455](../../backend/app/models/agent_contracts.py#L455)

Bases: `ContractedAgentOutput`.



```python
analyses: dict[str, dict[str, Any]] = Field(default_factory=dict)
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
stage_errors: dict[str, list[str]] = Field(default_factory=dict)
stage_quality: Optional[str] = None
low_confidence_count: int = 0
current_stage: str = 'summary'
```

## backend/app/models/agent_contracts.py — SummaryAgentOutput

[backend/app/models/agent_contracts.py:465](../../backend/app/models/agent_contracts.py#L465)

Bases: `ContractedAgentOutput`.



```python
executive_summary: Optional[str] = None
summary_markdown: Optional[str] = None
structured_summary: Optional[dict[str, Any]] = None
summary_provenance: Optional[dict[str, Any]] = None
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
current_stage: str = 'triage'
```

## backend/app/models/agent_contracts.py — DecisionReportAgentOutput

[backend/app/models/agent_contracts.py:475](../../backend/app/models/agent_contracts.py#L475)

Bases: `ContractedAgentOutput`.



```python
structured_summary: Optional[dict[str, Any]] = None
summary_markdown: Optional[str] = None
decision_intelligence: Optional[dict[str, Any]] = None
decision_evidence_snapshot: Optional[dict[str, Any]] = None
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
current_stage: str = 'decision_report_critic'
```

## backend/app/models/agent_contracts.py — DecisionReportCriticAgentOutput

[backend/app/models/agent_contracts.py:485](../../backend/app/models/agent_contracts.py#L485)

Bases: `ContractedAgentOutput`.



```python
structured_summary: Optional[dict[str, Any]] = None
summary_markdown: Optional[str] = None
decision_intelligence: Optional[dict[str, Any]] = None
decision_report_verification: Optional[dict[str, Any]] = None
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
current_stage: str = 'done'
```

## backend/app/models/agent_contracts.py — DefectTriageAgentOutput

[backend/app/models/agent_contracts.py:495](../../backend/app/models/agent_contracts.py#L495)

Bases: `ContractedAgentOutput`.



```python
triage_results: list[dict[str, Any]] = Field(default_factory=list)
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
current_stage: str = 'done'
```

## backend/app/models/agent_contracts.py — FlakySentinelAgentOutput

[backend/app/models/agent_contracts.py:502](../../backend/app/models/agent_contracts.py#L502)

Bases: `ContractedAgentOutput`.



```python
flaky_findings: list[dict[str, Any]] = Field(default_factory=list)
```

## backend/app/models/agent_contracts.py — TestHealthAgentOutput

[backend/app/models/agent_contracts.py:506](../../backend/app/models/agent_contracts.py#L506)

Bases: `ContractedAgentOutput`.



```python
test_health_findings: list[dict[str, Any]] = Field(default_factory=list)
```

## backend/app/models/agent_contracts.py — ReleaseRiskAgentOutput

[backend/app/models/agent_contracts.py:510](../../backend/app/models/agent_contracts.py#L510)

Bases: `ContractedAgentOutput`.



```python
release_decision: Optional[dict[str, Any]] = None
```

## backend/app/models/agent_contracts.py — RunCompareAgentOutput

[backend/app/models/agent_contracts.py:514](../../backend/app/models/agent_contracts.py#L514)

Bases: `ContractedAgentOutput`.



```python
model_config = ConfigDict(extra='allow')
status: str = 'ready'
executive_summary: Optional[str] = None
risk_level: Optional[str] = None
key_differences: list[str] = Field(default_factory=list)
new_risks: list[str] = Field(default_factory=list)
resolved_risks: list[str] = Field(default_factory=list)
duration_concerns: list[str] = Field(default_factory=list)
recommended_actions: list[str] = Field(default_factory=list)
confidence: int = 0
confidence_reason: str = ''
fallback_used: bool = False
markdown_report: Optional[str] = None
```

## backend/app/models/agent_contracts.py — LogIntelligenceAgentOutput

[backend/app/models/agent_contracts.py:530](../../backend/app/models/agent_contracts.py#L530)

Bases: `ContractedAgentOutput`.



```python
model_config = ConfigDict(extra='allow')
distributed_trace: dict[str, Any] = Field(default_factory=dict)
log_anomaly: dict[str, Any] = Field(default_factory=dict)
log_summary: str = ''
```

## backend/app/models/agent_contracts.py — RegressionWatchmanAgentOutput

[backend/app/models/agent_contracts.py:540](../../backend/app/models/agent_contracts.py#L540)

Bases: `ContractedAgentOutput`.



```python
model_config = ConfigDict(extra='allow')
regression_classification: dict[str, Any] = Field(default_factory=dict)
completed_stages: list[str] = Field(default_factory=list)
errors: list[str] = Field(default_factory=list)
current_stage: str = 'defect_commander'
```

## backend/app/models/agent_contracts.py — ChangeOwnershipAgentOutput

[backend/app/models/agent_contracts.py:551](../../backend/app/models/agent_contracts.py#L551)

Bases: `ContractedAgentOutput`.

Deterministic baseline/change and ownership specialist result.

```python
status: Literal['complete', 'not_enough_evidence', 'failed'] = 'not_enough_evidence'
baseline_diff: dict[str, Any] = Field(default_factory=dict)
ownership_resolutions: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
summary: str = Field(default='', max_length=240)
```

## backend/app/models/agent_contracts.py — GapReport

[backend/app/models/agent_contracts.py:560](../../backend/app/models/agent_contracts.py#L560)

Bases: `BaseModel`.

Nested coverage/integrity report matching the gap_detection payload.

```python
model_config = ConfigDict(extra='ignore')
failed_count: int = Field(default=0, ge=0)
analyzed_count: int = Field(default=0, ge=0)
skipped_count: int = Field(default=0, ge=0)
errored_count: int = Field(default=0, ge=0)
coverage_ratio: float = 0.0
integrity_ok: bool = True
gaps: list[GapItem] = Field(default_factory=list)
inconclusive_count: int = Field(default=0, ge=0)
no_evidence_count: int = Field(default=0, ge=0)
```

- Validator/serializer `_recompute_invariants`: [backend/app/models/agent_contracts.py:576](../../backend/app/models/agent_contracts.py#L576). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — GapDetectionAgentOutput

[backend/app/models/agent_contracts.py:592](../../backend/app/models/agent_contracts.py#L592)

Bases: `ContractedAgentOutput`.



```python
gap_report: GapReport = Field(default_factory=GapReport)
completed_stages: list[str] = Field(default_factory=list)
current_stage: str = 'report_refinement'
```

## backend/app/models/agent_contracts.py — RefinedReport

[backend/app/models/agent_contracts.py:598](../../backend/app/models/agent_contracts.py#L598)

Bases: `BaseModel`.

Nested dedup/contradiction report matching the report_refinement payload.

```python
model_config = ConfigDict(extra='ignore')
dedup_count: int = Field(default=0, ge=0)
contradictions_resolved: int = Field(default=0, ge=0)
contradictions: list[Contradiction] = Field(default_factory=list)
reconciled_tests: dict[str, dict[str, Any]] = Field(default_factory=dict)
multi_route_test_ids: list[str] = Field(default_factory=list)
unresolved_count: int = Field(default=0, ge=0)
```

- Validator/serializer `_recompute_resolution_counts`: [backend/app/models/agent_contracts.py:611](../../backend/app/models/agent_contracts.py#L611). Read source for the cross-field or conversion rule.
## backend/app/models/agent_contracts.py — ReportRefinementAgentOutput

[backend/app/models/agent_contracts.py:632](../../backend/app/models/agent_contracts.py#L632)

Bases: `ContractedAgentOutput`.



```python
refined_report: RefinedReport = Field(default_factory=RefinedReport)
completed_stages: list[str] = Field(default_factory=list)
current_stage: str = 'flaky_sentinel'
```

## backend/app/models/agent_contracts.py — InvestigatorHypothesisV1

[backend/app/models/agent_contracts.py:655](../../backend/app/models/agent_contracts.py#L655)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
id: Literal['infra', 'commit', 'environment', 'known_flaky', 'regression']
title: str = Field(min_length=1, max_length=300)
status: Literal['validated', 'invalidated', 'inconclusive', 'pending']
confidence: int = Field(ge=0, le=100)
confidence_basis: Literal['heuristic_estimate', 'llm_weighted']
summary: str = Field(max_length=1000)
evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
signals: dict[str, Any] = Field(default_factory=dict)
started_at: Optional[str] = None
completed_at: Optional[str] = None
llm_enrichment_stop_reason: Optional[InvestigatorStopReason] = None
```

## backend/app/models/agent_contracts.py — InvestigatorDegradationV1

[backend/app/models/agent_contracts.py:671](../../backend/app/models/agent_contracts.py#L671)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
degraded: bool = False
budget_exhausted: bool = False
hypotheses_with_stops: list[str] = Field(default_factory=list, max_length=5)
synthesis_stop_reason: Optional[InvestigatorStopReason] = None
```

## backend/app/models/agent_contracts.py — InvestigatorVerdictV1

[backend/app/models/agent_contracts.py:680](../../backend/app/models/agent_contracts.py#L680)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
primary_cause: Literal['infra', 'commit', 'environment', 'known_flaky', 'regression', 'unknown']
narrative: str = Field(min_length=1, max_length=4000)
confidence: int = Field(ge=0, le=100)
recommended_actions: list[str] = Field(default_factory=list, max_length=10)
degradation: InvestigatorDegradationV1 = Field(default_factory=InvestigatorDegradationV1)
```

## backend/app/models/agent_contracts.py — InvestigatorHypothesisOutput

[backend/app/models/agent_contracts.py:692](../../backend/app/models/agent_contracts.py#L692)

Bases: `ContractedAgentOutput`.

One hypothesis sub-agent's verdict (Agentic plan AI-1).

``hypothesis`` carries the pinned wire shape persisted onto
``AgentInvestigation.hypotheses`` (id/title/status/confidence/
confidence_basis/summary/evidence/started_at/completed_at).

```python
model_config = ConfigDict(extra='ignore')
hypothesis: InvestigatorHypothesisV1
```

## backend/app/models/agent_contracts.py — InvestigatorSynthesisOutput

[backend/app/models/agent_contracts.py:705](../../backend/app/models/agent_contracts.py#L705)

Bases: `ContractedAgentOutput`.

The Investigator's synthesized verdict (Agentic plan AI-1).

```python
model_config = ConfigDict(extra='ignore')
verdict: InvestigatorVerdictV1
```

## backend/app/models/agent_input_contracts.py — CatalogInputContract

[backend/app/models/agent_input_contracts.py:26](../../backend/app/models/agent_input_contracts.py#L26)

Bases: `BaseModel`.

Immutable, closed base for public catalog input schemas.

```python
model_config = ConfigDict(extra='forbid', frozen=True, populate_by_name=True)
```

## backend/app/models/agent_input_contracts.py — TestRun

[backend/app/models/agent_input_contracts.py:32](../../backend/app/models/agent_input_contracts.py#L32)

Bases: `CatalogInputContract`.

Stored test-run identity and lifecycle data consumed by ingestion.

```python
schema_version: Literal[1] = 1
id: uuid.UUID
project_id: uuid.UUID
build_number: str = Field(min_length=1, max_length=255)
status: str = Field(min_length=1, max_length=50)
started_at: datetime | None = None
completed_at: datetime | None = None
```

- Validator/serializer `_completion_follows_start`: [backend/app/models/agent_input_contracts.py:44](../../backend/app/models/agent_input_contracts.py#L44). Read source for the cross-field or conversion rule.
## backend/app/models/agent_input_contracts.py — AuthoritativeFailureClusterV1

[backend/app/models/agent_input_contracts.py:58](../../backend/app/models/agent_input_contracts.py#L58)

Bases: `CatalogInputContract`.

Minimal database-authoritative cluster identity accepted by the planner.

```python
schema_version: Literal[1] = 1
failure_cluster_id: uuid.UUID
cluster_id: str = Field(pattern='^[A-Za-z0-9_.:-]{1,20}$')
member_test_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10000)
```

- Validator/serializer `_members_are_unique`: [backend/app/models/agent_input_contracts.py:67](../../backend/app/models/agent_input_contracts.py#L67). Read source for the cross-field or conversion rule.
## backend/app/models/agent_input_contracts.py — ClusterBudgetV1

[backend/app/models/agent_input_contracts.py:73](../../backend/app/models/agent_input_contracts.py#L73)

Bases: `CatalogInputContract`.



```python
max_llm_calls: int = Field(ge=0)
max_tokens: int = Field(ge=0)
max_cost_usd: float = Field(ge=0)
max_seconds: int = Field(ge=0)
max_retries: int = Field(default=0, ge=0)
```

## backend/app/models/agent_input_contracts.py — ClusterPlanLimitsV1

[backend/app/models/agent_input_contracts.py:81](../../backend/app/models/agent_input_contracts.py#L81)

Bases: `CatalogInputContract`.



```python
max_children: int = Field(ge=0)
max_members: int = Field(gt=0)
```

## backend/app/models/agent_input_contracts.py — ClusterScopeV1

[backend/app/models/agent_input_contracts.py:86](../../backend/app/models/agent_input_contracts.py#L86)

Bases: `CatalogInputContract`.



```python
project_id: uuid.UUID
run_id: uuid.UUID
failure_cluster_id: uuid.UUID
cluster_id: str = Field(pattern='^[A-Za-z0-9_.:-]{1,20}$')
member_test_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10000)
```

## backend/app/models/agent_input_contracts.py — ClusterSlaV1

[backend/app/models/agent_input_contracts.py:94](../../backend/app/models/agent_input_contracts.py#L94)

Bases: `CatalogInputContract`.



```python
expected_latency_ms: int = Field(ge=0)
timeout_seconds: int = Field(ge=0)
```

## backend/app/models/agent_input_contracts.py — ClusterInvestigationTaskV1

[backend/app/models/agent_input_contracts.py:99](../../backend/app/models/agent_input_contracts.py#L99)

Bases: `CatalogInputContract`.



```python
failure_cluster_id: uuid.UUID | None = None
cluster_id: str | None = Field(default=None, pattern='^[A-Za-z0-9_.:-]{1,20}$')
member_test_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=10000)
member_count: int = Field(ge=0)
candidate_sha256: str = Field(pattern=SHA256_PATTERN)
cluster_scope: ClusterScopeV1 | None = None
cluster_scope_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
spawn_key: str | None = Field(default=None, pattern=SHA256_PATTERN)
task_key: str | None = Field(default=None, min_length=1)
capability_id: Literal['agent.cluster_investigation.v1']
selected: bool
skip_reason: str | None = None
rationale: str = Field(min_length=1, max_length=500)
dependencies: tuple[Literal['failure_clustering', 'cluster_investigation_dispatch'], ...]
permission: Literal['read_only']
sla: ClusterSlaV1 | None = None
budget: ClusterBudgetV1
fallback: str = Field(min_length=1)
concurrency_class: str = Field(min_length=1)
```

- Validator/serializer `_selection_fields_are_consistent`: [backend/app/models/agent_input_contracts.py:123](../../backend/app/models/agent_input_contracts.py#L123). Read source for the cross-field or conversion rule.
## backend/app/models/agent_input_contracts.py — ClusterInvestigationExpansionPlanV1

[backend/app/models/agent_input_contracts.py:146](../../backend/app/models/agent_input_contracts.py#L146)

Bases: `CatalogInputContract`.



```python
schema_version: Literal[1] = 1
planner_version: Literal['cluster-investigation-planner:v1']
parent_pipeline_run_id: uuid.UUID
project_id: uuid.UUID
run_id: uuid.UUID
aggregate_budget: ClusterBudgetV1
limits: ClusterPlanLimitsV1
candidate_count: int = Field(ge=0)
selected_count: int = Field(ge=0)
skipped_count: int = Field(ge=0)
selected: tuple[ClusterInvestigationTaskV1, ...] = ()
skipped: tuple[ClusterInvestigationTaskV1, ...] = ()
tasks: tuple[ClusterInvestigationTaskV1, ...] = ()
dependencies: tuple[Literal['failure_clustering', 'cluster_investigation_dispatch'], ...]
fallback: Literal['continue_without_cluster_children']
expansion_id: str = Field(pattern='^cluster-plan:[0-9a-f]{20}$')
expansion_sha256: str = Field(pattern=SHA256_PATTERN)
```

- Validator/serializer `_counts_and_task_projection_match`: [backend/app/models/agent_input_contracts.py:168](../../backend/app/models/agent_input_contracts.py#L168). Read source for the cross-field or conversion rule.
## backend/app/models/agent_input_contracts.py — ClusterScopedEvidenceBundleV1

[backend/app/models/agent_input_contracts.py:188](../../backend/app/models/agent_input_contracts.py#L188)

Bases: `CatalogInputContract`.

Verified evidence authority for one failure-cluster child.

```python
schema_version: Literal[1] = 1
project_id: uuid.UUID
test_run_id: uuid.UUID
pipeline_run_id: uuid.UUID
parent_pipeline_run_id: uuid.UUID
failure_cluster_id: uuid.UUID
cluster_id: str = Field(pattern='^[A-Za-z0-9_.:-]{1,20}$')
member_test_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10000)
cluster_scope_sha256: str = Field(pattern=SHA256_PATTERN)
evidence_refs: tuple[EvidenceReferenceV2, ...] = Field(default=(), max_length=500)
```

- Validator/serializer `_cluster_members_are_unique`: [backend/app/models/agent_input_contracts.py:203](../../backend/app/models/agent_input_contracts.py#L203). Read source for the cross-field or conversion rule.
## backend/app/models/agent_input_contracts.py — PreliminarySummary

[backend/app/models/agent_input_contracts.py:209](../../backend/app/models/agent_input_contracts.py#L209)

Bases: `CatalogInputContract`.

Summary-stage result consumed by deterministic gap detection.

```python
contract: AgentContractMetadata
executive_summary: str | None = None
summary_markdown: str | None = None
structured_summary: Mapping[str, Any] | None = None
summary_provenance: Mapping[str, Any] | None = None
completed_stages: tuple[str, ...] = ()
errors: tuple[str, ...] = ()
current_stage: str = 'triage'
```

## backend/app/models/agent_input_contracts.py — VerificationContextV3

[backend/app/models/agent_input_contracts.py:222](../../backend/app/models/agent_input_contracts.py#L222)

Bases: `CatalogInputContract`.



```python
known_test_ids: tuple[str, ...] = ()
completed_stages: tuple[str, ...] = ()
skipped_stages: tuple[str, ...] = ()
agent_contracts: Mapping[str, Any] = Field(default_factory=dict)
```

## backend/app/models/agent_input_contracts.py — DecisionEvidenceSnapshotV3

[backend/app/models/agent_input_contracts.py:229](../../backend/app/models/agent_input_contracts.py#L229)

Bases: `CatalogInputContract`.

Signed immutable authority used to generate a decision report.

```python
schema_version: Literal[3] = 3
snapshot_id: str = Field(alias='_id', pattern=SHA256_PATTERN)
test_run_id: uuid.UUID
pipeline_run_id: uuid.UUID
project_id: uuid.UUID
canonical_decision: Mapping[str, Any]
run_evidence_bundle: RunEvidenceBundleV1 | RunEvidenceBundleV2
verification_context: VerificationContextV3
release_policy_inputs: Mapping[str, Any]
content_sha256: str = Field(pattern=SHA256_PATTERN)
signature_version: Literal[1]
signature_key_id: str = Field(pattern='^[0-9a-f]{16}$')
signature_hmac_sha256: str = Field(pattern=SHA256_PATTERN)
created_at: datetime
```

## backend/app/models/agent_input_contracts.py — InvestigationRequestV1

[backend/app/models/agent_input_contracts.py:248](../../backend/app/models/agent_input_contracts.py#L248)

Bases: `CatalogInputContract`.



```python
schema_version: Literal[1] = 1
investigation_id: uuid.UUID
pipeline_run_id: uuid.UUID
run_id: uuid.UUID
project_id: uuid.UUID
build_number: str = Field(min_length=1, max_length=255)
mode: Literal['shadow', 'suggest', 'act']
triggered_by: str = Field(min_length=1, max_length=255)
scope_type: Literal['run', 'failure_cluster'] = 'run'
failure_cluster_id: uuid.UUID | None = None
cluster_member_test_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=10000)
```

- Validator/serializer `_scope_authority_is_complete`: [backend/app/models/agent_input_contracts.py:264](../../backend/app/models/agent_input_contracts.py#L264). Read source for the cross-field or conversion rule.
## backend/app/models/agent_input_contracts.py — InvestigationPlanV1

[backend/app/models/agent_input_contracts.py:276](../../backend/app/models/agent_input_contracts.py#L276)

Bases: `InvestigationRequestV1`.

Bounded plan shared by the five investigator hypothesis agents.

```python
budget: ClusterBudgetV1
deadline_ts: float = Field(gt=0)
bundle: Mapping[str, Any]
cancelled: bool = False
```

## backend/app/models/agentic_runtime.py — RuntimeContract

[backend/app/models/agentic_runtime.py:16](../../backend/app/models/agentic_runtime.py#L16)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid', frozen=True)
```

## backend/app/models/agentic_runtime.py — CapabilitySpecV1

[backend/app/models/agentic_runtime.py:36](../../backend/app/models/agentic_runtime.py#L36)

Bases: `RuntimeContract`.



```python
schema_version: Literal[1] = 1
capability_id: str
stage_name: str
input_schema: str
output_schema: str
required_evidence: tuple[str, ...] = ()
permission: Literal['read_only', 'propose_action', 'mutating'] = 'read_only'
dependencies: tuple[str, ...] = ()
expected_latency_ms: int = Field(ge=0)
expected_cost_usd: float = Field(ge=0)
timeout_seconds: int = Field(gt=0)
max_retries: int = Field(ge=0)
fallback: str
concurrency_class: str = 'default'
execution: Literal['planned', 'child_spawned', 'on_demand', 'runtime'] = 'planned'
```

## backend/app/models/agentic_runtime.py — TaskBudgetV1

[backend/app/models/agentic_runtime.py:60](../../backend/app/models/agentic_runtime.py#L60)

Bases: `RuntimeContract`.



```python
max_llm_calls: int | None = Field(default=None, ge=0)
max_tokens: int | None = Field(default=None, ge=0)
max_cost_usd: float | None = Field(default=None, ge=0)
max_seconds: int | None = Field(default=None, ge=0)
max_retries: int | None = Field(default=None, ge=0)
```

## backend/app/models/agentic_runtime.py — TaskUsageV1

[backend/app/models/agentic_runtime.py:68](../../backend/app/models/agentic_runtime.py#L68)

Bases: `RuntimeContract`.



```python
input_tokens: int = Field(default=0, ge=0)
output_tokens: int = Field(default=0, ge=0)
total_tokens: int = Field(default=0, ge=0)
llm_calls: int = Field(default=0, ge=0)
cost_usd: float = Field(default=0, ge=0)
duration_ms: int = Field(default=0, ge=0)
```

- Validator/serializer `_token_total_matches`: [backend/app/models/agentic_runtime.py:77](../../backend/app/models/agentic_runtime.py#L77). Read source for the cross-field or conversion rule.
## backend/app/models/agentic_runtime.py — AgentFindingV1

[backend/app/models/agentic_runtime.py:83](../../backend/app/models/agentic_runtime.py#L83)

Bases: `RuntimeContract`.



```python
schema_version: Literal[1] = 1
finding_id: str
task_id: str
kind: str
statement: str
classification: Literal['fact', 'inference', 'unknown', 'recommendation']
confidence: float | None = Field(default=None, ge=0, le=1)
confidence_basis: str | None = None
evidence_ids: tuple[str, ...] = ()
counter_evidence_ids: tuple[str, ...] = ()
```

## backend/app/models/agentic_runtime.py — AgentEvidenceV1

[backend/app/models/agentic_runtime.py:96](../../backend/app/models/agentic_runtime.py#L96)

Bases: `RuntimeContract`.



```python
schema_version: Literal[1] = 1
evidence_id: str = Field(min_length=1, max_length=160)
task_id: str = Field(min_length=1, max_length=240)
kind: str = Field(min_length=1, max_length=80)
label: str = Field(min_length=1, max_length=200)
description: str = Field(default='', max_length=2000)
classification: Literal['internal', 'restricted'] = 'internal'
```

- Validator/serializer `_sanitize_text`: [backend/app/models/agentic_runtime.py:107](../../backend/app/models/agentic_runtime.py#L107). Read source for the cross-field or conversion rule.
## backend/app/models/agentic_runtime.py — AgentTaskV1

[backend/app/models/agentic_runtime.py:114](../../backend/app/models/agentic_runtime.py#L114)

Bases: `RuntimeContract`.



```python
schema_version: Literal[1] = 1
task_id: str
parent_task_id: str | None = None
source_pipeline_run_id: str | None = None
failure_cluster_id: str | None = Field(default=None, max_length=200)
capability_id: str
stage_name: str
status: Literal['pending', 'running', 'completed', 'failed', 'skipped', 'cancelled', 'partial']
selected: bool
required: bool = False
selection_reason: str
dependencies: tuple[str, ...] = ()
attempt: int = Field(default=1, ge=1)
budget: TaskBudgetV1 = Field(default_factory=TaskBudgetV1)
usage: TaskUsageV1 = Field(default_factory=TaskUsageV1)
stop_reason: Literal['completed', 'policy_skip', 'not_selected', 'budget_exhausted', 'timeout', 'capability_error', 'cancelled', 'partial'] | None = None
enrichment_stop_reason: Literal['cancelled', 'llm_call_budget_exhausted', 'token_budget_exhausted', 'wall_clock_budget_exhausted', 'investigation_not_found', 'budget_reservation_failed', 'duplicate_reservation', 'budget_ledger_invalid', 'budget_reservation_identity_mismatch', 'budget_settlement_failed', 'budget_settlement_pending', 'budget_overrun', 'reservation_lease_expired', 'cost_budget_exhausted', 'cluster_child_join_timeout', 'cluster_child_cancelled', 'cluster_child_failed', 'cluster_child_dispatch_exhausted', 'project_child_capacity_exhausted'] | None = None
evidence_ids: tuple[str, ...] = ()
finding_ids: tuple[str, ...] = ()
started_at: datetime | None = None
completed_at: datetime | None = None
error: str | None = None
```

- Validator/serializer `_validate_lifecycle`: [backend/app/models/agentic_runtime.py:162](../../backend/app/models/agentic_runtime.py#L162). Read source for the cross-field or conversion rule.
## backend/app/models/agentic_runtime.py — TerminalOutcomeV1

[backend/app/models/agentic_runtime.py:170](../../backend/app/models/agentic_runtime.py#L170)

Bases: `RuntimeContract`.



```python
status: Literal['pending', 'running', 'retry_wait', 'completed', 'passed', 'failed']
error: str | None = None
workflow_verification: Mapping[str, Any] | None = None
decision_report_verification: Mapping[str, Any] | None = None
budget_exhausted: bool = False
budget_stop_reasons: tuple[str, ...] = ()
```

- Validator/serializer `_freeze_mapping`: [backend/app/models/agentic_runtime.py:183](../../backend/app/models/agentic_runtime.py#L183). Read source for the cross-field or conversion rule.
- Validator/serializer `_serialize_mapping`: [backend/app/models/agentic_runtime.py:187](../../backend/app/models/agentic_runtime.py#L187). Read source for the cross-field or conversion rule.
## backend/app/models/agentic_runtime.py — AgenticRunV1

[backend/app/models/agentic_runtime.py:191](../../backend/app/models/agentic_runtime.py#L191)

Bases: `RuntimeContract`.



```python
schema_version: Literal[1] = 1
agentic_run_id: str
source_pipeline_run_id: str
test_run_id: str
workflow_type: str
status: Literal['pending', 'running', 'retry_wait', 'completed', 'passed', 'failed']
public_status: Literal['in_progress', 'completed', 'failed', 'passed']
root_task_id: str
plan_id: str
plan_sha256: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')
plan_integrity_status: Literal['verified', 'failed', 'legacy_unhashed']
planner_version: str
run_budget: TaskBudgetV1 = Field(default_factory=TaskBudgetV1)
tasks: tuple[AgentTaskV1, ...] = ()
findings: tuple[AgentFindingV1, ...] = ()
evidence: tuple[AgentEvidenceV1, ...] = ()
child_agentic_run_ids: tuple[str, ...] = ()
observed_child_runs: int = Field(default=0, ge=0)
children_truncated: bool = False
selected_capability_ids: tuple[str, ...] = ()
skipped_capability_ids: tuple[str, ...] = ()
observed_task_rows: int = Field(default=0, ge=0)
tasks_truncated: bool = False
terminal_outcome: TerminalOutcomeV1
started_at: datetime | None = None
completed_at: datetime | None = None
```

- Validator/serializer `_validate_run`: [backend/app/models/agentic_runtime.py:221](../../backend/app/models/agentic_runtime.py#L221). Read source for the cross-field or conversion rule.
## backend/app/models/enums.py — WorkflowType

[backend/app/models/enums.py:9](../../backend/app/models/enums.py#L9)

Bases: `str, PyEnum`.



```python
OFFLINE = 'offline'
DEEP = 'deep'
LIVE = 'live'
```

## backend/app/models/enums.py — PipelineRunStatus

[backend/app/models/enums.py:15](../../backend/app/models/enums.py#L15)

Bases: `str, PyEnum`.

Internal vocabulary of ``agent_pipeline_runs.status`` (architecture E7.1).

Exactly these six values are allowed by the DB CHECK constraint from
migration 0173. The public API projects them onto four:
``in_progress | completed | failed | passed`` (see
``app.services.workflow_run_state.public_status``). ``partial`` and
``cancelled`` are legacy inputs that normalise to ``completed`` (with
``stage_quality=degraded``) and ``failed`` (with a ``cancelled:`` error)
respectively; nothing may write them.

```python
PENDING = 'pending'
RUNNING = 'running'
RETRY_WAIT = 'retry_wait'
COMPLETED = 'completed'
PASSED = 'passed'
FAILED = 'failed'
```

## backend/app/models/enums.py — InvestigationDepth

[backend/app/models/enums.py:34](../../backend/app/models/enums.py#L34)

Bases: `str, PyEnum`.



```python
SHALLOW = 'shallow'
STANDARD = 'standard'
DEEP = 'deep'
```

## backend/app/models/enums.py — SearchType

[backend/app/models/enums.py:40](../../backend/app/models/enums.py#L40)

Bases: `str, PyEnum`.



```python
KEYWORD = 'keyword'
SEMANTIC = 'semantic'
HYBRID = 'hybrid'
```

## backend/app/models/enums.py — CriticalityLevel

[backend/app/models/enums.py:46](../../backend/app/models/enums.py#L46)

Bases: `str, PyEnum`.



```python
CRITICAL = 'CRITICAL'
HIGH = 'HIGH'
MEDIUM = 'MEDIUM'
LOW = 'LOW'
```

## backend/app/models/enums.py — RegressionClassification

[backend/app/models/enums.py:53](../../backend/app/models/enums.py#L53)

Bases: `str, PyEnum`.



```python
NEW_REGRESSION = 'new_regression'
KNOWN_FLAKY = 'known_flaky'
ENVIRONMENTAL = 'environmental'
PRODUCT_BUG = 'product_bug'
INFRASTRUCTURE = 'infrastructure'
UNCLASSIFIED = 'unclassified'
```

## backend/app/models/enums.py — ExecutionPath

[backend/app/models/enums.py:62](../../backend/app/models/enums.py#L62)

Bases: `str, PyEnum`.

Records why a pipeline stage was reached or skipped.

```python
EXECUTED = 'executed'
ALL_GREEN_SKIP = 'all_green_skip'
LOW_CONFIDENCE_SKIP = 'low_confidence_skip'
CONDITIONAL_SKIP = 'conditional_skip'
DEADLINE_SKIP = 'deadline_skip'
```

## backend/app/models/evidence_contracts.py — FrozenContract

[backend/app/models/evidence_contracts.py:19](../../backend/app/models/evidence_contracts.py#L19)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid', frozen=True)
```

## backend/app/models/evidence_contracts.py — DataQualityFlagV1

[backend/app/models/evidence_contracts.py:23](../../backend/app/models/evidence_contracts.py#L23)

Bases: `FrozenContract`.



```python
code: str = Field(min_length=1, max_length=100)
severity: Literal['warning', 'error'] = 'warning'
detail: str = Field(max_length=240)
```

## backend/app/models/evidence_contracts.py — MetricWindowV1

[backend/app/models/evidence_contracts.py:29](../../backend/app/models/evidence_contracts.py#L29)

Bases: `FrozenContract`.



```python
kind: Literal['single_run'] = 'single_run'
test_run_id: str = Field(min_length=1)
```

## backend/app/models/evidence_contracts.py — RunMetricSnapshotV1

[backend/app/models/evidence_contracts.py:34](../../backend/app/models/evidence_contracts.py#L34)

Bases: `FrozenContract`.



```python
schema_version: Literal[1] = 1
definition_version: Literal['run_metrics_v1'] = 'run_metrics_v1'
window: MetricWindowV1
values: Mapping[str, int | float]
denominators: Mapping[str, int]
source_fields: Mapping[str, str]
quality_flags: tuple[DataQualityFlagV1, ...] = ()
content_sha256: str = Field(pattern=SHA256_PATTERN)
```

- Validator/serializer `_freeze_mapping`: [backend/app/models/evidence_contracts.py:46](../../backend/app/models/evidence_contracts.py#L46). Read source for the cross-field or conversion rule.
- Validator/serializer `_serialize_mapping`: [backend/app/models/evidence_contracts.py:50](../../backend/app/models/evidence_contracts.py#L50). Read source for the cross-field or conversion rule.
- Validator/serializer `_validate_metric_contract`: [backend/app/models/evidence_contracts.py:54](../../backend/app/models/evidence_contracts.py#L54). Read source for the cross-field or conversion rule.
## backend/app/models/evidence_contracts.py — EvidenceReferenceV1

[backend/app/models/evidence_contracts.py:108](../../backend/app/models/evidence_contracts.py#L108)

Bases: `FrozenContract`.



```python
schema_version: Literal[1] = 1
evidence_id: str = Field(pattern=SHA256_PATTERN)
kind: str = Field(min_length=1, max_length=100)
source: str = Field(min_length=1, max_length=100)
scope: Mapping[str, str]
checksum_sha256: str = Field(pattern=SHA256_PATTERN)
freshness: Literal['current_run', 'historical', 'unknown']
sensitivity: Literal['internal', 'restricted']
authorization_status: Literal['pipeline_scoped_unverified']
uri_or_ref: str | None = None
excerpt: str = Field(default='', max_length=500)
```

- Validator/serializer `_freeze_scope`: [backend/app/models/evidence_contracts.py:123](../../backend/app/models/evidence_contracts.py#L123). Read source for the cross-field or conversion rule.
- Validator/serializer `_serialize_scope`: [backend/app/models/evidence_contracts.py:127](../../backend/app/models/evidence_contracts.py#L127). Read source for the cross-field or conversion rule.
## backend/app/models/evidence_contracts.py — EvidenceReferenceV2

[backend/app/models/evidence_contracts.py:132](../../backend/app/models/evidence_contracts.py#L132)

Bases: `FrozenContract`.



```python
schema_version: Literal[2] = 2
artifact_id: str
evidence_id: str = Field(pattern=SHA256_PATTERN)
kind: str = Field(min_length=1, max_length=100)
source: str = Field(min_length=1, max_length=100)
scope: Mapping[str, str]
producer_pipeline_run_id: str
checksum_sha256: str = Field(pattern=SHA256_PATTERN)
freshness: Literal['current_run', 'historical', 'unknown']
sensitivity: Literal['internal', 'restricted']
authorization_status: Literal['tenant_run_pipeline_verified']
uri_or_ref: None = None
excerpt: str = Field(default='', max_length=500)
```

- Validator/serializer `_freeze_scope`: [backend/app/models/evidence_contracts.py:149](../../backend/app/models/evidence_contracts.py#L149). Read source for the cross-field or conversion rule.
- Validator/serializer `_serialize_scope`: [backend/app/models/evidence_contracts.py:153](../../backend/app/models/evidence_contracts.py#L153). Read source for the cross-field or conversion rule.
- Validator/serializer `_validate_authority`: [backend/app/models/evidence_contracts.py:157](../../backend/app/models/evidence_contracts.py#L157). Read source for the cross-field or conversion rule.
## backend/app/models/evidence_contracts.py — RunEvidenceBundleV1

[backend/app/models/evidence_contracts.py:168](../../backend/app/models/evidence_contracts.py#L168)

Bases: `FrozenContract`.



```python
schema_version: Literal[1] = 1
project_id: str = Field(min_length=1)
test_run_id: str = Field(min_length=1)
pipeline_run_id: str = Field(min_length=1)
build_number: str
branch: str | None = None
workflow_type: str
metric_snapshot: RunMetricSnapshotV1
failed_test_ids: tuple[str, ...] = Field(default=(), max_length=10000)
evidence_refs: tuple[EvidenceReferenceV1, ...] = Field(default=(), max_length=500)
specialist_payload_sha256: Mapping[str, str]
quality_flags: tuple[DataQualityFlagV1, ...] = ()
omitted_evidence_count: int = Field(default=0, ge=0)
content_sha256: str = Field(pattern=SHA256_PATTERN)
```

- Validator/serializer `_freeze_specialist_hashes`: [backend/app/models/evidence_contracts.py:186](../../backend/app/models/evidence_contracts.py#L186). Read source for the cross-field or conversion rule.
- Validator/serializer `_serialize_specialist_hashes`: [backend/app/models/evidence_contracts.py:198](../../backend/app/models/evidence_contracts.py#L198). Read source for the cross-field or conversion rule.
- Validator/serializer `_validate_bundle_scope`: [backend/app/models/evidence_contracts.py:204](../../backend/app/models/evidence_contracts.py#L204). Read source for the cross-field or conversion rule.
## backend/app/models/evidence_contracts.py — RunEvidenceBundleV2

[backend/app/models/evidence_contracts.py:221](../../backend/app/models/evidence_contracts.py#L221)

Bases: `FrozenContract`.



```python
schema_version: Literal[2] = 2
project_id: str = Field(min_length=1)
test_run_id: str = Field(min_length=1)
pipeline_run_id: str = Field(min_length=1)
build_number: str
branch: str | None = None
workflow_type: str
metric_snapshot: RunMetricSnapshotV1
failed_test_ids: tuple[str, ...] = Field(default=(), max_length=10000)
evidence_refs: tuple[EvidenceReferenceV2, ...] = Field(default=(), max_length=500)
specialist_payload_sha256: Mapping[str, str]
quality_flags: tuple[DataQualityFlagV1, ...] = ()
omitted_evidence_count: int = Field(default=0, ge=0)
content_sha256: str = Field(pattern=SHA256_PATTERN)
```

- Validator/serializer `_freeze_specialist_hashes`: [backend/app/models/evidence_contracts.py:239](../../backend/app/models/evidence_contracts.py#L239). Read source for the cross-field or conversion rule.
- Validator/serializer `_serialize_specialist_hashes`: [backend/app/models/evidence_contracts.py:251](../../backend/app/models/evidence_contracts.py#L251). Read source for the cross-field or conversion rule.
- Validator/serializer `_validate_bundle_scope`: [backend/app/models/evidence_contracts.py:255](../../backend/app/models/evidence_contracts.py#L255). Read source for the cross-field or conversion rule.
## backend/app/models/llm_schemas.py — IncidentView

[backend/app/models/llm_schemas.py:27](../../backend/app/models/llm_schemas.py#L27)

Bases: `BaseModel`.

Layer 2: Structured incident view from summary agent.

```python
what_failed: str = ''
likely_cause: str = ''
scope: str = ''
criticality: str = Field(default='MEDIUM')
release_impact: str = Field(default='CONDITIONAL_GO')
failure_breakdown: dict[str, int] = Field(default_factory=dict)
evidence_ids: list[str] = Field(default_factory=list)
citations: list[dict[str, Any]] = Field(default_factory=list)
```

- Validator/serializer `normalize_criticality`: [backend/app/models/llm_schemas.py:44](../../backend/app/models/llm_schemas.py#L44). Read source for the cross-field or conversion rule.
- Validator/serializer `normalize_release_impact`: [backend/app/models/llm_schemas.py:51](../../backend/app/models/llm_schemas.py#L51). Read source for the cross-field or conversion rule.
## backend/app/models/llm_schemas.py — EvidencePack

[backend/app/models/llm_schemas.py:57](../../backend/app/models/llm_schemas.py#L57)

Bases: `BaseModel`.

Layer 3: Evidence pack from summary agent.

```python
top_stack_traces: list[str] = Field(default_factory=list)
log_anomalies: list[str] = Field(default_factory=list)
flaky_test_ids: list[str] = Field(default_factory=list)
similar_historical_failures: list[str] = Field(default_factory=list)
data_sources_used: list[str] = Field(default_factory=list)
evidence_ids: list[str] = Field(default_factory=list)
citations: list[dict[str, Any]] = Field(default_factory=list)
```

## backend/app/models/llm_schemas.py — ActionPlan

[backend/app/models/llm_schemas.py:72](../../backend/app/models/llm_schemas.py#L72)

Bases: `BaseModel`.

Layer 4: Action plan from summary agent.

```python
immediate_mitigation: str = ''
fix_recommendations: list[str] = Field(default_factory=list)
validation_steps: list[str] = Field(default_factory=list)
rollback_guidance: str = ''
owner_hints: dict[str, str] = Field(default_factory=dict)
evidence_ids: list[str] = Field(default_factory=list)
citations: list[dict[str, Any]] = Field(default_factory=list)
```

## backend/app/models/llm_schemas.py — RootCauseAnalysis

[backend/app/models/llm_schemas.py:90](../../backend/app/models/llm_schemas.py#L90)

Bases: `BaseModel`.

Structured output from the ReAct root-cause triage agent.

```python
root_cause_summary: str = ''
failure_category: str = 'UNKNOWN'
backend_error_found: bool = False
pod_issue_found: bool = False
is_flaky: bool = False
confidence_score: int = Field(default=0, ge=0, le=100)
recommended_actions: list[str] = Field(default_factory=list)
role_actions: dict[str, str] = Field(default_factory=dict)
evidence_references: list[dict[str, Any]] = Field(default_factory=list)
```

- Validator/serializer `normalize_failure_category`: [backend/app/models/llm_schemas.py:104](../../backend/app/models/llm_schemas.py#L104). Read source for the cross-field or conversion rule.
- Validator/serializer `normalize_actions`: [backend/app/models/llm_schemas.py:118](../../backend/app/models/llm_schemas.py#L118). Read source for the cross-field or conversion rule.
## backend/app/models/llm_schemas.py — ExecutivePanelMetrics

[backend/app/models/llm_schemas.py:127](../../backend/app/models/llm_schemas.py#L127)

Bases: `BaseModel`.

Core run metrics for the executive summary panel.

```python
build_number: str = ''
branch: str = ''
total_tests: int = 0
passed: int = 0
failed: int = 0
skipped: int = 0
pass_rate: float = 0.0
duration_seconds: int | None = None
failure_clusters: int = 0
anomaly_count: int = 0
```

## backend/app/models/llm_schemas.py — DominantFailure

[backend/app/models/llm_schemas.py:141](../../backend/app/models/llm_schemas.py#L141)

Bases: `BaseModel`.

Dominant failure category in the executive panel.

```python
category: str = 'UNKNOWN'
count: int = 0
percentage: float = 0.0
```

## backend/app/models/llm_schemas.py — BaselineComparison

[backend/app/models/llm_schemas.py:148](../../backend/app/models/llm_schemas.py#L148)

Bases: `BaseModel`.

Baseline comparison metrics for the executive panel.

```python
pass_rate_delta: float = 0.0
new_failures: int = 0
resolved: int = 0
classification: str = 'unclassified'
```

## backend/app/models/llm_schemas.py — ExecutivePanel

[backend/app/models/llm_schemas.py:156](../../backend/app/models/llm_schemas.py#L156)

Bases: `BaseModel`.

Structured executive summary panel — deterministically generated.

```python
headline: str = ''
status_signal: str = Field(default='CONDITIONAL_GO')
risk_score: int | None = None
metrics: ExecutivePanelMetrics = Field(default_factory=ExecutivePanelMetrics)
dominant_failure: DominantFailure | None = None
key_takeaways: list[str] = Field(default_factory=list)
baseline_comparison: BaselineComparison | None = None
next_actions: list[str] = Field(default_factory=list)
```

- Validator/serializer `normalize_status_signal`: [backend/app/models/llm_schemas.py:169](../../backend/app/models/llm_schemas.py#L169). Read source for the cross-field or conversion rule.
## backend/app/models/llm_schemas.py — ClusterClassification

[backend/app/models/llm_schemas.py:178](../../backend/app/models/llm_schemas.py#L178)

Bases: `BaseModel`.

Single cluster classification from regression watchman.

```python
classification: str = 'new_regression'
confidence: int = Field(default=0, ge=0, le=100)
evidence: str = ''
```

- Validator/serializer `normalize_classification`: [backend/app/models/llm_schemas.py:186](../../backend/app/models/llm_schemas.py#L186). Read source for the cross-field or conversion rule.
## backend/app/models/llm_schemas.py — ReleaseReasoning

[backend/app/models/llm_schemas.py:195](../../backend/app/models/llm_schemas.py#L195)

Bases: `BaseModel`.

LLM reasoning output from release risk agent.

```python
reasoning: str = ''
blocking_issues: list[str] = Field(default_factory=list)
conditions_for_go: list[str] = Field(default_factory=list)
```

## backend/app/models/postgres.py — TestStatus

[backend/app/models/postgres.py:43](../../backend/app/models/postgres.py#L43)

Bases: `str, PyEnum`.



```python
PASSED = 'PASSED'
FAILED = 'FAILED'
SKIPPED = 'SKIPPED'
BROKEN = 'BROKEN'
UNKNOWN = 'UNKNOWN'
```

## backend/app/models/postgres.py — LaunchStatus

[backend/app/models/postgres.py:51](../../backend/app/models/postgres.py#L51)

Bases: `str, PyEnum`.



```python
IN_PROGRESS = 'IN_PROGRESS'
PASSED = 'PASSED'
FAILED = 'FAILED'
STOPPED = 'STOPPED'
```

## backend/app/models/postgres.py — IngestionSource

[backend/app/models/postgres.py:58](../../backend/app/models/postgres.py#L58)

Bases: `str, PyEnum`.

How a TestRun's results entered TestLookup.

* ``live``   — SDK live-stream session (events buffered in Redis, drained).
* ``sdk``    — SDK/CI batch POST to /api/v1/ingest (JSON results).
* ``upload`` — operator manually uploaded a report file via the UI.
* ``file``   — Allure/TestNG file landed via the MinIO webhook (sentinel) path.
* ``unknown``— pre-migration rows whose origin couldn't be inferred.

```python
LIVE = 'live'
SDK = 'sdk'
UPLOAD = 'upload'
FILE = 'file'
UNKNOWN = 'unknown'
```

## backend/app/models/postgres.py — FailureCategory

[backend/app/models/postgres.py:74](../../backend/app/models/postgres.py#L74)

Bases: `str, PyEnum`.



```python
PRODUCT_BUG = 'PRODUCT_BUG'
INFRASTRUCTURE = 'INFRASTRUCTURE'
TEST_DATA = 'TEST_DATA'
AUTOMATION_DEFECT = 'AUTOMATION_DEFECT'
FLAKY = 'FLAKY'
UNKNOWN = 'UNKNOWN'
```

## backend/app/models/postgres.py — Severity

[backend/app/models/postgres.py:83](../../backend/app/models/postgres.py#L83)

Bases: `str, PyEnum`.



```python
BLOCKER = 'BLOCKER'
CRITICAL = 'CRITICAL'
MAJOR = 'MAJOR'
MINOR = 'MINOR'
TRIVIAL = 'TRIVIAL'
```

## backend/app/models/postgres.py — UserRole

[backend/app/models/postgres.py:91](../../backend/app/models/postgres.py#L91)

Bases: `str, PyEnum`.



```python
VIEWER = 'VIEWER'
TESTER = 'TESTER'
QA_ENGINEER = 'QA_ENGINEER'
QA_LEAD = 'QA_LEAD'
ADMIN = 'ADMIN'
```

## backend/app/models/postgres.py — TestCaseLifecycleState

[backend/app/models/postgres.py:99](../../backend/app/models/postgres.py#L99)

Bases: `str, PyEnum`.

Stored lifecycle vocabulary for authored ``ManagedTestCase`` rows.

```python
DRAFT = 'draft'
REVIEW_REQUESTED = 'review_requested'
UNDER_REVIEW = 'under_review'
APPROVED = 'approved'
ACTIVE = 'active'
REJECTED = 'rejected'
NEEDS_UPDATE = 'needs_update'
DEPRECATED = 'deprecated'
ARCHIVED = 'archived'
```

## backend/app/models/postgres.py — TriageStatus

[backend/app/models/postgres.py:113](../../backend/app/models/postgres.py#L113)

Bases: `str, PyEnum`.

Per-failure triage workflow state (migration 0088).

Distinct from ``TestStatus`` (which is the test's execution outcome).
Every auto-assigned FAILED/BROKEN TestCase starts at ``PENDING_REVIEW``
and is moved off the ``/my-failures`` inbox once the assignee or a
QA Lead picks one of the resolved states.

```python
PENDING_REVIEW = 'PENDING_REVIEW'
REVIEWED_APPROVED = 'REVIEWED_APPROVED'
DEFECT_CREATED = 'DEFECT_CREATED'
WONT_FIX = 'WONT_FIX'
AUTOMATION_SCRIPT_ISSUE = 'AUTOMATION_SCRIPT_ISSUE'
FLAKY_TEST = 'FLAKY_TEST'
```

## backend/app/models/postgres.py — NotificationChannel

[backend/app/models/postgres.py:129](../../backend/app/models/postgres.py#L129)

Bases: `str, PyEnum`.



```python
EMAIL = 'email'
SLACK = 'slack'
TEAMS = 'teams'
```

## backend/app/models/postgres.py — NotificationEventType

[backend/app/models/postgres.py:135](../../backend/app/models/postgres.py#L135)

Bases: `str, PyEnum`.



```python
RUN_FAILED = 'run_failed'
RUN_PASSED = 'run_passed'
HIGH_FAILURE_RATE = 'high_failure_rate'
AI_ANALYSIS_COMPLETE = 'ai_analysis_complete'
QUALITY_GATE_FAILED = 'quality_gate_failed'
FLAKY_TEST_DETECTED = 'flaky_test_detected'
TEST_NEWLY_FAILING = 'test.newly_failing'
TEST_RECOVERED = 'test.recovered'
TEST_NEWLY_FLAKY = 'test.newly_flaky'
TEST_QUARANTINED = 'test.quarantined'
TEST_UNQUARANTINED = 'test.unquarantined'
TEST_QUARANTINE_STALE = 'test.quarantine_stale'
TEST_READY_TO_UNQUARANTINE = 'test.ready_to_unquarantine'
```

## backend/app/models/postgres.py — User

[backend/app/models/postgres.py:159](../../backend/app/models/postgres.py#L159)

Bases: `Base`.



```python
__tablename__ = 'users'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
full_name: Mapped[Optional[str]] = mapped_column(String(255))
hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
role: Mapped[UserRole] = mapped_column(String(20), default=UserRole.VIEWER.value)
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
is_service_account: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=text('false'))
is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=text('false'))
must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
avatar_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
mfa_enrolled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
mfa_last_used_step: Mapped[Optional[int]] = mapped_column(BigInteger)
last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — MfaRecoveryCode

[backend/app/models/postgres.py:212](../../backend/app/models/postgres.py#L212)

Bases: `Base`.

Single-use recovery code for a user with TOTP enabled.

Only the SHA-256 digest is stored, so a database compromise cannot recover
a usable code. Recovery codes are minted with ~130 bits of entropy from
``secrets.token_hex``, which is why a plain digest is adequate here and a
password KDF is not — there is nothing to brute-force. (Same reasoning as
``ApiKey.key_hash`` and ``ReportShareLink.token_hash``.)

A used code is retained with ``used_at`` set rather than deleted, so
"which codes are still live" and "a recovery code was burned on
<date>" both remain answerable. Rows are deleted only when the whole set
is regenerated or MFA is disabled.

```python
__tablename__ = 'mfa_recovery_codes'
__table_args__ = (Index('ix_mfa_recovery_user', 'user_id'), Index('ix_mfa_recovery_code_hash', 'code_hash', unique=True))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — Project

[backend/app/models/postgres.py:239](../../backend/app/models/postgres.py#L239)

Bases: `Base`.



```python
__tablename__ = 'projects'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
description: Mapped[Optional[str]] = mapped_column(Text)
jira_project_key: Mapped[Optional[str]] = mapped_column(String(50))
splunk_index: Mapped[Optional[str]] = mapped_column(String(255))
ocp_namespace: Mapped[Optional[str]] = mapped_column(String(255))
jenkins_job_pattern: Mapped[Optional[str]] = mapped_column(String(500))
component_owner_map: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
tags: Mapped[Optional[list]] = mapped_column(JSON)
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
manager_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
default_qa_lead_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
allow_unreviewed_distribution: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=text('false'))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
test_runs: Mapped[list['TestRun']] = relationship('TestRun', back_populates='project', lazy='dynamic')
quality_gates: Mapped[list['QualityGate']] = relationship('QualityGate', back_populates='project')
test_suites: Mapped[list['TestSuite']] = relationship('TestSuite', back_populates='project', cascade='all, delete-orphan')
canonical_test_cases: Mapped[list['CanonicalTestCase']] = relationship('CanonicalTestCase', back_populates='project', cascade='all, delete-orphan')
```

## backend/app/models/postgres.py — TestRun

[backend/app/models/postgres.py:292](../../backend/app/models/postgres.py#L292)

Bases: `Base`.

Represents a single CI/CD pipeline execution (Jenkins build).

```python
__tablename__ = 'test_runs'
__table_args__ = (Index('uq_test_runs_legacy_build', 'project_id', 'build_number', 'jenkins_job', unique=True, postgresql_where=text('ingestion_identity IS NULL')), Index('uq_test_runs_project_ingestion_identity', 'project_id', 'ingestion_identity', unique=True, postgresql_where=text('ingestion_identity IS NOT NULL')), Index('ix_test_runs_project_status', 'project_id', 'status'), Index('ix_test_runs_created_at', 'created_at'), Index('ix_test_runs_project_status_created', 'project_id', 'status', 'created_at'), Index('ix_test_runs_project_pr', 'project_id', 'pr_number', postgresql_where=text('pr_number IS NOT NULL')), Index('ix_test_runs_project_release_created', 'project_id', 'primary_release_id', 'created_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
build_number: Mapped[str] = mapped_column(String(100), nullable=False)
jenkins_job: Mapped[Optional[str]] = mapped_column(String(500))
trigger_source: Mapped[Optional[str]] = mapped_column(String(50))
branch: Mapped[Optional[str]] = mapped_column(String(255))
commit_hash: Mapped[Optional[str]] = mapped_column(String(64))
status: Mapped[LaunchStatus] = mapped_column(String(20), default=LaunchStatus.IN_PROGRESS)
ingestion_source: Mapped[str] = mapped_column(String(20), nullable=False, default=IngestionSource.UNKNOWN.value, server_default='unknown')
ci_provider: Mapped[Optional[str]] = mapped_column(String(30))
ci_repo: Mapped[Optional[str]] = mapped_column(String(300))
pr_number: Mapped[Optional[int]] = mapped_column(Integer)
ci_actor: Mapped[Optional[str]] = mapped_column(String(120))
ci_run_url: Mapped[Optional[str]] = mapped_column(String(1000))
ingestion_identity: Mapped[Optional[str]] = mapped_column(String(64), index=True)
ingestion_attempted_tests: Mapped[Optional[int]] = mapped_column(Integer)
ingestion_rejected_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
ingestion_complete: Mapped[Optional[bool]] = mapped_column(Boolean)
ingestion_rejection_reasons: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
environment: Mapped[Optional[str]] = mapped_column(String(100))
primary_release_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('releases.id', ondelete='SET NULL'), nullable=True)
total_tests: Mapped[int] = mapped_column(Integer, default=0)
passed_tests: Mapped[int] = mapped_column(Integer, default=0)
failed_tests: Mapped[int] = mapped_column(Integer, default=0)
skipped_tests: Mapped[int] = mapped_column(Integer, default=0)
broken_tests: Mapped[int] = mapped_column(Integer, default=0)
unknown_tests: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
pass_rate: Mapped[Optional[float]] = mapped_column(Float)
duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
ocp_pod_name: Mapped[Optional[str]] = mapped_column(String(255))
ocp_node: Mapped[Optional[str]] = mapped_column(String(255))
ocp_namespace: Mapped[Optional[str]] = mapped_column(String(255))
ocp_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
minio_prefix: Mapped[Optional[str]] = mapped_column(String(1000))
tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
primary_suite_name: Mapped[Optional[str]] = mapped_column(String(500), index=True)
suite_names: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
event_archive: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
event_archive_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
project: Mapped['Project'] = relationship('Project', back_populates='test_runs')
test_cases: Mapped[list['TestCase']] = relationship('TestCase', back_populates='test_run', lazy='dynamic')
```

## backend/app/models/postgres.py — LiveIngestionAttempt

[backend/app/models/postgres.py:454](../../backend/app/models/postgres.py#L454)

Bases: `Base`.

Durable record that an accepted live batch reached projection.

```python
__tablename__ = 'live_ingestion_attempts'
__table_args__ = (UniqueConstraint('session_id', 'batch_id', name='uq_live_ingestion_attempt_session_batch'), Index('ix_live_ingestion_attempt_run_created', 'run_id', 'created_at'), CheckConstraint('event_count >= 0', name='ck_live_ingestion_attempt_count'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
session_id: Mapped[str] = mapped_column(String(255), nullable=False)
batch_id: Mapped[str] = mapped_column(String(255), nullable=False)
event_count: Mapped[int] = mapped_column(Integer, nullable=False)
first_event_id: Mapped[Optional[str]] = mapped_column(String(64))
last_event_id: Mapped[Optional[str]] = mapped_column(String(64))
first_stream_id: Mapped[Optional[str]] = mapped_column(String(64))
last_stream_id: Mapped[Optional[str]] = mapped_column(String(64))
projected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

## backend/app/models/postgres.py — LiveEventReceipt

[backend/app/models/postgres.py:487](../../backend/app/models/postgres.py#L487)

Bases: `Base`.

Per-event idempotency receipt and bounded sanitized attempt evidence.

```python
__tablename__ = 'live_event_receipts'
__table_args__ = (UniqueConstraint('event_id', name='uq_live_event_receipt_event'), Index('ix_live_event_receipt_run_stream', 'run_id', 'stream_id'), CheckConstraint('event_index >= 0', name='ck_live_event_receipt_index'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('live_ingestion_attempts.id', ondelete='CASCADE'), nullable=False)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
event_id: Mapped[str] = mapped_column(String(64), nullable=False)
stream_id: Mapped[str] = mapped_column(String(64), nullable=False)
event_index: Mapped[int] = mapped_column(Integer, nullable=False)
event_type: Mapped[str] = mapped_column(String(50), nullable=False)
payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
projected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

## backend/app/models/postgres.py — LiveProjectionCheckpoint

[backend/app/models/postgres.py:516](../../backend/app/models/postgres.py#L516)

Bases: `Base`.

Committed per-run high watermark for Redis evidence projection.

```python
__tablename__ = 'live_projection_checkpoints'
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), primary_key=True)
stream_key: Mapped[str] = mapped_column(String(500), nullable=False)
consumer_group: Mapped[str] = mapped_column(String(100), nullable=False)
last_stream_id: Mapped[str] = mapped_column(String(64), nullable=False)
last_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — RunDownstreamOutbox

[backend/app/models/postgres.py:534](../../backend/app/models/postgres.py#L534)

Bases: `Base`.

Durable intent to publish one post-ingestion operation.

```python
__tablename__ = 'run_downstream_outbox'
__table_args__ = (UniqueConstraint('run_id', 'operation', 'input_version', name='uq_run_downstream_operation_version'), Index('ix_run_downstream_outbox_due_project', 'status', 'next_attempt_at', 'project_id', 'created_at'), CheckConstraint("status IN ('waiting', 'pending', 'sending', 'published', 'processing', 'completed', 'failed')", name='ck_run_downstream_outbox_status'), CheckConstraint('attempts >= 0', name='ck_run_downstream_outbox_attempts_nonnegative'), CheckConstraint('dispatch_failures >= 0', name='ck_run_downstream_outbox_dispatch_failures_nonnegative'), CheckConstraint('execution_attempts >= 0', name='ck_run_downstream_outbox_execution_attempts_nonnegative'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
operation: Mapped[str] = mapped_column(String(60), nullable=False)
input_version: Mapped[str] = mapped_column(String(64), nullable=False)
payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
queue: Mapped[str] = mapped_column(String(80), nullable=False, default='default')
priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', server_default='pending')
attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
dispatch_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
execution_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
dispatch_token: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
processing_task_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
completion_detail: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
last_error: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — SemanticReindexJob

[backend/app/models/postgres.py:628](../../backend/app/models/postgres.py#L628)

Bases: `Base`.

Durable, fenced progress for one global or project semantic rebuild.

```python
__tablename__ = 'semantic_reindex_jobs'
__table_args__ = (CheckConstraint("status IN ('running', 'finalizing', 'succeeded')", name='ck_semantic_reindex_job_status'), CheckConstraint('state_version = 1', name='ck_semantic_reindex_job_state_version'), CheckConstraint('processed_count >= 0', name='ck_semantic_reindex_job_processed_count'), UniqueConstraint('job_id', name='uq_semantic_reindex_jobs_job_id'), Index('ix_semantic_reindex_jobs_status_lease', 'status', 'lease_expires_at'))
scope_key: Mapped[str] = mapped_column(String(80), primary_key=True)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
status: Mapped[str] = mapped_column(String(20), nullable=False, default='running', server_default='running')
high_water_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
high_water_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
cursor_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
cursor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
processed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default='0')
lease_owner: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default='1')
last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — TestCase

[backend/app/models/postgres.py:707](../../backend/app/models/postgres.py#L707)

Bases: `Base`.

Individual test case result within a run.

```python
__tablename__ = 'test_cases'
__table_args__ = (UniqueConstraint('test_run_id', 'test_fingerprint', name='uq_test_cases_run_fingerprint'), Index('ix_test_cases_run_status', 'test_run_id', 'status'), Index('ix_test_cases_run_suite', 'test_run_id', 'suite_name'), Index('ix_test_cases_fingerprint', 'test_fingerprint'), Index('ix_test_cases_canonical', 'canonical_test_case_id'), Index('ix_test_cases_semantic_reindex_keyset', 'created_at', 'id'), Index('ix_test_cases_assignee_triage', 'assigned_to_user_id', 'triage_status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
canonical_test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('canonical_test_cases.id', ondelete='SET NULL'), nullable=True)
test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
full_name: Mapped[Optional[str]] = mapped_column(String(2000))
suite_name: Mapped[Optional[str]] = mapped_column(String(500))
class_name: Mapped[Optional[str]] = mapped_column(String(500))
package_name: Mapped[Optional[str]] = mapped_column(String(500))
status: Mapped[TestStatus] = mapped_column(String(20), nullable=False, default=TestStatus.UNKNOWN)
duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
severity: Mapped[Optional[Severity]] = mapped_column(String(20))
feature: Mapped[Optional[str]] = mapped_column(String(500))
story: Mapped[Optional[str]] = mapped_column(String(500))
epic: Mapped[Optional[str]] = mapped_column(String(500))
owner: Mapped[Optional[str]] = mapped_column(String(255))
tags: Mapped[Optional[list]] = mapped_column(JSON)
failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
error_message: Mapped[Optional[str]] = mapped_column(Text)
assigned_to_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
triage_status: Mapped[TriageStatus] = mapped_column(String(30), nullable=False, default=TriageStatus.PENDING_REVIEW.value, server_default=TriageStatus.PENDING_REVIEW.value)
triage_notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
triage_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
triage_updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
retry_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
is_flaky_run: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
stack_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
step_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
source_uuid: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
source_history_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
source_test_case_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
parser_format: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
parser_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
source_parameters: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
source_links: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
source_labels: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
source_extensions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
service_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
component_names: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
steps_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
minio_s3_prefix: Mapped[Optional[str]] = mapped_column(String(1000))
has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
test_run: Mapped['TestRun'] = relationship('TestRun', back_populates='test_cases')
history: Mapped[list['TestCaseHistory']] = relationship('TestCaseHistory', back_populates='test_case')
ai_analysis: Mapped[Optional['AIAnalysis']] = relationship('AIAnalysis', back_populates='test_case', uselist=False)
defects: Mapped[list['Defect']] = relationship('Defect', back_populates='test_case')
canonical_test_case: Mapped[Optional['CanonicalTestCase']] = relationship('CanonicalTestCase', back_populates='test_cases')
```

## backend/app/models/postgres.py — TestSuite

[backend/app/models/postgres.py:856](../../backend/app/models/postgres.py#L856)

Bases: `Base`.

Project-scoped grouping of test cases.

Replaces the prior string-based ``suite_name`` model with a first-class
entity. Every project gets a row with ``is_default=True`` named
``Default Suite ({project.name})`` — new test cases ingested without an
explicit suite are auto-assigned to it.

```python
__tablename__ = 'test_suites'
__table_args__ = (UniqueConstraint('project_id', 'name', name='uq_test_suites_project_name'), Index('ix_test_suites_project_id', 'project_id'), Index('ix_test_suites_project_default', 'project_id', unique=True, postgresql_where=text('is_default IS TRUE')))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
name: Mapped[str] = mapped_column(String(500), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
project: Mapped['Project'] = relationship('Project', back_populates='test_suites')
canonical_test_cases: Mapped[list['CanonicalTestCase']] = relationship('CanonicalTestCase', back_populates='test_suite', cascade='all, delete-orphan')
```

## backend/app/models/postgres.py — TestSuiteOwner

[backend/app/models/postgres.py:891](../../backend/app/models/postgres.py#L891)

Bases: `Base`.

Explicit owner for a (project, suite_name) pair (migration 0076).

Keyed by suite_name (string) to match the legacy aggregated Test Suites
view served from ``/api/v1/test-management/suites``. Falls back to
``Project.manager_user_id`` when no row exists for a given suite.

```python
__tablename__ = 'test_suite_owners'
__table_args__ = (UniqueConstraint('project_id', 'suite_name', name='uq_test_suite_owners_proj_suite'), Index('ix_test_suite_owners_project_id', 'project_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — SuiteRunReview

[backend/app/models/postgres.py:918](../../backend/app/models/postgres.py#L918)

Bases: `Base`.

Human-in-the-loop overlay on AI analysis for a (run, suite) (migration 0076).

Non-gating: the AI pipeline still completes runs without waiting for a
review. State machine: pending → confirmed | acknowledged | review_later.
Unique on ``(test_run_id, suite_name)`` so each run+suite has at most one
review (later updates mutate the row instead of inserting).

```python
__tablename__ = 'suite_run_reviews'
__table_args__ = (UniqueConstraint('test_run_id', 'suite_name', name='uq_suite_run_reviews_run_suite'), Index('ix_suite_run_reviews_project_suite', 'project_id', 'suite_name'), Index('ix_suite_run_reviews_state', 'state'), Index('ix_suite_run_reviews_test_run_id', 'test_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
reviewer_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
state: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', server_default=text("'pending'"))
note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — TestExecutionReview

[backend/app/models/postgres.py:957](../../backend/app/models/postgres.py#L957)

Bases: `Base`.

Per-TestCase human review overlay for AI-flagged failures (migration 0081).

When the AI analysis pipeline flags a failure as ``requires_human_review``
(model missing, low confidence, fallback path), the UI shows a "Pending
Human Review" tag. This row records the human's verdict once they look:
``reviewed`` (AI was right), ``defect_filed`` (ticket created;
``defect_link`` captures the URL), ``false_positive`` (flake or test bug;
downstream un-tags), or ``reproducible`` (failure confirmed locally,
awaiting fix).

Naming note: the older ``test_case_reviews`` table belongs to the
managed-test-AUTHORING workflow (review of an authored test definition
before it's published). This table is keyed on ``test_cases.id`` —
the execution row — and is unrelated.

One row per test_case_id (UNIQUE). Transitions mutate the row in-place;
cross-test audit lives in ``test_case_audit_logs`` for cases that need a
timeline. Keep this table small and queryable for the inbox + dashboards.

```python
__tablename__ = 'test_execution_reviews'
__table_args__ = (UniqueConstraint('test_case_id', name='uq_ter_test_case_id'), CheckConstraint("state IN ('pending_review', 'reviewed', 'defect_filed', 'false_positive', 'reproducible')", name='ck_ter_state_valid'), CheckConstraint("state <> 'defect_filed' OR NULLIF(BTRIM(defect_link), '') IS NOT NULL", name='ck_ter_defect_link_required'), Index('ix_ter_project_state', 'project_id', 'state'), Index('ix_ter_reviewed_by', 'reviewed_by_user_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_cases.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
state: Mapped[str] = mapped_column(String(30), nullable=False, default='pending_review', server_default=text("'pending_review'"))
reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
defect_link: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
transitioned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — CanonicalTestCase

[backend/app/models/postgres.py:1033](../../backend/app/models/postgres.py#L1033)

Bases: `Base`.

Project-scoped test case identity.

Merges the prior ``SuiteMembership`` lifecycle model with a relational
anchor that the per-run ``test_cases`` table FKs to. Identified by
``(project_id, test_fingerprint)`` — one row per logical test per project.
Every CanonicalTestCase belongs to exactly one TestSuite; manual re-linking
moves the row between suites without losing run history.

```python
__tablename__ = 'canonical_test_cases'
__table_args__ = (UniqueConstraint('project_id', 'test_fingerprint', name='uq_canonical_test_cases_project_fp'), Index('ix_ctc_project_id', 'project_id'), Index('ix_ctc_test_suite_id', 'test_suite_id'), Index('ix_ctc_project_status', 'project_id', 'status'), Index('ix_ctc_fingerprint', 'test_fingerprint'), Index('uq_ctc_managed_test_case_id', 'managed_test_case_id', unique=True, postgresql_where=text('managed_test_case_id IS NOT NULL')))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_suites.id', ondelete='RESTRICT'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
class_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='active', server_default='active')
source: Mapped[str] = mapped_column(String(20), nullable=False, default='execution', server_default='execution')
first_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
last_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
deleted_at_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
managed_test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='SET NULL'), nullable=True)
retirement_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
retirement_confirmed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
retirement_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
deleted_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
review_tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
project: Mapped['Project'] = relationship('Project', back_populates='canonical_test_cases')
test_suite: Mapped['TestSuite'] = relationship('TestSuite', back_populates='canonical_test_cases')
test_cases: Mapped[list['TestCase']] = relationship('TestCase', back_populates='canonical_test_case')
managed_test_case: Mapped[Optional['ManagedTestCase']] = relationship('ManagedTestCase')
steps: Mapped[list['TestStep']] = relationship('TestStep', back_populates='canonical_test_case', cascade='all, delete-orphan', lazy='select')
attachments: Mapped[list['TestAttachment']] = relationship('TestAttachment', back_populates='canonical_test_case', cascade='all, delete-orphan', lazy='select')
```

## backend/app/models/postgres.py — TestStep

[backend/app/models/postgres.py:1127](../../backend/app/models/postgres.py#L1127)

Bases: `Base`.

One granular step in the LATEST-RUN-ONLY snapshot for a logical test.

LOCKED retention model (migration 0093): steps anchor to the project-scoped
``canonical_test_cases`` identity — exactly ONE snapshot per
``(project_id, test_fingerprint)`` — NOT to the per-run ``test_cases`` rows
that accumulate. On ingest of a newer run, ingestion DELETEs this canonical
test's steps and INSERTs the new ones inside the ingestion-pipeline
transaction. ``source_test_run_id`` is provenance only (which run produced
the snapshot). ``parent_step_id`` (self-FK, CASCADE) models nested steps.

```python
__tablename__ = 'test_steps'
__table_args__ = (Index('ix_test_steps_canonical_ordinal', 'canonical_test_case_id', 'ordinal'), Index('ix_test_steps_parent', 'parent_step_id'), Index('ix_test_steps_source_run', 'source_test_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
canonical_test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('canonical_test_cases.id', ondelete='CASCADE'), nullable=False)
source_test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
parent_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_steps.id', ondelete='CASCADE'), nullable=True)
ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text('0'))
name: Mapped[str] = mapped_column(String(2000), nullable=False)
keyword: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
status: Mapped[str] = mapped_column(String(20), nullable=False)
duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
start_ms: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
assertion_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
assertion_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
expected_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
actual_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
parameters: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
canonical_test_case: Mapped['CanonicalTestCase'] = relationship('CanonicalTestCase', back_populates='steps')
children: Mapped[list['TestStep']] = relationship('TestStep', back_populates='parent', cascade='all, delete-orphan', lazy='select')
parent: Mapped[Optional['TestStep']] = relationship('TestStep', back_populates='children', remote_side='TestStep.id')
attachments: Mapped[list['TestAttachment']] = relationship('TestAttachment', back_populates='test_step', cascade='all, delete-orphan', lazy='select')
```

## backend/app/models/postgres.py — TestStepRun

[backend/app/models/postgres.py:1191](../../backend/app/models/postgres.py#L1191)

Bases: `Base`.

Per-run, compact step-outcome history for cross-run step-flip analysis.

Distinct from :class:`TestStep`, which is a LATEST-RUN-ONLY snapshot
(delete+reinsert on every ingest) and therefore cannot answer "which step
flipped between run N-1 and run N". ``test_step_runs`` instead RETAINS one
flat row per ``(canonical_test_case_id, source_test_run_id, ordinal)`` so a
test's step outcomes accumulate across runs (migration 0097).

Deliberately compact: only the identity (ordinal/depth/name/keyword) and the
per-run signal (status/duration_ms) needed to detect a step-flip. The heavy,
PII-bearing columns (assertion_message/trace, expected/actual, parameters,
attachments) live ONLY on the latest-run :class:`TestStep` snapshot and are
NOT duplicated per run. ``source_test_run_id`` is CASCADE (the row IS about
that run — when the run is deleted its step history goes with it), unlike the
snapshot's SET NULL provenance pointer.

Written by ingestion alongside the snapshot, idempotent per ``(canonical,
run)`` (delete this run's rows then reinsert). NEVER committed by the
service — the ingestion router owns the commit.

```python
__tablename__ = 'test_step_runs'
__table_args__ = (UniqueConstraint('canonical_test_case_id', 'source_test_run_id', 'ordinal', name='uq_test_step_runs_canonical_run_ordinal'), Index('ix_test_step_runs_source_run', 'source_test_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
canonical_test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('canonical_test_cases.id', ondelete='CASCADE'), nullable=False)
source_test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text('0'))
name: Mapped[str] = mapped_column(String(2000), nullable=False)
keyword: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
status: Mapped[str] = mapped_column(String(20), nullable=False)
duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — TestAttachment

[backend/app/models/postgres.py:1243](../../backend/app/models/postgres.py#L1243)

Bases: `Base`.

Index-only attachment metadata for the latest-run snapshot.

Phase 1 stores references (``source_ref``) only — bytes are not proxied.
Anchored to the canonical test (CASCADE) like steps; ``test_step_id``
(CASCADE, nullable) links a step-scoped attachment, NULL = test-level.
Migration 0093.

```python
__tablename__ = 'test_attachments'
__table_args__ = (Index('ix_test_attachments_canonical', 'canonical_test_case_id'), Index('ix_test_attachments_step', 'test_step_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
canonical_test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('canonical_test_cases.id', ondelete='CASCADE'), nullable=False)
test_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_steps.id', ondelete='CASCADE'), nullable=True)
source_test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
name: Mapped[str] = mapped_column(String(500), nullable=False)
source_ref: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
media_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
canonical_test_case: Mapped['CanonicalTestCase'] = relationship('CanonicalTestCase', back_populates='attachments')
test_step: Mapped[Optional['TestStep']] = relationship('TestStep', back_populates='attachments')
```

## backend/app/models/postgres.py — TestCaseHistory

[backend/app/models/postgres.py:1280](../../backend/app/models/postgres.py#L1280)

Bases: `Base`.

Denormalized history for fast timeline queries.

```python
__tablename__ = 'test_case_history'
__table_args__ = (Index('ix_history_fingerprint_date', 'test_fingerprint', 'created_at'), Index('ix_history_fingerprint_date_status', 'test_fingerprint', 'created_at', 'status'), Index('ix_history_test_case_id', 'test_case_id'), Index('ix_history_test_run_id', 'test_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_cases.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
status: Mapped[TestStatus] = mapped_column(String(20), nullable=False)
duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
test_case: Mapped['TestCase'] = relationship('TestCase', back_populates='history')
```

## backend/app/models/postgres.py — AIAnalysis

[backend/app/models/postgres.py:1308](../../backend/app/models/postgres.py#L1308)

Bases: `Base`.

Stored AI triage results per test case.

```python
__tablename__ = 'ai_analysis'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_cases.id', ondelete='CASCADE'), unique=True)
root_cause_summary: Mapped[Optional[str]] = mapped_column(Text)
failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
backend_error_found: Mapped[bool] = mapped_column(Boolean, default=False)
pod_issue_found: Mapped[bool] = mapped_column(Boolean, default=False)
is_flaky: Mapped[bool] = mapped_column(Boolean, default=False)
confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
recommended_actions: Mapped[Optional[list]] = mapped_column(JSON)
evidence_references: Mapped[Optional[list]] = mapped_column(JSON)
tools_used: Mapped[Optional[list]] = mapped_column(JSON)
role_actions: Mapped[Optional[dict]] = mapped_column(JSON)
llm_provider: Mapped[Optional[str]] = mapped_column(String(50))
llm_model: Mapped[Optional[str]] = mapped_column(String(100))
requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
routing_metadata: Mapped[Optional[dict]] = mapped_column(JSONB)
test_case: Mapped['TestCase'] = relationship('TestCase', back_populates='ai_analysis')
```

## backend/app/models/postgres.py — Defect

[backend/app/models/postgres.py:1344](../../backend/app/models/postgres.py#L1344)

Bases: `Base`.

Defect records linked to test cases or failure clusters.

```python
__tablename__ = 'defects'
__table_args__ = (Index('ix_defects_test_case_open_unique', 'test_case_id', unique=True, postgresql_where=text("resolution_status = 'OPEN' AND test_case_id IS NOT NULL")), Index('ix_defects_project_id', 'project_id'), Index('ix_defects_project_status_severity', 'project_id', 'resolution_status', 'severity'), Index('ix_defects_project_signature', 'project_id', 'signature_fingerprint', postgresql_where=text('signature_fingerprint IS NOT NULL')))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_cases.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'))
jira_ticket_id: Mapped[Optional[str]] = mapped_column(String(50))
jira_ticket_url: Mapped[Optional[str]] = mapped_column(String(1000))
jira_status: Mapped[Optional[str]] = mapped_column(String(50))
ai_confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
resolution_status: Mapped[str] = mapped_column(String(50), default='OPEN')
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
cluster_id: Mapped[Optional[str]] = mapped_column(String(255))
title: Mapped[Optional[str]] = mapped_column(String(255))
description: Mapped[Optional[str]] = mapped_column(Text())
severity: Mapped[Optional[str]] = mapped_column(String(20))
component: Mapped[Optional[str]] = mapped_column(String(255))
owner_team: Mapped[Optional[str]] = mapped_column(String(255))
labels: Mapped[Optional[list]] = mapped_column(JSON)
criticality_scores: Mapped[Optional[dict]] = mapped_column(JSON)
release_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('releases.id', ondelete='SET NULL'), nullable=True)
affects_releases: Mapped[Optional[list]] = mapped_column(JSON)
evidence_bundle: Mapped[Optional[dict]] = mapped_column(JSON)
duplicate_of: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
promotion_source: Mapped[Optional[str]] = mapped_column(String(50))
approval_status: Mapped[str] = mapped_column(String(20), default='approved')
approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
policy_evaluation: Mapped[Optional[dict]] = mapped_column(JSON)
signature_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
recurrence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default='0')
last_recurrence_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
external_status_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
external_status_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=text('false'))
test_case: Mapped[Optional['TestCase']] = relationship('TestCase', back_populates='defects')
approver: Mapped[Optional['User']] = relationship('User', foreign_keys=[approved_by])
```

## backend/app/models/postgres.py — DefectCandidate

[backend/app/models/postgres.py:1460](../../backend/app/models/postgres.py#L1460)

Bases: `Base`.

Staging area for defect candidates before promotion to full defects.

```python
__tablename__ = 'defect_candidates'
__table_args__ = (Index('ix_defect_cand_run', 'run_id'), Index('ix_defect_cand_status', 'status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
cluster_id: Mapped[str] = mapped_column(String(20), nullable=False)
severity: Mapped[str] = mapped_column(String(20), nullable=False, default='HIGH')
owner_team: Mapped[Optional[str]] = mapped_column(String(255))
component: Mapped[Optional[str]] = mapped_column(String(255))
title: Mapped[str] = mapped_column(String(500), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text)
duplicate_of: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
evidence_bundle: Mapped[Optional[dict]] = mapped_column(JSON)
criticality_scores: Mapped[Optional[dict]] = mapped_column(JSON)
composite_score: Mapped[Optional[float]] = mapped_column(Float)
failure_category: Mapped[Optional[str]] = mapped_column(String(30))
member_count: Mapped[int] = mapped_column(Integer, default=0)
status: Mapped[str] = mapped_column(String(30), nullable=False, default='pending')
promoted_defect_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('defects.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — QualityGate

[backend/app/models/postgres.py:1489](../../backend/app/models/postgres.py#L1489)

Bases: `Base`.

Quality gate rule configuration per project.

```python
__tablename__ = 'quality_gates'
__table_args__ = (Index('ix_quality_gates_project', 'project_id'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'))
name: Mapped[str] = mapped_column(String(255), nullable=False)
rules: Mapped[list] = mapped_column(JSON, default=list)
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
project: Mapped['Project'] = relationship('Project', back_populates='quality_gates')
```

## backend/app/models/postgres.py — CoverageSnapshot

[backend/app/models/postgres.py:1509](../../backend/app/models/postgres.py#L1509)

Bases: `Base`.

Daily test coverage snapshots for trend charts.

```python
__tablename__ = 'coverage_snapshots'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'))
snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
total_suites: Mapped[int] = mapped_column(Integer, default=0)
total_tests: Mapped[int] = mapped_column(Integer, default=0)
automated_count: Mapped[int] = mapped_column(Integer, default=0)
suite_coverage: Mapped[Optional[dict]] = mapped_column(JSON)
__table_args__ = (UniqueConstraint('project_id', 'snapshot_date', name='uq_coverage_project_date'),)
```

## backend/app/models/postgres.py — NotificationPreference

[backend/app/models/postgres.py:1525](../../backend/app/models/postgres.py#L1525)

Bases: `Base`.

Per-user, per-channel notification configuration.

```python
__tablename__ = 'notification_preferences'
__table_args__ = (UniqueConstraint('user_id', 'project_id', 'channel', name='uq_notif_pref'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
channel: Mapped[NotificationChannel] = mapped_column(String(20), nullable=False)
enabled: Mapped[bool] = mapped_column(Boolean, default=True)
events: Mapped[list] = mapped_column(JSONB, default=list)
failure_rate_threshold: Mapped[Optional[float]] = mapped_column(Float, default=80.0)
email_override: Mapped[Optional[str]] = mapped_column(String(255))
slack_webhook_url: Mapped[Optional[str]] = mapped_column(String(2000))
teams_webhook_url: Mapped[Optional[str]] = mapped_column(String(2000))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — NotificationTestState

[backend/app/models/postgres.py:1553](../../backend/app/models/postgres.py#L1553)

Bases: `Base`.

Per-(project, test_fingerprint) rolling state for transition-based
notifications (PMF US-7.1).

The transition engine (``services/notification_transitions.py``) updates
one row per logical test at run finalization and emits a notification
only when the tracked state CHANGES (pass→confirmed-failing,
failing→recovered, entered the known-flaky set). ``last_run_id`` is the
idempotency anchor: re-finalizing the same run skips rows already
stamped with that run, so transitions never double-fire (per-(entity,
run) idempotency convention).

```python
__tablename__ = 'notification_test_states'
__table_args__ = (UniqueConstraint('project_id', 'test_fingerprint', name='uq_notif_test_state_project_fp'), Index('ix_notif_test_states_project', 'project_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
state: Mapped[str] = mapped_column(String(20), nullable=False, default='passing')
consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
last_notified_state: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
is_known_flaky: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
last_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — NotificationTransitionPolicy

[backend/app/models/postgres.py:1599](../../backend/app/models/postgres.py#L1599)

Bases: `Base`.

Per-project policy for transition-based notifications (PMF US-7.1).

One row per project. Semantics of a MISSING row = the new-project
default: transitions ON, per-run spam OFF. Migration 0103 backfills an
explicit row (transitions OFF, per-run ON) for every project existing
at upgrade time so EXISTING projects keep their current behaviour with
no surprise change; projects created afterwards get the new defaults.

```python
__tablename__ = 'notification_transition_policies'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, unique=True)
transitions_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
per_run_events_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
enabled_events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
consecutive_failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — TeamNotificationChannel

[backend/app/models/postgres.py:1629](../../backend/app/models/postgres.py#L1629)

Bases: `Base`.

Per-(project, team) notification target for ownership-routed
transition notifications (PMF US-7.3).

Teams exist only as free-text ``team_name`` strings on
``service_ownership_rules`` rows — many rules can share one team, so
the channel lives in its own table keyed by (project, team_name)
instead of being duplicated per rule. A transition event whose test
resolves (via the ownership rules) to a team with an active row here
is delivered directly to that team's channel; everything else falls
back to the project's default notification preferences.

```python
__tablename__ = 'team_notification_channels'
__table_args__ = (UniqueConstraint('project_id', 'team_name', name='uq_team_notif_channel_project_team'), Index('ix_team_notif_channels_project', 'project_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
team_name: Mapped[str] = mapped_column(String(255), nullable=False)
channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
target: Mapped[str] = mapped_column(String(2000), nullable=False)
is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — AgentInvestigation

[backend/app/models/postgres.py:1665](../../backend/app/models/postgres.py#L1665)

Bases: `Base`.

One Investigator run against a test run (Agentic plan AI-1, shadow).

Persists the full InvestigationDetail wire shape served by
``GET /api/v1/investigations/{id}``: lifecycle status, mode, trigger,
budget + spend JSONB, the per-hypothesis results (written incrementally
as hypothesis nodes complete — the UI polls), the synthesis verdict, the
prompt-version snapshot, and the cooperative-cancel flag.

One ACTIVE investigation per run is enforced by the partial unique index
``uq_agent_investigations_one_active_per_run`` (migration 0108) over
``ACTIVE_STATUSES`` — the API's 409 is backed by a real constraint.

```python
__tablename__ = 'agent_investigations'
__table_args__ = (Index('ix_agent_investigations_project_created', 'project_id', 'created_at'), Index('ix_agent_investigations_run', 'run_id'), Index('uq_agent_investigations_active_run_scope', 'run_id', unique=True, postgresql_where=text("scope_type = 'run' AND status IN ('queued', 'running', 'synthesizing')")), Index('uq_agent_investigations_active_failure_cluster', 'parent_pipeline_run_id', 'failure_cluster_id', unique=True, postgresql_where=text("scope_type = 'failure_cluster' AND status IN ('queued', 'running', 'synthesizing')")), Index('ux_agent_investigations_spawn_key', 'spawn_key', unique=True, postgresql_where=text('spawn_key IS NOT NULL')), CheckConstraint("scope_type IN ('run', 'failure_cluster')", name='ck_agent_investigation_scope_type'), CheckConstraint('spawn_depth >= 0 AND spawn_depth <= 8', name='ck_agent_investigation_spawn_depth'), CheckConstraint("cluster_scope_sha256 IS NULL OR cluster_scope_sha256 ~ '^[0-9a-f]{64}$'", name='ck_agent_investigation_cluster_scope_sha256'), CheckConstraint("spawn_key IS NULL OR spawn_key ~ '^[0-9a-f]{64}$'", name='ck_agent_investigation_spawn_key_sha256'), CheckConstraint("jsonb_typeof(cluster_member_test_ids) = 'array'", name='ck_agent_investigation_cluster_members_array'), CheckConstraint("(scope_type = 'run' AND failure_cluster_id IS NULL AND cluster_scope_sha256 IS NULL AND parent_pipeline_run_id IS NULL AND parent_task_id IS NULL AND spawn_lineage_id IS NULL AND spawn_key IS NULL AND spawn_depth = 0 AND jsonb_array_length(cluster_member_test_ids) = 0) OR (scope_type = 'failure_cluster' AND failure_cluster_id IS NOT NULL AND cluster_scope_sha256 IS NOT NULL AND parent_pipeline_run_id IS NOT NULL AND parent_task_id IS NOT NULL AND length(parent_task_id) > 0 AND spawn_lineage_id IS NOT NULL AND spawn_key IS NOT NULL AND spawn_depth >= 1 AND jsonb_array_length(cluster_member_test_ids) > 0)", name='ck_agent_investigation_scope_consistency'))
ACTIVE_STATUSES = ('queued', 'running', 'synthesizing')
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default='run', server_default='run')
failure_cluster_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('failure_clusters.id', ondelete='CASCADE'), nullable=True)
cluster_scope_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
cluster_member_test_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
parent_pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='CASCADE'), nullable=True)
parent_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
spawn_lineage_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
spawn_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
spawn_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
selection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='queued')
mode: Mapped[str] = mapped_column(String(10), nullable=False, default='shadow')
triggered_by: Mapped[str] = mapped_column(String(40), nullable=False, default='manual')
requested_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
budget: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
spend: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
hypotheses: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
verdict: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
prompt_versions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
model_info: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
cancelled_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — AgentChildDispatchOutbox

[backend/app/models/postgres.py:1809](../../backend/app/models/postgres.py#L1809)

Bases: `Base`.

Durable dispatch record for a planner-spawned Investigator child.

```python
__tablename__ = 'agent_child_dispatch_outbox'
__table_args__ = (Index('ix_agent_child_dispatch_status_next_attempt', 'status', 'next_attempt_at'), CheckConstraint("spawn_key ~ '^[0-9a-f]{64}$'", name='ck_agent_child_outbox_spawn_key_sha256'), CheckConstraint('attempts >= 0', name='ck_agent_child_outbox_attempts_nonnegative'), CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed')", name='ck_agent_child_outbox_status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
spawn_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_investigations.id', ondelete='CASCADE'), nullable=False, unique=True)
parent_pipeline_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', server_default='pending')
attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, onupdate=func.now(), server_default=func.now())
last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

## backend/app/models/postgres.py — AgentRun

[backend/app/models/postgres.py:1875](../../backend/app/models/postgres.py#L1875)

Bases: `Base`.

Agent activity ledger (Agentic plan AI-3): one row per agent
execution — what ran, why, what it proposed, what it actually did
(always nothing in shadow/suggest), and what it cost.

Written on every investigation completion/cancel/failure; mirrored as a
durable event to the Mongo pipeline event log; snapshotted into release
compliance packs (``agent_activity.json``).

```python
__tablename__ = 'agent_runs'
__table_args__ = (Index('ix_agent_runs_project_agent_created', 'project_id', 'agent_id', 'created_at'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
mode: Mapped[str] = mapped_column(String(10), nullable=False, default='shadow')
trigger: Mapped[str] = mapped_column(String(40), nullable=False, default='manual')
status: Mapped[str] = mapped_column(String(20), nullable=False)
summary: Mapped[str] = mapped_column(Text, nullable=False, default='')
actions_proposed: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
actions_taken: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
prompt_registry_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
details_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — FixAttempt

[backend/app/models/postgres.py:1913](../../backend/app/models/postgres.py#L1913)

Bases: `Base`.

One (fixer run, candidate test, attempt) of the Fixer (Agentic plan AI-2).

The Fixer selects flaky/quarantined tests, generates a test-code-only
candidate fix, validates it by rerunning the test in a sandbox, and — in
suggest mode only — opens a DRAFT PR. This row is the progress surface
the UI polls (``GET .../fixer/attempts``) and the per-stage audit trail.

``status`` walks the pinned lifecycle: ``selected`` → ``diagnosing`` →
``generating`` → ``validating`` → one terminal of ``validated`` (all
reruns passed; shadow mode, or suggest with no PR opened),
``rejected_globs`` (the diff touched a non-test file — rejected BEFORE
any execution), ``failed_validation`` (the fix did not validate),
``pr_opened`` (validated + a draft PR was created), ``error`` (infra
trouble — no runner, docker unavailable, generation offline), or
``skipped_budget`` (a budget cap or the kill switch stopped it).

Invariant: a PR is opened ONLY from ``validated`` in suggest mode.
``error`` and ``failed_validation`` NEVER produce a ``pr_url``.

```python
__tablename__ = 'fix_attempts'
__table_args__ = (Index('ix_fix_attempts_project_created', 'project_id', 'created_at'), Index('ix_fix_attempts_fixer_run', 'fixer_run_id'))
TERMINAL_STATUSES = ('validated', 'rejected_globs', 'failed_validation', 'pr_opened', 'error', 'skipped_budget')
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
fixer_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
status: Mapped[str] = mapped_column(String(30), nullable=False, default='selected')
attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
patch_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
patch: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
validation_reruns: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
validation_passed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
runner_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
runner_log_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
egress_opened: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
pr_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
pr_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
pr_state: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
ledger_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

## backend/app/models/postgres.py — AgentPipelineRun

[backend/app/models/postgres.py:1977](../../backend/app/models/postgres.py#L1977)

Bases: `Base`.

Tracks a single execution of the multi-agent pipeline for a test run.

```python
__tablename__ = 'agent_pipeline_runs'
__table_args__ = (Index('ix_pipeline_runs_test_run', 'test_run_id'), Index('ix_pipeline_runs_status', 'status'), CheckConstraint('spawn_depth >= 0 AND spawn_depth <= 8', name='ck_agent_pipeline_spawn_depth'), CheckConstraint("status IN ('pending', 'running', 'retry_wait', 'completed', 'passed', 'failed')", name='ck_agent_pipeline_status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
requested_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
parent_pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True)
parent_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
spawn_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
workflow_type: Mapped[str] = mapped_column(String(20), default='offline')
status: Mapped[str] = mapped_column(String(20), default=PipelineRunStatus.PENDING.value)
started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
error: Mapped[Optional[str]] = mapped_column(Text)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default='5')
next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
lease_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
fencing_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
review_policy: Mapped[str] = mapped_column(String(40), nullable=False, default='human_required', server_default='human_required')
rerun_of: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True)
execution_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
provenance_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
stages: Mapped[list['AgentStageResult']] = relationship('AgentStageResult', back_populates='pipeline_run', cascade='all, delete-orphan')
```

## backend/app/models/postgres.py — AgentStageResult

[backend/app/models/postgres.py:2046](../../backend/app/models/postgres.py#L2046)

Bases: `Base`.

Per-stage result for an AgentPipelineRun.

```python
__tablename__ = 'agent_stage_results'
__table_args__ = (Index('ix_stage_results_pipeline', 'pipeline_run_id'), Index('ix_agent_stage_results_stage_status', 'stage_name', 'status'), Index('ux_agent_stage_pipeline_task_key', 'pipeline_run_id', 'task_key', unique=True, postgresql_where=text('task_key IS NOT NULL')), Index('ux_agent_stage_pipeline_idempotency', 'pipeline_run_id', 'idempotency_key', unique=True, postgresql_where=text('idempotency_key IS NOT NULL')), CheckConstraint('attempt >= 1', name='ck_agent_stage_attempt_positive'), CheckConstraint("idempotency_key IS NULL OR idempotency_key ~ '^[0-9a-f]{64}$'", name='ck_agent_stage_idempotency_sha256'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
pipeline_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='CASCADE'), nullable=False)
task_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
capability_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
parent_task_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
failure_cluster_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('failure_clusters.id', ondelete='SET NULL'), nullable=True)
attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
selected: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
dependencies: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
allocated_budget: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
stop_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
stage_name: Mapped[str] = mapped_column(String(50), nullable=False)
status: Mapped[str] = mapped_column(String(20), default='pending')
started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
result_data: Mapped[Optional[dict]] = mapped_column(JSON)
error: Mapped[Optional[str]] = mapped_column(Text)
skipped_reason: Mapped[Optional[str]] = mapped_column(Text)
execution_path: Mapped[Optional[str]] = mapped_column(String(50))
fallback_used: Mapped[Optional[bool]] = mapped_column(Boolean)
checkpoint_data: Mapped[Optional[dict]] = mapped_column(JSON)
input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
total_tokens: Mapped[Optional[int]] = mapped_column(Integer)
llm_calls_count: Mapped[Optional[int]] = mapped_column(Integer)
cost_usd: Mapped[Optional[float]] = mapped_column(Float)
error_category: Mapped[Optional[str]] = mapped_column(String(30))
confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
evidence_count: Mapped[Optional[int]] = mapped_column(Integer)
route_rationale: Mapped[Optional[str]] = mapped_column(Text)
decision_log: Mapped[Optional[list]] = mapped_column(JSON)
fallback_reason: Mapped[Optional[str]] = mapped_column(String(200))
analysis_mode: Mapped[Optional[str]] = mapped_column(String(20))
pipeline_run: Mapped['AgentPipelineRun'] = relationship('AgentPipelineRun', back_populates='stages')
```

## backend/app/models/postgres.py — ChatSession

[backend/app/models/postgres.py:2131](../../backend/app/models/postgres.py#L2131)

Bases: `Base`.

A conversation session between a user and the Conversation Agent.

```python
__tablename__ = 'chat_sessions'
__table_args__ = (Index('ix_chat_sessions_user', 'user_id'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True)
active_test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True, index=True)
active_report_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
active_report_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
title: Mapped[Optional[str]] = mapped_column(String(500))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
messages: Mapped[list['ChatMessage']] = relationship('ChatMessage', back_populates='session', cascade='all, delete-orphan')
```

## backend/app/models/postgres.py — ChatMessage

[backend/app/models/postgres.py:2153](../../backend/app/models/postgres.py#L2153)

Bases: `Base`.

A single message in a ChatSession.

```python
__tablename__ = 'chat_messages'
__table_args__ = (Index('ix_chat_messages_session', 'session_id', 'created_at'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('chat_sessions.id', ondelete='CASCADE'), nullable=False)
role: Mapped[str] = mapped_column(String(20), nullable=False)
content: Mapped[str] = mapped_column(Text, nullable=False)
sources: Mapped[Optional[list]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
session: Mapped['ChatSession'] = relationship('ChatSession', back_populates='messages')
```

## backend/app/models/postgres.py — FeedbackRating

[backend/app/models/postgres.py:2171](../../backend/app/models/postgres.py#L2171)

Bases: `str, PyEnum`.



```python
CORRECT = 'correct'
INCORRECT = 'incorrect'
PARTIALLY_CORRECT = 'partially_correct'
```

## backend/app/models/postgres.py — AIFeedback

[backend/app/models/postgres.py:2177](../../backend/app/models/postgres.py#L2177)

Bases: `Base`.

Human feedback on AI triage results — the primary training signal.

Sources:
  - manual: engineer rates analysis card in the UI (explicit)
  - jira_resolved: Jira ticket created by AI was resolved (implicit positive)
  - jira_invalid: Jira ticket closed as invalid/won't-fix (implicit negative)
  - category_correction: engineer changed the failure_category in the UI

```python
__tablename__ = 'ai_feedback'
__table_args__ = (CheckConstraint("eval_manifest_checksum IS NULL OR eval_manifest_checksum ~ '^[0-9a-f]{64}$'", name='ck_ai_feedback_eval_manifest_checksum'), Index('ix_ai_feedback_analysis', 'analysis_id'), Index('ix_ai_feedback_created', 'created_at'), Index('ix_ai_feedback_rating', 'rating'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('ai_analysis.id', ondelete='CASCADE'), nullable=False)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_cases.id', ondelete='CASCADE'), nullable=False, index=True)
user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
rating: Mapped[FeedbackRating] = mapped_column(String(25), nullable=False)
corrected_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30), nullable=True)
corrected_root_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
source: Mapped[str] = mapped_column(String(50), default='manual')
exported: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
eval_manifest_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
```

## backend/app/models/postgres.py — DecisionReportFeedback

[backend/app/models/postgres.py:2217](../../backend/app/models/postgres.py#L2217)

Bases: `Base`.

Structured human feedback bound to one immutable DecisionReport version.

```python
__tablename__ = 'decision_report_feedback'
__table_args__ = (Index('ix_decision_report_feedback_report', 'project_id', 'test_run_id', 'report_id', 'report_version'), Index('ix_decision_report_feedback_created', 'created_at'), UniqueConstraint('user_id', 'idempotency_key', name='uq_decision_report_feedback_user_idempotency'), CheckConstraint('report_version >= 1', name='ck_decision_report_feedback_report_version_positive'), CheckConstraint("feedback_kind IN ('utility', 'claim_correction')", name='ck_decision_report_feedback_kind'), CheckConstraint("utility_rating IS NULL OR utility_rating IN ('useful', 'partially_useful', 'not_useful')", name='ck_decision_report_feedback_utility_rating'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
report_id: Mapped[str] = mapped_column(String(64), nullable=False)
report_version: Mapped[int] = mapped_column(Integer, nullable=False)
report_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
feedback_kind: Mapped[str] = mapped_column(String(30), nullable=False)
utility_rating: Mapped[Optional[str]] = mapped_column(String(25), nullable=True)
claim_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
claim_kind: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
correction_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
corrected_value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
```

## backend/app/models/postgres.py — ModelVersion

[backend/app/models/postgres.py:2246](../../backend/app/models/postgres.py#L2246)

Bases: `Base`.

Registry of fine-tuned model versions per training track.

Tracks the full lifecycle: training → evaluation → active/retired.
The model_registry service uses this table + Redis for hot-swap lookups.

```python
__tablename__ = 'model_versions'
__table_args__ = (Index('ix_model_versions_track_status', 'track', 'status'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
track: Mapped[str] = mapped_column(String(30), nullable=False)
model_name: Mapped[str] = mapped_column(String(200), nullable=False)
provider: Mapped[str] = mapped_column(String(30), nullable=False)
status: Mapped[str] = mapped_column(String(20), default='training')
training_examples: Mapped[int] = mapped_column(Integer, default=0)
holdout_examples: Mapped[int] = mapped_column(Integer, default=0)
eval_accuracy: Mapped[Optional[float]] = mapped_column(Float)
baseline_accuracy: Mapped[Optional[float]] = mapped_column(Float)
eval_details: Mapped[Optional[dict]] = mapped_column(JSON)
provider_job_id: Mapped[Optional[str]] = mapped_column(String(200))
training_file_path: Mapped[Optional[str]] = mapped_column(String(1000))
promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — FailureCluster

[backend/app/models/postgres.py:2283](../../backend/app/models/postgres.py#L2283)

Bases: `Base`.

Semantic cluster of failures grouped by root-cause similarity.

```python
__tablename__ = 'failure_clusters'
__table_args__ = (Index('ix_failure_clusters_run', 'test_run_id'), UniqueConstraint('pipeline_run_id', 'cluster_id', name='uq_failure_clusters_pipeline_cluster'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True)
cluster_id: Mapped[str] = mapped_column(String(20), nullable=False)
label: Mapped[str] = mapped_column(String(500), nullable=False)
representative_error: Mapped[Optional[str]] = mapped_column(Text)
member_test_ids: Mapped[list] = mapped_column(JSON, default=list)
size: Mapped[int] = mapped_column(Integer, default=1)
cohesion_score: Mapped[Optional[float]] = mapped_column(Float)
regression_classification: Mapped[Optional[str]] = mapped_column(String(50))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — RunCommitRange

[backend/app/models/postgres.py:2308](../../backend/app/models/postgres.py#L2308)

Bases: `Base`.

Commit range associated with a run — Epic 8 US-8.1 (migration 0110).

One row per run (``run_id`` UNIQUE, idempotent per run). Records the
commits landed since the run's last-green baseline. ``source``:
``connector`` (fetched from the GitHub integration), ``supplied``
(caller pushed the list on ingest — air-gapped path, no VCS call;
supplied always wins over connector), or ``unavailable`` (neither
path yielded data — honest empty state). ``commits`` is a bounded
list of ``{sha, author, message, files, committed_at}`` ordered
oldest→newest. Suspect ranking (US-8.2) reads this table.

``base_source`` (migration 0116) records HOW ``base_commit`` was
anchored — a strong anchor and a weak one must never look alike:

  - ``supplied``           — the caller pushed the base ref itself.
  - ``green_baseline``     — last fully-green prior run (strongest).
  - ``last_completed_run`` — most recent completed prior run, regardless
    of pass/fail. WEAKER: "landed since" is then only true relative to
    that run, not relative to a known-good state.
  - ``unavailable``        — no base could be determined.

```python
__tablename__ = 'run_commit_ranges'
__table_args__ = (UniqueConstraint('run_id', name='uq_run_commit_ranges_run'), Index('ix_run_commit_ranges_project_resolved', 'project_id', 'resolved_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
base_commit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
head_commit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
base_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
source: Mapped[str] = mapped_column(String(20), nullable=False, server_default='unavailable')
base_source: Mapped[str] = mapped_column(String(30), nullable=False, default='unavailable', server_default='unavailable')
commits: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default='[]')
resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — DeepFinding

[backend/app/models/postgres.py:2359](../../backend/app/models/postgres.py#L2359)

Bases: `Base`.

Deep investigation result per failure cluster.

Writers (AI-F4): the deep pipeline persists one row per
(test_run_id, cluster_id) via ``agents/deep_persistence.py``
(``log_evidence.origin == "pipeline"``); the demo seed scripts tag
theirs ``origin == "seed"``. ``causal_chain`` / ``affected_services`` /
``contract_violations`` are only populated by seeds today. The
``contract_validation`` and ``log_intelligence`` stages DO run in the
deep graph and produce findings, but nothing folds their output into
these columns — so they stay None rather than carrying a value this
table would imply came from the cluster synthesis.

```python
__tablename__ = 'deep_findings'
__table_args__ = (Index('ix_deep_findings_run', 'test_run_id'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
cluster_id: Mapped[str] = mapped_column(String(20), nullable=False)
root_cause: Mapped[Optional[str]] = mapped_column(Text)
failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
causal_chain: Mapped[Optional[list]] = mapped_column(JSON)
evidence: Mapped[Optional[list]] = mapped_column(JSON)
affected_services: Mapped[Optional[list]] = mapped_column(JSON)
contract_violations: Mapped[Optional[list]] = mapped_column(JSON)
log_evidence: Mapped[Optional[dict]] = mapped_column(JSON)
recommended_actions: Mapped[Optional[list]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — ReleaseDecision

[backend/app/models/postgres.py:2392](../../backend/app/models/postgres.py#L2392)

Bases: `Base`.

Release gate decision produced by ReleaseRiskAgent.

```python
__tablename__ = 'release_decisions'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False, unique=True)
pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True, index=True)
recommendation: Mapped[str] = mapped_column(String(20), nullable=False)
risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
blocking_issues: Mapped[Optional[list]] = mapped_column(JSON)
conditions_for_go: Mapped[Optional[list]] = mapped_column(JSON)
reasoning: Mapped[Optional[str]] = mapped_column(Text)
dimension_scores: Mapped[Optional[dict]] = mapped_column(JSON)
composite_risk: Mapped[Optional[float]] = mapped_column(Float)
score_model_version: Mapped[Optional[int]] = mapped_column(Integer)
human_override: Mapped[Optional[str]] = mapped_column(Text)
overridden_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
input_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
override_audit: Mapped[Optional[list]] = mapped_column(JSON)
original_recommendation: Mapped[Optional[str]] = mapped_column(String(20))
original_risk_score: Mapped[Optional[int]] = mapped_column(Integer)
policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('release_gate_policies.id', ondelete='SET NULL'), nullable=True)
policy_evaluation: Mapped[Optional[dict]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — ContractViolation

[backend/app/models/postgres.py:2426](../../backend/app/models/postgres.py#L2426)

Bases: `Base`.

API contract violation detected by ContractAgent.

```python
__tablename__ = 'contract_violations'
__table_args__ = (Index('ix_contract_violations_run', 'test_run_id'), Index('ix_contract_violations_tc', 'test_case_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_cases.id', ondelete='CASCADE'), nullable=False)
endpoint: Mapped[Optional[str]] = mapped_column(String(500))
violation_type: Mapped[str] = mapped_column(String(50), nullable=False)
field_path: Mapped[Optional[str]] = mapped_column(String(500))
expected: Mapped[Optional[str]] = mapped_column(String(500))
actual: Mapped[Optional[str]] = mapped_column(String(500))
severity: Mapped[str] = mapped_column(String(20), default='warning')
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — NotificationLog

[backend/app/models/postgres.py:2446](../../backend/app/models/postgres.py#L2446)

Bases: `Base`.

Audit trail for every dispatched notification.

```python
__tablename__ = 'notification_logs'
__table_args__ = (Index('ix_notif_log_user_created', 'user_id', 'created_at'), Index('ix_notif_log_project', 'project_id'), Index('uq_notification_log_delivery_key', 'delivery_key', unique=True), Index('ix_notification_log_delivery_due', 'status', 'next_delivery_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True)
run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
preference_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('notification_preferences.id', ondelete='SET NULL'), nullable=True)
delivery_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
delivery_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default='0')
delivery_token: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
delivery_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
delivery_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
next_delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
channel: Mapped[str] = mapped_column(String(20), nullable=False)
event_type: Mapped[str] = mapped_column(String(50), nullable=False)
title: Mapped[str] = mapped_column(String(500), nullable=False)
body: Mapped[str] = mapped_column(Text, nullable=False)
status: Mapped[str] = mapped_column(String(20), default='pending')
error_detail: Mapped[Optional[str]] = mapped_column(Text)
routed_team: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
routing_fallback: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
is_read: Mapped[bool] = mapped_column(Boolean, default=False)
sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — ManagedTestCase

[backend/app/models/postgres.py:2510](../../backend/app/models/postgres.py#L2510)

Bases: `Base`.

Manually authored or AI-generated test case with full lifecycle management.

```python
__tablename__ = 'managed_test_cases'
__table_args__ = (Index('ix_mtc_project_status', 'project_id', 'status'), Index('ix_mtc_project_fingerprint', 'project_id', 'test_fingerprint'), Index('ix_mtc_project_last_executed', 'project_id', 'last_executed_at'), Index('ix_mtc_author', 'author_id'), Index('ix_mtc_fingerprint', 'test_fingerprint'), Index('ix_mtc_dup_fingerprint', 'dup_fingerprint'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
title: Mapped[str] = mapped_column(String(500), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text)
objective: Mapped[Optional[str]] = mapped_column(Text)
preconditions: Mapped[Optional[str]] = mapped_column(Text)
steps: Mapped[Optional[list]] = mapped_column(JSON)
expected_result: Mapped[Optional[str]] = mapped_column(Text)
test_data: Mapped[Optional[str]] = mapped_column(Text)
test_type: Mapped[str] = mapped_column(String(50), default='functional')
priority: Mapped[str] = mapped_column(String(20), default='medium')
severity: Mapped[str] = mapped_column(String(20), default='major')
feature_area: Mapped[Optional[str]] = mapped_column(String(500))
suite_name: Mapped[Optional[str]] = mapped_column(String(500))
test_suite_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_suites.id', ondelete='SET NULL'), nullable=True)
tags: Mapped[Optional[list]] = mapped_column(JSON)
parameters: Mapped[Optional[list]] = mapped_column(JSON)
status: Mapped[str] = mapped_column(String(30), default=TestCaseLifecycleState.DRAFT.value, index=True)
version: Mapped[int] = mapped_column(Integer, default=1)
lifecycle_state_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
needs_update_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
deprecation_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
deprecated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
deprecated_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
archived_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
author_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
assignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
is_automated: Mapped[bool] = mapped_column(Boolean, default=False)
automation_status: Mapped[str] = mapped_column(String(30), default='not_automated')
test_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
dup_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
ai_generation_prompt: Mapped[Optional[str]] = mapped_column(Text)
ai_quality_score: Mapped[Optional[int]] = mapped_column(Integer)
ai_review_notes: Mapped[Optional[dict]] = mapped_column(JSON)
estimated_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
last_executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_execution_status: Mapped[Optional[str]] = mapped_column(String(20))
generation_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('generation_batches.id', ondelete='SET NULL', use_alter=True), nullable=True)
is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
stale_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
faithfulness_score: Mapped[Optional[float]] = mapped_column(Float)
faithfulness_evaluator: Mapped[Optional[str]] = mapped_column(String(30))
faithfulness_evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
needs_review_reason: Mapped[Optional[str]] = mapped_column(String(500))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — TestCaseVersion

[backend/app/models/postgres.py:2631](../../backend/app/models/postgres.py#L2631)

Bases: `Base`.

Immutable snapshot of a ManagedTestCase at each save.

```python
__tablename__ = 'test_case_versions'
__table_args__ = (Index('ix_tcv_test_case', 'test_case_id'), UniqueConstraint('test_case_id', 'version', name='uq_test_case_versions_case_version'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
version: Mapped[int] = mapped_column(Integer, nullable=False)
title: Mapped[str] = mapped_column(String(500), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text)
objective: Mapped[Optional[str]] = mapped_column(Text)
preconditions: Mapped[Optional[str]] = mapped_column(Text)
steps: Mapped[Optional[list]] = mapped_column(JSON)
parameters: Mapped[Optional[list]] = mapped_column(JSON)
expected_result: Mapped[Optional[str]] = mapped_column(Text)
test_data: Mapped[Optional[str]] = mapped_column(Text)
test_type: Mapped[Optional[str]] = mapped_column(String(50))
priority: Mapped[Optional[str]] = mapped_column(String(20))
severity: Mapped[Optional[str]] = mapped_column(String(20))
feature_area: Mapped[Optional[str]] = mapped_column(String(500))
suite_name: Mapped[Optional[str]] = mapped_column(String(500))
test_suite_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
tags: Mapped[Optional[list]] = mapped_column(JSON)
estimated_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
is_automated: Mapped[Optional[bool]] = mapped_column(Boolean)
automation_status: Mapped[Optional[str]] = mapped_column(String(30))
test_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
status: Mapped[str] = mapped_column(String(30), nullable=False)
changed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
change_summary: Mapped[Optional[str]] = mapped_column(String(500))
change_type: Mapped[str] = mapped_column(String(30), default='updated')
changed_fields: Mapped[Optional[list]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — TestCaseReview

[backend/app/models/postgres.py:2676](../../backend/app/models/postgres.py#L2676)

Bases: `Base`.

Review cycle for a ManagedTestCase.

```python
__tablename__ = 'test_case_reviews'
__table_args__ = (Index('ix_tcr_test_case', 'test_case_id'), Index('ix_tcr_reviewer', 'reviewer_id'), Index('uq_test_case_reviews_one_open_per_case', 'test_case_id', unique=True, postgresql_where=text("status IN ('pending', 'in_progress')")))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
requested_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
status: Mapped[str] = mapped_column(String(30), default='pending')
ai_review_completed: Mapped[bool] = mapped_column(Boolean, default=False)
ai_quality_score: Mapped[Optional[int]] = mapped_column(Integer)
ai_review_notes: Mapped[Optional[dict]] = mapped_column(JSON)
ai_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
human_notes: Mapped[Optional[str]] = mapped_column(Text)
reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — TestCaseComment

[backend/app/models/postgres.py:2712](../../backend/app/models/postgres.py#L2712)

Bases: `Base`.

Threaded comment on a ManagedTestCase.

```python
__tablename__ = 'test_case_comments'
__table_args__ = (Index('ix_tcc_test_case', 'test_case_id'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
author_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
content: Mapped[str] = mapped_column(Text, nullable=False)
comment_type: Mapped[str] = mapped_column(String(30), default='general')
parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_case_comments.id', ondelete='SET NULL'), nullable=True)
step_number: Mapped[Optional[int]] = mapped_column(Integer)
is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — DuplicateTestCaseCandidate

[backend/app/models/postgres.py:2733](../../backend/app/models/postgres.py#L2733)

Bases: `Base`.

One detected near-duplicate PAIR of authored ManagedTestCases (Phase 4).

Tiered, offline-first, per-project duplicate detection (migration 0094).
Exactly one row per logical pair: the producer enforces canonical ordering
``case_a_id < case_b_id`` (also a DB CHECK), and the pair is idempotent on
``(project_id, case_a_id, case_b_id)``.

EXPLAINABILITY is first-class: every row carries the component breakdown
(``component_scores``), an aggregate ``score`` (0.0-1.0), a human-readable
``reason``, the detection ``method`` (fingerprint|structural|semantic), and a
``band`` (exact|strong|possible).

MERGE IS NON-DESTRUCTIVE this phase: resolving a pair only flips ``status``
(open → merged|dismissed) and may set a soft deprecate flag on the losing
case. It never deletes cases or redirects ``test_fingerprint``.

```python
__tablename__ = 'duplicate_test_case_candidates'
__table_args__ = (UniqueConstraint('project_id', 'case_a_id', 'case_b_id', name='uq_dup_candidate_pair'), CheckConstraint('case_a_id < case_b_id', name='ck_dup_candidate_canonical_order'), Index('ix_dup_candidate_project_status', 'project_id', 'status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
case_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
case_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
band: Mapped[str] = mapped_column(String(20), nullable=False)
score: Mapped[float] = mapped_column(Float, nullable=False)
reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
method: Mapped[str] = mapped_column(String(20), nullable=False)
component_scores: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
status: Mapped[str] = mapped_column(String(20), nullable=False, default='open', server_default=text("'open'"))
```

## backend/app/models/postgres.py — DismissedDuplicatePair

[backend/app/models/postgres.py:2776](../../backend/app/models/postgres.py#L2776)

Bases: `Base`.

Suppression record so a dismissed duplicate pair stays dismissed (Phase 4).

Consulted by the detector before staging a candidate so a re-detection run
never resurfaces a pair the user already dismissed. Same canonical ordering
(``case_a_id < case_b_id``, DB CHECK) + uniqueness on
``(project_id, case_a_id, case_b_id)`` as the candidate table.

```python
__tablename__ = 'dismissed_duplicate_pairs'
__table_args__ = (UniqueConstraint('project_id', 'case_a_id', 'case_b_id', name='uq_dismissed_dup_pair'), CheckConstraint('case_a_id < case_b_id', name='ck_dismissed_dup_canonical_order'), Index('ix_dismissed_dup_project', 'project_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
case_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
case_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
dismissed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — TestPlan

[backend/app/models/postgres.py:2807](../../backend/app/models/postgres.py#L2807)

Bases: `Base`.

Named collection of test cases forming an executable test plan.

```python
__tablename__ = 'test_plans'
__table_args__ = (Index('ix_tp_project', 'project_id'), Index('ix_tp_status', 'status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
name: Mapped[str] = mapped_column(String(500), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text)
objective: Mapped[Optional[str]] = mapped_column(Text)
status: Mapped[str] = mapped_column(String(30), default='draft')
planned_start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
planned_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
actual_start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
actual_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
assigned_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
ai_generation_context: Mapped[Optional[str]] = mapped_column(Text)
total_cases: Mapped[int] = mapped_column(Integer, default=0)
executed_cases: Mapped[int] = mapped_column(Integer, default=0)
passed_cases: Mapped[int] = mapped_column(Integer, default=0)
failed_cases: Mapped[int] = mapped_column(Integer, default=0)
blocked_cases: Mapped[int] = mapped_column(Integer, default=0)
tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — TestPlanItem

[backend/app/models/postgres.py:2850](../../backend/app/models/postgres.py#L2850)

Bases: `Base`.

A test case entry within a TestPlan.

```python
__tablename__ = 'test_plan_items'
__table_args__ = (Index('ix_tpi_plan', 'plan_id'), UniqueConstraint('plan_id', 'test_case_id', name='uq_plan_test_case'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_plans.id', ondelete='CASCADE'), nullable=False)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
order_index: Mapped[int] = mapped_column(Integer, default=0)
priority_override: Mapped[Optional[str]] = mapped_column(String(20))
execution_status: Mapped[str] = mapped_column(String(30), default='not_run')
executed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
execution_notes: Mapped[Optional[str]] = mapped_column(Text)
actual_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
test_case_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_cases.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — TestStrategy

[backend/app/models/postgres.py:2879](../../backend/app/models/postgres.py#L2879)

Bases: `Base`.

AI-generated or manually authored test strategy document for a project.

```python
__tablename__ = 'test_strategies'
__table_args__ = (Index('ix_ts_project', 'project_id'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
name: Mapped[str] = mapped_column(String(500), nullable=False)
version_label: Mapped[str] = mapped_column(String(50), default='v1.0')
status: Mapped[str] = mapped_column(String(30), default='draft')
objective: Mapped[Optional[str]] = mapped_column(Text)
scope: Mapped[Optional[str]] = mapped_column(Text)
out_of_scope: Mapped[Optional[str]] = mapped_column(Text)
test_approach: Mapped[Optional[str]] = mapped_column(Text)
risk_assessment: Mapped[Optional[list]] = mapped_column(JSON)
test_types: Mapped[Optional[list]] = mapped_column(JSON)
entry_criteria: Mapped[Optional[list]] = mapped_column(JSON)
exit_criteria: Mapped[Optional[list]] = mapped_column(JSON)
environments: Mapped[Optional[list]] = mapped_column(JSON)
automation_approach: Mapped[Optional[str]] = mapped_column(Text)
defect_management: Mapped[Optional[str]] = mapped_column(Text)
ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
generation_context: Mapped[Optional[str]] = mapped_column(Text)
ai_model_used: Mapped[Optional[str]] = mapped_column(String(100))
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — KnowledgeSourceType

[backend/app/models/postgres.py:2923](../../backend/app/models/postgres.py#L2923)

Bases: `str, PyEnum`.



```python
JIRA_ISSUE = 'jira_issue'
JIRA_EPIC = 'jira_epic'
CONFLUENCE_PAGE = 'confluence_page'
UPLOADED_DOC = 'uploaded_document'
INTERNAL_URL = 'internal_url'
EXTERNAL_URL = 'external_url'
```

## backend/app/models/postgres.py — KnowledgeSyncStatus

[backend/app/models/postgres.py:2932](../../backend/app/models/postgres.py#L2932)

Bases: `str, PyEnum`.



```python
PENDING = 'pending'
SYNCING = 'syncing'
SYNCED = 'synced'
FAILED = 'failed'
SKIPPED = 'skipped'
```

## backend/app/models/postgres.py — KnowledgeClassification

[backend/app/models/postgres.py:2940](../../backend/app/models/postgres.py#L2940)

Bases: `str, PyEnum`.



```python
PUBLIC = 'public'
INTERNAL = 'internal'
CONFIDENTIAL = 'confidential'
RESTRICTED = 'restricted'
```

## backend/app/models/postgres.py — KnowledgeSource

[backend/app/models/postgres.py:2947](../../backend/app/models/postgres.py#L2947)

Bases: `Base`.

Registry of external knowledge sources attached to a project for RAG-grounded test generation.

```python
__tablename__ = 'knowledge_sources'
__table_args__ = (UniqueConstraint('project_id', 'canonical_url', name='uq_ks_project_url'), Index('ix_ks_project_type', 'project_id', 'source_type'), Index('ix_ks_project_status', 'project_id', 'sync_status'), Index('ix_ks_owner', 'owner_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
source_type: Mapped[str] = mapped_column(String(30), nullable=False)
title: Mapped[str] = mapped_column(String(500), nullable=False)
canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)
external_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default=KnowledgeSyncStatus.PENDING.value)
last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
classification: Mapped[str] = mapped_column(String(20), nullable=False, default=KnowledgeClassification.INTERNAL.value)
is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
storage_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — KnowledgeSyncEvent

[backend/app/models/postgres.py:2982](../../backend/app/models/postgres.py#L2982)

Bases: `Base`.

Audit log for every sync attempt on a KnowledgeSource.

```python
__tablename__ = 'knowledge_sync_events'
__table_args__ = (Index('ix_kse_source_created', 'source_id', 'created_at'), Index('ix_kse_project_created', 'project_id', 'created_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('knowledge_sources.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
trigger: Mapped[str] = mapped_column(String(20), nullable=False, default='manual')
status: Mapped[str] = mapped_column(String(20), nullable=False)
content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
previous_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
content_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — KnowledgeChunk

[backend/app/models/postgres.py:3008](../../backend/app/models/postgres.py#L3008)

Bases: `Base`.

PostgreSQL metadata for each chunk stored in ChromaDB knowledge_chunks collection.

```python
__tablename__ = 'knowledge_chunks'
__table_args__ = (Index('ix_kc_source_active', 'source_id', 'is_active'), Index('ix_kc_project_active', 'project_id', 'is_active'), Index('ix_kc_sync_version', 'source_id', 'sync_version'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('knowledge_sources.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
vector_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
section_heading: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
requirement_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
chunk_text_preview: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
sync_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — GenerationBatch

[backend/app/models/postgres.py:3038](../../backend/app/models/postgres.py#L3038)

Bases: `Base`.

One row per AI test-case generation request (grounded or raw).

```python
__tablename__ = 'generation_batches'
__table_args__ = (Index('ix_gb_project_created', 'project_id', 'created_at'), Index('ix_gb_author', 'created_by_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
prompt_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
source_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
generation_mode: Mapped[str] = mapped_column(String(20), nullable=False, default='raw')
generation_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
cases_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
cases_accepted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
cases_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
coverage_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
prompt_redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
llm_model_used: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

## backend/app/models/postgres.py — GenerationCaseSource

[backend/app/models/postgres.py:3069](../../backend/app/models/postgres.py#L3069)

Bases: `Base`.

Maps a generated ManagedTestCase to the KnowledgeChunks cited for it.

```python
__tablename__ = 'generation_case_sources'
__table_args__ = (UniqueConstraint('case_id', 'chunk_vector_id', name='uq_gcs_case_chunk'), Index('ix_gcs_case_id', 'case_id'), Index('ix_gcs_batch_id', 'batch_id'), Index('ix_gcs_source_id', 'source_id'), Index('ix_gcs_stale', 'is_stale'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('generation_batches.id', ondelete='CASCADE'), nullable=False)
case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='CASCADE'), nullable=False)
source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('knowledge_sources.id', ondelete='CASCADE'), nullable=False)
chunk_vector_id: Mapped[str] = mapped_column(String(64), nullable=False)
relevance_score: Mapped[Optional[float]] = mapped_column(nullable=True)
section_heading: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
chunk_text_preview: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
source_content_hash_at_generation: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
stale_detected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — RequirementCoverage

[backend/app/models/postgres.py:3097](../../backend/app/models/postgres.py#L3097)

Bases: `Base`.

Tracks which requirements are covered/uncovered by a generation batch.

```python
__tablename__ = 'requirement_coverage'
__table_args__ = (UniqueConstraint('batch_id', 'requirement_id', name='uq_rc_batch_req'), Index('ix_rc_batch_id', 'batch_id'), Index('ix_rc_project_req', 'project_id', 'requirement_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('generation_batches.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
requirement_id: Mapped[str] = mapped_column(String(200), nullable=False)
requirement_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
coverage_status: Mapped[str] = mapped_column(String(20), nullable=False, default='uncovered')
covered_by_case_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — LiveSession

[backend/app/models/postgres.py:3116](../../backend/app/models/postgres.py#L3116)

Bases: `Base`.

Tracks an active live test execution session from a client machine.

Client machines register a session before streaming events.
The session_token (stored as a SHA-256 hash here; plaintext lives in Redis)
is used for lightweight authentication on the hot-path batch endpoint —
avoiding JWT decode + DB query overhead at 10k+ concurrent sessions.

```python
__tablename__ = 'live_sessions'
__table_args__ = (Index('ix_live_sessions_project_status', 'project_id', 'status'), Index('ix_live_sessions_token_hash', 'session_token_hash'), Index('ix_live_sessions_started_at', 'started_at'), Index('ux_live_sessions_active_project_run', 'project_id', 'run_id', unique=True, postgresql_where=text("status = 'active'")))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
run_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
client_name: Mapped[str] = mapped_column(String(255), nullable=False)
machine_id: Mapped[Optional[str]] = mapped_column(String(255))
build_number: Mapped[Optional[str]] = mapped_column(String(100))
framework: Mapped[Optional[str]] = mapped_column(String(50))
branch: Mapped[Optional[str]] = mapped_column(String(255))
commit_hash: Mapped[Optional[str]] = mapped_column(String(64))
session_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
status: Mapped[str] = mapped_column(String(20), default='active', index=True)
release_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
launch_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
suite_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
total_tests: Mapped[int] = mapped_column(Integer, default=0)
events_received: Mapped[int] = mapped_column(Integer, default=0)
extra_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — TestCaseAuditLog

[backend/app/models/postgres.py:3181](../../backend/app/models/postgres.py#L3181)

Bases: `Base`.

Append-only compliance audit trail for all test management actions.

**Append-only by application convention, not by database enforcement.**
There is no UPDATE trigger, no revoked grant and no WORM storage on this
table — the only trigger in the whole migration set is the search-vector
one in ``0001``. What actually holds the property is the
``backend.audit-write-discipline`` quality gate
(``scripts/quality_gate.py``): it fails CI on any application code that
UPDATEs an audit table, or DELETEs from one outside
``services/retention_service.py``.

Rows ARE removed — deliberately — by the US-11.4 retention purge on the
**audit clock** (``ProjectRetentionPolicy.audit_days``; floor 365 days,
default 2555 ≈ 7 years, and validated to be ≥ ``runs_days`` so audit rows
outlive the runs they describe). Purges only run for projects that
explicitly enable a policy, and every execute-mode purge writes its own
``settings_audit_log`` record.

Beyond that boundary, durability is the **operator's** responsibility:
anyone holding direct Postgres credentials can still rewrite or drop rows.
Real immutability comes from outside the application — restricted
UPDATE/DELETE grants for the app role, WORM / object-lock storage for
shipped logs, and off-host backups.

```python
__tablename__ = 'test_case_audit_logs'
__table_args__ = (Index('ix_tcal_entity', 'entity_type', 'entity_id'), Index('ix_tcal_project_created', 'project_id', 'created_at'), Index('ix_tcal_actor', 'actor_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True)
action: Mapped[str] = mapped_column(String(50), nullable=False)
actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
actor_name: Mapped[Optional[str]] = mapped_column(String(200))
old_values: Mapped[Optional[dict]] = mapped_column(JSON)
new_values: Mapped[Optional[dict]] = mapped_column(JSON)
details: Mapped[Optional[str]] = mapped_column(Text)
reason: Mapped[Optional[str]] = mapped_column(String(500))
policy_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
transition_from: Mapped[Optional[str]] = mapped_column(String(30))
transition_to: Mapped[Optional[str]] = mapped_column(String(30))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
```

## backend/app/models/postgres.py — SuiteMembership

[backend/app/models/postgres.py:3239](../../backend/app/models/postgres.py#L3239)

Bases: `Base`.

Tracks which test cases belong to which suite, linked to the run that confirmed membership.

```python
__tablename__ = 'suite_memberships'
__table_args__ = (UniqueConstraint('project_id', 'suite_name', 'test_fingerprint', name='uq_suite_membership'), Index('ix_sm_project_suite', 'project_id', 'suite_name'), Index('ix_sm_fingerprint', 'test_fingerprint'), Index('ix_sm_status', 'status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
class_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
managed_test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('managed_test_cases.id', ondelete='SET NULL'), nullable=True)
source: Mapped[str] = mapped_column(String(20), nullable=False, default='execution')
status: Mapped[str] = mapped_column(String(20), nullable=False, default='active')
last_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
first_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
deleted_at_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
review_tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — SuiteMembershipEvent

[backend/app/models/postgres.py:3269](../../backend/app/models/postgres.py#L3269)

Bases: `Base`.

Immutable audit log of suite membership changes detected during sync.

```python
__tablename__ = 'suite_membership_events'
__table_args__ = (Index('ix_sme_project_suite', 'project_id', 'suite_name', 'created_at'), Index('ix_sme_run', 'run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[str] = mapped_column(String(1000), nullable=False, default='')
event_type: Mapped[str] = mapped_column(String(20), nullable=False)
run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
old_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
new_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — LinkSource

[backend/app/models/postgres.py:3293](../../backend/app/models/postgres.py#L3293)

Bases: `str, PyEnum`.

How a run came to be attributed to a release (migration 0150).

The ordering here is the resolution ladder's own precedence, strongest
evidence first. Everything from ``RULE_MATCH`` down is an *inference*, and
the release scorecard reports the split so a verdict never hides how much
of its evidence was asserted versus derived.

```python
EXPLICIT_CLIENT = 'explicit_client'
MANUAL_UI = 'manual_ui'
EXTERNAL_MATCH = 'external_match'
RULE_MATCH = 'rule_match'
CUTOFF_WINDOW = 'cutoff_window'
ACTIVE_RELEASE = 'active_release'
DEFAULT_FALLBACK = 'default_fallback'
UNKNOWN = 'unknown'
```

## backend/app/models/postgres.py — Release

[backend/app/models/postgres.py:3345](../../backend/app/models/postgres.py#L3345)

Bases: `Base`.

A software release tracked through the QA lifecycle.

```python
__tablename__ = 'releases'
__table_args__ = (Index('ix_releases_project_status', 'project_id', 'status'), Index('ix_releases_project_default', 'project_id', unique=True, postgresql_where=text('is_default IS TRUE')), Index('ix_releases_project_active', 'project_id', unique=True, postgresql_where=text('is_active IS TRUE')), Index('ix_releases_project_sort', 'project_id', 'sort_key'), Index('ix_releases_external_identity', 'project_id', 'source_system', 'external_id', unique=True, postgresql_where=text('external_id IS NOT NULL')))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
name: Mapped[str] = mapped_column(String(255), nullable=False)
version: Mapped[Optional[str]] = mapped_column(String(100))
description: Mapped[Optional[str]] = mapped_column(Text)
status: Mapped[str] = mapped_column(String(30), default='planning')
is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
is_auto_named: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
release_type: Mapped[Optional[str]] = mapped_column(String(20))
sort_key: Mapped[Optional[str]] = mapped_column(String(64))
baseline_release_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('releases.id', ondelete='SET NULL'), nullable=True)
cutoff_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
cutoff_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
target_environment: Mapped[Optional[str]] = mapped_column(String(100))
source_system: Mapped[Optional[str]] = mapped_column(String(20))
external_id: Mapped[Optional[str]] = mapped_column(String(255))
external_url: Mapped[Optional[str]] = mapped_column(String(1000))
last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
planned_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
phases: Mapped[list['ReleasePhase']] = relationship('ReleasePhase', back_populates='release', cascade='all, delete-orphan', order_by='ReleasePhase.order_index')
test_run_links: Mapped[list['ReleaseTestRunLink']] = relationship('ReleaseTestRunLink', back_populates='release', cascade='all, delete-orphan')
```

## backend/app/models/postgres.py — ReleaseOutcome

[backend/app/models/postgres.py:3528](../../backend/app/models/postgres.py#L3528)

Bases: `Base`.

A human-marked production incident or rollback for a release (E9.9).

```python
__tablename__ = 'release_outcomes'
__table_args__ = (CheckConstraint("outcome_kind IN ('incident', 'rollback')", name='ck_release_outcomes_kind'), CheckConstraint('length(trim(reason)) >= 3', name='ck_release_outcomes_reason'), Index('ix_release_outcomes_release_marked', 'release_id', 'marked_at'), Index('ix_release_outcomes_project_marked', 'project_id', 'marked_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('releases.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
outcome_kind: Mapped[str] = mapped_column(String(20), nullable=False)
reason: Mapped[str] = mapped_column(Text, nullable=False)
marked_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

## backend/app/models/postgres.py — AttributionMatchField

[backend/app/models/postgres.py:3564](../../backend/app/models/postgres.py#L3564)

Bases: `str, PyEnum`.

What a rule looks at on the run (migration 0154).

Every value must be a column that exists on ``TestRun`` at ingest time —
a rule matching on something derived later would evaluate against NULL
and silently never fire.

```python
BRANCH = 'branch'
BUILD_NUMBER = 'build_number'
ENVIRONMENT = 'environment'
TAG = 'tag'
```

## backend/app/models/postgres.py — ReleaseAttributionRule

[backend/app/models/postgres.py:3578](../../backend/app/models/postgres.py#L3578)

Bases: `Base`.

Per-project rule mapping run metadata to a release (migration 0154).

The bridge for teams that cannot send an explicit ``release_name``: match
on what a run already carries and name the release it belongs to. Without
these, such projects fall straight to the active release, which cannot tell
a hotfix branch from a release candidate from trunk CI.

```python
__tablename__ = 'release_attribution_rules'
__table_args__ = (Index('ix_attribution_rules_project_priority', 'project_id', 'priority'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
name: Mapped[str] = mapped_column(String(255), nullable=False)
priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default='100')
is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
match_field: Mapped[str] = mapped_column(String(30), nullable=False)
match_pattern: Mapped[str] = mapped_column(String(255), nullable=False)
target_release_name: Mapped[str] = mapped_column(String(255), nullable=False)
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — ReleasePhase

[backend/app/models/postgres.py:3624](../../backend/app/models/postgres.py#L3624)

Bases: `Base`.

A phase / milestone within a Release lifecycle.

```python
__tablename__ = 'release_phases'
__table_args__ = (Index('ix_release_phases_release', 'release_id'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('releases.id', ondelete='CASCADE'), nullable=False)
name: Mapped[str] = mapped_column(String(255), nullable=False)
phase_type: Mapped[str] = mapped_column(String(50), default='qa_testing')
status: Mapped[str] = mapped_column(String(30), default='pending')
description: Mapped[Optional[str]] = mapped_column(Text)
order_index: Mapped[int] = mapped_column(Integer, default=0)
planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
planned_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
exit_criteria: Mapped[Optional[dict]] = mapped_column(JSON)
notes: Mapped[Optional[str]] = mapped_column(Text)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
release: Mapped['Release'] = relationship('Release', back_populates='phases')
```

## backend/app/models/postgres.py — ReleaseTestRunLink

[backend/app/models/postgres.py:3659](../../backend/app/models/postgres.py#L3659)

Bases: `Base`.

Links a test run to a release for metrics aggregation.

```python
__tablename__ = 'release_test_run_links'
__table_args__ = (UniqueConstraint('release_id', 'test_run_id', name='uq_release_test_run'), Index('ix_rtr_links_release', 'release_id'), Index('ix_rtr_links_test_run', 'test_run_id'), Index('ix_rtr_links_primary', 'test_run_id', unique=True, postgresql_where=text('is_primary IS TRUE')))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('releases.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
phase_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('release_phases.id', ondelete='SET NULL'), nullable=True)
link_source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
linked_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
release: Mapped['Release'] = relationship('Release', back_populates='test_run_links')
```

## backend/app/models/postgres.py — SecretRef

[backend/app/models/postgres.py:3722](../../backend/app/models/postgres.py#L3722)

Bases: `Base`.

Stores sensitive values (API keys, tokens, passwords) separately from settings.

```python
__tablename__ = 'secret_refs'
__table_args__ = (Index('ix_secret_refs_scope_key', 'scope', 'key_name', unique=True),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
scope: Mapped[str] = mapped_column(String(100), nullable=False)
provider: Mapped[str] = mapped_column(String(50), nullable=False, default='db')
key_name: Mapped[str] = mapped_column(String(255), nullable=False)
encrypted_value: Mapped[Optional[str]] = mapped_column(Text)
masked_value: Mapped[Optional[str]] = mapped_column(String(50))
rotation_status: Mapped[str] = mapped_column(String(30), default='active')
updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — AppSetting

[backend/app/models/postgres.py:3741](../../backend/app/models/postgres.py#L3741)

Bases: `Base`.

Key-value store for application-level configuration (e.g. SMTP settings).

```python
__tablename__ = 'app_settings'
key: Mapped[str] = mapped_column(String(100), primary_key=True)
value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
secret_ref_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('secret_refs.id', ondelete='SET NULL'), nullable=True)
is_secret_backed: Mapped[bool] = mapped_column(Boolean, default=False)
updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — SettingsAuditLog

[backend/app/models/postgres.py:3758](../../backend/app/models/postgres.py#L3758)

Bases: `Base`.

Append-only audit trail for settings and secret changes.

**Append-only by application convention, not by database enforcement** —
no UPDATE trigger, no revoked grant, no WORM storage. The property is held
by the ``backend.audit-write-discipline`` quality gate
(``scripts/quality_gate.py``), which fails CI on application code that
UPDATEs an audit table or DELETEs from one outside
``services/retention_service.py``.

Unlike the other audit tables this one is **never purged**: it has no
project scope and it holds the retention purge-audit records themselves
(``setting_key = "retention_purge:{project_id}"``), so the US-11.4 audit
clock deliberately skips it. It therefore grows without bound until an
operator prunes it out-of-band.

Beyond that, durability is the **operator's** responsibility: direct
Postgres credentials can still rewrite or drop rows. Real immutability
comes from restricted UPDATE/DELETE grants for the app role, WORM /
object-lock storage for shipped logs, and off-host backups.

```python
__tablename__ = 'settings_audit_log'
__table_args__ = (Index('ix_settings_audit_key', 'setting_key'), Index('ix_settings_audit_actor', 'actor_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
setting_key: Mapped[str] = mapped_column(String(100), nullable=False)
action: Mapped[str] = mapped_column(String(30), nullable=False)
actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
actor_name: Mapped[Optional[str]] = mapped_column(String(200))
changed_fields: Mapped[Optional[list]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — ProjectMember

[backend/app/models/postgres.py:3796](../../backend/app/models/postgres.py#L3796)

Bases: `Base`.

Per-project role assignment for a user.

```python
__tablename__ = 'project_members'
__table_args__ = (UniqueConstraint('user_id', 'project_id', name='uq_project_member'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.QA_ENGINEER.value)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — ApiKey

[backend/app/models/postgres.py:3810](../../backend/app/models/postgres.py#L3810)

Bases: `Base`.

Scoped personal access token (PAT) for CI/CD and API access.

When ``project_id`` is NULL the key is **user-scoped** and inherits the
owning user's project permissions.  When set, the key is
**project-scoped** — requests using this key are restricted to the
specified project.

```python
__tablename__ = 'api_keys'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True, index=True)
name: Mapped[str] = mapped_column(String(100), nullable=False)
key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
key_hint: Mapped[str] = mapped_column(String(12), nullable=False)
scopes: Mapped[list] = mapped_column(JSON, default=list)
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
minted_by_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('api_keys.id', ondelete='SET NULL', name='fk_api_keys_minted_by_key_id'), nullable=True, index=True)
```

## backend/app/models/postgres.py — RunIntelligenceSnapshot

[backend/app/models/postgres.py:3844](../../backend/app/models/postgres.py#L3844)

Bases: `Base`.

Cached run intelligence payload for fast page loads.

```python
__tablename__ = 'run_intelligence_snapshots'
__table_args__ = (Index('ix_ris_run_id', 'run_id', unique=True), Index('ix_ris_generated', 'generated_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False, unique=True)
schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
payload: Mapped[dict] = mapped_column(JSON, nullable=False)
generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
stale: Mapped[bool] = mapped_column(Boolean, default=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — EvidenceArtifact

[backend/app/models/postgres.py:3863](../../backend/app/models/postgres.py#L3863)

Bases: `Base`.

Tenant-bound immutable evidence captured from an executed tool.

```python
__tablename__ = 'evidence_artifacts'
__table_args__ = (Index('ix_evidence_run_id', 'run_id'), Index('ix_evidence_cluster', 'cluster_id'), Index('ix_evidence_project_run', 'project_id', 'run_id'), Index('ix_evidence_run_test', 'run_id', 'test_case_id'), Index('ix_evidence_pipeline', 'producer_pipeline_run_id'), Index('ux_evidence_idempotency', 'idempotency_key', unique=True), CheckConstraint("content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'", name='ck_evidence_content_sha256'), CheckConstraint('content_size_bytes IS NULL OR content_size_bytes >= 0', name='ck_evidence_content_size_nonnegative'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
producer_pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True)
cluster_id: Mapped[Optional[str]] = mapped_column(String(20))
test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_cases.id', ondelete='SET NULL'), nullable=True)
artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
source_system: Mapped[str] = mapped_column(String(100), nullable=False)
uri_or_ref: Mapped[Optional[str]] = mapped_column(String(1000))
summary_excerpt: Mapped[Optional[str]] = mapped_column(Text)
relevance_score: Mapped[Optional[float]] = mapped_column(Float)
schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
source_version: Mapped[Optional[str]] = mapped_column(String(100))
content_sha256: Mapped[Optional[str]] = mapped_column(String(64))
content_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
media_type: Mapped[Optional[str]] = mapped_column(String(100))
sensitivity: Mapped[Optional[str]] = mapped_column(String(20))
freshness: Mapped[Optional[str]] = mapped_column(String(20))
observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
integrity_status: Mapped[str] = mapped_column(String(30), nullable=False, default='legacy_unverified')
retention_class: Mapped[str] = mapped_column(String(30), nullable=False, default='artifacts')
idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — AIProvenanceRecord

[backend/app/models/postgres.py:3931](../../backend/app/models/postgres.py#L3931)

Bases: `Base`.

Tracks which model/method produced each AI conclusion.

Retention (US-11.4, migration 0113): provenance is AUDIT-class data —
it must outlive the run it describes. ``run_id`` therefore detaches
(``SET NULL``) when the run is purged on the runs clock, and the row
itself is deleted only by the retention audit clock via the dedicated
``project_id`` scope column (backfilled from test_runs in 0113).

```python
__tablename__ = 'ai_provenance_records'
__table_args__ = (Index('ix_provenance_entity', 'entity_type', 'entity_id'), Index('ix_provenance_run', 'run_id'), Index('ix_provenance_project', 'project_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
model_name: Mapped[Optional[str]] = mapped_column(String(200))
fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
confidence: Mapped[Optional[int]] = mapped_column(Integer)
confidence_reason: Mapped[Optional[str]] = mapped_column(Text)
evidence_count: Mapped[int] = mapped_column(Integer, default=0)
sources_used: Mapped[Optional[list]] = mapped_column(JSON)
deterministic_checks_used: Mapped[Optional[list]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — RunBaseline

[backend/app/models/postgres.py:3964](../../backend/app/models/postgres.py#L3964)

Bases: `Base`.

Records which baseline was selected for a run and why.

```python
__tablename__ = 'run_baselines'
__table_args__ = (Index('ix_run_baselines_run_id', 'run_id', unique=True), Index('ix_run_baselines_baseline', 'baseline_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False, unique=True)
baseline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
selection_reason: Mapped[str] = mapped_column(String(100), nullable=False)
classification: Mapped[str] = mapped_column(String(50), nullable=False)
baseline_build_number: Mapped[Optional[str]] = mapped_column(String(100))
pass_rate_delta: Mapped[Optional[float]] = mapped_column(Float)
commit_range: Mapped[Optional[dict]] = mapped_column(JSON)
config_drift: Mapped[Optional[list]] = mapped_column(JSON)
computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — RunDiff

[backend/app/models/postgres.py:3984](../../backend/app/models/postgres.py#L3984)

Bases: `Base`.

Persisted diff payload for a run — avoids recomputation.

```python
__tablename__ = 'run_diffs'
__table_args__ = (Index('ix_run_diffs_run_id', 'run_id', unique=True), Index('ix_run_diffs_baseline_run_id', 'baseline_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False, unique=True)
baseline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
diff_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — RunComparisonReport

[backend/app/models/postgres.py:4003](../../backend/app/models/postgres.py#L4003)

Bases: `Base`.

Cached AI report for a run or suite comparison.

```python
__tablename__ = 'run_comparison_reports'
__table_args__ = (UniqueConstraint('project_id', 'left_run_id', 'right_run_id', 'suite_name_normalized', 'prompt_version', name='uq_run_comparison_report_scope'), Index('ix_run_comparison_reports_project', 'project_id', 'created_at'), Index('ix_run_comparison_reports_runs', 'left_run_id', 'right_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
left_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
right_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
suite_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
suite_name_normalized: Mapped[str] = mapped_column(String(500), nullable=False, default='')
compare_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
ai_report: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='queued')
fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default='run_compare_v1')
model_name: Mapped[Optional[str]] = mapped_column(String(200))
created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
error_message: Mapped[Optional[str]] = mapped_column(Text)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — IntegrationHealthCheck

[backend/app/models/postgres.py:4037](../../backend/app/models/postgres.py#L4037)

Bases: `Base`.

Integration provider health status (latest snapshot per provider).

```python
__tablename__ = 'integration_health_checks'
provider: Mapped[str] = mapped_column(String(50), primary_key=True)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='unknown')
last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
message: Mapped[Optional[str]] = mapped_column(Text)
response_ms: Mapped[Optional[int]] = mapped_column(Integer)
consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
```

## backend/app/models/postgres.py — IntegrationProbeResult

[backend/app/models/postgres.py:4050](../../backend/app/models/postgres.py#L4050)

Bases: `Base`.

Historical record of each integration health probe (OPS-01).

```python
__tablename__ = 'integration_probe_results'
__table_args__ = (Index('ix_ipr_provider_time', 'provider', 'checked_at'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
provider: Mapped[str] = mapped_column(String(50), nullable=False)
status: Mapped[str] = mapped_column(String(20), nullable=False)
response_ms: Mapped[Optional[int]] = mapped_column(Integer)
message: Mapped[Optional[str]] = mapped_column(Text)
auth_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
payload_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — TenantOnboardingStatus

[backend/app/models/postgres.py:4067](../../backend/app/models/postgres.py#L4067)

Bases: `Base`.

Tracks onboarding wizard progress per project.

```python
__tablename__ = 'tenant_onboarding_status'
__table_args__ = (Index('ix_tos_project_step', 'project_id', 'step_key', unique=True),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
step_key: Mapped[str] = mapped_column(String(50), nullable=False)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
completed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — ProductUsageEvent

[backend/app/models/postgres.py:4083](../../backend/app/models/postgres.py#L4083)

Bases: `Base`.

Tracks product adoption events for analytics.

```python
__tablename__ = 'product_usage_events'
__table_args__ = (Index('ix_pue_user', 'user_id'), Index('ix_pue_event', 'event_name'), Index('ix_pue_created', 'created_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True)
event_name: Mapped[str] = mapped_column(String(100), nullable=False)
event_payload: Mapped[Optional[dict]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — AccessAuditLog

[backend/app/models/postgres.py:4100](../../backend/app/models/postgres.py#L4100)

Bases: `Base`.

Append-only audit trail for user role and project membership changes.

**Append-only by application convention, not by database enforcement** —
no UPDATE trigger, no revoked grant, no WORM storage. The property is held
by the ``backend.audit-write-discipline`` quality gate
(``scripts/quality_gate.py``), which fails CI on application code that
UPDATEs an audit table or DELETEs from one outside
``services/retention_service.py``.

Rows ARE removed — deliberately — by the US-11.4 retention purge on the
**audit clock** (``ProjectRetentionPolicy.audit_days``; floor 365 days,
default 2555 ≈ 7 years, validated ≥ ``runs_days``), and only for projects
that explicitly enabled a policy.

Beyond that boundary, durability is the **operator's** responsibility:
direct Postgres credentials can still rewrite or drop rows. Real
immutability comes from restricted UPDATE/DELETE grants for the app role,
WORM / object-lock storage for shipped logs, and off-host backups.

```python
__tablename__ = 'access_audit_logs'
__table_args__ = (Index('ix_aal_actor', 'actor_user_id'), Index('ix_aal_target', 'target_user_id'), Index('ix_aal_created', 'created_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
actor_name: Mapped[Optional[str]] = mapped_column(String(200))
target_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True)
action: Mapped[str] = mapped_column(String(50), nullable=False)
before_value: Mapped[Optional[dict]] = mapped_column(JSON)
after_value: Mapped[Optional[dict]] = mapped_column(JSON)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — UserInvitation

[backend/app/models/postgres.py:4138](../../backend/app/models/postgres.py#L4138)

Bases: `Base`.

Email invite token for onboarding new users.

```python
__tablename__ = 'user_invitations'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.QA_ENGINEER)
token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
invited_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
is_used: Mapped[bool] = mapped_column(Boolean, default=False)
expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — TestHealthRecommendation

[backend/app/models/postgres.py:4154](../../backend/app/models/postgres.py#L4154)

Bases: `Base`.

Persisted test health findings per run from TestHealthAgent.

```python
__tablename__ = 'test_health_recommendations'
__table_args__ = (Index('ix_thr_run', 'test_run_id'), Index('ix_thr_test_case', 'test_case_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_cases.id', ondelete='CASCADE'), nullable=False)
test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
health_score: Mapped[int] = mapped_column(Integer, nullable=False)
violations: Mapped[Optional[list]] = mapped_column(JSON, default=list)
critical_count: Mapped[int] = mapped_column(Integer, default=0)
warning_count: Mapped[int] = mapped_column(Integer, default=0)
recommendation: Mapped[Optional[str]] = mapped_column(Text)
anti_patterns: Mapped[Optional[list]] = mapped_column(JSON, default=list)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — FlakyCoachResult

[backend/app/models/postgres.py:4175](../../backend/app/models/postgres.py#L4175)

Bases: `Base`.

Project-level flaky test coaching results with quarantine recommendations.

```python
__tablename__ = 'flaky_coach_results'
__table_args__ = (Index('ix_fcr_project', 'project_id'), Index('ix_fcr_fingerprint', 'test_fingerprint'), Index('ix_fcr_quarantine', 'quarantine_recommendation'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
suite_name: Mapped[Optional[str]] = mapped_column(String(500))
failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
failed_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
flaky_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
quarantine_recommendation: Mapped[str] = mapped_column(String(30), nullable=False, default='MONITOR')
stabilization_actions: Mapped[Optional[list]] = mapped_column(JSON, default=list)
impact_score: Mapped[float] = mapped_column(Float, default=0.0)
status_history: Mapped[Optional[list]] = mapped_column(JSON, default=list)
flaky_confidence_low: Mapped[Optional[float]] = mapped_column(Float)
flaky_confidence_high: Mapped[Optional[float]] = mapped_column(Float)
is_flaky_confidence: Mapped[Optional[float]] = mapped_column(Float)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — SSOProviderType

[backend/app/models/postgres.py:4214](../../backend/app/models/postgres.py#L4214)

Bases: `str, PyEnum`.



```python
SAML = 'SAML'
OIDC = 'OIDC'
```

## backend/app/models/postgres.py — SSOEnforcementMode

[backend/app/models/postgres.py:4219](../../backend/app/models/postgres.py#L4219)

Bases: `str, PyEnum`.



```python
OPTIONAL = 'OPTIONAL'
SSO_REQUIRED = 'SSO_REQUIRED'
```

## backend/app/models/postgres.py — IdentityEventType

[backend/app/models/postgres.py:4224](../../backend/app/models/postgres.py#L4224)

Bases: `str, PyEnum`.



```python
SSO_LOGIN = 'SSO_LOGIN'
SSO_LOGIN_FAILED = 'SSO_LOGIN_FAILED'
SSO_CONFIG_CREATED = 'SSO_CONFIG_CREATED'
SSO_CONFIG_UPDATED = 'SSO_CONFIG_UPDATED'
SSO_CONFIG_DELETED = 'SSO_CONFIG_DELETED'
SSO_TEST_CONNECTION = 'SSO_TEST_CONNECTION'
SCIM_USER_CREATED = 'SCIM_USER_CREATED'
SCIM_USER_UPDATED = 'SCIM_USER_UPDATED'
SCIM_USER_DEACTIVATED = 'SCIM_USER_DEACTIVATED'
SCIM_USER_REACTIVATED = 'SCIM_USER_REACTIVATED'
SCIM_SYNC_ERROR = 'SCIM_SYNC_ERROR'
SCIM_TOKEN_CREATED = 'SCIM_TOKEN_CREATED'
SCIM_TOKEN_REVOKED = 'SCIM_TOKEN_REVOKED'
ADMIN_FALLBACK_LOGIN = 'ADMIN_FALLBACK_LOGIN'
JIT_PROVISIONED = 'JIT_PROVISIONED'
ROLE_MAPPED = 'ROLE_MAPPED'
MFA_ENROLL_STARTED = 'MFA_ENROLL_STARTED'
MFA_ENABLED = 'MFA_ENABLED'
MFA_DISABLED = 'MFA_DISABLED'
MFA_VERIFY_SUCCESS = 'MFA_VERIFY_SUCCESS'
MFA_VERIFY_FAILED = 'MFA_VERIFY_FAILED'
MFA_RECOVERY_CODE_USED = 'MFA_RECOVERY_CODE_USED'
MFA_RECOVERY_CODES_REISSUED = 'MFA_RECOVERY_CODES_REISSUED'
MFA_BREAKGLASS_RESET = 'MFA_BREAKGLASS_RESET'
MFA_POLICY_UPDATED = 'MFA_POLICY_UPDATED'
ACCOUNT_LOCKED = 'ACCOUNT_LOCKED'
ACCOUNT_UNLOCKED = 'ACCOUNT_UNLOCKED'
```

## backend/app/models/postgres.py — SSOConfiguration

[backend/app/models/postgres.py:4264](../../backend/app/models/postgres.py#L4264)

Bases: `Base`.

Tenant/system-level SSO configuration for a SAML identity provider.

```python
__tablename__ = 'sso_configurations'
__table_args__ = (Index('ix_sso_config_active', 'is_active'), Index('uq_sso_config_single_active', 'is_active', unique=True, postgresql_where=text('is_active IS TRUE'), sqlite_where=text('is_active IS TRUE')))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
display_name: Mapped[str] = mapped_column(String(255), nullable=False)
provider_type: Mapped[SSOProviderType] = mapped_column(String(20), nullable=False, default=SSOProviderType.SAML.value)
idp_entity_id: Mapped[str] = mapped_column(String(1000), nullable=False)
idp_sso_url: Mapped[str] = mapped_column(String(2000), nullable=False)
idp_slo_url: Mapped[Optional[str]] = mapped_column(String(2000))
idp_certificate: Mapped[str] = mapped_column(Text, nullable=False)
sp_entity_id: Mapped[str] = mapped_column(String(1000), nullable=False)
sp_acs_url: Mapped[str] = mapped_column(String(2000), nullable=False)
audience: Mapped[Optional[str]] = mapped_column(String(1000))
role_mapping: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
default_role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.VIEWER.value)
group_attribute: Mapped[Optional[str]] = mapped_column(String(255))
enforcement_mode: Mapped[SSOEnforcementMode] = mapped_column(String(20), nullable=False, default=SSOEnforcementMode.OPTIONAL.value)
is_active: Mapped[bool] = mapped_column(Boolean, default=False)
last_test_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_test_success: Mapped[Optional[bool]] = mapped_column(Boolean)
last_test_error: Mapped[Optional[str]] = mapped_column(Text)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — FederatedIdentity

[backend/app/models/postgres.py:4309](../../backend/app/models/postgres.py#L4309)

Bases: `Base`.

Links an external IdP subject to a local User.

```python
__tablename__ = 'federated_identities'
__table_args__ = (UniqueConstraint('sso_config_id', 'external_id', name='uq_federated_identity'), Index('ix_federated_user', 'user_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
sso_config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('sso_configurations.id', ondelete='CASCADE'), nullable=False)
external_id: Mapped[str] = mapped_column(String(1000), nullable=False)
external_email: Mapped[Optional[str]] = mapped_column(String(255))
external_display_name: Mapped[Optional[str]] = mapped_column(String(500))
external_groups: Mapped[Optional[list]] = mapped_column(JSON, default=list)
last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — SCIMToken

[backend/app/models/postgres.py:4329](../../backend/app/models/postgres.py#L4329)

Bases: `Base`.

Bearer token for SCIM 2.0 provisioning endpoints.

```python
__tablename__ = 'scim_tokens'
__table_args__ = (Index('ix_scim_token_hash', 'token_hash', unique=True),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
name: Mapped[str] = mapped_column(String(255), nullable=False)
token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
token_hint: Mapped[str] = mapped_column(String(12), nullable=False)
sso_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('sso_configurations.id', ondelete='SET NULL'))
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — IdentityEvent

[backend/app/models/postgres.py:4348](../../backend/app/models/postgres.py#L4348)

Bases: `Base`.

Append-only audit trail for SSO, SCIM, and identity lifecycle events.

**Append-only by application convention, not by database enforcement** —
no UPDATE trigger, no revoked grant, no WORM storage. The property is held
by the ``backend.audit-write-discipline`` quality gate
(``scripts/quality_gate.py``), which fails CI on application code that
UPDATEs an audit table or DELETEs from one outside
``services/retention_service.py``.

Not currently covered by any retention clock: the US-11.4 purge does not
touch this table (it has no project scope), so rows accumulate until an
operator prunes them out-of-band.

Beyond that, durability is the **operator's** responsibility: direct
Postgres credentials can still rewrite or drop rows. Real immutability
comes from restricted UPDATE/DELETE grants for the app role, WORM /
object-lock storage for shipped logs, and off-host backups.

```python
__tablename__ = 'identity_events'
__table_args__ = (Index('ix_identity_event_type', 'event_type'), Index('ix_identity_event_user', 'user_id'), Index('ix_identity_event_created', 'created_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
event_type: Mapped[IdentityEventType] = mapped_column(String(40), nullable=False)
user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
sso_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('sso_configurations.id', ondelete='SET NULL'))
actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
actor_name: Mapped[Optional[str]] = mapped_column(String(200))
detail: Mapped[Optional[dict]] = mapped_column(JSON)
ip_address: Mapped[Optional[str]] = mapped_column(String(45))
success: Mapped[bool] = mapped_column(Boolean, default=True)
error_message: Mapped[Optional[str]] = mapped_column(Text)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — ReleaseGatePolicy

[backend/app/models/postgres.py:4390](../../backend/app/models/postgres.py#L4390)

Bases: `Base`.

Versioned release gate policy — per-project or system-wide default.

```python
__tablename__ = 'release_gate_policies'
__table_args__ = (Index('ix_rgp_project_active', 'project_id', 'is_active'), UniqueConstraint('project_id', 'version', name='uq_rgp_project_version'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
version: Mapped[int] = mapped_column(Integer, nullable=False)
name: Mapped[str] = mapped_column(String(255), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text)
rules: Mapped[dict] = mapped_column(JSON, nullable=False)
is_active: Mapped[bool] = mapped_column(Boolean, default=False)
is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=False)
activated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — ReportShareLink

[backend/app/models/postgres.py:4416](../../backend/app/models/postgres.py#L4416)

Bases: `Base`.

Time-limited share token for run intelligence reports.

The raw token is shown once at creation and never persisted. Only the
SHA-256 hex digest (``token_hash``) is stored, so a DB compromise cannot
recover active share tokens.

```python
__tablename__ = 'report_share_links'
__table_args__ = (Index('ix_rsl_token_hash', 'token_hash', unique=True), Index('ix_rsl_run', 'run_id'), Index('ix_rsl_expires', 'expires_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
report_layout: Mapped[str] = mapped_column(String(20), nullable=False, default='executive')
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_by_name: Mapped[Optional[str]] = mapped_column(String(200))
expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
access_count: Mapped[int] = mapped_column(Integer, default=0)
last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — RefreshTokenRecord

[backend/app/models/postgres.py:4444](../../backend/app/models/postgres.py#L4444)

Bases: `Base`.

Server-side record of issued refresh tokens for rotation and replay detection.

Each refresh token carries a random ``jti`` claim; only the SHA-256 hex digest
is persisted. On refresh, the record is marked ``rotated_to_id`` and a new
record is created. Presenting an already-rotated or revoked token triggers
family-wide revocation for the owning user (``replay_detected = true``).

```python
__tablename__ = 'refresh_token_records'
__table_args__ = (Index('ix_rtr_jti_hash', 'jti_hash', unique=True), Index('ix_rtr_user_id', 'user_id'), Index('ix_rtr_expires_at', 'expires_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
jti_hash: Mapped[str] = mapped_column(String(64), nullable=False)
issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
rotated_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
replay_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

## backend/app/models/postgres.py — ServiceOwnershipRule

[backend/app/models/postgres.py:4472](../../backend/app/models/postgres.py#L4472)

Bases: `Base`.

Maps a matcher pattern (suite, component, package, path) to a team/service owner.

```python
__tablename__ = 'service_ownership_rules'
__table_args__ = (Index('ix_sor_project', 'project_id'), Index('ix_sor_active', 'project_id', 'is_active'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
match_type: Mapped[str] = mapped_column(String(30), nullable=False)
match_pattern: Mapped[str] = mapped_column(String(500), nullable=False)
service_name: Mapped[str] = mapped_column(String(255), nullable=False)
team_name: Mapped[str] = mapped_column(String(255), nullable=False)
team_contact: Mapped[Optional[str]] = mapped_column(String(500))
priority: Mapped[int] = mapped_column(Integer, default=0)
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — SavedView

[backend/app/models/postgres.py:4500](../../backend/app/models/postgres.py#L4500)

Bases: `Base`.

Persisted filter/scope configuration — personal or shared.

The `filters` JSON field supports both legacy filter-only payloads and
analytics widget configurations (AC-2):
  Legacy: {"severity": "critical", "date_range": 7}
  Analytics: {"page": "dashboard", "widgets": ["w1", "w2"], "filters": {...}, "version": 1}

```python
__tablename__ = 'saved_views'
__table_args__ = (Index('ix_sv_user', 'user_id'), Index('ix_sv_project', 'project_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
name: Mapped[str] = mapped_column(String(255), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text)
page: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
filters: Mapped[dict] = mapped_column(JSON, nullable=False)
is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
is_default: Mapped[bool] = mapped_column(Boolean, default=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — ReleaseGateDecision

[backend/app/models/postgres.py:4527](../../backend/app/models/postgres.py#L4527)

Bases: `Base`.

A go/no-go verdict for a RELEASE, not for a run (migration 0156).

``ReleaseDecision`` — the table this sits beside, not replaces — is keyed
``test_run_id`` UNIQUE: exactly one verdict per run, answering "is this run
shippable?". That is a real question, and it is not the one a release
manager asks. "Is 2.4.0 shippable?" is a judgement over the whole set of
runs attributed to the release, and it has no run to hang off.

Append-only, deliberately
-------------------------
A verdict is evidence about a moment. Re-evaluating UPDATEs nothing: it
inserts a new row and demotes the previous one, so the history of what was
decided, on what evidence, under which policy, survives. A gate that
rewrites its own past cannot be audited, and "why did we ship that?" is
exactly the question this table exists to answer months later.

Two partial unique indexes
--------------------------
One CURRENT release-level verdict per release, and one CURRENT verdict per
phase — with history rows carrying ``is_current = false`` and constrained by
neither. Partial rather than plain, for the same reason ``is_active`` is on
``Release``: an unfiltered unique index would collapse the history this
table is built to keep.

Phase-level rows land here from S6b; the column and its index exist now so
that slice does not need a second migration against a live table.

Everything is SNAPSHOTTED
-------------------------
``denominator``, ``run_ids``, ``policy_snapshot`` and ``status_rollup`` are
stored, not recomputed on read. A policy edited next month must not silently
restate last month's verdict, and a run deleted by retention must not change
what the gate saw. The FK to the policy is kept for provenance and is
``SET NULL`` — the snapshot is the authority, the pointer is the reference.

```python
__tablename__ = 'release_gate_decisions'
__table_args__ = (Index('ix_rgd_release_current', 'release_id', unique=True, postgresql_where=text('is_current IS TRUE AND phase_id IS NULL')), Index('ix_rgd_phase_current', 'release_id', 'phase_id', unique=True, postgresql_where=text('is_current IS TRUE AND phase_id IS NOT NULL')), Index('ix_rgd_release_created', 'release_id', 'created_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('releases.id', ondelete='CASCADE'), nullable=False)
phase_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('release_phases.id', ondelete='CASCADE'), nullable=True)
is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
verdict: Mapped[str] = mapped_column(String(20), nullable=False)
denominator: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
run_ids: Mapped[Optional[list]] = mapped_column(JSON)
status_rollup: Mapped[Optional[dict]] = mapped_column(JSON)
attribution_mix: Mapped[Optional[dict]] = mapped_column(JSON)
ingestion_complete: Mapped[Optional[bool]] = mapped_column(Boolean)
incomplete_runs: Mapped[Optional[dict]] = mapped_column(JSON)
policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('release_gate_policies.id', ondelete='SET NULL'), nullable=True)
policy_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
baseline_release_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('releases.id', ondelete='SET NULL'), nullable=True)
blocking_reasons: Mapped[Optional[list]] = mapped_column(JSON)
conditions_for_go: Mapped[Optional[list]] = mapped_column(JSON)
created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

## backend/app/models/postgres.py — UserUIDismissal

[backend/app/models/postgres.py:4662](../../backend/app/models/postgres.py#L4662)

Bases: `Base`.

A UI prompt this user has dismissed (migration 0145).

Deliberately generic: one row per ``(user_id, dismissal_key)``. The first
consumer is the retention-activation nudge, which exists because
``ProjectRetentionPolicy.enabled`` defaults to ``False`` and nothing in the
product ever asked an operator to turn retention on — the feature shipped
present, discoverable, and inert.

Per-USER, not per-browser. ``localStorage`` was the cheaper option and is
wrong here: the same operator on a second machine would be re-prompted to
enable a destructive background job they had already declined.

Writes are idempotent — dismissing twice is a no-op, so the endpoint can be
retried safely and a double-clicked button cannot raise.

```python
__tablename__ = 'user_ui_dismissals'
__table_args__ = (UniqueConstraint('user_id', 'dismissal_key', name='uq_user_ui_dismissal'), Index('ix_uuid_user', 'user_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
dismissal_key: Mapped[str] = mapped_column(String(100), nullable=False)
dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

## backend/app/models/postgres.py — DigestSchedule

[backend/app/models/postgres.py:4694](../../backend/app/models/postgres.py#L4694)

Bases: `str, PyEnum`.



```python
DAILY = 'DAILY'
WEEKLY = 'WEEKLY'
PER_RUN = 'PER_RUN'
PER_RELEASE = 'PER_RELEASE'
PER_SUITE = 'PER_SUITE'
WEEKLY_RETRO = 'WEEKLY_RETRO'
```

## backend/app/models/postgres.py — DigestSubscription

[backend/app/models/postgres.py:4705](../../backend/app/models/postgres.py#L4705)

Bases: `Base`.

User subscription to a scheduled or event-driven report delivery.

```python
__tablename__ = 'digest_subscriptions'
__table_args__ = (Index('ix_ds_user', 'user_id'), Index('ix_ds_next', 'next_delivery_at'), Index('ix_ds_schedule_active', 'schedule', 'is_active', 'is_paused'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
saved_view_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('saved_views.id', ondelete='SET NULL'), nullable=True)
name: Mapped[str] = mapped_column(String(255), nullable=False)
schedule: Mapped[DigestSchedule] = mapped_column(String(20), nullable=False, default=DigestSchedule.WEEKLY.value)
channel: Mapped[NotificationChannel] = mapped_column(String(20), nullable=False, default=NotificationChannel.EMAIL.value)
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
scope_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default='project')
scope_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
trigger_filter: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default='all')
send_when_unchanged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
report_attachment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
last_delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
next_delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
delivery_count: Mapped[int] = mapped_column(Integer, default=0)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — TenantMetricSnapshot

[backend/app/models/postgres.py:4746](../../backend/app/models/postgres.py#L4746)

Bases: `Base`.

Per-project observability metric snapshot — aggregated periodically.

```python
__tablename__ = 'tenant_metric_snapshots'
__table_args__ = (Index('ix_tms_project_time', 'project_id', 'recorded_at'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
total_runs: Mapped[int] = mapped_column(Integer, default=0)
total_tests: Mapped[int] = mapped_column(Integer, default=0)
avg_pass_rate: Mapped[Optional[float]] = mapped_column(Float)
failed_runs: Mapped[int] = mapped_column(Integer, default=0)
ai_analyses_count: Mapped[int] = mapped_column(Integer, default=0)
release_decisions_count: Mapped[int] = mapped_column(Integer, default=0)
audit_events_count: Mapped[int] = mapped_column(Integer, default=0)
```

## backend/app/models/postgres.py — AIEvalDataset

[backend/app/models/postgres.py:4768](../../backend/app/models/postgres.py#L4768)

Bases: `Base`.

Labeled evaluation dataset for measuring AI quality over time.

```python
__tablename__ = 'ai_eval_datasets'
__table_args__ = (Index('ix_aed_task_type', 'task_type'),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
name: Mapped[str] = mapped_column(String(255), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text)
task_type: Mapped[str] = mapped_column(String(50), nullable=False)
items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
item_count: Mapped[int] = mapped_column(Integer, default=0)
created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — AIEvalRun

[backend/app/models/postgres.py:4788](../../backend/app/models/postgres.py#L4788)

Bases: `Base`.

Record of running an evaluation dataset against a model version.

```python
__tablename__ = 'ai_eval_runs'
__table_args__ = (Index('ix_aer_dataset', 'dataset_id'), Index('ix_aer_time', 'evaluated_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('ai_eval_datasets.id', ondelete='CASCADE'), nullable=False)
model_name: Mapped[str] = mapped_column(String(200), nullable=False)
model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('model_versions.id', ondelete='SET NULL'))
task_type: Mapped[str] = mapped_column(String(50), nullable=False)
precision: Mapped[Optional[float]] = mapped_column(Float)
recall: Mapped[Optional[float]] = mapped_column(Float)
f1_score: Mapped[Optional[float]] = mapped_column(Float)
accuracy: Mapped[Optional[float]] = mapped_column(Float)
agreement_rate: Mapped[Optional[float]] = mapped_column(Float)
item_results: Mapped[Optional[list]] = mapped_column(JSONB)
total_items: Mapped[int] = mapped_column(Integer, default=0)
correct_items: Mapped[int] = mapped_column(Integer, default=0)
fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
```

## backend/app/models/postgres.py — AIEvalBaseline

[backend/app/models/postgres.py:4824](../../backend/app/models/postgres.py#L4824)

Bases: `Base`.

Baseline metrics per agent/task_type for comparison gating.

```python
__tablename__ = 'ai_eval_baselines'
__table_args__ = (Index('ix_aeb_task_agent', 'task_type', 'agent_name'), UniqueConstraint('task_type', 'agent_name', 'prompt_version', name='uq_aeb_task_agent_prompt'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
task_type: Mapped[str] = mapped_column(String(50), nullable=False)
agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default='v1')
model_name: Mapped[Optional[str]] = mapped_column(String(200))
baseline_accuracy: Mapped[Optional[float]] = mapped_column(Float)
baseline_precision: Mapped[Optional[float]] = mapped_column(Float)
baseline_recall: Mapped[Optional[float]] = mapped_column(Float)
baseline_f1: Mapped[Optional[float]] = mapped_column(Float)
min_accuracy: Mapped[float] = mapped_column(Float, default=0.8)
min_f1: Mapped[float] = mapped_column(Float, default=0.75)
max_regression_pct: Mapped[float] = mapped_column(Float, default=5.0)
eval_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('ai_eval_runs.id', ondelete='SET NULL'))
dataset_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('ai_eval_datasets.id', ondelete='SET NULL'))
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — AIEvalGateRun

[backend/app/models/postgres.py:4855](../../backend/app/models/postgres.py#L4855)

Bases: `Base`.

Historical record of an agent-stack release gate decision.

```python
__tablename__ = 'ai_eval_gate_runs'
__table_args__ = (Index('ix_aeg_change_id', 'change_id'), Index('ix_aeg_status', 'status'), Index('ix_aeg_manifest_checksum', 'manifest_checksum_sha256'), Index('ix_aeg_evaluated_at', 'evaluated_at'), Index('ix_aeg_tier_comparison_lookup', 'project_id', 'agent_id', 'candidate_tier', 'evaluated_at', postgresql_where=text("gate_type = 'tier_comparison'")))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
change_id: Mapped[str] = mapped_column(String(200), nullable=False)
status: Mapped[str] = mapped_column(String(20), nullable=False)
manifest_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
gate_results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
blocking_gates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
version_changes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
gate_type: Mapped[Optional[str]] = mapped_column(String(40))
project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'))
agent_id: Mapped[Optional[str]] = mapped_column(String(80))
baseline_tier: Mapped[Optional[str]] = mapped_column(String(20))
candidate_tier: Mapped[Optional[str]] = mapped_column(String(20))
sample_count: Mapped[Optional[int]] = mapped_column(Integer)
evaluated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — AIEvalShadowPair

[backend/app/models/postgres.py:4892](../../backend/app/models/postgres.py#L4892)

Bases: `Base`.

A bounded live tier pair awaiting a human or golden-input label.

```python
__tablename__ = 'ai_eval_shadow_pairs'
__table_args__ = (UniqueConstraint('project_id', 'agent_id', 'sample_key', name='uq_ai_eval_shadow_pair_sample'), Index('ix_aesp_project_agent_created', 'project_id', 'agent_id', 'created_at'), CheckConstraint("label_status IN ('pending', 'human_labelled', 'golden_match')", name='ck_aesp_label_status'), CheckConstraint('total_tokens >= 0', name='ck_aesp_tokens_nonnegative'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
sample_key: Mapped[str] = mapped_column(String(128), nullable=False)
incumbent_tier: Mapped[str] = mapped_column(String(20), nullable=False)
candidate_tier: Mapped[str] = mapped_column(String(20), nullable=False)
incumbent_output: Mapped[dict] = mapped_column(JSONB, nullable=False)
candidate_output: Mapped[dict] = mapped_column(JSONB, nullable=False)
incumbent_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
candidate_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
label_status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
label: Mapped[Optional[dict]] = mapped_column(JSONB)
labelled_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
labelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — AIEvalReviewerQuality

[backend/app/models/postgres.py:4922](../../backend/app/models/postgres.py#L4922)

Bases: `Base`.

A G3 reviewer-quality observation batch or scheduled 30-day rollup.

```python
__tablename__ = 'ai_eval_reviewer_quality'
__table_args__ = (CheckConstraint("source IN ('observation_batch', 'scheduled')", name='ck_aerq_source'), CheckConstraint("status IN ('pass', 'fail', 'insufficient_samples')", name='ck_aerq_status'), CheckConstraint('clean_sample_count >= 0 AND human_outcome_count >= 0', name='ck_aerq_sample_counts_nonnegative'), CheckConstraint('window_started_at <= window_ended_at', name='ck_aerq_window_order'), Index('ix_aerq_project_agent_evaluated', 'project_id', 'agent_id', 'evaluated_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
source: Mapped[str] = mapped_column(String(24), nullable=False)
status: Mapped[str] = mapped_column(String(24), nullable=False)
manifest_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
window_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
mutation_observations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
clean_observations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
human_outcomes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
metrics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
regressions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
semantic_sample_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
clean_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
human_outcome_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
deterministic_recall: Mapped[Optional[float]] = mapped_column(Float)
second_model_recall: Mapped[Optional[float]] = mapped_column(Float)
recall_delta: Mapped[Optional[float]] = mapped_column(Float)
false_flag_rate: Mapped[Optional[float]] = mapped_column(Float)
false_omission_rate: Mapped[Optional[float]] = mapped_column(Float)
auto_disable_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
auto_disable_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
evaluated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

## backend/app/models/postgres.py — DecisionReportEvalCycle

[backend/app/models/postgres.py:4984](../../backend/app/models/postgres.py#L4984)

Bases: `Base`.

Durable evidence for a report-level evaluation corpus cycle.

**RET-D16 — deliberately outside the project-scoped purge, and it must
stay that way.** This table has neither ``project_id`` nor ``test_run_id``,
which the retention epic flagged as "structurally unreachable by any
project-scoped purge, forever".

The decision recorded here is that adding a scope column would be WRONG,
not merely unnecessary. A cycle evaluates the report-generation corpus as
a whole and spans reports drawn from many projects; stamping any single
``project_id`` on it would be a lie, and purging it when that project was
deleted would destroy an attestation that still describes live behaviour
elsewhere. It belongs with the eval-gate evidence (``prompt_manifest_eval``),
not with tenant data.

Unbounded growth is bounded by cadence instead of by a clock: ``cycle_key``
is unique and one row is written per evaluation cycle — an attestation
event, orders of magnitude rarer than a test run. If that cadence ever
changes, this needs a DEPLOYMENT-wide clock, not a project-scoped one.

```python
__tablename__ = 'decision_report_eval_cycles'
__table_args__ = (Index('ix_drec_corpus_evaluated', 'corpus_version', 'evaluated_at'), Index('ix_drec_status', 'status'), UniqueConstraint('cycle_key', name='uq_drec_cycle_key'), CheckConstraint("status IN ('pass', 'warn', 'fail')", name='ck_drec_status'), CheckConstraint("corpus_sha256 ~ '^[0-9a-f]{64}$'", name='ck_drec_corpus_hash'), CheckConstraint('report_count >= 0', name='ck_drec_report_count'), CheckConstraint('consecutive_passes >= 0', name='ck_drec_consecutive_nonnegative'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
cycle_key: Mapped[str] = mapped_column(String(128), nullable=False)
corpus_version: Mapped[str] = mapped_column(String(120), nullable=False)
corpus_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
status: Mapped[str] = mapped_column(String(20), nullable=False)
metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
checks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
unavailable_metrics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
consecutive_passes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
evaluated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — DecisionReportSupersessionRequest

[backend/app/models/postgres.py:5046](../../backend/app/models/postgres.py#L5046)

Bases: `Base`.

Durable, idempotent request to publish a child-enriched report version.

The parent deep pipeline never carries child prompts or evidence through a
broker payload.  It records this small, tenant-bound request instead; a
worker later re-resolves the immutable parent report and terminal child
rows before publishing a new DecisionReportV1 version.

```python
__tablename__ = 'decision_report_supersession_requests'
__table_args__ = (UniqueConstraint('parent_pipeline_run_id', name='uq_drsr_parent_pipeline'), Index('ix_drsr_status_next_attempt', 'status', 'next_attempt_at'), CheckConstraint("status IN ('pending', 'processing', 'published', 'rejected', 'failed')", name='ck_drsr_status'), CheckConstraint('attempts >= 0', name='ck_drsr_attempts_nonnegative'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
parent_pipeline_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='CASCADE'), nullable=False)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', server_default='pending')
reason: Mapped[str] = mapped_column(String(120), nullable=False, default='children_terminal')
attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
published_report_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
published_report_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

## backend/app/models/postgres.py — AgentMemoryEntry

[backend/app/models/postgres.py:5101](../../backend/app/models/postgres.py#L5101)

Bases: `Base`.

Unified memory linking a run/snapshot to related entities (clusters, defects,
release decisions, ownership) for project-scoped historical recall.

Each entry represents a single relationship discovered during a pipeline run.
Agents query these entries to retrieve similar historical failures, prior
defect decisions, and release outcomes for the same project.

```python
__tablename__ = 'agent_memory_entries'
__table_args__ = (Index('ix_ame_project_id', 'project_id'), Index('ix_ame_run_id', 'run_id'), Index('ix_ame_entity', 'entity_type', 'entity_id'), Index('ix_ame_project_entity', 'project_id', 'entity_type'), Index('ix_ame_created', 'created_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True)
entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
error_signature: Mapped[Optional[str]] = mapped_column(Text)
failure_category: Mapped[Optional[str]] = mapped_column(String(50))
root_cause_summary: Mapped[Optional[str]] = mapped_column(Text)
payload: Mapped[Optional[dict]] = mapped_column(JSON)
confidence: Mapped[Optional[int]] = mapped_column(Integer)
resolution: Mapped[Optional[str]] = mapped_column(String(50))
source_type: Mapped[str] = mapped_column(String(40), nullable=False, default='pipeline_agent')
trust_level: Mapped[str] = mapped_column(String(30), nullable=False, default='derived')
lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False, default='active')
source_snapshot_id: Mapped[Optional[str]] = mapped_column(String(128))
source_hash: Mapped[Optional[str]] = mapped_column(String(64))
expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_memory_entries.id', ondelete='SET NULL'), nullable=True)
superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — AgentActionLedger

[backend/app/models/postgres.py:5156](../../backend/app/models/postgres.py#L5156)

Bases: `Base`.

Append-only proposal/execution ledger for agent-originated actions.

The ledger is the durable approval boundary for external mutations. The
request payload is sanitized and hashed at proposal time; execution and
rollback outcomes are recorded separately so a caller can never replace a
proposal with a different payload under the same idempotency key.

```python
__tablename__ = 'agent_action_ledger'
__table_args__ = (Index('ix_agent_action_project_created', 'project_id', 'created_at'), Index('ix_agent_action_status', 'project_id', 'status'), Index('ix_agent_action_target', 'project_id', 'target_type', 'target_id'), UniqueConstraint('project_id', 'idempotency_key', name='uq_agent_action_project_idempotency'), CheckConstraint("status IN ('proposed', 'pending_review', 'approved', 'executing', 'executed', 'failed', 'rejected', 'rolled_back')", name='ck_agent_action_status'), CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name='ck_agent_action_request_hash'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True)
actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
action_type: Mapped[str] = mapped_column(String(60), nullable=False)
target_type: Mapped[str] = mapped_column(String(60), nullable=False)
target_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='proposed', server_default='proposed')
approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default='true')
idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
execution_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
execution_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
result_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
rollback_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — ReviewRequest

[backend/app/models/postgres.py:5224](../../backend/app/models/postgres.py#L5224)

Bases: `Base`.

One human review for one AI report-producing run (architecture section 8.1).

Every AI-generated report is a proposal until a human accepts it. There is
one LIVE request per subject -- the reports a run produced inherit its review
state -- enforced by a partial unique index; superseded rows are kept, so
the history of who accepted what survives a re-run.

``notes`` is free text and is never exported (section 8.1); ``reason_code``
is the closed vocabulary that becomes an eval label.

```python
__tablename__ = 'review_requests'
__table_args__ = (CheckConstraint(f'kind IN ({_review_in(REVIEW_KINDS)})', name='ck_review_requests_kind'), CheckConstraint(f'subject_type IN ({_review_in(REVIEW_SUBJECT_TYPES)})', name='ck_review_requests_subject_type'), CheckConstraint(f'state IN ({_review_in(REVIEW_STATES)})', name='ck_review_requests_state'), CheckConstraint(f'reason_code IS NULL OR reason_code IN ({_review_in(REVIEW_REASON_CODES)})', name='ck_review_requests_reason_code'), CheckConstraint("state <> 'rejected' OR reason_code IS NOT NULL", name='ck_review_requests_rejection_has_reason'), CheckConstraint("state NOT IN ('accepted', 'rejected') OR reviewed_at IS NOT NULL", name='ck_review_requests_settled_has_time'), CheckConstraint("evidence_bundle_sha256 IS NULL OR evidence_bundle_sha256 ~ '^[0-9a-f]{64}$'", name='ck_review_requests_evidence_hash'), CheckConstraint("eval_manifest_checksum IS NULL OR eval_manifest_checksum ~ '^[0-9a-f]{64}$'", name='ck_review_requests_eval_manifest_checksum'), Index('ix_review_requests_project_state', 'project_id', 'state', 'created_at'), Index('uq_review_requests_live_subject', 'kind', 'subject_type', 'subject_id', unique=True, postgresql_where=text("state <> 'superseded'")), Index('ix_review_requests_pending_scope', 'project_id', 'test_run_id', 'workflow_type', postgresql_where=text("state = 'pending_review'")))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
kind: Mapped[str] = mapped_column(String(20), nullable=False, default='report', server_default='report')
subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='SET NULL'), nullable=True)
test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
workflow_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
capability_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
state: Mapped[str] = mapped_column(String(20), nullable=False, default='pending_review', server_default='pending_review')
requested_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_by: Mapped[str] = mapped_column(String(40), nullable=False, default='system', server_default='system')
reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
reason_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
evidence_bundle_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
eval_manifest_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
ai_disclaimer_version: Mapped[str] = mapped_column(String(40), nullable=False)
superseded_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('review_requests.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

## backend/app/models/postgres.py — AgentInvocation

[backend/app/models/postgres.py:5322](../../backend/app/models/postgres.py#L5322)

Bases: `Base`.

One call of a single agent through the public API (architecture E1.2).

Carries no status of its own: the invocation runs as the pipeline run named
by ``pipeline_run_id`` (minted here before the worker creates that run, so
it is not a foreign key), and status, attempts and review are read from it.

```python
__tablename__ = 'agent_invocations'
__table_args__ = (CheckConstraint(f'mode IN ({_review_in(INVOCATION_MODES)})', name='ck_agent_invocations_mode'), Index('ix_agent_invocations_project_created', 'project_id', 'created_at'), Index('ix_agent_invocations_run_agent', 'test_run_id', 'agent_id', 'created_at'), Index('ux_agent_invocations_pipeline_run', 'pipeline_run_id', unique=True), Index('ux_agent_invocations_scoped_idempotency_key', 'requested_by', 'project_id', 'agent_id', 'idempotency_key', unique=True, postgresql_where=text('idempotency_key IS NOT NULL')))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
stage_name: Mapped[str] = mapped_column(String(60), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
pipeline_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
workflow_type: Mapped[str] = mapped_column(String(20), nullable=False)
mode: Mapped[str] = mapped_column(String(10), nullable=False, default='async', server_default='async')
requested_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
correlation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
request_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
resolved_config_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
```

## backend/app/models/postgres.py — AgentConfig

[backend/app/models/postgres.py:5385](../../backend/app/models/postgres.py#L5385)

Bases: `Base`.

A project's configuration of one agent (architecture E4.1, section 4.2).

``mode`` and ``enabled`` are columns; ``config`` holds the rest of the
validated ``AgentConfigV1`` document. A missing row means the defaults.
Every write bumps ``config_version``.

```python
__tablename__ = 'agent_configs'
__table_args__ = (UniqueConstraint('project_id', 'agent_id', name='uq_agent_configs_project_agent'), CheckConstraint(f'mode IN ({_review_in(AGENT_CONFIG_MODES)})', name='ck_agent_configs_mode'), CheckConstraint('config_version >= 1', name='ck_agent_configs_version_positive'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
mode: Mapped[str] = mapped_column(String(10), nullable=False, default='shadow', server_default='shadow')
config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

## backend/app/models/postgres.py — WorkflowDefinition

[backend/app/models/postgres.py:5418](../../backend/app/models/postgres.py#L5418)

Bases: `Base`.

One immutable-on-publish version of a project workflow (E3.1).

```python
__tablename__ = 'workflow_definitions'
__table_args__ = (UniqueConstraint('project_id', 'workflow_id', 'version', name='uq_workflow_definitions_project_workflow_version'), CheckConstraint('version >= 1', name='ck_workflow_definitions_version_positive'), CheckConstraint(f'base IN ({_review_in(WORKFLOW_BASES)})', name='ck_workflow_definitions_base'), CheckConstraint(f'status IN ({_review_in(WORKFLOW_DEFINITION_STATUSES)})', name='ck_workflow_definitions_status'), CheckConstraint("(status = 'published' AND published_at IS NOT NULL) OR (status = 'draft' AND published_at IS NULL)", name='ck_workflow_definitions_published_at'), CheckConstraint("eval_verdict IS NULL OR eval_verdict IN ('pass', 'fail', 'insufficient_samples')", name='ck_workflow_definitions_eval_verdict'), CheckConstraint('eval_coverage IS NULL OR (eval_coverage >= 0 AND eval_coverage <= 1)', name='ck_workflow_definitions_eval_coverage'), CheckConstraint('(eval_verdict IS NULL AND eval_coverage IS NULL AND eval_gate_run_id IS NULL AND evaluated_at IS NULL) OR (eval_verdict IS NOT NULL AND eval_coverage IS NOT NULL AND eval_gate_run_id IS NOT NULL AND evaluated_at IS NOT NULL)', name='ck_workflow_definitions_eval_evidence'), CheckConstraint("(eval_regression_accepted IS FALSE AND eval_regression_reason IS NULL AND eval_regression_accepted_by IS NULL AND eval_regression_accepted_at IS NULL) OR (eval_regression_accepted IS TRUE AND eval_verdict = 'fail' AND length(trim(eval_regression_reason)) > 0 AND eval_regression_accepted_by IS NOT NULL AND eval_regression_accepted_at IS NOT NULL)", name='ck_workflow_definitions_regression_acceptance'), Index('ix_workflow_definitions_project_status', 'project_id', 'status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
workflow_id: Mapped[str] = mapped_column(String(80), nullable=False)
version: Mapped[int] = mapped_column(Integer, nullable=False)
name: Mapped[str] = mapped_column(String(120), nullable=False)
description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
base: Mapped[str] = mapped_column(String(16), nullable=False)
definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
status: Mapped[str] = mapped_column(String(16), nullable=False, default='draft', server_default='draft')
published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
eval_verdict: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
eval_coverage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
eval_gate_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('ai_eval_gate_runs.id', ondelete='SET NULL'), nullable=True)
evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
eval_regression_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
eval_regression_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
eval_regression_accepted_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
eval_regression_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

## backend/app/models/postgres.py — WorkflowReplayCorpus

[backend/app/models/postgres.py:5512](../../backend/app/models/postgres.py#L5512)

Bases: `Base`.

A project run output cached for deterministic workflow evaluation (E9.5).

```python
__tablename__ = 'workflow_replay_corpus'
__table_args__ = (UniqueConstraint('agent_id', 'prompt_version', 'input_hash', name='uq_workflow_replay_agent_prompt_input'), CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name='ck_workflow_replay_input_hash'), CheckConstraint('cost_usd >= 0', name='ck_workflow_replay_cost_nonnegative'), CheckConstraint('latency_ms >= 0', name='ck_workflow_replay_latency_nonnegative'), Index('ix_workflow_replay_project_run', 'project_id', 'pipeline_run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
pipeline_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_pipeline_runs.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
step_id: Mapped[str] = mapped_column(String(80), nullable=False)
agent_id: Mapped[str] = mapped_column(String(120), nullable=False)
prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
output: Mapped[dict] = mapped_column(JSONB, nullable=False)
stage_status: Mapped[str] = mapped_column(String(20), nullable=False)
degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default='0')
latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

## backend/app/models/postgres.py — AgentActionDispatchOutbox

[backend/app/models/postgres.py:5560](../../backend/app/models/postgres.py#L5560)

Bases: `Base`.

Durable delivery intent for approved actions; never contains prompts.

```python
__tablename__ = 'agent_action_dispatch_outbox'
__table_args__ = (Index('ix_agent_action_outbox_status_next', 'status', 'next_attempt_at'), CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed')", name='ck_agent_action_outbox_status'), CheckConstraint('attempts >= 0', name='ck_agent_action_outbox_attempts_nonnegative'), UniqueConstraint('action_id', name='uq_agent_action_outbox_action'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_action_ledger.id', ondelete='CASCADE'), nullable=False)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', server_default='pending')
attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
last_error: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — FeatureFlag

[backend/app/models/postgres.py:5608](../../backend/app/models/postgres.py#L5608)

Bases: `Base`.

Named capability toggle gated by project, role, and rollout percent.

```python
__tablename__ = 'feature_flags'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
description: Mapped[Optional[str]] = mapped_column(Text)
enabled_global: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
enabled_projects: Mapped[Optional[list]] = mapped_column(JSONB)
enabled_roles: Mapped[Optional[list]] = mapped_column(JSONB)
rollout_percent: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — QuotaCapAction

[backend/app/models/postgres.py:5670](../../backend/app/models/postgres.py#L5670)

Bases: `str, PyEnum`.



```python
SOFT_WARN = 'SOFT_WARN'
AUTO_DOWNGRADE_TO_ML = 'AUTO_DOWNGRADE_TO_ML'
AUTO_DOWNGRADE_TO_RULES = 'AUTO_DOWNGRADE_TO_RULES'
HARD_BLOCK = 'HARD_BLOCK'
```

## backend/app/models/postgres.py — ProjectLlmQuota

[backend/app/models/postgres.py:5677](../../backend/app/models/postgres.py#L5677)

Bases: `Base`.

Per-project LLM spend config used by the usage-based billing gate.

```python
__tablename__ = 'project_llm_quota'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, unique=True)
enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
period_type: Mapped[str] = mapped_column(String(20), default='MONTHLY', nullable=False)
included_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
overage_rate_usd: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
hard_cap_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
soft_warn_threshold_pct: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
at_cap_action: Mapped[str] = mapped_column(String(32), default=QuotaCapAction.AUTO_DOWNGRADE_TO_ML.value, nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — FlakyQuarantineStatus

[backend/app/models/postgres.py:5764](../../backend/app/models/postgres.py#L5764)

Bases: `str, PyEnum`.



```python
DETECTED = 'DETECTED'
PROPOSED = 'PROPOSED'
APPROVED = 'APPROVED'
QUARANTINED = 'QUARANTINED'
RECHECK_SCHEDULED = 'RECHECK_SCHEDULED'
RELEASED = 'RELEASED'
RE_QUARANTINED = 'RE_QUARANTINED'
REJECTED = 'REJECTED'
EXPIRED = 'EXPIRED'
```

## backend/app/models/postgres.py — FailureAttribution

[backend/app/models/postgres.py:5800](../../backend/app/models/postgres.py#L5800)

Bases: `Base`.



```python
__tablename__ = 'failure_attribution'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_cases.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('test_runs.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
verdict: Mapped[str] = mapped_column(String(32), nullable=False)
confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
rationale: Mapped[str] = mapped_column(String(1000), default='', nullable=False)
inputs: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
__table_args__ = (Index('ux_failure_attribution_test_case', 'test_case_id', unique=True), Index('ix_failure_attribution_run', 'test_run_id'), Index('ix_failure_attribution_project_verdict', 'project_id', 'verdict'))
```

## backend/app/models/postgres.py — SystemicFlakeCluster

[backend/app/models/postgres.py:5838](../../backend/app/models/postgres.py#L5838)

Bases: `Base`.



```python
__tablename__ = 'systemic_flake_cluster'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
cluster_key: Mapped[str] = mapped_column(String(32), nullable=False)
label: Mapped[str] = mapped_column(String(500), nullable=False)
cause_family: Mapped[Optional[str]] = mapped_column(String(40))
size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
cohesion: Mapped[Optional[float]] = mapped_column(Float)
co_failure_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
window_days: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
__table_args__ = (Index('ux_systemic_cluster_project_key', 'project_id', 'cluster_key', unique=True),)
```

## backend/app/models/postgres.py — SystemicFlakeClusterMember

[backend/app/models/postgres.py:5863](../../backend/app/models/postgres.py#L5863)

Bases: `Base`.



```python
__tablename__ = 'systemic_flake_cluster_member'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('systemic_flake_cluster.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[Optional[str]] = mapped_column(String(1000))
failure_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
__table_args__ = (Index('ux_systemic_member_cluster_fingerprint', 'cluster_id', 'test_fingerprint', unique=True),)
```

## backend/app/models/postgres.py — FlakyScore

[backend/app/models/postgres.py:5893](../../backend/app/models/postgres.py#L5893)

Bases: `Base`.



```python
__tablename__ = 'flaky_score'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[Optional[str]] = mapped_column(String(1000))
score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
components: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
confidence: Mapped[str] = mapped_column(String(20), default='none', nullable=False)
window_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
__table_args__ = (Index('ux_flaky_score_project_fingerprint', 'project_id', 'test_fingerprint', unique=True), Index('ix_flaky_score_project_score', 'project_id', 'score'))
```

## backend/app/models/postgres.py — FlakyClassifierCalibration

[backend/app/models/postgres.py:5927](../../backend/app/models/postgres.py#L5927)

Bases: `Base`.



```python
__tablename__ = 'flaky_classifier_calibration'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
method: Mapped[str] = mapped_column(String(50), nullable=False)
specificity: Mapped[Optional[float]] = mapped_column(Float)
sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
true_negatives: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
false_positives: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
insufficient_reason: Mapped[Optional[str]] = mapped_column(String(200))
window_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
__table_args__ = (Index('ux_flaky_calibration_project_method', 'project_id', 'method', unique=True),)
```

## backend/app/models/postgres.py — FlakyDetectionState

[backend/app/models/postgres.py:5963](../../backend/app/models/postgres.py#L5963)

Bases: `Base`.



```python
__tablename__ = 'flaky_detection_state'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[Optional[str]] = mapped_column(String(1000))
first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
first_seen_is_exact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
screen_reason: Mapped[str] = mapped_column(String(32), nullable=False)
first_screened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
first_scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
first_scored_confidence: Mapped[Optional[str]] = mapped_column(String(20))
observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
last_swept_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
__table_args__ = (Index('ux_flaky_detection_state_project_fingerprint', 'project_id', 'test_fingerprint', unique=True), Index('ix_flaky_detection_state_project_scored', 'project_id', 'first_scored_at'), Index('ix_flaky_detection_state_project_swept', 'project_id', 'last_swept_at'))
```

## backend/app/models/postgres.py — PerfBaseline

[backend/app/models/postgres.py:6013](../../backend/app/models/postgres.py#L6013)

Bases: `Base`.

Per-test running duration statistics.

```python
__tablename__ = 'perf_baselines'
__table_args__ = (Index('ix_perf_baseline_project', 'project_id'), UniqueConstraint('project_id', 'test_fingerprint', name='uq_perf_baseline_fingerprint'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[Optional[str]] = mapped_column(String(500))
suite_name: Mapped[Optional[str]] = mapped_column(String(500))
sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
mean_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
m2: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
stddev_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
p95_ms: Mapped[Optional[float]] = mapped_column(Float)
last_observed_ms: Mapped[Optional[int]] = mapped_column(Integer)
last_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — WebhookSubscription

[backend/app/models/postgres.py:6063](../../backend/app/models/postgres.py#L6063)

Bases: `Base`.

Customer-managed outbound webhook subscription.

```python
__tablename__ = 'webhook_subscriptions'
__table_args__ = (Index('ix_webhook_sub_project', 'project_id'), Index('ix_webhook_sub_enabled', 'enabled', 'project_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
name: Mapped[str] = mapped_column(String(255), nullable=False)
target_url: Mapped[str] = mapped_column(String(1000), nullable=False)
events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
has_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
max_retries: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
last_delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_error: Mapped[Optional[str]] = mapped_column(Text)
failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
total_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — WebhookDelivery

[backend/app/models/postgres.py:6109](../../backend/app/models/postgres.py#L6109)

Bases: `Base`.

Audit trail for every webhook delivery attempt.

One row per (subscription, event emission). Retries update the same
row — ``attempt_count`` is incremented and the final outcome lands in
``status``. Rows older than 30 days are pruned by a celery beat task
to keep the table bounded.

```python
__tablename__ = 'webhook_deliveries'
__table_args__ = (Index('ix_webhook_delivery_sub_created', 'subscription_id', 'created_at'), Index('ix_webhook_delivery_status', 'status', 'created_at'), Index('ix_webhook_delivery_dispatch_due', 'status', 'next_dispatch_at'), Index('uq_webhook_delivery_delivery_key', 'delivery_key', unique=True), Index('ix_webhook_delivery_run_id', 'run_id'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('webhook_subscriptions.id', ondelete='CASCADE'), nullable=False)
run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
event_type: Mapped[str] = mapped_column(String(64), nullable=False)
event_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
delivery_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
status: Mapped[str] = mapped_column(String(20), default='PENDING', nullable=False)
attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default='0')
dispatch_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default='0')
next_dispatch_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
dispatch_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
dispatch_token: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
http_status: Mapped[Optional[int]] = mapped_column(Integer)
response_preview: Mapped[Optional[str]] = mapped_column(String(2000))
error: Mapped[Optional[str]] = mapped_column(Text)
delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## backend/app/models/postgres.py — GitHubIntegration

[backend/app/models/postgres.py:6185](../../backend/app/models/postgres.py#L6185)

Bases: `Base`.

Per-project GitHub Checks API integration config.

```python
__tablename__ = 'github_integrations'
__table_args__ = (Index('ix_github_integrations_project', 'project_id', unique=True),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, unique=True)
enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
repo_owner: Mapped[str] = mapped_column(String(255), nullable=False)
repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
api_base_url: Mapped[str] = mapped_column(String(500), default='https://api.github.com', nullable=False)
has_pat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
pr_comment_mode: Mapped[str] = mapped_column(String(20), default='failures_only', server_default='failures_only', nullable=False)
last_posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_error: Mapped[Optional[str]] = mapped_column(Text)
last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — GitLabIntegration

[backend/app/models/postgres.py:6244](../../backend/app/models/postgres.py#L6244)

Bases: `Base`.

Per-project GitLab integration config (PMF Epic 3 US-3.1).

Mirror of ``GitHubIntegration`` for GitLab (self-managed or gitlab.com).
Drives the sticky **MR note** (US-3.2) and **commit status** (US-3.2)
outbound surfaces. The PAT lives in ``secret_service`` under scope
``gitlab_integration``, key ``project:{project_id}:pat`` — never a column,
so a DB dump cannot recover active tokens.

```python
__tablename__ = 'gitlab_integrations'
__table_args__ = (Index('ix_gitlab_integrations_project', 'project_id', unique=True),)
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, unique=True)
enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
base_url: Mapped[str] = mapped_column(String(500), default='https://gitlab.com', server_default='https://gitlab.com', nullable=False)
project_path: Mapped[str] = mapped_column(String(500), default='', server_default='', nullable=False)
mr_comment_mode: Mapped[str] = mapped_column(String(20), default='failures_only', server_default='failures_only', nullable=False)
commit_status_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'), nullable=False)
has_pat: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
last_posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
last_error: Mapped[Optional[str]] = mapped_column(Text)
last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — CompliancePack

[backend/app/models/postgres.py:6327](../../backend/app/models/postgres.py#L6327)

Bases: `Base`.

Generated compliance export pack (ZIP) for a release decision.

```python
__tablename__ = 'compliance_packs'
__table_args__ = (Index('ix_compliance_packs_release', 'release_id'), Index('ix_compliance_packs_project', 'project_id', 'generated_at'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
release_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('releases.id', ondelete='SET NULL'), nullable=True)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
minio_key: Mapped[str] = mapped_column(String(500), nullable=False)
manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
generated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
metadata_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
notes: Mapped[Optional[str]] = mapped_column(Text)
```

## backend/app/models/postgres.py — FlakyQuarantineRequest

[backend/app/models/postgres.py:6391](../../backend/app/models/postgres.py#L6391)

Bases: `Base`.

Workflow record for proposing, approving, and enforcing quarantine
of a flaky test.

One row per quarantine lifecycle. When an approved quarantine is
``RELEASED`` and the test flakes again, a fresh row is created rather
than reusing the old one — this keeps the history immutable and lets
QA leads see every past decision on the same test.

```python
__tablename__ = 'flaky_quarantine_requests'
__table_args__ = (Index('ix_fqr_project_status', 'project_id', 'status'), Index('ix_fqr_fingerprint', 'project_id', 'test_fingerprint'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
test_name: Mapped[Optional[str]] = mapped_column(String(500))
suite_name: Mapped[Optional[str]] = mapped_column(String(500))
status: Mapped[str] = mapped_column(String(32), default=FlakyQuarantineStatus.PROPOSED.value, nullable=False)
detection_method: Mapped[str] = mapped_column(String(50), default='pass_fail_ratio')
flip_rate: Mapped[Optional[float]] = mapped_column(Float)
flip_window_size: Mapped[Optional[int]] = mapped_column(Integer)
pass_count: Mapped[Optional[int]] = mapped_column(Integer)
fail_count: Mapped[Optional[int]] = mapped_column(Integer)
detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
proposed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
rejected_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
quarantine_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
quarantine_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
quarantine_duration_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
recheck_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
rationale: Mapped[Optional[dict]] = mapped_column(JSONB)
reviewer_notes: Mapped[Optional[str]] = mapped_column(Text)
owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
defect_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('defects.id', ondelete='SET NULL'), nullable=True)
sla_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
stale_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
stale_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
consecutive_passes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
last_stability_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('test_runs.id', ondelete='SET NULL'), nullable=True)
ready_to_promote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
ready_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — QuarantineLifecyclePolicy

[backend/app/models/postgres.py:6521](../../backend/app/models/postgres.py#L6521)

Bases: `Base`.

Per-project quarantine lifecycle configuration (PMF US-5.4/5.5/5.6).

One row per project; a MISSING row resolves to the defaults in
``flaky_quarantine_service.EffectiveLifecyclePolicy`` (SLA 14 days,
no auto-defect, no auto-promote, promote after 20 consecutive passes,
detection floor 20% flip rate over 10 runs) — no project-creation hook
needed.

```python
__tablename__ = 'quarantine_lifecycle_policies'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, unique=True)
sla_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
auto_create_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
auto_promote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
promote_after_passes: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
max_active_quarantined: Mapped[int] = mapped_column(Integer, default=8, server_default='8', nullable=False)
detection_flip_rate_threshold: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
detection_min_runs: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — ValueMetricAssumptions

[backend/app/models/postgres.py:6562](../../backend/app/models/postgres.py#L6562)

Bases: `Base`.

Per-project tunable assumptions for the engineer-hours-saved model
(PMF US-12.1, migration 0112).

One row per project; a MISSING row resolves to the defaults in
``value_metrics_service.EffectiveAssumptions`` (triage 20 min/failure,
blocked-run wait 30 min, defect filing 15 min) — no project-creation
hook needed (same pattern as ``QuarantineLifecyclePolicy``).

Every defaulted column carries a matching ``server_default`` so the
migration DDL and the ORM cannot drift (the #433 lesson).

```python
__tablename__ = 'value_metric_assumptions'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, unique=True)
triage_minutes_per_failure: Mapped[float] = mapped_column(Float, default=20.0, server_default=text('20.0'), nullable=False)
blocked_run_wait_minutes: Mapped[float] = mapped_column(Float, default=30.0, server_default=text('30.0'), nullable=False)
defect_filing_minutes: Mapped[float] = mapped_column(Float, default=15.0, server_default=text('15.0'), nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — RunTombstone

[backend/app/models/postgres.py:6603](../../backend/app/models/postgres.py#L6603)

Bases: `Base`.

A run that was deliberately deleted, and must not come back (0148, S2c).

Five code paths create a ``TestRun`` from a caller-supplied id on a SELECT
miss — the stream stub, ``persist_live_session``, the live-session drainer,
the live-persist Celery task, and ``ingestion_pipeline`` when a ``run_id``
is passed. Each is correct on its own terms; together they are why a
per-run delete could not previously be offered. Delete a run while any is
in flight and the row reappears seconds later with its events, objects and
archive already gone.

Refusing to delete an ``IN_PROGRESS`` run narrows that window but does not
close it: a Celery task already holding the id does not re-read the status.

``run_id`` is the primary key and is deliberately NOT a foreign key to
``test_runs`` — the row it names has been deleted, which is the whole
point. Retention retires tombstones on the audit clock so this does not
become a table that only grows.

```python
__tablename__ = 'run_tombstones'
__table_args__ = (Index('ix_run_tombstones_project_deleted', 'project_id', 'deleted_at'),)
run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
deleted_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
deletion_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('deletion_jobs.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — DeletionJob

[backend/app/models/postgres.py:6644](../../backend/app/models/postgres.py#L6644)

Bases: `Base`.

One record of a deletion actually happening (migration 0147, S2b).

Retention already writes a purge record into ``settings_audit_log``, which
is never purged and stays the compliance evidence. This table is the
OPERATIONAL surface: what ran, what it removed, and — the part the audit row
cannot express — whether it is still running, and whether it finished.

**Written on its own session.** A ``failed`` or ``partial`` status written
inside the transaction that failed is erased by that transaction's rollback,
so the only states a same-session writer can ever record are the successful
ones. Same reason the purge-audit row is written after the commit.

``resolved_run_ids`` records exactly which runs went, and ``candidate_hash``
fingerprints that set. Today that is an audit detail; criteria deletion
(S5) reuses both to freeze a previewed candidate set and refuse to execute
if the world moved underneath it.

Status vocabulary is limited to states something can actually reach:
``queued|previewed|running|completed|failed|partial``. ``previewed`` was
withheld until S5's preview endpoint gave it a producer. ``cancelled`` is
still NOT declared, because nothing can produce it — shipping a state
nothing writes is the defect this epic catalogues elsewhere.

```python
__tablename__ = 'deletion_jobs'
__table_args__ = (Index('ix_deletion_jobs_project_started', 'project_id', 'started_at'), Index('ix_deletion_jobs_status', 'status'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
job_kind: Mapped[str] = mapped_column(String(20), nullable=False)
status: Mapped[str] = mapped_column(String(20), nullable=False, default='queued')
criteria: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
resolved_run_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
candidate_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
counts: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
bytes_reclaimed: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
holds_honoured: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
references_broken: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
requested_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
```

## backend/app/models/postgres.py — ProjectRetentionPolicy

[backend/app/models/postgres.py:6715](../../backend/app/models/postgres.py#L6715)

Bases: `Base`.

Per-project data retention policy (PMF US-11.4, migration 0113).

One row per project; a MISSING row resolves to the code defaults in
``retention_service.EffectiveRetentionPolicy`` (disabled; raw events
90 d, runs 365 d, artifacts 180 d, audit 2555 d ≈ 7 y) — no
project-creation hook needed (``ValueMetricAssumptions`` pattern).

Four retention classes, each with its own clock:

* ``raw_events_days``  — live event docs + ``TestRun.event_archive`` +
  raw ingest payloads in Mongo.
* ``runs_days``        — TestRun rows (Postgres CASCADE) + run-scoped
  Mongo docs + the run's MinIO uploads.
* ``artifacts_days``   — MinIO report/upload/pipeline artifacts.
* ``audit_days``       — access/test-case audit rows, AI provenance,
  pipeline event log, expired compliance packs. Must be ≥
  ``runs_days`` (enforced in the service) so audit records always
  outlive the runs they describe. ``settings_audit_log`` (which holds
  the purge-audit records themselves) is NEVER purged.

Every defaulted column carries a matching ``server_default`` so the
migration DDL and the ORM cannot drift (the #433 lesson).

```python
__tablename__ = 'project_retention_policies'
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, unique=True)
enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
raw_events_days: Mapped[int] = mapped_column(Integer, default=90, server_default=text('90'), nullable=False)
runs_days: Mapped[int] = mapped_column(Integer, default=365, server_default=text('365'), nullable=False)
artifacts_days: Mapped[int] = mapped_column(Integer, default=180, server_default=text('180'), nullable=False)
audit_days: Mapped[int] = mapped_column(Integer, default=2555, server_default=text('2555'), nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

## backend/app/models/postgres.py — ProjectLlmUsage

[backend/app/models/postgres.py:6771](../../backend/app/models/postgres.py#L6771)

Bases: `Base`.

Running per-period LLM cost meter for a project.

One row per ``(project_id, period_start)``. ``record_usage`` upserts with
atomic arithmetic increments so two workers writing concurrently don't
clobber each other. The ``(project_id, period_start)`` unique index is
what makes the upsert safe.

```python
__tablename__ = 'project_llm_usage'
__table_args__ = (UniqueConstraint('project_id', 'period_start', name='uq_project_period'), Index('ix_llm_usage_period', 'period_start'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
total_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
total_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
total_llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
cap_hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## backend/app/models/postgres.py — ProjectActivityEvent

[backend/app/models/postgres.py:6811](../../backend/app/models/postgres.py#L6811)

Bases: `Base`.

Append-only, project-scoped product feed of everything that happens.

**This is a derived read surface, not the compliance record.** The four
compliance tables (``access_audit_logs``, ``settings_audit_log``,
``test_case_audit_logs``, ``identity_events``) keep their role and their
retention guarantees; where a ledger row mirrors one of them it carries
``source_table`` / ``source_id`` so a reader can get back to the row that
legally matters.

**Append-only by application convention, not by database enforcement** —
same boundary as the other audit tables, held by the
``backend.audit-write-discipline`` quality gate
(``scripts/quality_gate.py``), which fails CI on application code that
UPDATEs this table or DELETEs from it outside
``services/retention_service.py``.

Rows ARE removed by the US-11.4 retention purge on the **audit clock**
(``ProjectRetentionPolicy.audit_days``; floor 365 days, validated to be
>= ``runs_days`` so ledger rows outlive the runs they describe), and only
for projects that explicitly enabled a policy.

``ON DELETE CASCADE`` on ``project_id`` is deliberate and has one visible
consequence: there is no ``project.deleted`` event here, because the row
would be deleted by the very thing it records. That event goes to
``access_audit_logs``, whose FK is ON DELETE SET NULL.

``entity_id`` is TEXT, not UUID, on purpose: live-stream sessions address
runs by slug (``LiveSession.run_id``), and forcing a cast here would either
drop those events or 500 the ingest path.

```python
__tablename__ = 'project_activity_events'
__table_args__ = (Index('ix_pae_project_time', 'project_id', 'occurred_at', 'id'), Index('ix_pae_project_category_time', 'project_id', 'category', 'occurred_at', 'id'), Index('ix_pae_project_entity', 'project_id', 'entity_type', 'entity_id', 'occurred_at'), Index('ix_pae_project_actor', 'project_id', 'actor_id', 'occurred_at'), Index('ix_pae_project_release', 'project_id', 'release_id', 'occurred_at', postgresql_where=text('release_id IS NOT NULL')), Index('ix_pae_project_event_type', 'project_id', 'event_type', 'occurred_at'), Index('ix_pae_group_key', 'project_id', 'group_key'), CheckConstraint("category IN ('runs', 'analysis', 'release', 'quality', 'configuration', 'membership', 'test_management', 'integration', 'agent', 'system')", name='ck_pae_category'), CheckConstraint("actor_type IN ('user', 'api_key', 'service_account', 'system', 'agent')", name='ck_pae_actor_type'))
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
release_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('releases.id', ondelete='SET NULL'), nullable=True)
occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
category: Mapped[str] = mapped_column(String(20), nullable=False)
event_type: Mapped[str] = mapped_column(String(60), nullable=False)
schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
actor_name: Mapped[Optional[str]] = mapped_column(String(200))
actor_ref: Mapped[Optional[str]] = mapped_column(String(120))
entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
entity_label: Mapped[Optional[str]] = mapped_column(String(300))
target_type: Mapped[Optional[str]] = mapped_column(String(40))
target_id: Mapped[Optional[str]] = mapped_column(String(120))
summary: Mapped[str] = mapped_column(String(500), nullable=False)
diff: Mapped[Optional[dict]] = mapped_column(JSON)
context: Mapped[Optional[dict]] = mapped_column(JSON)
source_table: Mapped[Optional[str]] = mapped_column(String(40))
source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
request_id: Mapped[Optional[str]] = mapped_column(String(64))
group_key: Mapped[Optional[str]] = mapped_column(String(120))
```

## backend/app/models/schemas.py — TimestampMixin

[backend/app/models/schemas.py:43](../../backend/app/models/schemas.py#L43)

Bases: `BaseModel`.



```python
created_at: datetime
updated_at: Optional[datetime] = None
```

## backend/app/models/schemas.py — UserCreate

[backend/app/models/schemas.py:68](../../backend/app/models/schemas.py#L68)

Bases: `BaseModel`.



```python
email: EmailStr
username: str = Field(..., min_length=3, max_length=50)
full_name: Optional[str] = Field(None, max_length=255)
password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)
```

## backend/app/models/schemas.py — UserResponse

[backend/app/models/schemas.py:75](../../backend/app/models/schemas.py#L75)

Bases: `TimestampMixin`.



```python
id: uuid.UUID
email: str
username: str
full_name: Optional[str] = None
role: UserRole
is_active: bool
must_change_password: bool = False
avatar_color: Optional[str] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SelfUpdateProfileRequest

[backend/app/models/schemas.py:88](../../backend/app/models/schemas.py#L88)

Bases: `BaseModel`.

Fields a user can update about themselves (no role/status changes).

```python
full_name: Optional[str] = Field(None, max_length=255)
avatar_color: Optional[str] = Field(None, max_length=20)
```

## backend/app/models/schemas.py — RetireCompliancePackRequest

[backend/app/models/schemas.py:94](../../backend/app/models/schemas.py#L94)

Bases: `BaseModel`.

Bring a pack's retention window forward to now.

Typed confirmation rather than a checkbox: a pack is audit evidence, and
retiring it early hands it to the next nightly purge.

```python
confirmation_id: str = Field(..., min_length=1, max_length=64, description='Must equal the pack id exactly.')
reason: str = Field(..., min_length=3, max_length=500)
```

## backend/app/models/schemas.py — CompliancePackLifecycleResponse

[backend/app/models/schemas.py:110](../../backend/app/models/schemas.py#L110)

Bases: `BaseModel`.



```python
pack_id: uuid.UUID
retention_expires_at: datetime
purgeable_now: bool
```

## backend/app/models/schemas.py — DeletionPreviewResponse

[backend/app/models/schemas.py:119](../../backend/app/models/schemas.py#L119)

Bases: `BaseModel`.

A frozen candidate set an ADMIN authorises from.

The ids are materialized and hashed here, and ``execute`` replays them.
Re-resolving at execute time would delete a different set from the one
that was reviewed: criteria read columns other code rewrites while the job
is queued (``TestRun.status`` by aggregate updates and live-session close,
``primary_suite_name`` at session close).

```python
job_id: uuid.UUID
project_id: uuid.UUID
run_count: int
run_ids: List[uuid.UUID]
candidate_hash: str
truncated: bool = Field(False, description='The candidate set exceeded the reviewable bound and was cut. Reported rather than silently deleting the first N.')
refused_prefixes: List[str] = Field(default_factory=list)
blocked: List[dict] = Field(default_factory=list, description='Runs that cannot be deleted and why — in flight, or cited by a compliance pack, release or decision report. Surfaced at preview so an ADMIN sees them before authorising, not after.')
```

## backend/app/models/schemas.py — DeletionExecuteRequest

[backend/app/models/schemas.py:152](../../backend/app/models/schemas.py#L152)

Bases: `BaseModel`.

Execute takes a JOB ID, never a criteria body.

Accepting criteria here would re-resolve them, which is the bug the freeze
exists to prevent — the set executed would not be the set reviewed.

```python
job_id: uuid.UUID
confirmation_name: str = Field(..., min_length=1, max_length=255, description="Must equal the project's name exactly. A typed confirmation, not a checkbox, because this is irreversible across five stores.")
```

## backend/app/models/schemas.py — DeletionExecuteAcceptedResponse

[backend/app/models/schemas.py:171](../../backend/app/models/schemas.py#L171)

Bases: `BaseModel`.



```python
job_id: uuid.UUID
run_count: int
status: Literal['accepted'] = 'accepted'
```

## backend/app/models/schemas.py — DeletionJobResponse

[backend/app/models/schemas.py:177](../../backend/app/models/schemas.py#L177)

Bases: `BaseModel`.

One deletion that ran (or is running).

`bytes_reclaimed` is nullable and is NOT defaulted to 0 — "reclaimed
nothing" and "nobody measured" are opposite findings, and this is the
surface that tells an operator whether a purge was worth running.

```python
id: uuid.UUID
project_id: uuid.UUID
job_kind: str
status: str
criteria: Optional[dict] = None
counts: Optional[dict] = None
bytes_reclaimed: Optional[int] = None
error: Optional[str] = None
requested_at: datetime
started_at: Optional[datetime] = None
finished_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — DeletionJobListResponse

[backend/app/models/schemas.py:199](../../backend/app/models/schemas.py#L199)

Bases: `BaseModel`.



```python
jobs: List[DeletionJobResponse] = Field(default_factory=list)
```

## backend/app/models/schemas.py — StoreFootprintResponse

[backend/app/models/schemas.py:203](../../backend/app/models/schemas.py#L203)

Bases: `BaseModel`.

One store's contribution to a project's storage footprint.

``measured=False`` means the store could not be reached — ``bytes`` and
``items`` are then ``None``, never ``0``. Zero and unreachable are opposite
findings and must not render alike.

``exact=False`` means the byte figure is a proportional estimate over a
store shared with other projects; ``estimate_basis`` says how.

```python
store: str
measured: bool
exact: bool
complete: bool = True
bytes: Optional[int] = None
items: Optional[int] = None
estimate_basis: Optional[str] = None
unreachable_reason: Optional[str] = None
```

## backend/app/models/schemas.py — ProjectStorageResponse

[backend/app/models/schemas.py:225](../../backend/app/models/schemas.py#L225)

Bases: `BaseModel`.



```python
project_id: str
computed_at: datetime
stores: List[StoreFootprintResponse]
total_bytes: Optional[int] = None
total_is_estimate: bool = False
fully_measured: bool = True
```

## backend/app/models/schemas.py — DeletedProjectStorageEntry

[backend/app/models/schemas.py:237](../../backend/app/models/schemas.py#L237)

Bases: `BaseModel`.



```python
project_id: str
name: str
reachable_by_retention: bool
footprint: ProjectStorageResponse
```

## backend/app/models/schemas.py — DeletedProjectsStorageResponse

[backend/app/models/schemas.py:248](../../backend/app/models/schemas.py#L248)

Bases: `BaseModel`.



```python
computed_at: datetime
projects_total: int
projects_measured: int
truncated: bool = False
projects: List[DeletedProjectStorageEntry] = Field(default_factory=list)
total_bytes: Optional[int] = None
total_is_estimate: bool = False
unreachable_by_retention: int = 0
```

## backend/app/models/schemas.py — UIDismissalCreate

[backend/app/models/schemas.py:262](../../backend/app/models/schemas.py#L262)

Bases: `BaseModel`.

Dismiss a UI prompt for the authenticated user.

``dismissal_key`` is validated against a closed allowlist in
``services/ui_dismissal_service.KNOWN_DISMISSAL_KEYS`` rather than by a
Pydantic enum here: a strict enum over a ``String(100)`` column silently
422s when the vocabularies drift, and the UI shows nothing at all.

```python
dismissal_key: str = Field(..., min_length=1, max_length=100)
```

## backend/app/models/schemas.py — UIDismissalListResponse

[backend/app/models/schemas.py:273](../../backend/app/models/schemas.py#L273)

Bases: `BaseModel`.



```python
dismissed: List[str] = Field(default_factory=list)
```

## backend/app/models/schemas.py — TokenResponse

[backend/app/models/schemas.py:277](../../backend/app/models/schemas.py#L277)

Bases: `BaseModel`.



```python
access_token: str
refresh_token: str
token_type: str = 'bearer'
expires_in: int
must_change_password: bool = False
```

## backend/app/models/schemas.py — LoginRequest

[backend/app/models/schemas.py:285](../../backend/app/models/schemas.py#L285)

Bases: `BaseModel`.



```python
username: str
password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
```

## backend/app/models/schemas.py — RefreshRequest

[backend/app/models/schemas.py:298](../../backend/app/models/schemas.py#L298)

Bases: `BaseModel`.



```python
refresh_token: str
```

## backend/app/models/schemas.py — ChangePasswordRequest

[backend/app/models/schemas.py:302](../../backend/app/models/schemas.py#L302)

Bases: `BaseModel`.



```python
current_password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
new_password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)
```

## backend/app/models/schemas.py — FirstTimeResetRequest

[backend/app/models/schemas.py:311](../../backend/app/models/schemas.py#L311)

Bases: `BaseModel`.

Used for forced password reset on first login — no current password required.

```python
new_password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)
confirm_password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)
```

## backend/app/models/schemas.py — MfaChallengeResponse

[backend/app/models/schemas.py:326](../../backend/app/models/schemas.py#L326)

Bases: `BaseModel`.

Password accepted; a second factor is required to finish.

``challenge_token`` is NOT an access token — it carries ``type:
"mfa_challenge"`` and is rejected by ``get_current_user`` at the decode
layer. It is only accepted by ``POST /auth/mfa/verify``.

```python
mfa_required: Literal[True] = True
challenge_token: str
expires_in: int
methods: List[str] = Field(default_factory=lambda: ['totp', 'recovery_code'])
```

## backend/app/models/schemas.py — MfaEnrollmentRequiredResponse

[backend/app/models/schemas.py:339](../../backend/app/models/schemas.py#L339)

Bases: `BaseModel`.

Password accepted; workspace policy requires MFA and the user has none.

``enrollment_token`` carries ``type: "mfa_enroll"`` and is accepted only by
the two enrollment endpoints.

```python
mfa_enrollment_required: Literal[True] = True
enrollment_token: str
expires_in: int
required_for_role: Optional[str] = None
```

## backend/app/models/schemas.py — MfaEnrollStartRequest

[backend/app/models/schemas.py:351](../../backend/app/models/schemas.py#L351)

Bases: `BaseModel`.



```python
enrollment_token: Optional[str] = None
```

## backend/app/models/schemas.py — MfaEnrollStartResponse

[backend/app/models/schemas.py:357](../../backend/app/models/schemas.py#L357)

Bases: `BaseModel`.



```python
secret: str
otpauth_uri: str
issuer: str
account_name: str
digits: int
period_seconds: int
```

## backend/app/models/schemas.py — MfaEnrollConfirmRequest

[backend/app/models/schemas.py:366](../../backend/app/models/schemas.py#L366)

Bases: `BaseModel`.



```python
code: str = Field(..., max_length=12)
enrollment_token: Optional[str] = None
```

## backend/app/models/schemas.py — MfaEnrollConfirmResponse

[backend/app/models/schemas.py:371](../../backend/app/models/schemas.py#L371)

Bases: `BaseModel`.



```python
enabled: Literal[True] = True
recovery_codes: List[str]
tokens: Optional[TokenResponse] = None
```

## backend/app/models/schemas.py — MfaVerifyRequest

[backend/app/models/schemas.py:380](../../backend/app/models/schemas.py#L380)

Bases: `BaseModel`.



```python
challenge_token: str
code: Optional[str] = Field(None, max_length=12)
recovery_code: Optional[str] = Field(None, max_length=64)
```

## backend/app/models/schemas.py — MfaDisableRequest

[backend/app/models/schemas.py:386](../../backend/app/models/schemas.py#L386)

Bases: `BaseModel`.

Disabling requires the password *and* a live second factor.

Password alone would let anyone holding a stolen session strip the factor
that session was supposed to be protected by.

```python
password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
code: Optional[str] = Field(None, max_length=12)
recovery_code: Optional[str] = Field(None, max_length=64)
```

## backend/app/models/schemas.py — MfaRecoveryCodesRequest

[backend/app/models/schemas.py:397](../../backend/app/models/schemas.py#L397)

Bases: `BaseModel`.



```python
password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
code: Optional[str] = Field(None, max_length=12)
recovery_code: Optional[str] = Field(None, max_length=64)
```

## backend/app/models/schemas.py — MfaRecoveryCodesResponse

[backend/app/models/schemas.py:403](../../backend/app/models/schemas.py#L403)

Bases: `BaseModel`.



```python
recovery_codes: List[str]
```

## backend/app/models/schemas.py — MfaStatusResponse

[backend/app/models/schemas.py:407](../../backend/app/models/schemas.py#L407)

Bases: `BaseModel`.



```python
enabled: bool
enrolled_at: Optional[datetime] = None
recovery_codes_remaining: int = 0
required_by_policy: bool = False
sso_managed: bool = False
secret_unreadable: bool = False
```

## backend/app/models/schemas.py — MfaPolicyRead

[backend/app/models/schemas.py:421](../../backend/app/models/schemas.py#L421)

Bases: `BaseModel`.



```python
require_mfa: bool
required_for_role: Optional[UserRole] = None
lockout_enabled: bool
lockout_threshold: int
lockout_duration_minutes: int
```

## backend/app/models/schemas.py — MfaPolicyUpdate

[backend/app/models/schemas.py:429](../../backend/app/models/schemas.py#L429)

Bases: `BaseModel`.



```python
require_mfa: Optional[bool] = None
required_for_role: Optional[UserRole] = None
lockout_enabled: Optional[bool] = None
lockout_threshold: Optional[int] = Field(None, ge=3, le=100)
lockout_duration_minutes: Optional[int] = Field(None, ge=1, le=1440)
clear_required_for_role: bool = False
```

## backend/app/models/schemas.py — ProjectCreate

[backend/app/models/schemas.py:443](../../backend/app/models/schemas.py#L443)

Bases: `BaseModel`.



```python
name: str = Field(..., min_length=2, max_length=255)
slug: str = Field(..., min_length=2, max_length=100, pattern='^[a-z0-9-]+$')
description: Optional[str] = Field(None, max_length=2000)
jira_project_key: Optional[str] = Field(None, max_length=50)
splunk_index: Optional[str] = Field(None, max_length=255)
ocp_namespace: Optional[str] = Field(None, max_length=255)
jenkins_job_pattern: Optional[str] = Field(None, max_length=500)
component_owner_map: Optional[dict] = None
default_qa_lead_user_id: Optional[uuid.UUID] = None
```

## backend/app/models/schemas.py — ProjectUpdate

[backend/app/models/schemas.py:458](../../backend/app/models/schemas.py#L458)

Bases: `BaseModel`.

Partial update for project attributes. None = keep existing.

```python
name: Optional[str] = Field(None, min_length=2, max_length=255)
description: Optional[str] = Field(None, max_length=2000)
jira_project_key: Optional[str] = Field(None, max_length=50)
splunk_index: Optional[str] = Field(None, max_length=255)
ocp_namespace: Optional[str] = Field(None, max_length=255)
jenkins_job_pattern: Optional[str] = Field(None, max_length=500)
component_owner_map: Optional[dict] = None
start_date: Optional[datetime] = None
end_date: Optional[datetime] = None
tags: Optional[List[str]] = None
manager_user_id: Optional[uuid.UUID] = None
default_qa_lead_user_id: Optional[uuid.UUID] = None
```

## backend/app/models/schemas.py — ProjectResponse

[backend/app/models/schemas.py:474](../../backend/app/models/schemas.py#L474)

Bases: `TimestampMixin`.



```python
id: uuid.UUID
name: str
slug: str
description: Optional[str] = None
jira_project_key: Optional[str] = None
splunk_index: Optional[str] = None
ocp_namespace: Optional[str] = None
jenkins_job_pattern: Optional[str] = None
component_owner_map: Optional[dict] = None
start_date: Optional[datetime] = None
end_date: Optional[datetime] = None
tags: Optional[List[Any]] = None
is_active: bool
manager_user_id: Optional[uuid.UUID] = None
default_qa_lead_user_id: Optional[uuid.UUID] = None
retention_status: Optional[Literal['enabled', 'disabled', 'unconfigured']] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — DeleteRunRequest

[backend/app/models/schemas.py:501](../../backend/app/models/schemas.py#L501)

Bases: `BaseModel`.

Payload for ``DELETE /api/v1/runs/{run_id}``.

``confirm`` is a deliberate second step, not ceremony: this deletes across
five stores and is irreversible. ``reason`` is recorded on the deletion job
and the tombstone, so "why is this run gone" has an answer later.

```python
confirm: bool = Field(..., description='Must be true. A DELETE without it is refused, not assumed.')
reason: str = Field(..., min_length=3, max_length=500)
```

## backend/app/models/schemas.py — DeleteRunAcceptedResponse

[backend/app/models/schemas.py:516](../../backend/app/models/schemas.py#L516)

Bases: `BaseModel`.

202, not 204.

A synchronous delete of a multi-GB prefix times out at the gateway, and
because the cross-store order is Mongo -> MinIO -> Postgres the caller
would get a 504 with the artifacts already gone and the run still listed.
Poll ``job_id`` on the deletion-jobs endpoint instead.

```python
job_id: Optional[uuid.UUID] = Field(None, description='Deletion job to poll. NULL when the job record could not be written — the deletion still runs; only its bookkeeping failed.')
run_id: uuid.UUID
status: Literal['accepted'] = 'accepted'
refused_prefixes: List[str] = Field(default_factory=list, description="Object prefixes NOT deleted because they fall outside the project's scope. Reported rather than silently skipped: storage that does not fall needs an explanation.")
```

## backend/app/models/schemas.py — DeleteRunRefusedResponse

[backend/app/models/schemas.py:544](../../backend/app/models/schemas.py#L544)

Bases: `BaseModel`.

409 body. Every blocker is listed, not just the first one found.

```python
run_id: uuid.UUID
blockers: List[str]
```

## backend/app/models/schemas.py — ProjectResetRequest

[backend/app/models/schemas.py:551](../../backend/app/models/schemas.py#L551)

Bases: `BaseModel`.

Destructive reset payload. ``mode`` selects the wipe scope; the
backend rejects any request whose ``confirmation_name`` doesn't
exactly equal the project's ``name`` — a typed-confirmation guard
against autopilot clicks. See services/project_reset_service.py for
the table list per mode.

```python
mode: Literal['runs', 'full']
confirmation_name: str = Field(..., min_length=1, max_length=255)
```

## backend/app/models/schemas.py — ProjectResetResponse

[backend/app/models/schemas.py:562](../../backend/app/models/schemas.py#L562)

Bases: `BaseModel`.



```python
mode: Literal['runs', 'full']
deleted: dict[str, int]
```

## backend/app/models/schemas.py — TestRunSummary

[backend/app/models/schemas.py:569](../../backend/app/models/schemas.py#L569)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
build_number: str
jenkins_job: Optional[str] = None
trigger_source: Optional[str] = None
ingestion_source: Optional[str] = None
ingestion_attempted_tests: Optional[int] = None
ingestion_rejected_tests: int = 0
ingestion_complete: Optional[bool] = None
ingestion_rejection_reasons: Optional[List[dict]] = None
branch: Optional[str] = None
status: LaunchStatus
total_tests: int
passed_tests: int
failed_tests: int
skipped_tests: int
broken_tests: int
unknown_tests: int = 0
pass_rate: Optional[float] = None
duration_ms: Optional[int] = None
ocp_pod_name: Optional[str] = None
ocp_namespace: Optional[str] = None
primary_suite_name: Optional[str] = None
suite_names: Optional[List[str]] = None
start_time: Optional[datetime] = None
end_time: Optional[datetime] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestRunListResponse

[backend/app/models/schemas.py:601](../../backend/app/models/schemas.py#L601)

Bases: `BaseModel`.



```python
items: List[TestRunSummary]
total: int
page: int
size: int
pages: int
```

## backend/app/models/schemas.py — TestCaseSummary

[backend/app/models/schemas.py:611](../../backend/app/models/schemas.py#L611)

Bases: `BaseModel`.



```python
id: uuid.UUID
test_run_id: uuid.UUID
test_name: str
suite_name: Optional[str] = None
class_name: Optional[str] = None
status: TestStatus
duration_ms: Optional[int] = None
severity: Optional[str] = None
feature: Optional[str] = None
failure_category: Optional[str] = None
has_attachments: bool = False
step_count: Optional[int] = None
created_at: datetime
assigned_to_user_id: Optional[uuid.UUID] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestCaseDetail

[backend/app/models/schemas.py:665](../../backend/app/models/schemas.py#L665)

Bases: `TestCaseSummary`.



```python
full_name: Optional[str] = None
package_name: Optional[str] = None
story: Optional[str] = None
epic: Optional[str] = None
owner: Optional[str] = None
tags: Optional[List[str]] = None
error_message: Optional[str] = None
minio_s3_prefix: Optional[str] = None
```

## backend/app/models/schemas.py — TestCaseListResponse

[backend/app/models/schemas.py:676](../../backend/app/models/schemas.py#L676)

Bases: `BaseModel`.



```python
items: List[TestCaseSummary]
total: int
page: int
size: int
pages: int
```

## backend/app/models/schemas.py — TestAttachmentResponse

[backend/app/models/schemas.py:690](../../backend/app/models/schemas.py#L690)

Bases: `BaseModel`.

Index-only attachment metadata (Phase 1 stores refs, not bytes).

```python
id: uuid.UUID
test_step_id: Optional[uuid.UUID] = None
source_test_run_id: Optional[uuid.UUID] = None
name: str
source_ref: Optional[str] = None
media_type: Optional[str] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestStepResponse

[backend/app/models/schemas.py:702](../../backend/app/models/schemas.py#L702)

Bases: `BaseModel`.

One granular step in a logical test's latest-run snapshot.

``steps`` carries the nested child steps (Allure before/after + sub-steps).

```python
id: uuid.UUID
parent_step_id: Optional[uuid.UUID] = None
ordinal: int
depth: int
name: str
keyword: Optional[str] = None
status: TestStatus
duration_ms: Optional[int] = None
start_ms: Optional[int] = None
assertion_message: Optional[str] = None
assertion_trace: Optional[str] = None
expected_value: Optional[str] = None
actual_value: Optional[str] = None
parameters: Optional[Union[Dict[str, Any], List[Any]]] = None
created_at: datetime
steps: List['TestStepResponse'] = Field(default_factory=list)
attachments: List[TestAttachmentResponse] = Field(default_factory=list)
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestCaseIdentitySection

[backend/app/models/schemas.py:733](../../backend/app/models/schemas.py#L733)

Bases: `BaseModel`.

Stable and source-native identifiers for one executed test result.

```python
test_case_id: uuid.UUID
test_run_id: uuid.UUID
canonical_test_case_id: Optional[uuid.UUID] = None
test_fingerprint: str
test_name: str
full_name: Optional[str] = None
source_uuid: Optional[str] = None
source_history_id: Optional[str] = None
source_test_case_id: Optional[str] = None
history_id: Optional[str] = None
fingerprint: Optional[str] = None
display_name: Optional[str] = None
```

## backend/app/models/schemas.py — TestCaseClassificationSection

[backend/app/models/schemas.py:750](../../backend/app/models/schemas.py#L750)

Bases: `BaseModel`.

Classification fields supplied by the report or existing catalog.

```python
suite_name: Optional[str] = None
class_name: Optional[str] = None
package_name: Optional[str] = None
severity: Optional[str] = None
feature: Optional[str] = None
story: Optional[str] = None
epic: Optional[str] = None
owner: Optional[str] = None
tags: List[str] = Field(default_factory=list)
service_name: Optional[str] = None
component_name: Optional[str] = None
suite: Optional[Dict[str, Optional[str]]] = None
service: Optional[Dict[str, Any]] = None
components: List[Dict[str, Any]] = Field(default_factory=list)
file_path: Optional[str] = None
framework: Optional[str] = None
language: Optional[str] = None
labels: List[Dict[str, str]] = Field(default_factory=list)
```

## backend/app/models/schemas.py — TestCaseExecutionSection

[backend/app/models/schemas.py:774](../../backend/app/models/schemas.py#L774)

Bases: `BaseModel`.

Outcome and bounded execution metadata for the result snapshot.

```python
status: TestStatus
duration_ms: Optional[int] = None
retry_count: Optional[int] = None
is_flaky_run: Optional[bool] = None
step_count: Optional[int] = None
steps_present: bool = False
has_attachments: bool = False
failure_category: Optional[str] = None
error_message: Optional[str] = None
stack_trace: Optional[str] = None
parameters: List[Dict[str, Any]] = Field(default_factory=list)
```

## backend/app/models/schemas.py — TestCaseProvenanceSection

[backend/app/models/schemas.py:790](../../backend/app/models/schemas.py#L790)

Bases: `BaseModel`.

Where the current normalized detail came from.

```python
source_test_run_id: Optional[uuid.UUID] = None
parser_format: Optional[str] = None
parser_version: Optional[str] = None
minio_s3_prefix: Optional[str] = None
format: Optional[str] = None
source_file: Optional[str] = None
field_sources: Dict[str, str] = Field(default_factory=dict)
warnings: List[str] = Field(default_factory=list)
```

## backend/app/models/schemas.py — EnrichedTestCaseDetailResponse

[backend/app/models/schemas.py:803](../../backend/app/models/schemas.py#L803)

Bases: `TestCaseDetail`.

Versioned additive contract for an executed test case.

Inherited flat fields intentionally remain present for existing clients.
Structured sections provide stable extension points for richer metadata.

```python
contract: Literal['test-case-detail'] = 'test-case-detail'
schema_version: int = 1
contract_version: Literal['1'] = '1'
identity: TestCaseIdentitySection
classification: TestCaseClassificationSection
execution: TestCaseExecutionSection
provenance: TestCaseProvenanceSection
steps_present: bool = False
steps: List[TestStepResponse] = Field(default_factory=list)
attachments: List[TestAttachmentResponse] = Field(default_factory=list)
definition: Optional[Dict[str, Any]] = None
links: List[Dict[str, Optional[str]]] = Field(default_factory=list)
extensions: Dict[str, Any] = Field(default_factory=dict)
```

## backend/app/models/schemas.py — DuplicateCaseRef

[backend/app/models/schemas.py:835](../../backend/app/models/schemas.py#L835)

Bases: `BaseModel`.

Minimal reference to one ManagedTestCase in a duplicate pair.

```python
id: uuid.UUID
title: str
suite_name: Optional[str] = None
status: Optional[str] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — DuplicateCandidateResponse

[backend/app/models/schemas.py:844](../../backend/app/models/schemas.py#L844)

Bases: `BaseModel`.

One detected near-duplicate pair with its explainable score breakdown.

```python
id: uuid.UUID
project_id: uuid.UUID
band: DuplicateBand
score: float
reason: Optional[str] = None
method: DuplicateMethod
component_scores: Optional[Dict[str, Any]] = None
status: DuplicateCandidateStatus
detected_at: datetime
case_a: DuplicateCaseRef
case_b: DuplicateCaseRef
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — DuplicateCandidateListResponse

[backend/app/models/schemas.py:860](../../backend/app/models/schemas.py#L860)

Bases: `BaseModel`.

Paginated list of duplicate candidates for a project's review queue.

```python
items: List[DuplicateCandidateResponse] = Field(default_factory=list)
total: int = 0
open_count: int = 0
```

## backend/app/models/schemas.py — DuplicateDismissRequest

[backend/app/models/schemas.py:867](../../backend/app/models/schemas.py#L867)

Bases: `BaseModel`.

Dismiss a candidate pair so it stays suppressed across re-detection runs.

```python
candidate_id: uuid.UUID
```

## backend/app/models/schemas.py — DuplicateMergeRequest

[backend/app/models/schemas.py:872](../../backend/app/models/schemas.py#L872)

Bases: `BaseModel`.

Non-destructive merge: flip the candidate to ``merged`` and optionally
soft-deprecate the losing case. NEVER deletes a case this phase.

```python
candidate_id: uuid.UUID
keep_case_id: uuid.UUID
deprecate_loser: bool = False
```

## backend/app/models/schemas.py — DuplicateActionResponse

[backend/app/models/schemas.py:886](../../backend/app/models/schemas.py#L886)

Bases: `BaseModel`.

Result of a dismiss / merge action on a candidate pair.

```python
candidate_id: uuid.UUID
status: DuplicateCandidateStatus
deprecated_case_id: Optional[uuid.UUID] = None
```

## backend/app/models/schemas.py — DuplicateDetectionRunResponse

[backend/app/models/schemas.py:893](../../backend/app/models/schemas.py#L893)

Bases: `BaseModel`.

Summary of a triggered detection sweep over a project's authored cases.

```python
project_id: uuid.UUID
candidates_created: int = 0
candidates_total: int = 0
cases_scanned: int = 0
sampled: bool = False
note: Optional[str] = None
```

## backend/app/models/schemas.py — TestExecutionReviewRead

[backend/app/models/schemas.py:917](../../backend/app/models/schemas.py#L917)

Bases: `BaseModel`.

Current review state for an AI-flagged failure.

```python
id: uuid.UUID
test_case_id: uuid.UUID
project_id: uuid.UUID
state: TestExecutionReviewState
reviewed_by_user_id: Optional[uuid.UUID] = None
reviewed_by_username: Optional[str] = None
reviewed_by_full_name: Optional[str] = None
defect_link: Optional[str] = None
note: Optional[str] = None
transitioned_at: Optional[datetime] = None
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestExecutionReviewUpdate

[backend/app/models/schemas.py:934](../../backend/app/models/schemas.py#L934)

Bases: `BaseModel`.

Transition the review state. ``state`` is required; other fields are
optional context the reviewer can attach (e.g. defect URL on
``defect_filed``, freeform note explaining the verdict).

```python
state: TestExecutionReviewState
defect_link: Optional[str] = Field(None, max_length=2000)
note: Optional[str] = Field(None, max_length=4000)
```

## backend/app/models/schemas.py — SummaryTotals

[backend/app/models/schemas.py:946](../../backend/app/models/schemas.py#L946)

Bases: `BaseModel`.



```python
total_test_cases: int
passed: int
failed: int
skipped: int
broken: int
evaluated: int
pass_rate_pct: float
pass_rate_basis: str = 'unique_tests'
pass_rate_basis_label: str = 'per unique test'
fail_rate_pct: float
skip_rate_pct: float
broken_rate_pct: float
weighted_pass_rate_pct: float
```

## backend/app/models/schemas.py — SummaryStepBreakdownRow

[backend/app/models/schemas.py:970](../../backend/app/models/schemas.py#L970)

Bases: `BaseModel`.

One captured granular step for a failing test (Phase 5 enrichment).

```python
name: Optional[str] = None
status: Optional[str] = None
assertion_message: Optional[str] = None
```

## backend/app/models/schemas.py — SummarySuiteRow

[backend/app/models/schemas.py:977](../../backend/app/models/schemas.py#L977)

Bases: `BaseModel`.



```python
suite_name: str
total: int
passed: int
failed: int
skipped: int
broken: int
pass_rate_pct: float
weighted_pass_rate_pct: float
last_run_at: Optional[str] = None
step_success_rate: Optional[float] = None
passed_steps: Optional[int] = None
total_steps: Optional[int] = None
```

## backend/app/models/schemas.py — SummaryTopFailingTest

[backend/app/models/schemas.py:996](../../backend/app/models/schemas.py#L996)

Bases: `BaseModel`.



```python
suite_name: Optional[str] = None
class_name: Optional[str] = None
test_name: str
failures: int
failure_step: Optional[str] = None
step_breakdown: Optional[List[SummaryStepBreakdownRow]] = None
```

## backend/app/models/schemas.py — FlakyCountCriteria

[backend/app/models/schemas.py:1009](../../backend/app/models/schemas.py#L1009)

Bases: `BaseModel`.

What a flaky-test count actually measured.

A bare count cannot distinguish "this project has no flaky tests" from
"no test cleared this particular bar", and surfaces applying different bars
then look like they contradict each other.

```python
window_runs: int = Field(..., description="How many of each test's most recent runs were examined.")
min_runs: int = Field(..., description='Runs a test must have inside the window to be judged at all. A test with fewer is not counted as flaky and not counted as healthy — there is not enough history to say.')
min_flips: int = Field(..., description='Pass<->fail transitions required, in run order. This is what separates a flaky test from a persistent regression, which a failure ratio alone cannot do.')
min_failure_ratio: float = Field(..., description='Lower bound of the failure ratio. Below it the test is treated as healthy rather than flaky.')
max_failure_ratio: float = Field(..., description='Upper bound of the failure ratio. Above it the test is treated as broken rather than flaky — it is not intermittent, it is failing.')
```

## backend/app/models/schemas.py — SummaryReportResponse

[backend/app/models/schemas.py:1052](../../backend/app/models/schemas.py#L1052)

Bases: `BaseModel`.



```python
project_id: Optional[str] = None
project_name: Optional[str] = None
mode: Literal['window', 'latest']
window_days: int
generated_at: str
period_start: str
period_end: str
totals: SummaryTotals
run_count: int
runs_per_day: Optional[float] = None
avg_duration_ms: int
latest_run_at: Optional[str] = None
flaky_test_count: int
flaky_rate_pct: float
flaky_criteria: Optional[FlakyCountCriteria] = None
suites: List[SummarySuiteRow]
top_failing_tests: List[SummaryTopFailingTest]
```

## backend/app/models/schemas.py — MyFailureItem

[backend/app/models/schemas.py:1088](../../backend/app/models/schemas.py#L1088)

Bases: `BaseModel`.

A single auto-assigned failure surfaced on the calling user's inbox.

Carries enough context to render a triage row without a follow-up fetch:
test name + suite + run identity + project label + relative age. The
``navigation_url`` is the canonical deep link to the run-detail page's
test-case drawer.

```python
id: uuid.UUID
test_name: str
suite_name: Optional[str] = None
class_name: Optional[str] = None
status: TestStatus
severity: Optional[str] = None
failure_category: Optional[str] = None
error_message: Optional[str] = None
duration_ms: Optional[int] = None
created_at: datetime
test_run_id: uuid.UUID
build_number: Optional[str] = None
project_id: uuid.UUID
project_name: Optional[str] = None
navigation_url: str
triage_status: str = 'PENDING_REVIEW'
triage_notes: Optional[str] = None
run_seq: Optional[int] = None
failure_count: int = 1
last_failure_step: Optional[str] = None
assignment_reason: Optional[str] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TriageStatusUpdate

[backend/app/models/schemas.py:1138](../../backend/app/models/schemas.py#L1138)

Bases: `BaseModel`.

Body of ``PUT /api/v1/me/assigned-failures/{id}/triage``.

``status`` is validated against the ``TriageStatus`` enum at the
service layer (the regex form here keeps the OpenAPI schema readable
while still rejecting arbitrary strings; we don't gain anything
from using a Pydantic Enum directly because the service maps to the
canonical enum anyway).

```python
status: str = Field(..., pattern='^(PENDING_REVIEW|REVIEWED_APPROVED|DEFECT_CREATED|WONT_FIX|AUTOMATION_SCRIPT_ISSUE|FLAKY_TEST)$', description="New triage status. Any value other than PENDING_REVIEW drops the row from the assignee's /my-failures inbox.")
notes: Optional[str] = Field(None, max_length=2000, description='Free-form context. Typically a defect link for DEFECT_CREATED or a rationale for WONT_FIX / REVIEWED_APPROVED.')
```

## backend/app/models/schemas.py — MyFailureListResponse

[backend/app/models/schemas.py:1159](../../backend/app/models/schemas.py#L1159)

Bases: `BaseModel`.



```python
items: List[MyFailureItem]
total: int
page: int
size: int
pages: int
unresolved_total: int
```

## backend/app/models/schemas.py — MetricCard

[backend/app/models/schemas.py:1172](../../backend/app/models/schemas.py#L1172)

Bases: `BaseModel`.



```python
value: Any
trend: Optional[float] = None
trend_direction: Optional[str] = None
```

## backend/app/models/schemas.py — DashboardSummary

[backend/app/models/schemas.py:1178](../../backend/app/models/schemas.py#L1178)

Bases: `BaseModel`.



```python
total_executions_7d: MetricCard
avg_pass_rate_7d: MetricCard
active_defects: MetricCard
flaky_test_count: MetricCard
avg_duration_ms: MetricCard
new_failures_24h: MetricCard
coverage_pct: Optional[MetricCard] = None
release_readiness: Optional[str] = None
release_readiness_band: Optional[str] = None
release_readiness_downgrades: List[str] = Field(default_factory=list)
```

## backend/app/models/schemas.py — TrendDataPoint

[backend/app/models/schemas.py:1194](../../backend/app/models/schemas.py#L1194)

Bases: `BaseModel`.



```python
date: str
passed: int
failed: int
skipped: int
broken: int
total: int
pass_rate: float
```

## backend/app/models/schemas.py — TrendResponse

[backend/app/models/schemas.py:1204](../../backend/app/models/schemas.py#L1204)

Bases: `BaseModel`.



```python
data: List[TrendDataPoint]
period_days: int
```

## backend/app/models/schemas.py — MinIOWebhookEvent

[backend/app/models/schemas.py:1211](../../backend/app/models/schemas.py#L1211)

Bases: `BaseModel`.

MinIO ObjectCreated webhook payload.

```python
EventName: str
Key: str
Records: Optional[List[dict]] = None
```

## backend/app/models/schemas.py — SentinelFile

[backend/app/models/schemas.py:1218](../../backend/app/models/schemas.py#L1218)

Bases: `BaseModel`.

upload_complete.json sentinel file content.

```python
build_number: str
project_id: str
jenkins_job: Optional[str] = None
trigger_source: Optional[str] = 'push'
branch: Optional[str] = None
commit_hash: Optional[str] = None
ocp_pod_name: Optional[str] = None
ocp_namespace: Optional[str] = None
release_name: Optional[str] = None
```

## backend/app/models/schemas.py — AnalyzeRequest

[backend/app/models/schemas.py:1235](../../backend/app/models/schemas.py#L1235)

Bases: `BaseModel`.



```python
test_case_id: uuid.UUID
service_name: Optional[str] = None
timestamp: Optional[str] = None
ocp_pod_name: Optional[str] = None
ocp_namespace: Optional[str] = None
```

## backend/app/models/schemas.py — EvidenceReference

[backend/app/models/schemas.py:1243](../../backend/app/models/schemas.py#L1243)

Bases: `BaseModel`.



```python
source: str
reference_id: str
excerpt: str
```

## backend/app/models/schemas.py — RoleActions

[backend/app/models/schemas.py:1249](../../backend/app/models/schemas.py#L1249)

Bases: `BaseModel`.

Role-aware recommended actions generated by the ReAct agent.

```python
qa: str = ''
developer: str = ''
sre: str = ''
release_manager: str = ''
```

## backend/app/models/schemas.py — ConfidenceWhy

[backend/app/models/schemas.py:1257](../../backend/app/models/schemas.py#L1257)

Bases: `BaseModel`.

Explains the basis of the confidence score for transparency.

```python
evidence_count: int = 0
data_sources: List[str] = []
is_llm_inference: bool = False
investigation_depth: str = 'fast_path'
confidence_basis: Optional[str] = None
```

## backend/app/models/schemas.py — ThresholdCheck

[backend/app/models/schemas.py:1272](../../backend/app/models/schemas.py#L1272)

Bases: `BaseModel`.

US-15.2 — the recorded confidence-gate evaluation for one AI output.

Persisted verbatim in ``AIAnalysis.routing_metadata["threshold_check"]``
and ``Defect.policy_evaluation["threshold_check"]``. Built by
``services.confidence_gate.build_threshold_check`` — keep the shapes in
sync (four keys, no more).

```python
threshold: int
observed_confidence: Optional[int] = None
passed: bool
source: str
```

## backend/app/models/schemas.py — AnalysisProvenance

[backend/app/models/schemas.py:1289](../../backend/app/models/schemas.py#L1289)

Bases: `BaseModel`.

US-15.1 — which engine actually produced this conclusion, and why.

Populated verbatim from ``AIAnalysis.routing_metadata`` (written by the
analysis router). It is NEVER recomputed or inferred: rows analysed before
this feature carry no routing metadata, so ``AnalysisResponse.provenance``
is ``None`` for them rather than a plausible-looking guess.

The point of this block is a specific honesty case: when the LLM was
unavailable and the rules engine ran instead, the card must be able to say
so (``fallback_occurred`` / ``fallback_from`` / ``fallback_reason``)
instead of silently presenting heuristics as model output.

```python
mode_used: Optional[str] = None
mode_requested: Optional[str] = None
mode_resolved: Optional[str] = None
fallback_from: Optional[str] = None
fallback_reason: Optional[str] = None
fallback_occurred: bool = False
llm_provider: Optional[str] = None
llm_model: Optional[str] = None
prompt_versions: Dict[str, str] = Field(default_factory=dict)
confidence_basis: Optional[str] = None
threshold_check: Optional[ThresholdCheck] = None
```

## backend/app/models/schemas.py — AnalysisResponse

[backend/app/models/schemas.py:1327](../../backend/app/models/schemas.py#L1327)

Bases: `BaseModel`.



```python
test_case_id: uuid.UUID
root_cause_summary: str
failure_category: FailureCategory
backend_error_found: bool
pod_issue_found: bool
is_flaky: bool
confidence_score: int
recommended_actions: List[str]
role_actions: RoleActions = Field(default_factory=RoleActions)
evidence_references: List[EvidenceReference]
tools_used: List[str] = []
confidence_why: ConfidenceWhy = Field(default_factory=ConfidenceWhy)
kind_evidence: Optional[Dict[str, Any]] = None
llm_provider: str
llm_model: str
requires_human_review: bool
analysis_id: Optional[uuid.UUID] = None
provenance: Optional[AnalysisProvenance] = None
low_confidence: bool = False
confidence_gate_status: str = 'not_evaluated'
confidence_gate: Optional[ThresholdCheck] = None
```

## backend/app/models/schemas.py — JiraIssueRequest

[backend/app/models/schemas.py:1378](../../backend/app/models/schemas.py#L1378)

Bases: `BaseModel`.



```python
project_key: str
test_case_id: uuid.UUID
test_name: str
run_id: uuid.UUID
ai_summary: str
recommended_action: str
```

## backend/app/models/schemas.py — JiraIssueResponse

[backend/app/models/schemas.py:1387](../../backend/app/models/schemas.py#L1387)

Bases: `BaseModel`.



```python
ticket_id: Optional[str] = None
ticket_key: Optional[str] = None
ticket_url: Optional[str] = None
approval_status: Optional[str] = None
requires_approval: bool = False
policy_reasons: List[str] = Field(default_factory=list)
defect_id: Optional[uuid.UUID] = None
mutating_action: Optional[str] = None
```

## backend/app/models/schemas.py — SearchRequest

[backend/app/models/schemas.py:1400](../../backend/app/models/schemas.py#L1400)

Bases: `BaseModel`.



```python
q: str = Field(..., min_length=1)
project_id: Optional[uuid.UUID] = None
status: Optional[TestStatus] = None
suite: Optional[str] = None
days: Optional[int] = Field(None, ge=1, le=365)
use_semantic: bool = False
page: int = Field(1, ge=1)
size: int = Field(20, ge=1, le=100)
```

## backend/app/models/schemas.py — SearchResult

[backend/app/models/schemas.py:1411](../../backend/app/models/schemas.py#L1411)

Bases: `BaseModel`.



```python
test_case_id: uuid.UUID
test_run_id: uuid.UUID
test_name: str
suite_name: Optional[str] = None
status: TestStatus
last_run_date: datetime
failure_count: int
relevance_score: Optional[float] = None
match_reasons: List[str] = []
source_mode_used: str = 'keyword'
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SearchResponse

[backend/app/models/schemas.py:1426](../../backend/app/models/schemas.py#L1426)

Bases: `BaseModel`.



```python
items: List[SearchResult]
total: int
query: str
search_type: str
```

## backend/app/models/schemas.py — GlobalSearchResult

[backend/app/models/schemas.py:1435](../../backend/app/models/schemas.py#L1435)

Bases: `BaseModel`.

A single result from system-wide global search.

```python
entity_type: str
entity_id: str
title: str
subtitle: str = ''
project_id: Optional[str] = None
project_name: Optional[str] = None
navigation_url: str
relevance_score: float = 0.0
match_reasons: List[str] = []
metadata: dict = Field(default_factory=dict)
```

## backend/app/models/schemas.py — GlobalSearchResponse

[backend/app/models/schemas.py:1449](../../backend/app/models/schemas.py#L1449)

Bases: `BaseModel`.

Response from the global search endpoint.

```python
items: List[GlobalSearchResult]
total: int
query: str
search_type: str = 'keyword'
entity_counts: dict = Field(default_factory=dict)
page: int = 1
size: int = 20
pages: int = 1
```

## backend/app/models/schemas.py — QualityGateRule

[backend/app/models/schemas.py:1463](../../backend/app/models/schemas.py#L1463)

Bases: `BaseModel`.



```python
rule_type: str
threshold: Any
severity: str = 'FAIL'
description: Optional[str] = None
```

## backend/app/models/schemas.py — QualityGateCreate

[backend/app/models/schemas.py:1470](../../backend/app/models/schemas.py#L1470)

Bases: `BaseModel`.



```python
name: str = Field(..., max_length=255)
rules: List[QualityGateRule]
```

## backend/app/models/schemas.py — QualityGateEvaluationResult

[backend/app/models/schemas.py:1475](../../backend/app/models/schemas.py#L1475)

Bases: `BaseModel`.



```python
gate_id: uuid.UUID
run_id: uuid.UUID
status: str
rules_evaluated: List[dict]
evaluated_at: datetime
```

## backend/app/models/schemas.py — NotificationPreferenceCreate

[backend/app/models/schemas.py:1485](../../backend/app/models/schemas.py#L1485)

Bases: `BaseModel`.

Create or replace a single channel preference.

```python
project_id: Optional[uuid.UUID] = None
channel: NotificationChannel
enabled: bool = True
events: List[str] = Field(default_factory=lambda: ['run_failed', 'high_failure_rate', 'test.newly_failing', 'test.recovered', 'test.newly_flaky', 'test.quarantined', 'test.unquarantined'], description='List of NotificationEventType values')
failure_rate_threshold: float = Field(default=80.0, ge=0.0, le=100.0)
email_override: Optional[EmailStr] = None
slack_webhook_url: Optional[str] = Field(None, max_length=2000)
teams_webhook_url: Optional[str] = Field(None, max_length=2000)
```

## backend/app/models/schemas.py — NotificationPreferenceResponse

[backend/app/models/schemas.py:1512](../../backend/app/models/schemas.py#L1512)

Bases: `BaseModel`.



```python
id: uuid.UUID
user_id: uuid.UUID
project_id: Optional[uuid.UUID]
channel: str
enabled: bool
events: List[str]
failure_rate_threshold: Optional[float]
email_override: Optional[str]
slack_webhook_url: Optional[str]
teams_webhook_url: Optional[str]
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — NotificationLogResponse

[backend/app/models/schemas.py:1529](../../backend/app/models/schemas.py#L1529)

Bases: `BaseModel`.



```python
id: uuid.UUID
channel: str
event_type: str
title: str
body: str
status: str
is_read: bool
sent_at: Optional[datetime]
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestNotificationRequest

[backend/app/models/schemas.py:1543](../../backend/app/models/schemas.py#L1543)

Bases: `BaseModel`.



```python
channel: NotificationChannel
preference_id: Optional[uuid.UUID] = None
```

## backend/app/models/schemas.py — NotificationTransitionPolicyUpdate

[backend/app/models/schemas.py:1562](../../backend/app/models/schemas.py#L1562)

Bases: `BaseModel`.

Per-project transition-notification policy (PMF US-7.1).

```python
transitions_enabled: bool = True
per_run_events_enabled: bool = False
enabled_events: List[str] = Field(default_factory=lambda: list(TRANSITION_EVENT_VALUES), description='Transition NotificationEventType values enabled for the project')
consecutive_failure_threshold: int = Field(default=2, ge=1, le=20)
```

- Validator/serializer `_known_transition_events`: [backend/app/models/schemas.py:1574](../../backend/app/models/schemas.py#L1574). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — NotificationTransitionPolicyResponse

[backend/app/models/schemas.py:1586](../../backend/app/models/schemas.py#L1586)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
transitions_enabled: bool
per_run_events_enabled: bool
enabled_events: List[str]
consecutive_failure_threshold: int
is_default: bool = False
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TriggerPipelineRequest

[backend/app/models/schemas.py:1601](../../backend/app/models/schemas.py#L1601)

Bases: `BaseModel`.



```python
test_run_id: uuid.UUID
workflow_id: Optional[str] = Field(default=None, min_length=3, max_length=80, pattern='^(?:wf\\.[a-z0-9_.-]+|offline|deep|live)$')
workflow_version: Optional[int] = Field(default=None, ge=1)
```

- Validator/serializer `version_requires_workflow`: [backend/app/models/schemas.py:1612](../../backend/app/models/schemas.py#L1612). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — AgentStageResultResponse

[backend/app/models/schemas.py:1618](../../backend/app/models/schemas.py#L1618)

Bases: `BaseModel`.



```python
stage_name: str
status: str
started_at: Optional[Any] = None
completed_at: Optional[Any] = None
result_data: Optional[Any] = None
error: Optional[str] = None
skipped_reason: Optional[str] = None
execution_path: Optional[str] = None
fallback_used: Optional[bool] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AgentPipelineReviewSummary

[backend/app/models/schemas.py:1633](../../backend/app/models/schemas.py#L1633)

Bases: `BaseModel`.

Minimal review projection for a pipeline card.

Reviewer identity deliberately stays out of this API response.  A settled
timestamp is enough for the status card to distinguish an accepted report
from a non-report pipeline that passed without human review.

```python
state: Literal['pending_review', 'accepted', 'rejected']
settled_at: Optional[datetime] = None
```

## backend/app/models/schemas.py — AgentPipelineResponse

[backend/app/models/schemas.py:1645](../../backend/app/models/schemas.py#L1645)

Bases: `BaseModel`.



```python
id: uuid.UUID
test_run_id: uuid.UUID
workflow_type: str
status: str
public_status: Optional[str] = None
started_at: Optional[Any] = None
completed_at: Optional[Any] = None
error: Optional[str] = None
created_at: Any
execution_metadata: Optional[Any] = None
provenance_metadata: Optional[Any] = None
build_number: Optional[str] = None
run_seq: Optional[int] = None
suite_name: Optional[str] = None
attempt: Optional[int] = None
max_attempts: Optional[int] = None
next_retry_at: Optional[Any] = None
cancel_requested: Optional[bool] = None
rerun_of: Optional[uuid.UUID] = None
review_summary: Optional[AgentPipelineReviewSummary] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — ReviewBlock

[backend/app/models/schemas.py:1685](../../backend/app/models/schemas.py#L1685)

Bases: `BaseModel`.

Human-review status of an AI report (architecture section 8.2, E8.3).

Carries WHETHER and WHEN a report was reviewed, never WHO: reviewer identity
stays in-app, out of API and export payloads.

```python
state: Literal['pending_review', 'accepted', 'rejected', 'superseded', 'not_applicable'] = Field(description='pending_review: a draft until a human accepts it (also used when no review was ever recorded). not_applicable: the content was not AI-generated.')
message: str
review_id: Optional[str] = None
reviewed_at: Optional[str] = None
```

## backend/app/models/schemas.py — AgentRunSummaryResponse

[backend/app/models/schemas.py:1703](../../backend/app/models/schemas.py#L1703)

Bases: `BaseModel`.



```python
test_run_id: str
project_id: Optional[str] = None
build_number: Optional[str] = None
executive_summary: str
markdown_report: str
executive_panel: Optional[dict] = None
anomaly_count: int = 0
is_regression: bool = False
analysis_count: int = 0
generated_at: Optional[Any] = None
requires_human_review: bool = Field(default=True, description='True for AI-generated content: a draft until review.state == accepted.')
review: Optional[ReviewBlock] = None
ai_disclaimer: Optional[str] = None
ai_disclaimer_version: Optional[str] = None
```

## backend/app/models/schemas.py — PipelineTimelineEventResponse

[backend/app/models/schemas.py:1726](../../backend/app/models/schemas.py#L1726)

Bases: `BaseModel`.



```python
event_type: str
stage_name: Optional[str] = None
test_case_id: Optional[str] = None
timestamp: datetime
detail: Dict[str, Any] = Field(default_factory=dict)
```

## backend/app/models/schemas.py — PipelineTimelineSummary

[backend/app/models/schemas.py:1734](../../backend/app/models/schemas.py#L1734)

Bases: `BaseModel`.



```python
total_stages: int = 0
completed_stages: int = 0
running_stages: int = 0
failed_stages: int = 0
skipped_stages: int = 0
pending_stages: int = 0
progress_percent: float = 0.0
```

## backend/app/models/schemas.py — PipelineReplayAuditGaps

[backend/app/models/schemas.py:1744](../../backend/app/models/schemas.py#L1744)

Bases: `BaseModel`.



```python
missing_start_events: List[str] = Field(default_factory=list)
missing_terminal_events: List[str] = Field(default_factory=list)
missing_replay_checksums: List[str] = Field(default_factory=list)
missing_checkpoints: List[str] = Field(default_factory=list)
missing_final_state_checksum: bool = False
```

## backend/app/models/schemas.py — PipelineReplayIntegritySummary

[backend/app/models/schemas.py:1752](../../backend/app/models/schemas.py#L1752)

Bases: `BaseModel`.



```python
replayable: bool = False
audit_gaps: PipelineReplayAuditGaps = Field(default_factory=PipelineReplayAuditGaps)
```

## backend/app/models/schemas.py — PipelineTimelineResponse

[backend/app/models/schemas.py:1757](../../backend/app/models/schemas.py#L1757)

Bases: `BaseModel`.



```python
schema_version: int = 2
pipeline_run_id: uuid.UUID
workflow_type: str
status: str
started_at: Optional[Any] = None
completed_at: Optional[Any] = None
duration_seconds: Optional[float] = None
cost_summary: Dict[str, Any] = Field(default_factory=dict)
agent_observability: Dict[str, Any] = Field(default_factory=dict)
alerts: List[Dict[str, Any]] = Field(default_factory=list)
stages: List[Dict[str, Any]] = Field(default_factory=list)
events: List[PipelineTimelineEventResponse] = Field(default_factory=list)
summary: PipelineTimelineSummary = Field(default_factory=PipelineTimelineSummary)
replay_integrity: PipelineReplayIntegritySummary = Field(default_factory=PipelineReplayIntegritySummary)
```

## backend/app/models/schemas.py — PipelineReplayEventResponse

[backend/app/models/schemas.py:1774](../../backend/app/models/schemas.py#L1774)

Bases: `BaseModel`.



```python
event_type: str
stage_name: Optional[str] = None
test_case_id: Optional[str] = None
timestamp: Optional[Any] = None
detail: Dict[str, Any] = Field(default_factory=dict)
source: Optional[str] = None
```

## backend/app/models/schemas.py — PipelineReplayStageResponse

[backend/app/models/schemas.py:1783](../../backend/app/models/schemas.py#L1783)

Bases: `BaseModel`.



```python
stage_name: str
status: str
started_at: Optional[Any] = None
completed_at: Optional[Any] = None
input_checksum_sha256: Optional[str] = None
output_checksum_sha256: Optional[str] = None
runtime_versions: Dict[str, str] = Field(default_factory=dict)
checkpoint_available: bool = False
restored_from_checkpoint: bool = False
decision_count: int = 0
```

## backend/app/models/schemas.py — MemoryReference

[backend/app/models/schemas.py:1796](../../backend/app/models/schemas.py#L1796)

Bases: `BaseModel`.

Canonical pointer from generated output back to an auditable memory row.

```python
memory_entry_id: uuid.UUID
entity_type: str
entity_id: str
source_snapshot_id: Optional[uuid.UUID] = None
payload_sha256: str
retrieval_audit: Optional[Dict[str, Any]] = None
retrieval_audit_sha256: Optional[str] = None
memory_reference_id: Optional[str] = None
evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
```

## backend/app/models/schemas.py — PipelineReplayResponse

[backend/app/models/schemas.py:1809](../../backend/app/models/schemas.py#L1809)

Bases: `BaseModel`.



```python
schema_version: int = 1
pipeline_run_id: uuid.UUID
test_run_id: uuid.UUID
workflow_type: str
status: str
started_at: Optional[Any] = None
completed_at: Optional[Any] = None
analysis_mode_requested: Optional[str] = None
analysis_mode_resolved: Optional[str] = None
analysis_mode_resolution: Dict[str, Any] = Field(default_factory=dict)
final_state_checksum_sha256: Optional[str] = None
runtime_versions: Dict[str, str] = Field(default_factory=dict)
workflow_plan: Dict[str, Any] = Field(default_factory=dict)
workflow_verification: Dict[str, Any] = Field(default_factory=dict)
route_decisions: List[Dict[str, Any]] = Field(default_factory=list)
stage_replay: List[PipelineReplayStageResponse] = Field(default_factory=list)
memory_references: List[MemoryReference] = Field(default_factory=list)
events: List[PipelineReplayEventResponse] = Field(default_factory=list)
event_counts: Dict[str, int] = Field(default_factory=dict)
replayable: bool = False
audit_gaps: PipelineReplayAuditGaps = Field(default_factory=PipelineReplayAuditGaps)
```

## backend/app/models/schemas.py — PipelineEventLogHealthResponse

[backend/app/models/schemas.py:1833](../../backend/app/models/schemas.py#L1833)

Bases: `BaseModel`.



```python
status: str = 'healthy'
write_failure_count: int = 0
dead_letter_count: int = 0
dead_letter_limit: int = 0
recent_dead_letters: List[Dict[str, Any]] = Field(default_factory=list)
```

## backend/app/models/schemas.py — Provenance

[backend/app/models/schemas.py:1843](../../backend/app/models/schemas.py#L1843)

Bases: `BaseModel`.



```python
schema_version: int = 1
fallback_used: bool = False
generated_by: str = 'ai_pipeline'
tools_used_count: int = 0
generated_at: Optional[datetime] = None
confidence: Optional[int] = None
confidence_reason: Optional[str] = None
evidence_count: int = 0
sources_used: List[str] = []
deterministic_checks_used: List[str] = []
```

## backend/app/models/schemas.py — EvidenceArtifactResponse

[backend/app/models/schemas.py:1857](../../backend/app/models/schemas.py#L1857)

Bases: `BaseModel`.

Evidence item returned in API responses.

```python
id: str
artifact_type: str
source_system: str
summary_excerpt: Optional[str] = None
relevance_score: Optional[float] = None
cluster_id: Optional[str] = None
test_case_id: Optional[str] = None
producer_pipeline_run_id: Optional[str] = None
content_sha256: Optional[str] = None
sensitivity: Optional[str] = None
freshness: Optional[str] = None
integrity_status: Optional[str] = None
```

## backend/app/models/schemas.py — DimensionScore

[backend/app/models/schemas.py:1873](../../backend/app/models/schemas.py#L1873)

Bases: `BaseModel`.



```python
name: str
label: str
score: float
weight: float
contribution: float
```

## backend/app/models/schemas.py — ClusterInsightResponse

[backend/app/models/schemas.py:1881](../../backend/app/models/schemas.py#L1881)

Bases: `BaseModel`.



```python
id: str
cluster_id: str
label: str
size: int
representative_error: Optional[str] = None
member_test_ids: List[str] = []
cohesion_score: Optional[float] = None
criticality_level: Optional[str] = None
dimension_scores: List[DimensionScore] = []
```

## backend/app/models/schemas.py — CommitRange

[backend/app/models/schemas.py:1893](../../backend/app/models/schemas.py#L1893)

Bases: `BaseModel`.



```python
from_commit: Optional[str] = None
to_commit: Optional[str] = None
same_commit: bool = False
```

## backend/app/models/schemas.py — ConfigDriftEntry

[backend/app/models/schemas.py:1899](../../backend/app/models/schemas.py#L1899)

Bases: `BaseModel`.



```python
field: str
old_value: Optional[str] = None
new_value: Optional[str] = None
```

## backend/app/models/schemas.py — BaselineDiff

[backend/app/models/schemas.py:1905](../../backend/app/models/schemas.py#L1905)

Bases: `BaseModel`.



```python
baseline_run_id: Optional[str] = None
baseline_build_number: Optional[str] = None
pass_rate_delta: Optional[float] = None
new_failures: List[str] = []
resolved_failures: List[str] = []
regression_classification: str = 'unclassified'
regression_clusters: List[dict] = []
classified_new_failures: List[dict] = []
suites_impacted_delta: int = 0
current_suite_count: int = 0
baseline_suite_count: int = 0
commit_range: Optional[dict] = None
config_drift: List[dict] = []
selection_reason: str = 'latest_passing'
```

## backend/app/models/schemas.py — DefectCandidate

[backend/app/models/schemas.py:1924](../../backend/app/models/schemas.py#L1924)

Bases: `BaseModel`.



```python
cluster_id: str
label: str
severity_hint: str
failure_category: str
confidence: int
recommended_actions: List[str] = []
```

## backend/app/models/schemas.py — DefectCandidateResponse

[backend/app/models/schemas.py:1933](../../backend/app/models/schemas.py#L1933)

Bases: `BaseModel`.

Pre-assembled defect candidate for a cluster (editable before submission).

```python
cluster_id: str
run_id: str
title: str
description: str
severity: str
component: str
owner_team: str
labels: List[str] = []
duplicate_hint: str = ''
duplicate_detected: bool = False
duplicate_defect_id: Optional[str] = None
criticality_scores: dict = {}
composite_score: float = 0.0
evidence_bundle: dict = {}
failure_category: str = 'UNKNOWN'
member_count: int = 0
```

## backend/app/models/schemas.py — DefectPromotionRequest

[backend/app/models/schemas.py:1953](../../backend/app/models/schemas.py#L1953)

Bases: `BaseModel`.

User-editable fields submitted from the promotion modal.

```python
title: str
severity: str = 'HIGH'
component: str = ''
owner_team: str = ''
labels: List[str] = []
description: str = ''
project_key: Optional[str] = None
```

## backend/app/models/schemas.py — DefectPromotionResponse

[backend/app/models/schemas.py:1964](../../backend/app/models/schemas.py#L1964)

Bases: `BaseModel`.



```python
defect_id: str
cluster_id: str
severity: str
title: str
owner_team: Optional[str] = None
component: Optional[str] = None
duplicate_detected: bool = False
duplicate_defect_id: Optional[str] = None
jira_ticket: Optional[dict] = None
jira_url: Optional[str] = None
approval_status: Optional[str] = None
requires_approval: Optional[bool] = None
policy_reasons: Optional[List[str]] = None
```

## backend/app/models/schemas.py — DefectApprovalRequest

[backend/app/models/schemas.py:1981](../../backend/app/models/schemas.py#L1981)

Bases: `BaseModel`.



```python
action: str
reason: Optional[str] = None
```

## backend/app/models/schemas.py — DefectApprovalResponse

[backend/app/models/schemas.py:1986](../../backend/app/models/schemas.py#L1986)

Bases: `BaseModel`.



```python
defect_id: str
approval_status: str
message: str
jira_ticket: Optional[dict] = None
jira_url: Optional[str] = None
```

## backend/app/models/schemas.py — JiraDefectCreateRequest

[backend/app/models/schemas.py:1999](../../backend/app/models/schemas.py#L1999)

Bases: `BaseModel`.

Body for POST /projects/{project_id}/defects/jira.

Exactly one of ``fingerprint`` / ``cluster_id`` identifies the failure.
``target`` picks the delivery: "jira" calls the Jira REST API, "webhook"
emits the ``defect.create_requested`` outbound-webhook event instead
(US-6.3).

```python
fingerprint: Optional[str] = Field(None, min_length=1, max_length=64)
cluster_id: Optional[str] = Field(None, min_length=1, max_length=255)
issue_type: str = Field('Bug', max_length=100)
jira_project_key: Optional[str] = Field(None, max_length=50)
assignee: Optional[str] = Field(None, max_length=128)
extra_comment: Optional[str] = Field(None, max_length=2000)
target: str = Field('jira', pattern='^(jira|webhook)$')
confirm_not_filed: bool = False
```

## backend/app/models/schemas.py — JiraDefectCreateResponse

[backend/app/models/schemas.py:2020](../../backend/app/models/schemas.py#L2020)

Bases: `BaseModel`.



```python
target: str
deduplicated: bool = False
defect_id: Optional[str] = None
jira_key: Optional[str] = None
jira_url: Optional[str] = None
external_status: Optional[str] = None
recurrence_count: int = 0
recurrence_comment_posted: bool = False
subscriptions_notified: Optional[int] = None
message: str = ''
```

## backend/app/models/schemas.py — JiraDefectOccurrences

[backend/app/models/schemas.py:2033](../../backend/app/models/schemas.py#L2033)

Bases: `BaseModel`.



```python
first_seen: Optional[datetime] = None
last_seen: Optional[datetime] = None
failing_runs: int = 0
```

## backend/app/models/schemas.py — JiraDefectContext

[backend/app/models/schemas.py:2039](../../backend/app/models/schemas.py#L2039)

Bases: `BaseModel`.



```python
branch: Optional[str] = None
build_number: Optional[str] = None
ci_run_url: Optional[str] = None
```

## backend/app/models/schemas.py — JiraDefectExistingLink

[backend/app/models/schemas.py:2045](../../backend/app/models/schemas.py#L2045)

Bases: `BaseModel`.



```python
defect_id: str
jira_key: Optional[str] = None
jira_url: Optional[str] = None
external_status: Optional[str] = None
```

## backend/app/models/schemas.py — JiraDefectPreviewResponse

[backend/app/models/schemas.py:2052](../../backend/app/models/schemas.py#L2052)

Bases: `BaseModel`.

Pre-filled payload shown (read-only) in the create dialog.

```python
signature: str
summary: str
description: str
test_name: Optional[str] = None
suite_name: Optional[str] = None
cluster_id: Optional[str] = None
error_message: Optional[str] = None
occurrences: JiraDefectOccurrences
context: JiraDefectContext
ai_analysis: Optional[dict] = None
deep_link: str
latest_run_id: Optional[str] = None
existing_defect: Optional[JiraDefectExistingLink] = None
```

## backend/app/models/schemas.py — JiraProjectOption

[backend/app/models/schemas.py:2069](../../backend/app/models/schemas.py#L2069)

Bases: `BaseModel`.



```python
key: str
name: Optional[str] = None
```

## backend/app/models/schemas.py — JiraDefectMetadataResponse

[backend/app/models/schemas.py:2074](../../backend/app/models/schemas.py#L2074)

Bases: `BaseModel`.

Dialog-picker metadata. ``available=false`` + ``reason`` instead of
an HTTP error when Jira is offline-gated/unconfigured/unreachable —
the UI uses it to disable the action with a tooltip.

```python
available: bool
reason: Optional[str] = None
projects: List[JiraProjectOption] = []
issue_types: List[str] = []
default_project_key: Optional[str] = None
webhook_available: bool = False
```

## backend/app/models/schemas.py — SummaryModes

[backend/app/models/schemas.py:2086](../../backend/app/models/schemas.py#L2086)

Bases: `BaseModel`.



```python
available: List[str]
default: str = 'executive'
```

## backend/app/models/schemas.py — Citation

[backend/app/models/schemas.py:2091](../../backend/app/models/schemas.py#L2091)

Bases: `BaseModel`.



```python
source: str
excerpt: str
test_id: str = ''
```

## backend/app/models/schemas.py — RunModeSummaryResponse

[backend/app/models/schemas.py:2097](../../backend/app/models/schemas.py#L2097)

Bases: `BaseModel`.

Response for GET /runs/{run_id}/summary?mode=developer|manager

```python
test_run_id: str
mode: str
executive_summary: Optional[str] = None
markdown_report: str
layer1_executive: Optional[str] = None
layer2_incident: Optional[Any] = None
layer3_evidence: Optional[Any] = None
layer4_action_plan: Optional[Any] = None
executive_panel: Optional[dict] = None
fallback_used: bool = False
generated_at: Optional[Any] = None
citations: List[Citation] = []
similar_failures: List[dict] = []
provenance: Optional[dict] = None
```

## backend/app/models/schemas.py — ChatSessionCreate

[backend/app/models/schemas.py:2117](../../backend/app/models/schemas.py#L2117)

Bases: `BaseModel`.



```python
project_id: Optional[uuid.UUID] = None
active_test_run_id: Optional[uuid.UUID] = None
active_report_id: Optional[str] = Field(None, min_length=1, max_length=64)
active_report_version: Optional[int] = Field(None, ge=1)
title: Optional[str] = Field(None, max_length=500)
```

## backend/app/models/schemas.py — ChatSessionResponse

[backend/app/models/schemas.py:2125](../../backend/app/models/schemas.py#L2125)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: Optional[uuid.UUID] = None
active_test_run_id: Optional[uuid.UUID] = None
active_report_id: Optional[str] = None
active_report_version: Optional[int] = None
title: Optional[str] = None
created_at: Any
updated_at: Any
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — ChatMessageResponse

[backend/app/models/schemas.py:2138](../../backend/app/models/schemas.py#L2138)

Bases: `BaseModel`.



```python
id: uuid.UUID
session_id: uuid.UUID
role: str
content: str
sources: Optional[List[Any]] = None
created_at: Any
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SendMessageRequest

[backend/app/models/schemas.py:2149](../../backend/app/models/schemas.py#L2149)

Bases: `BaseModel`.



```python
message: str = Field(..., min_length=1, max_length=4000)
project_id: Optional[str] = None
```

## backend/app/models/schemas.py — SendMessageResponse

[backend/app/models/schemas.py:2154](../../backend/app/models/schemas.py#L2154)

Bases: `BaseModel`.



```python
session_id: uuid.UUID
reply: str
sources: List[Any] = []
tool_trace: List[Any] = []
suggested_actions: List[Any] = []
```

## backend/app/models/schemas.py — TestCaseStepSchema

[backend/app/models/schemas.py:2167](../../backend/app/models/schemas.py#L2167)

Bases: `BaseModel`.

Authored step shape; expected outcomes are optional by design.

```python
step_number: int = Field(..., ge=1)
action: str = Field(..., min_length=1, max_length=MAX_LONG_TEXT)
expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
```

## backend/app/models/schemas.py — TestCaseParameterSchema

[backend/app/models/schemas.py:2174](../../backend/app/models/schemas.py#L2174)

Bases: `BaseModel`.

Optional authored input metadata; sensitive values are never required.

```python
name: str = Field(..., min_length=1, max_length=255)
value: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
mode: Optional[str] = Field(None, max_length=30)
masked: bool = False
```

## backend/app/models/schemas.py — ManagedTestCaseCreate

[backend/app/models/schemas.py:2182](../../backend/app/models/schemas.py#L2182)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
title: str = Field(..., min_length=3, max_length=500)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
steps: Optional[List[TestCaseStepSchema]] = None
parameters: Optional[List[TestCaseParameterSchema]] = None
expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_data: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_type: str = 'functional'
priority: str = 'medium'
severity: str = 'major'
feature_area: Optional[str] = Field(None, max_length=500)
suite_name: Optional[str] = Field(None, max_length=500)
tags: Optional[List[str]] = None
estimated_duration_minutes: Optional[int] = None
is_automated: bool = False
automation_status: str = 'not_automated'
```

## backend/app/models/schemas.py — ManagedTestCaseUpdate

[backend/app/models/schemas.py:2207](../../backend/app/models/schemas.py#L2207)

Bases: `BaseModel`.



```python
expected_version: int = Field(..., ge=1)
title: Optional[str] = Field(None, min_length=3, max_length=500)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
steps: Optional[List[TestCaseStepSchema]] = None
parameters: Optional[List[TestCaseParameterSchema]] = None
expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_data: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_type: Optional[str] = None
priority: Optional[str] = None
severity: Optional[str] = None
feature_area: Optional[str] = Field(None, max_length=500)
suite_name: Optional[str] = Field(None, max_length=500)
tags: Optional[List[str]] = None
estimated_duration_minutes: Optional[int] = None
is_automated: Optional[bool] = None
automation_status: Optional[str] = None
change_summary: Optional[str] = Field(None, max_length=500)
```

## backend/app/models/schemas.py — ManagedTestCaseResponse

[backend/app/models/schemas.py:2229](../../backend/app/models/schemas.py#L2229)

Bases: `BaseModel`.



```python
source: str = 'managed'
owner: Optional[str] = None
latest_run_id: Optional[uuid.UUID] = None
latest_test_case_id: Optional[uuid.UUID] = None
canonical_test_case_id: Optional[uuid.UUID] = None
id: uuid.UUID
project_id: uuid.UUID
title: str
description: Optional[str] = None
objective: Optional[str] = None
preconditions: Optional[str] = None
steps: Optional[List[TestCaseStepSchema]] = None
parameters: Optional[List[TestCaseParameterSchema]] = None
expected_result: Optional[str] = None
test_data: Optional[str] = None
test_type: str
priority: str
severity: str
feature_area: Optional[str] = None
suite_name: Optional[str] = None
test_suite_id: Optional[uuid.UUID] = None
tags: Optional[List[Any]] = None
status: str
version: int
allowed_actions: List[str] = Field(default_factory=list)
lifecycle_state_changed_at: Optional[datetime] = None
approved_at: Optional[datetime] = None
approved_by_id: Optional[uuid.UUID] = None
needs_update_reason: Optional[str] = None
deprecation_reason: Optional[str] = None
deprecated_at: Optional[datetime] = None
deprecated_by_id: Optional[uuid.UUID] = None
archived_at: Optional[datetime] = None
archived_by_id: Optional[uuid.UUID] = None
author_id: Optional[uuid.UUID] = None
assignee_id: Optional[uuid.UUID] = None
reviewer_id: Optional[uuid.UUID] = None
is_automated: bool
automation_status: str
test_fingerprint: Optional[str] = None
ai_generated: bool
ai_quality_score: Optional[int] = None
ai_review_notes: Optional[dict] = None
estimated_duration_minutes: Optional[int] = None
last_executed_at: Optional[datetime] = None
last_execution_status: Optional[str] = None
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — ManagedTestCaseListResponse

[backend/app/models/schemas.py:2294](../../backend/app/models/schemas.py#L2294)

Bases: `BaseModel`.



```python
items: List[ManagedTestCaseResponse]
total: int
page: int
size: int
pages: int
```

## backend/app/models/schemas.py — TestCaseVersionResponse

[backend/app/models/schemas.py:2302](../../backend/app/models/schemas.py#L2302)

Bases: `BaseModel`.



```python
id: uuid.UUID
test_case_id: uuid.UUID
version: int
title: str
description: Optional[str] = None
objective: Optional[str] = None
preconditions: Optional[str] = None
steps: Optional[List[TestCaseStepSchema]] = None
parameters: Optional[List[TestCaseParameterSchema]] = None
expected_result: Optional[str] = None
test_data: Optional[str] = None
test_type: Optional[str] = None
priority: Optional[str] = None
severity: Optional[str] = None
feature_area: Optional[str] = None
suite_name: Optional[str] = None
test_suite_id: Optional[uuid.UUID] = None
tags: Optional[List[Any]] = None
estimated_duration_minutes: Optional[int] = None
is_automated: Optional[bool] = None
automation_status: Optional[str] = None
test_fingerprint: Optional[str] = None
status: str
changed_by_id: Optional[uuid.UUID] = None
change_summary: Optional[str] = None
change_type: str
changed_fields: Optional[List[str]] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestCaseReviewResponse

[backend/app/models/schemas.py:2335](../../backend/app/models/schemas.py#L2335)

Bases: `BaseModel`.



```python
id: uuid.UUID
test_case_id: uuid.UUID
reviewer_id: Optional[uuid.UUID] = None
requested_by_id: Optional[uuid.UUID] = None
status: str
ai_review_completed: bool
ai_quality_score: Optional[int] = None
ai_review_notes: Optional[dict] = None
ai_reviewed_at: Optional[datetime] = None
human_notes: Optional[str] = None
reviewed_at: Optional[datetime] = None
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — ReviewActionRequest

[backend/app/models/schemas.py:2353](../../backend/app/models/schemas.py#L2353)

Bases: `BaseModel`.



```python
action: Literal['approve', 'reject', 'request_changes']
notes: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
```

## backend/app/models/schemas.py — TestCaseTransitionRequest

[backend/app/models/schemas.py:2358](../../backend/app/models/schemas.py#L2358)

Bases: `BaseModel`.



```python
action: Literal['request_review', 'claim_review', 'withdraw_review', 'unclaim', 'approve', 'reject', 'request_changes', 'activate', 'flag_stale', 'revise', 'deprecate', 'reinstate', 'archive']
reason: Optional[str] = Field(None, max_length=500)
notes: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
expected_version: int = Field(..., ge=1)
```

## backend/app/models/schemas.py — TestCaseDeprecateRequest

[backend/app/models/schemas.py:2379](../../backend/app/models/schemas.py#L2379)

Bases: `BaseModel`.



```python
reason: str = Field(..., min_length=1, max_length=500)
```

## backend/app/models/schemas.py — AllowedTransitionResponse

[backend/app/models/schemas.py:2383](../../backend/app/models/schemas.py#L2383)

Bases: `BaseModel`.



```python
action: str
allowed: bool
blocked_reason: Optional[str] = None
```

## backend/app/models/schemas.py — TestCaseCommentCreate

[backend/app/models/schemas.py:2389](../../backend/app/models/schemas.py#L2389)

Bases: `BaseModel`.



```python
content: str = Field(..., min_length=1, max_length=MAX_LONG_TEXT)
comment_type: str = 'general'
parent_id: Optional[uuid.UUID] = None
step_number: Optional[int] = None
```

## backend/app/models/schemas.py — TestCaseCommentResponse

[backend/app/models/schemas.py:2396](../../backend/app/models/schemas.py#L2396)

Bases: `BaseModel`.



```python
id: uuid.UUID
test_case_id: uuid.UUID
author_id: Optional[uuid.UUID] = None
content: str
comment_type: str
parent_id: Optional[uuid.UUID] = None
step_number: Optional[int] = None
is_resolved: bool
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestPlanCreate

[backend/app/models/schemas.py:2411](../../backend/app/models/schemas.py#L2411)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
name: str = Field(..., min_length=3, max_length=500)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
planned_start_date: Optional[datetime] = None
planned_end_date: Optional[datetime] = None
assigned_to_id: Optional[uuid.UUID] = None
tags: Optional[List[str]] = None
```

## backend/app/models/schemas.py — TestPlanUpdate

[backend/app/models/schemas.py:2422](../../backend/app/models/schemas.py#L2422)

Bases: `BaseModel`.



```python
name: Optional[str] = Field(None, min_length=3, max_length=500)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
status: Optional[str] = None
planned_start_date: Optional[datetime] = None
planned_end_date: Optional[datetime] = None
actual_start_date: Optional[datetime] = None
actual_end_date: Optional[datetime] = None
assigned_to_id: Optional[uuid.UUID] = None
tags: Optional[List[str]] = None
```

## backend/app/models/schemas.py — TestPlanResponse

[backend/app/models/schemas.py:2435](../../backend/app/models/schemas.py#L2435)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
name: str
description: Optional[str] = None
objective: Optional[str] = None
status: str
planned_start_date: Optional[datetime] = None
planned_end_date: Optional[datetime] = None
actual_start_date: Optional[datetime] = None
actual_end_date: Optional[datetime] = None
created_by_id: Optional[uuid.UUID] = None
assigned_to_id: Optional[uuid.UUID] = None
ai_generated: bool
total_cases: int
executed_cases: int
passed_cases: int
failed_cases: int
blocked_cases: int
tags: Optional[List[str]] = None
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestPlanListResponse

[backend/app/models/schemas.py:2461](../../backend/app/models/schemas.py#L2461)

Bases: `BaseModel`.



```python
items: List[TestPlanResponse]
total: int
page: int
size: int
pages: int
```

## backend/app/models/schemas.py — TestPlanItemCreate

[backend/app/models/schemas.py:2469](../../backend/app/models/schemas.py#L2469)

Bases: `BaseModel`.



```python
test_case_id: uuid.UUID
order_index: int = 0
priority_override: Optional[str] = None
```

## backend/app/models/schemas.py — TestPlanItemResponse

[backend/app/models/schemas.py:2475](../../backend/app/models/schemas.py#L2475)

Bases: `BaseModel`.



```python
id: uuid.UUID
plan_id: uuid.UUID
test_case_id: uuid.UUID
order_index: int
priority_override: Optional[str] = None
execution_status: str
executed_by_id: Optional[uuid.UUID] = None
executed_at: Optional[datetime] = None
execution_notes: Optional[str] = None
actual_duration_minutes: Optional[int] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — ExecuteTestPlanItemRequest

[backend/app/models/schemas.py:2491](../../backend/app/models/schemas.py#L2491)

Bases: `BaseModel`.



```python
execution_status: Literal['passed', 'failed', 'blocked', 'skipped']
execution_notes: Optional[str] = None
actual_duration_minutes: Optional[int] = None
```

## backend/app/models/schemas.py — TestStrategyCreate

[backend/app/models/schemas.py:2497](../../backend/app/models/schemas.py#L2497)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
name: str = Field(..., min_length=3, max_length=500)
version_label: str = Field('v1.0', max_length=50)
objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
scope: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_approach: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
```

## backend/app/models/schemas.py — TestStrategyUpdate

[backend/app/models/schemas.py:2506](../../backend/app/models/schemas.py#L2506)

Bases: `BaseModel`.



```python
name: Optional[str] = Field(None, max_length=500)
version_label: Optional[str] = Field(None, max_length=50)
status: Optional[str] = None
objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
scope: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
out_of_scope: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_approach: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
risk_assessment: Optional[List[dict]] = None
test_types: Optional[List[dict]] = None
entry_criteria: Optional[List[str]] = None
exit_criteria: Optional[List[str]] = None
environments: Optional[List[dict]] = None
automation_approach: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
defect_management: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
```

## backend/app/models/schemas.py — TestStrategyResponse

[backend/app/models/schemas.py:2523](../../backend/app/models/schemas.py#L2523)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
name: str
version_label: str
status: str
objective: Optional[str] = None
scope: Optional[str] = None
out_of_scope: Optional[str] = None
test_approach: Optional[str] = None
risk_assessment: Optional[List[Any]] = None
test_types: Optional[List[Any]] = None
entry_criteria: Optional[List[Any]] = None
exit_criteria: Optional[List[Any]] = None
environments: Optional[List[Any]] = None
automation_approach: Optional[str] = None
defect_management: Optional[str] = None
ai_generated: bool
ai_model_used: Optional[str] = None
created_by_id: Optional[uuid.UUID] = None
approved_by_id: Optional[uuid.UUID] = None
approved_at: Optional[datetime] = None
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AuditLogResponse

[backend/app/models/schemas.py:2551](../../backend/app/models/schemas.py#L2551)

Bases: `BaseModel`.



```python
id: uuid.UUID
entity_type: str
entity_id: uuid.UUID
project_id: Optional[uuid.UUID] = None
action: str
actor_id: Optional[uuid.UUID] = None
actor_name: Optional[str] = None
old_values: Optional[dict] = None
new_values: Optional[dict] = None
details: Optional[str] = None
reason: Optional[str] = None
policy_snapshot: Optional[dict] = None
transition_from: Optional[str] = None
transition_to: Optional[str] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AuditLogListResponse

[backend/app/models/schemas.py:2571](../../backend/app/models/schemas.py#L2571)

Bases: `BaseModel`.



```python
items: List[AuditLogResponse]
total: int
page: int
size: int
pages: int
```

## backend/app/models/schemas.py — SuiteMembershipResponse

[backend/app/models/schemas.py:2582](../../backend/app/models/schemas.py#L2582)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
suite_name: str
test_fingerprint: str
test_name: str
class_name: Optional[str] = None
managed_test_case_id: Optional[uuid.UUID] = None
source: str
status: str
last_seen_run_id: Optional[uuid.UUID] = None
first_seen_run_id: Optional[uuid.UUID] = None
deleted_at_run_id: Optional[uuid.UUID] = None
review_tag: Optional[str] = None
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SuiteMembershipEventResponse

[backend/app/models/schemas.py:2601](../../backend/app/models/schemas.py#L2601)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
suite_name: str
test_fingerprint: str
test_name: str
event_type: str
run_id: Optional[uuid.UUID] = None
old_values: Optional[dict] = None
new_values: Optional[dict] = None
details: Optional[str] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SuiteSyncSummary

[backend/app/models/schemas.py:2616](../../backend/app/models/schemas.py#L2616)

Bases: `BaseModel`.



```python
suite_name: str
run_id: uuid.UUID
added_count: int = 0
deleted_count: int = 0
modified_count: int = 0
restored_count: int = 0
unchanged_count: int = 0
```

## backend/app/models/schemas.py — TestSuiteCreate

[backend/app/models/schemas.py:2629](../../backend/app/models/schemas.py#L2629)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
name: str = Field(..., min_length=1, max_length=500)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
tags: Optional[List[str]] = None
owner_user_id: Optional[uuid.UUID] = None
```

## backend/app/models/schemas.py — TestSuiteUpdate

[backend/app/models/schemas.py:2642](../../backend/app/models/schemas.py#L2642)

Bases: `BaseModel`.

None = keep existing value.

```python
name: Optional[str] = Field(None, min_length=1, max_length=500)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
tags: Optional[List[str]] = None
```

## backend/app/models/schemas.py — TestSuiteResponse

[backend/app/models/schemas.py:2649](../../backend/app/models/schemas.py#L2649)

Bases: `TimestampMixin`.



```python
id: uuid.UUID
project_id: uuid.UUID
name: str
description: Optional[str] = None
is_default: bool
tags: Optional[List[str]] = None
test_case_count: Optional[int] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — TestSuiteListResponse

[backend/app/models/schemas.py:2660](../../backend/app/models/schemas.py#L2660)

Bases: `BaseModel`.



```python
items: List[TestSuiteResponse]
total: int
```

## backend/app/models/schemas.py — CanonicalTestCaseResponse

[backend/app/models/schemas.py:2665](../../backend/app/models/schemas.py#L2665)

Bases: `TimestampMixin`.



```python
id: uuid.UUID
project_id: uuid.UUID
test_suite_id: uuid.UUID
test_suite_name: Optional[str] = None
test_fingerprint: str
test_name: str
class_name: Optional[str] = None
status: str
source: str
first_seen_run_id: Optional[uuid.UUID] = None
last_seen_run_id: Optional[uuid.UUID] = None
last_seen_test_case_id: Optional[uuid.UUID] = None
deleted_at_run_id: Optional[uuid.UUID] = None
managed_test_case_id: Optional[uuid.UUID] = None
retirement_confirmed_at: Optional[datetime] = None
retirement_confirmed_by_id: Optional[uuid.UUID] = None
retirement_reason: Optional[str] = None
deleted_observed_at: Optional[datetime] = None
review_tag: Optional[str] = None
tags: Optional[List[str]] = None
run_count: Optional[int] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — CanonicalTestCaseListResponse

[backend/app/models/schemas.py:2694](../../backend/app/models/schemas.py#L2694)

Bases: `BaseModel`.



```python
items: List[CanonicalTestCaseResponse]
total: int
page: Optional[int] = None
size: Optional[int] = None
```

## backend/app/models/schemas.py — CanonicalPromotionResponse

[backend/app/models/schemas.py:2702](../../backend/app/models/schemas.py#L2702)

Bases: `BaseModel`.



```python
canonical: CanonicalTestCaseResponse
managed_case: ManagedTestCaseResponse
```

## backend/app/models/schemas.py — CanonicalManagedUnlinkRequest

[backend/app/models/schemas.py:2707](../../backend/app/models/schemas.py#L2707)

Bases: `BaseModel`.



```python
reason: str = Field(..., min_length=1, max_length=500)
```

- Validator/serializer `normalize_reason`: [backend/app/models/schemas.py:2712](../../backend/app/models/schemas.py#L2712). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — CanonicalRetirementConfirmRequest

[backend/app/models/schemas.py:2719](../../backend/app/models/schemas.py#L2719)

Bases: `BaseModel`.



```python
reason: str = Field(..., min_length=1, max_length=500)
```

- Validator/serializer `normalize_reason`: [backend/app/models/schemas.py:2724](../../backend/app/models/schemas.py#L2724). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — EvidenceGapItem

[backend/app/models/schemas.py:2731](../../backend/app/models/schemas.py#L2731)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
title: str
status: str
canonical_test_case_id: Optional[uuid.UUID] = None
canonical_status: Optional[str] = None
deleted_observed_at: Optional[datetime] = None
last_executed_at: Optional[datetime] = None
```

## backend/app/models/schemas.py — EvidenceGapListResponse

[backend/app/models/schemas.py:2742](../../backend/app/models/schemas.py#L2742)

Bases: `BaseModel`.



```python
items: List[EvidenceGapItem]
total: int
```

## backend/app/models/schemas.py — CanonicalTestCaseLinkRequest

[backend/app/models/schemas.py:2747](../../backend/app/models/schemas.py#L2747)

Bases: `BaseModel`.

Move a canonical test case to a different suite within the same project.

```python
test_suite_id: uuid.UUID
```

## backend/app/models/schemas.py — CanonicalTestCaseBulkLinkRequest

[backend/app/models/schemas.py:2752](../../backend/app/models/schemas.py#L2752)

Bases: `BaseModel`.

Move multiple canonical test cases to a different suite within the
same project. Pair with ``POST /api/v1/canonical-test-cases/bulk-link``.

```python
target_test_suite_id: uuid.UUID
canonical_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=200)
```

## backend/app/models/schemas.py — CanonicalTestCaseBulkLinkResponse

[backend/app/models/schemas.py:2763](../../backend/app/models/schemas.py#L2763)

Bases: `BaseModel`.

Outcome of a bulk-link request. ``moved`` and
``skipped_already_in_target`` always sum to the number of ids that
actually resolved to a canonical row; ``missing_ids`` lists requested
ids that didn't resolve (stale UI selection, deleted in flight).

```python
moved: int
skipped_already_in_target: int
missing_ids: List[uuid.UUID]
```

## backend/app/models/schemas.py — AIGenerateTestCasesRequest

[backend/app/models/schemas.py:2773](../../backend/app/models/schemas.py#L2773)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
requirements: str = Field(..., min_length=3)
persist: bool = False
```

## backend/app/models/schemas.py — AIGenerateTestCasesResponse

[backend/app/models/schemas.py:2779](../../backend/app/models/schemas.py#L2779)

Bases: `BaseModel`.



```python
test_cases: List[dict]
coverage_summary: Optional[str] = None
gaps_noted: List[str] = []
created_ids: List[str] = []
```

## backend/app/models/schemas.py — AIReviewTestCaseResponse

[backend/app/models/schemas.py:2786](../../backend/app/models/schemas.py#L2786)

Bases: `BaseModel`.



```python
quality_score: Optional[int] = None
grade: Optional[str] = None
summary: Optional[str] = None
score_breakdown: Optional[dict] = None
issues: Optional[List[dict]] = None
suggestions: Optional[List[dict]] = None
best_practices_violations: Optional[List[str]] = None
coverage_gaps: Optional[List[str]] = None
positive_aspects: Optional[List[str]] = None
error: Optional[str] = None
```

## backend/app/models/schemas.py — AICoverageAnalysisRequest

[backend/app/models/schemas.py:2799](../../backend/app/models/schemas.py#L2799)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
requirements: str = Field(..., min_length=3)
```

## backend/app/models/schemas.py — AICoverageAnalysisResponse

[backend/app/models/schemas.py:2804](../../backend/app/models/schemas.py#L2804)

Bases: `BaseModel`.



```python
coverage_score: Optional[int] = None
covered_areas: Optional[List[str]] = None
partial_coverage: Optional[List[dict]] = None
uncovered_areas: Optional[List[str]] = None
recommended_new_tests: Optional[List[dict]] = None
risk_assessment: Optional[str] = None
summary: Optional[str] = None
error: Optional[str] = None
```

## backend/app/models/schemas.py — AIGenerateStrategyRequest

[backend/app/models/schemas.py:2815](../../backend/app/models/schemas.py#L2815)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
project_context: str = Field(..., min_length=3)
strategy_name: Optional[str] = None
```

## backend/app/models/schemas.py — AIOptimizePlanRequest

[backend/app/models/schemas.py:2821](../../backend/app/models/schemas.py#L2821)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
plan_name: Optional[str] = None
constraints: Optional[str] = None
```

## backend/app/models/schemas.py — AIOptimizePlanResponse

[backend/app/models/schemas.py:2827](../../backend/app/models/schemas.py#L2827)

Bases: `BaseModel`.



```python
optimized_order: Optional[List[dict]] = None
execution_phases: Optional[List[dict]] = None
total_estimated_duration_minutes: Optional[int] = None
parallel_execution_possible: Optional[bool] = None
parallel_groups: Optional[List[Any]] = None
risk_areas_first: Optional[bool] = None
optimization_notes: Optional[str] = None
error: Optional[str] = None
```

## backend/app/models/schemas.py — AITaskEnqueueResponse

[backend/app/models/schemas.py:2838](../../backend/app/models/schemas.py#L2838)

Bases: `BaseModel`.



```python
task_id: str
status: str = 'queued'
```

## backend/app/models/schemas.py — AITaskStatusResponse

[backend/app/models/schemas.py:2843](../../backend/app/models/schemas.py#L2843)

Bases: `BaseModel`.



```python
task_id: str
status: str
result: Optional[dict] = None
error: Optional[str] = None
```

## backend/app/models/schemas.py — SuppliedCommit

[backend/app/models/schemas.py:2852](../../backend/app/models/schemas.py#L2852)

Bases: `BaseModel`.

One commit in a caller-supplied commit range (US-8.1 air-gapped path).

CI/SDK callers that already know the commits landed since the last green
run can push them on ingest so TestLookup needs NO outbound VCS call.
Bounded so a payload can't balloon: ``files`` is capped and the enclosing
``commit_range`` list is capped on each ingest schema.

```python
sha: str = Field(..., min_length=1, max_length=64)
author: Optional[str] = Field(None, max_length=255)
message: Optional[str] = Field(None, max_length=2000)
files: Optional[List[str]] = Field(None, max_length=500)
committed_at: Optional[str] = Field(None, max_length=40)
```

## backend/app/models/schemas.py — SuppliedCommitRange

[backend/app/models/schemas.py:2871](../../backend/app/models/schemas.py#L2871)

Bases: `BaseModel`.

A caller-supplied commit range WITH its boundary refs.

The original US-8.1 wire shape for ``commit_range`` was a bare list of
commits, which threw away the range's boundary: the backend stored
``base_commit = NULL`` and nobody downstream could reconstruct what the
range was *relative to*. That makes the row useless as test-impact
(Epic 10) training data. This object form carries the boundary the
caller already knows (it ran ``git log base..head`` to build the list).

Both wire shapes stay accepted — ``commit_range`` is
``list[SuppliedCommit] | SuppliedCommitRange`` on every ingest schema,
so existing SDK/CLI callers that push a bare list are unaffected.
Field aliases accept ``base``/``base_commit`` and ``head``/``head_commit``
so callers do not have to guess which spelling we wanted.

```python
model_config = ConfigDict(populate_by_name=True, extra='ignore')
base: Optional[str] = Field(None, max_length=64, validation_alias=AliasChoices('base', 'base_commit', 'from_commit'), description='Commit the range starts AFTER (exclusive) — the baseline ref.')
head: Optional[str] = Field(None, max_length=64, validation_alias=AliasChoices('head', 'head_commit', 'to_commit'), description="Commit the range ends AT (inclusive) — usually this run's commit.")
commits: List[SuppliedCommit] = Field(default_factory=list, max_length=_COMMIT_RANGE_MAX)
```

## backend/app/models/schemas.py — LiveSessionCreate

[backend/app/models/schemas.py:2911](../../backend/app/models/schemas.py#L2911)

Bases: `BaseModel`.

Request body to register a new live execution session.

``project_id`` accepts either a project UUID *or* a human-readable project
name (case-insensitive exact match). The server resolves it to a real UUID
in ``stream_service.create_session``. Keeping the field name ``project_id``
preserves wire compatibility with SDK callers that already map their
``testlookup.project`` config (conventionally a name, à la
``rp.project``) onto this field.

```python
project_id: str = Field(..., min_length=1, max_length=255)
client_name: str = Field(..., min_length=1, max_length=255)
machine_id: Optional[str] = Field(None, max_length=255)
build_number: Optional[str] = Field(None, max_length=100)
framework: Optional[str] = Field(None, max_length=50)
branch: Optional[str] = Field(None, max_length=255)
commit_hash: Optional[str] = Field(None, max_length=64)
total_tests: Optional[int] = Field(None, ge=0)
metadata: Optional[dict] = None
release_name: Optional[str] = Field(None, max_length=255)
launch_name: Optional[str] = Field(None, max_length=255)
suite_name: Optional[str] = Field(None, max_length=500)
ci_provider: Optional[str] = Field(None, max_length=30)
ci_repo: Optional[str] = Field(None, max_length=300)
pr_number: Optional[int] = Field(None, ge=1)
ci_actor: Optional[str] = Field(None, max_length=120)
ci_run_url: Optional[str] = Field(None, max_length=1000)
commit_range: Optional[SuppliedCommitRangeInput] = None
```

## backend/app/models/schemas.py — LiveSessionResponse

[backend/app/models/schemas.py:2956](../../backend/app/models/schemas.py#L2956)

Bases: `BaseModel`.

Response returned when a session is created.

```python
session_id: str
session_token: str
run_id: str
project_id: str
expires_in: int
created_at: datetime
```

## backend/app/models/schemas.py — LiveEvent

[backend/app/models/schemas.py:2966](../../backend/app/models/schemas.py#L2966)

Bases: `BaseModel`.

A single test execution event from a client machine.

```python
event_type: str = Field(..., description="run_start | test_start | test_result | log | metric | run_complete | live_heartbeat. live_heartbeat is a no-op refresh emitted by SDK clients during long inter-test gaps — it only bumps the Redis last_event_at field so the reaper doesn't close the session as idle.")
test_name: Optional[str] = Field(None, max_length=1000)
status: Optional[str] = Field(None, description='PASSED | FAILED | SKIPPED | BROKEN')
duration_ms: Optional[int] = Field(None, ge=0)
error_message: Optional[str] = None
stack_trace: Optional[str] = None
suite_name: Optional[str] = Field(None, max_length=500)
class_name: Optional[str] = Field(None, max_length=500)
tags: Optional[List[str]] = None
timestamp_ms: Optional[int] = None
metadata: Optional[dict] = None
```

## backend/app/models/schemas.py — LiveEventBatch

[backend/app/models/schemas.py:2990](../../backend/app/models/schemas.py#L2990)

Bases: `BaseModel`.

A batch of events sent from a client machine.
Batching amortises HTTP overhead — 50–1000 events per call is recommended.

```python
session_id: str = Field(..., min_length=1, max_length=255)
run_id: str = Field(..., min_length=1, max_length=100)
batch_id: Optional[str] = Field(None, min_length=1, max_length=255, pattern='^[^\\x00-\\x1f\\x7f]+$', description='Client-generated identity for this batch. The same value must be reused for every HTTP retry; omitted for legacy callers.')
events: List[LiveEvent] = Field(..., min_length=1, max_length=1000)
```

## backend/app/models/schemas.py — LiveEventBatchResponse

[backend/app/models/schemas.py:3010](../../backend/app/models/schemas.py#L3010)

Bases: `BaseModel`.



```python
accepted: int
run_id: str
session_id: str
```

## backend/app/models/schemas.py — LiveStreamMeta

[backend/app/models/schemas.py:3016](../../backend/app/models/schemas.py#L3016)

Bases: `BaseModel`.

Optional CI/run metadata that enriches the auto-created session.

All fields are optional — when omitted the server falls back to the API
key's name (for client_name) and the run_id (for build_number).

```python
build_number: Optional[str] = Field(None, max_length=100)
branch: Optional[str] = Field(None, max_length=255)
commit_hash: Optional[str] = Field(None, max_length=64)
framework: Optional[str] = Field(None, max_length=50)
total_tests: Optional[int] = Field(None, ge=0)
machine_id: Optional[str] = Field(None, max_length=255)
release_name: Optional[str] = Field(None, max_length=255)
launch_name: Optional[str] = Field(None, max_length=255)
metadata: Optional[dict] = None
```

## backend/app/models/schemas.py — LiveStreamIngestRequest

[backend/app/models/schemas.py:3033](../../backend/app/models/schemas.py#L3033)

Bases: `BaseModel`.

API-key-authenticated streaming ingest. Server auto-manages the session.

A client-chosen ``run_id`` (any stable identifier — CI build id, UUID, etc.)
keys the live session along with the API key's bound project. The first
call for a given ``(project_id, run_id)`` pair auto-creates the session;
subsequent calls reuse it. Clients never call ``/sessions`` themselves.

```python
run_id: str = Field(..., min_length=1, max_length=100)
batch_id: Optional[str] = Field(None, min_length=1, max_length=255, pattern='^[^\\x00-\\x1f\\x7f]+$', description='Client-generated identity for this batch. The same value must be reused for every HTTP retry; omitted for legacy callers.')
events: List[LiveEvent] = Field(..., min_length=1, max_length=1000)
meta: Optional[LiveStreamMeta] = None
```

## backend/app/models/schemas.py — LiveStreamIngestResponse

[backend/app/models/schemas.py:3056](../../backend/app/models/schemas.py#L3056)

Bases: `BaseModel`.



```python
accepted: int
run_id: str
session_id: str
created_session: bool
```

## backend/app/models/schemas.py — IngestTestResult

[backend/app/models/schemas.py:3067](../../backend/app/models/schemas.py#L3067)

Bases: `BaseModel`.

A single test result in a JSON batch ingest.

```python
test_name: str = Field(..., min_length=1, max_length=1000)
status: str = Field(..., pattern='^(PASSED|FAILED|SKIPPED|BROKEN)$')
duration_ms: Optional[int] = Field(None, ge=0)
suite_name: Optional[str] = Field(None, max_length=500)
class_name: Optional[str] = Field(None, max_length=500)
error_message: Optional[str] = None
stack_trace: Optional[str] = None
tags: Optional[List[str]] = None
metadata: Optional[Dict[str, Any]] = None
```

## backend/app/models/schemas.py — IngestPayload

[backend/app/models/schemas.py:3080](../../backend/app/models/schemas.py#L3080)

Bases: `BaseModel`.

JSON batch ingest request body for POST /api/v1/ingest.

```python
project_id: str = Field(..., description='Project UUID')
build_number: str = Field(..., min_length=1, max_length=BUILD_NUMBER_MAX_LENGTH)
results: List[IngestTestResult] = Field(..., min_length=1, max_length=MAX_RESULTS_PER_INGEST)
branch: Optional[str] = Field(None, max_length=255)
commit_hash: Optional[str] = Field(None, max_length=64)
framework: Optional[str] = Field(None, max_length=50)
trigger_source: Optional[str] = 'api'
release_name: Optional[str] = Field(None, max_length=255)
ci_provider: Optional[str] = Field(None, max_length=30)
ci_repo: Optional[str] = Field(None, max_length=300)
pr_number: Optional[int] = Field(None, ge=1)
ci_actor: Optional[str] = Field(None, max_length=120)
ci_run_url: Optional[str] = Field(None, max_length=1000)
jenkins_job: Optional[str] = Field(None, max_length=500)
environment: Optional[str] = Field(None, max_length=100)
commit_range: Optional[SuppliedCommitRangeInput] = None
```

## backend/app/models/schemas.py — IngestResponse

[backend/app/models/schemas.py:3113](../../backend/app/models/schemas.py#L3113)

Bases: `BaseModel`.

Response for accepted ingest request.

```python
status: str = 'accepted'
run_id: str
task_id: str
total_results: int
```

## backend/app/models/schemas.py — UploadStatusResponse

[backend/app/models/schemas.py:3121](../../backend/app/models/schemas.py#L3121)

Bases: `BaseModel`.

Async status of an uploaded report (GET /api/v1/ingest/uploads/{task_id}).

state: pending | parsing | ingesting | succeeded | failed.

```python
task_id: str
run_id: Optional[str] = None
state: str
progress: Optional[dict] = None
result: Optional[dict] = None
error: Optional[dict] = None
```

## backend/app/models/schemas.py — LiveSessionState

[backend/app/models/schemas.py:3134](../../backend/app/models/schemas.py#L3134)

Bases: `BaseModel`.

Live state of an active or recently completed session.

```python
run_id: str
test_run_id: Optional[str] = None
project_id: str
build_number: str
status: str
total: int
passed: int
failed: int
skipped: int
broken: int
pass_rate: float
current_test: Optional[str] = None
started_at: Optional[str] = None
last_event_at: Optional[str] = None
client_name: Optional[str] = None
completed_at: Optional[str] = None
release_name: Optional[str] = None
launch_name: Optional[str] = None
suite_name: Optional[str] = None
run_seq: Optional[int] = None
```

## backend/app/models/schemas.py — ActiveSessionsResponse

[backend/app/models/schemas.py:3168](../../backend/app/models/schemas.py#L3168)

Bases: `BaseModel`.



```python
sessions: List[LiveSessionState]
count: int
```

## backend/app/models/schemas.py — SmtpConfigRead

[backend/app/models/schemas.py:3175](../../backend/app/models/schemas.py#L3175)

Bases: `BaseModel`.

SMTP server configuration returned to the client (no password).

```python
enabled: bool
host: str
port: int
user: Optional[str]
from_address: str
implicit_tls: bool = Field(description='When True, implicit TLS (SSL/TLS on connect, typically port 465) is used. When False, STARTTLS (upgrade after connect, typically port 587) is used. Plain (unencrypted) SMTP is not supported.')
password_set: bool
```

## backend/app/models/schemas.py — SmtpConfigUpdate

[backend/app/models/schemas.py:3192](../../backend/app/models/schemas.py#L3192)

Bases: `BaseModel`.

Payload for updating SMTP server configuration.

```python
enabled: bool = False
host: str = Field(default='localhost', max_length=255)
port: int = Field(default=587, ge=1, le=65535)
user: Optional[str] = Field(default=None, max_length=255)
password: Optional[str] = Field(default=None, max_length=1000)
from_address: str = Field(default='noreply@testlookup.io', max_length=255)
implicit_tls: bool = Field(default=True, description='When True, implicit TLS (SSL/TLS on connect, typically port 465) is used. When False, STARTTLS (upgrade after connect, typically port 587) is used. Plain (unencrypted) SMTP is not supported.')
```

## backend/app/models/schemas.py — SmtpTestResult

[backend/app/models/schemas.py:3210](../../backend/app/models/schemas.py#L3210)

Bases: `BaseModel`.



```python
success: bool
message: str
```

## backend/app/models/schemas.py — AIConfigRead

[backend/app/models/schemas.py:3217](../../backend/app/models/schemas.py#L3217)

Bases: `BaseModel`.

AI / LLM configuration returned to the client (no API keys).

```python
llm_provider: str
llm_model: str
llm_temperature: float
llm_max_tokens: int
ai_offline_mode: bool
ai_offline_mode_source: str = 'not_offline'
ai_offline_mode_env_pinned: bool = False
embedding_provider: str
embedding_model: str
ai_confidence_threshold: int
ai_confidence_threshold_source: str = 'env_default'
ai_timeout_seconds: int
deep_investigation_enabled: bool
finetune_enabled: bool
openai_key_set: bool
google_key_set: bool
anthropic_key_set: bool = False
openrouter_key_set: bool = False
base_url: Optional[str] = None
analysis_mode: str
ml_model_available: bool = False
ml_model_accuracy: Optional[float] = None
ml_training_sample_count: int = 0
ml_human_label_count: int = 0
ml_human_label_floor: int = 50
ml_maturity: str = 'not_trained'
knowledge_rag_enabled: bool = False
```

## backend/app/models/schemas.py — AIConfigUpdate

[backend/app/models/schemas.py:3261](../../backend/app/models/schemas.py#L3261)

Bases: `BaseModel`.

Payload for updating AI configuration. None = keep existing.

```python
llm_provider: Optional[str] = Field(None, pattern='^(ollama|lmstudio|localai|vllm|openai|gemini|anthropic|openrouter)$')
llm_model: Optional[str] = None
llm_temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
llm_max_tokens: Optional[int] = Field(None, ge=256, le=32768)
ai_offline_mode: Optional[bool] = None
embedding_provider: Optional[str] = None
embedding_model: Optional[str] = None
ai_confidence_threshold: Optional[int] = Field(None, ge=0, le=100)
ai_timeout_seconds: Optional[int] = Field(None, ge=30, le=1800)
deep_investigation_enabled: Optional[bool] = None
finetune_enabled: Optional[bool] = None
openai_api_key: Optional[str] = Field(None, max_length=500)
google_api_key: Optional[str] = Field(None, max_length=500)
anthropic_api_key: Optional[str] = Field(None, max_length=500)
openrouter_api_key: Optional[str] = Field(None, max_length=500)
base_url: Optional[str] = Field(None, max_length=500)
analysis_mode: Optional[str] = Field(None, pattern='^(llm|ml|rules|auto)$')
knowledge_rag_enabled: Optional[bool] = None
```

## backend/app/models/schemas.py — IntegrationsConfigRead

[backend/app/models/schemas.py:3290](../../backend/app/models/schemas.py#L3290)

Bases: `BaseModel`.

External integrations configuration (no tokens).

```python
jira_enabled: bool
jira_domain: Optional[str]
jira_email: Optional[str]
jira_token_set: bool
jira_default_project_key: str
splunk_enabled: bool
splunk_base_url: Optional[str]
splunk_token_set: bool
ocp_enabled: bool
ocp_api_url: Optional[str]
ocp_token_set: bool
ocp_default_namespace: str
slack_enabled: bool
slack_webhook_url: Optional[str] = None
slack_webhook_set: bool
slack_default_channel: str
teams_enabled: bool
teams_webhook_url: Optional[str] = None
teams_webhook_set: bool
github_repo: Optional[str]
github_token_set: bool
```

## backend/app/models/schemas.py — IntegrationsConfigUpdate

[backend/app/models/schemas.py:3315](../../backend/app/models/schemas.py#L3315)

Bases: `BaseModel`.

Payload for updating integrations. None = keep existing.

```python
jira_enabled: Optional[bool] = None
jira_domain: Optional[str] = Field(None, max_length=500)
jira_email: Optional[str] = Field(None, max_length=255)
jira_api_token: Optional[str] = Field(None, max_length=500)
jira_default_project_key: Optional[str] = Field(None, max_length=50)
splunk_enabled: Optional[bool] = None
splunk_base_url: Optional[str] = Field(None, max_length=500)
splunk_api_token: Optional[str] = Field(None, max_length=500)
ocp_enabled: Optional[bool] = None
ocp_api_url: Optional[str] = Field(None, max_length=500)
ocp_sa_token: Optional[str] = Field(None, max_length=1000)
ocp_default_namespace: Optional[str] = Field(None, max_length=255)
slack_enabled: Optional[bool] = None
slack_webhook_url: Optional[str] = Field(None, max_length=1000)
slack_default_channel: Optional[str] = Field(None, max_length=100)
teams_enabled: Optional[bool] = None
teams_webhook_url: Optional[str] = Field(None, max_length=1000)
github_repo: Optional[str] = Field(None, max_length=500)
github_token: Optional[str] = Field(None, max_length=500)
```

## backend/app/models/schemas.py — StorageConfigRead

[backend/app/models/schemas.py:3340](../../backend/app/models/schemas.py#L3340)

Bases: `BaseModel`.

Data & storage configuration returned to the client.
Infrastructure connection details are masked to prevent credential/topology disclosure.

```python
storage_backend: str
postgres_connected: bool
mongo_connected: bool
redis_connected: bool
minio_endpoint: str
minio_bucket_name: str
minio_use_ssl: bool
chroma_host: str
chroma_port: int
chroma_collection: str
```

## backend/app/models/schemas.py — StorageConfigUpdate

[backend/app/models/schemas.py:3357](../../backend/app/models/schemas.py#L3357)

Bases: `BaseModel`.

Payload for updating storage config. None = keep existing.

```python
storage_backend: Optional[Literal['minio', 's3', 'local']] = None
chroma_host: Optional[str] = Field(None, min_length=1, max_length=255)
chroma_port: Optional[int] = Field(None, ge=1, le=65535)
chroma_collection: Optional[str] = Field(None, min_length=1, max_length=255)
minio_endpoint: Optional[str] = Field(None, min_length=1, max_length=500)
minio_bucket_name: Optional[str] = Field(None, min_length=1, max_length=255)
minio_use_ssl: Optional[bool] = None
```

## backend/app/models/schemas.py — UserListResponse

[backend/app/models/schemas.py:3370](../../backend/app/models/schemas.py#L3370)

Bases: `BaseModel`.



```python
id: uuid.UUID
email: str
username: str
full_name: Optional[str] = None
role: UserRole
is_active: bool
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — UpdateUserRoleRequest

[backend/app/models/schemas.py:3381](../../backend/app/models/schemas.py#L3381)

Bases: `BaseModel`.



```python
role: UserRole
```

## backend/app/models/schemas.py — UpdateUserStatusRequest

[backend/app/models/schemas.py:3385](../../backend/app/models/schemas.py#L3385)

Bases: `BaseModel`.



```python
is_active: bool
```

## backend/app/models/schemas.py — UpdateUserProfileRequest

[backend/app/models/schemas.py:3389](../../backend/app/models/schemas.py#L3389)

Bases: `BaseModel`.

Editable user attributes. Only non-None fields are applied.

```python
email: Optional[EmailStr] = None
username: Optional[str] = Field(None, min_length=3, max_length=50)
full_name: Optional[str] = None
role: Optional[UserRole] = None
is_active: Optional[bool] = None
```

## backend/app/models/schemas.py — InviteUserRequest

[backend/app/models/schemas.py:3398](../../backend/app/models/schemas.py#L3398)

Bases: `BaseModel`.



```python
email: EmailStr
role: UserRole = UserRole.QA_ENGINEER
```

## backend/app/models/schemas.py — InviteUserResponse

[backend/app/models/schemas.py:3403](../../backend/app/models/schemas.py#L3403)

Bases: `BaseModel`.



```python
id: uuid.UUID
email: str
role: UserRole
expires_at: datetime
invitation_link: str
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AdminCreateUserRequest

[backend/app/models/schemas.py:3412](../../backend/app/models/schemas.py#L3412)

Bases: `BaseModel`.



```python
email: EmailStr
username: str = Field(..., min_length=3, max_length=50)
full_name: Optional[str] = None
role: UserRole = UserRole.QA_ENGINEER
```

## backend/app/models/schemas.py — AdminCreateUserResponse

[backend/app/models/schemas.py:3419](../../backend/app/models/schemas.py#L3419)

Bases: `BaseModel`.



```python
id: uuid.UUID
email: str
username: str
full_name: Optional[str] = None
role: UserRole
is_active: bool
created_at: datetime
temp_password: str
```

## backend/app/models/schemas.py — ProjectMemberResponse

[backend/app/models/schemas.py:3432](../../backend/app/models/schemas.py#L3432)

Bases: `BaseModel`.



```python
id: uuid.UUID
user_id: uuid.UUID
project_id: uuid.UUID
role: UserRole
created_at: datetime
email: str
username: str
full_name: Optional[str] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AddProjectMemberRequest

[backend/app/models/schemas.py:3445](../../backend/app/models/schemas.py#L3445)

Bases: `BaseModel`.



```python
user_id: uuid.UUID
role: UserRole = UserRole.QA_ENGINEER
```

## backend/app/models/schemas.py — UpdateProjectMemberRoleRequest

[backend/app/models/schemas.py:3450](../../backend/app/models/schemas.py#L3450)

Bases: `BaseModel`.



```python
role: UserRole
```

## backend/app/models/schemas.py — ApiKeyCreate

[backend/app/models/schemas.py:3456](../../backend/app/models/schemas.py#L3456)

Bases: `BaseModel`.



```python
name: str = Field(..., min_length=2, max_length=100)
scopes: List[str] = Field(default_factory=list)
expires_days: Optional[int] = Field(None, ge=1, le=365)
project_id: Optional[uuid.UUID] = Field(None, description='Bind key to a single project (ADMIN only)')
target_user_id: Optional[uuid.UUID] = Field(None, description='Create key for another user (ADMIN only)')
```

## backend/app/models/schemas.py — ApiKeyResponse

[backend/app/models/schemas.py:3464](../../backend/app/models/schemas.py#L3464)

Bases: `BaseModel`.



```python
id: uuid.UUID
name: str
key_hint: str
scopes: List[str]
project_id: Optional[uuid.UUID] = None
is_active: bool
expires_at: Optional[datetime] = None
last_used_at: Optional[datetime] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — ApiKeyCreatedResponse

[backend/app/models/schemas.py:3477](../../backend/app/models/schemas.py#L3477)

Bases: `ApiKeyResponse`.

Returned ONCE at creation — includes the plaintext key.

```python
raw_key: str
```

## backend/app/models/schemas.py — OverrideAuditEntry

[backend/app/models/schemas.py:3484](../../backend/app/models/schemas.py#L3484)

Bases: `BaseModel`.

Single entry in the override audit trail.

```python
timestamp: str
actor_id: Optional[str] = None
actor_name: Optional[str] = None
before_recommendation: str
before_risk_score: int
after_recommendation: str
reason: str
policy_id: Optional[str] = None
policy_version: Optional[int] = None
```

## backend/app/models/schemas.py — ReleaseCouncilResponse

[backend/app/models/schemas.py:3498](../../backend/app/models/schemas.py#L3498)

Bases: `BaseModel`.

Extended release decision with council context.

```python
run_id: str
recommendation: str
risk_score: int
composite_risk: Optional[float] = None
dimension_scores: List[DimensionScore] = []
blocking_issues: List[str] = []
conditions_for_go: List[str] = []
reasoning: Optional[str] = None
score_model_version: Optional[int] = None
input_snapshot: Optional[dict] = None
cluster_insights: List[ClusterInsightResponse] = []
baseline_diff: Optional[BaselineDiff] = None
open_defects_by_component: List[dict] = []
human_override: Optional[str] = None
overridden_by: Optional[str] = None
original_recommendation: Optional[str] = None
original_risk_score: Optional[int] = None
override_audit: List[OverrideAuditEntry] = []
pass_rate: Optional[float] = None
build_number: Optional[str] = None
policy_id: Optional[str] = None
policy_version: Optional[int] = None
policy_level: Optional[str] = None
band_policy_id: Optional[str] = None
band_policy_version: Optional[int] = None
band_policy_level: Optional[str] = None
rule_evaluations: List['RuleEvaluationResponse'] = []
synthesized: bool = False
release_readiness_band: Optional[str] = None
band_downgrades: List[str] = []
requires_human_review: bool = False
review: Optional[ReviewBlock] = None
review_gate_enforced: bool = False
draft_recommendation: Optional[str] = None
```

## backend/app/models/schemas.py — ReleaseCouncilOverrideRequest

[backend/app/models/schemas.py:3564](../../backend/app/models/schemas.py#L3564)

Bases: `BaseModel`.

Override request with enhanced audit fields.

```python
override_recommendation: str
reason: str
```

## backend/app/models/schemas.py — TestHealthViolation

[backend/app/models/schemas.py:3572](../../backend/app/models/schemas.py#L3572)

Bases: `BaseModel`.



```python
pattern: str
severity: str
occurrences: int = 1
```

## backend/app/models/schemas.py — TestHealthFinding

[backend/app/models/schemas.py:3578](../../backend/app/models/schemas.py#L3578)

Bases: `BaseModel`.

Per-test health finding.

```python
test_case_id: str
test_name: str
health_score: int
violations: List[TestHealthViolation] = []
critical_count: int = 0
warning_count: int = 0
recommendation: str = ''
anti_patterns: List[str] = []
```

## backend/app/models/schemas.py — TestHealthResponse

[backend/app/models/schemas.py:3590](../../backend/app/models/schemas.py#L3590)

Bases: `BaseModel`.

Test health findings for a run.

```python
run_id: str
total_analyzed: int = 0
with_violations: int = 0
avg_health_score: Optional[float] = None
findings: List[TestHealthFinding] = []
```

## backend/app/models/schemas.py — FlakyCoachEntry

[backend/app/models/schemas.py:3599](../../backend/app/models/schemas.py#L3599)

Bases: `BaseModel`.

Single flaky test with coaching recommendation.

```python
test_fingerprint: str
test_name: str
suite_name: Optional[str] = None
failure_rate: float
total_runs: int = 0
failed_runs: int = 0
flaky_since: Optional[str] = None
last_failure_at: Optional[str] = None
quarantine_recommendation: str = 'MONITOR'
stabilization_actions: List[str] = []
impact_score: float = 0.0
status_history: List[str] = []
status_volatility: Optional[float] = None
error_signature_diversity: Optional[float] = None
stack_trace_diversity: Optional[float] = None
in_run_retry_rate: Optional[float] = None
intermittency_label: Optional[str] = None
flaky_confidence_low: Optional[float] = None
flaky_confidence_high: Optional[float] = None
is_flaky_confidence: Optional[float] = None
flaky_likely_cause: Optional[str] = None
flaky_likely_cause_code: Optional[str] = None
failing_step: Optional[str] = None
failing_step_detail: Optional[str] = None
```

## backend/app/models/schemas.py — FlakyCoachScope

[backend/app/models/schemas.py:3643](../../backend/app/models/schemas.py#L3643)

Bases: `BaseModel`.

What each number in the leaderboard is scoped to.

A filtered list LOOKS release-scoped, and here only half of it is:
membership is, the impact score is not. Stated in the payload rather than
only in the docs, because the number is what gets read — a reader who takes
a score as "how flaky during 2.4.0" would be wrong, and nothing in a bare
filtered list would tell them.

```python
membership: str = 'project'
score: str = 'project_window'
release_id: Optional[str] = None
note: Optional[str] = None
```

## backend/app/models/schemas.py — FlakyCoachResponse

[backend/app/models/schemas.py:3659](../../backend/app/models/schemas.py#L3659)

Bases: `BaseModel`.

Project-level flaky coach leaderboard.

```python
project_id: str
total_flaky: int = 0
quarantine_candidates: int = 0
entries: List[FlakyCoachEntry] = []
scope: Optional[FlakyCoachScope] = None
```

## backend/app/models/schemas.py — TestCaseHistoryPointResponse

[backend/app/models/schemas.py:3672](../../backend/app/models/schemas.py#L3672)

Bases: `BaseModel`.

One cross-run point in a logical test's timeline (most-recent-first).

```python
model_config = ConfigDict(from_attributes=True)
run_id: Optional[str] = None
run_label: str
build_number: Optional[str] = None
run_seq: Optional[int] = None
status: str
duration_ms: Optional[int] = None
created_at: Optional[datetime] = None
```

## backend/app/models/schemas.py — TestCaseFlakinessResponse

[backend/app/models/schemas.py:3685](../../backend/app/models/schemas.py#L3685)

Bases: `BaseModel`.

Computed flakiness for the in-window timeline.

``failure_rate``/``failure_rate_pct`` match ``analytics_service.flaky_tests``;
``classification`` + ``impact_score`` reuse ``test_health_coach_service``
thresholds (no new formula).

```python
model_config = ConfigDict(from_attributes=True)
is_flaky: bool = False
failure_rate: float = 0.0
failure_rate_pct: float = 0.0
impact_score: float = 0.0
classification: str = 'HEALTHY'
window_days: int = 30
total_runs: int = 0
passed: int = 0
failed: int = 0
```

## backend/app/models/schemas.py — TestCaseMetadataResponse

[backend/app/models/schemas.py:3705](../../backend/app/models/schemas.py#L3705)

Bases: `BaseModel`.

Identity metadata: owner, effective suite, first/last seen, timestamps.

```python
model_config = ConfigDict(from_attributes=True)
owner: Optional[str] = None
assigned_to_user_id: Optional[str] = None
suite: Optional[str] = None
severity: Optional[str] = None
feature: Optional[str] = None
first_seen_run_id: Optional[str] = None
first_seen_run_label: Optional[str] = None
first_seen_at: Optional[datetime] = None
last_seen_run_id: Optional[str] = None
last_seen_run_label: Optional[str] = None
last_seen_at: Optional[datetime] = None
created_at: Optional[datetime] = None
updated_at: Optional[datetime] = None
```

## backend/app/models/schemas.py — TestCaseHistoryResponse

[backend/app/models/schemas.py:3724](../../backend/app/models/schemas.py#L3724)

Bases: `BaseModel`.

Wrapper for GET /runs/{run_id}/tests/{test_id}/history.

```python
model_config = ConfigDict(from_attributes=True)
run_id: str
test_id: str
test_fingerprint: Optional[str] = None
test_name: str
history: List[TestCaseHistoryPointResponse] = []
flakiness: TestCaseFlakinessResponse
metadata: TestCaseMetadataResponse
```

## backend/app/models/schemas.py — SSOConfigCreate

[backend/app/models/schemas.py:3740](../../backend/app/models/schemas.py#L3740)

Bases: `BaseModel`.

Create a new SSO configuration.

```python
display_name: str = Field(..., min_length=2, max_length=255)
provider_type: SSOProviderType = SSOProviderType.SAML
idp_entity_id: str = Field(..., min_length=1, max_length=1000)
idp_sso_url: str = Field(..., min_length=1, max_length=2000)
idp_slo_url: Optional[str] = Field(None, max_length=2000)
idp_certificate: str = Field(..., min_length=1)
sp_entity_id: str = Field(..., min_length=1, max_length=1000)
sp_acs_url: str = Field(..., min_length=1, max_length=2000)
audience: Optional[str] = Field(None, max_length=1000)
role_mapping: Optional[dict] = None
default_role: UserRole = UserRole.VIEWER
group_attribute: Optional[str] = Field(None, max_length=255)
enforcement_mode: SSOEnforcementMode = SSOEnforcementMode.OPTIONAL
```

## backend/app/models/schemas.py — SSOConfigUpdate

[backend/app/models/schemas.py:3757](../../backend/app/models/schemas.py#L3757)

Bases: `BaseModel`.

Partial update of SSO configuration.

```python
display_name: Optional[str] = Field(None, min_length=2, max_length=255)
idp_entity_id: Optional[str] = Field(None, min_length=1, max_length=1000)
idp_sso_url: Optional[str] = Field(None, min_length=1, max_length=2000)
idp_slo_url: Optional[str] = Field(None, max_length=2000)
idp_certificate: Optional[str] = None
sp_entity_id: Optional[str] = Field(None, min_length=1, max_length=1000)
sp_acs_url: Optional[str] = Field(None, min_length=1, max_length=2000)
audience: Optional[str] = Field(None, max_length=1000)
role_mapping: Optional[dict] = None
default_role: Optional[UserRole] = None
group_attribute: Optional[str] = Field(None, max_length=255)
enforcement_mode: Optional[SSOEnforcementMode] = None
is_active: Optional[bool] = None
```

## backend/app/models/schemas.py — SSOConfigResponse

[backend/app/models/schemas.py:3774](../../backend/app/models/schemas.py#L3774)

Bases: `BaseModel`.

SSO configuration response (certificate is masked).

```python
id: uuid.UUID
display_name: str
provider_type: SSOProviderType
idp_entity_id: str
idp_sso_url: str
idp_slo_url: Optional[str] = None
idp_certificate_fingerprint: str = ''
sp_entity_id: str
sp_acs_url: str
audience: Optional[str] = None
role_mapping: Optional[dict] = None
default_role: UserRole
group_attribute: Optional[str] = None
enforcement_mode: SSOEnforcementMode
is_active: bool
last_test_at: Optional[datetime] = None
last_test_success: Optional[bool] = None
last_test_error: Optional[str] = None
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SSOTestConnectionResponse

[backend/app/models/schemas.py:3799](../../backend/app/models/schemas.py#L3799)

Bases: `BaseModel`.

Result of testing SSO configuration.

```python
success: bool
message: str
idp_entity_id: Optional[str] = None
certificate_valid: Optional[bool] = None
certificate_expires_at: Optional[str] = None
```

## backend/app/models/schemas.py — SAMLLoginInitResponse

[backend/app/models/schemas.py:3808](../../backend/app/models/schemas.py#L3808)

Bases: `BaseModel`.

Response with redirect URL for SP-initiated SAML login.

```python
redirect_url: str
request_id: str
```

## backend/app/models/schemas.py — SAMLACSRequest

[backend/app/models/schemas.py:3814](../../backend/app/models/schemas.py#L3814)

Bases: `BaseModel`.

SAML Assertion Consumer Service callback payload.

```python
SAMLResponse: str
RelayState: Optional[str] = None
```

## backend/app/models/schemas.py — SSOLoginResponse

[backend/app/models/schemas.py:3820](../../backend/app/models/schemas.py#L3820)

Bases: `BaseModel`.

Token response after successful SSO authentication.

```python
access_token: str
refresh_token: str
token_type: str = 'bearer'
expires_in: int
user: UserResponse
is_new_user: bool = False
```

## backend/app/models/schemas.py — SCIMName

[backend/app/models/schemas.py:3844](../../backend/app/models/schemas.py#L3844)

Bases: `BaseModel`.



```python
givenName: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)
familyName: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)
formatted: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)
```

## backend/app/models/schemas.py — SCIMEmail

[backend/app/models/schemas.py:3850](../../backend/app/models/schemas.py#L3850)

Bases: `BaseModel`.



```python
value: str = Field(..., min_length=1, max_length=SCIM_EMAIL_MAX_LENGTH)
type: Optional[str] = Field('work', max_length=100)
primary: bool = False
```

## backend/app/models/schemas.py — SCIMGroup

[backend/app/models/schemas.py:3856](../../backend/app/models/schemas.py#L3856)

Bases: `BaseModel`.



```python
value: str = Field(..., min_length=1, max_length=SCIM_GROUP_VALUE_MAX_LENGTH)
display: Optional[str] = Field(None, max_length=SCIM_GROUP_DISPLAY_MAX_LENGTH)
```

## backend/app/models/schemas.py — SCIMUserResource

[backend/app/models/schemas.py:3861](../../backend/app/models/schemas.py#L3861)

Bases: `BaseModel`.

SCIM 2.0 User resource — used for both request and response.

```python
schemas: List[str] = [SCIM_USER_SCHEMA]
id: Optional[str] = None
externalId: Optional[str] = Field(None, max_length=SCIM_EXTERNAL_ID_MAX_LENGTH)
userName: str = Field(..., min_length=1, max_length=SCIM_USERNAME_MAX_LENGTH)
name: Optional[SCIMName] = None
emails: List[SCIMEmail] = Field(default=[], max_length=SCIM_EMAILS_MAX_ITEMS)
displayName: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)
active: bool = True
groups: List[SCIMGroup] = Field(default=[], max_length=SCIM_GROUPS_MAX_ITEMS)
meta: Optional[dict] = None
```

- Validator/serializer `require_user_schema`: [backend/app/models/schemas.py:3876](../../backend/app/models/schemas.py#L3876). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — SCIMUserRequest

[backend/app/models/schemas.py:3882](../../backend/app/models/schemas.py#L3882)

Bases: `SCIMUserResource`.

Inbound SCIM user payload; the protocol schemas member is required.

```python
schemas: List[str] = Field(...)
```

- Validator/serializer `require_storable_user_fields`: [backend/app/models/schemas.py:3888](../../backend/app/models/schemas.py#L3888). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — SCIMListResponse

[backend/app/models/schemas.py:3902](../../backend/app/models/schemas.py#L3902)

Bases: `BaseModel`.

SCIM 2.0 ListResponse.

```python
schemas: List[str] = ['urn:ietf:params:scim:api:messages:2.0:ListResponse']
totalResults: int
startIndex: int = 1
itemsPerPage: int = 100
Resources: List[SCIMUserResource] = []
```

## backend/app/models/schemas.py — SCIMPatchOp

[backend/app/models/schemas.py:3911](../../backend/app/models/schemas.py#L3911)

Bases: `BaseModel`.



```python
op: str
path: Optional[str] = None
value: Optional[Any] = None
```

## backend/app/models/schemas.py — SCIMPatchRequest

[backend/app/models/schemas.py:3920](../../backend/app/models/schemas.py#L3920)

Bases: `BaseModel`.



```python
schemas: List[str] = [SCIM_PATCH_SCHEMA]
Operations: List[SCIMPatchOp] = Field(..., min_length=1, max_length=SCIM_PATCH_MAX_OPERATIONS)
```

- Validator/serializer `require_patch_schema`: [backend/app/models/schemas.py:3930](../../backend/app/models/schemas.py#L3930). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — SCIMPatchRequestPayload

[backend/app/models/schemas.py:3936](../../backend/app/models/schemas.py#L3936)

Bases: `SCIMPatchRequest`.

Inbound PATCH payload; the protocol schemas member is required.

```python
schemas: List[str] = Field(...)
```

## backend/app/models/schemas.py — SCIMErrorResponse

[backend/app/models/schemas.py:3942](../../backend/app/models/schemas.py#L3942)

Bases: `BaseModel`.



```python
schemas: List[str] = ['urn:ietf:params:scim:api:messages:2.0:Error']
status: str
detail: str
scimType: Optional[str] = None
```

## backend/app/models/schemas.py — SCIMTokenCreate

[backend/app/models/schemas.py:3949](../../backend/app/models/schemas.py#L3949)

Bases: `BaseModel`.

Create a new SCIM bearer token.

```python
name: str = Field(..., min_length=2, max_length=255)
sso_config_id: Optional[uuid.UUID] = None
expires_days: Optional[int] = Field(None, ge=1, le=365)
```

## backend/app/models/schemas.py — SCIMTokenResponse

[backend/app/models/schemas.py:3956](../../backend/app/models/schemas.py#L3956)

Bases: `BaseModel`.



```python
id: uuid.UUID
name: str
token_hint: str
sso_config_id: Optional[uuid.UUID] = None
is_active: bool
last_used_at: Optional[datetime] = None
expires_at: Optional[datetime] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SCIMTokenCreatedResponse

[backend/app/models/schemas.py:3968](../../backend/app/models/schemas.py#L3968)

Bases: `SCIMTokenResponse`.

Returned once at creation — includes the plaintext token.

```python
raw_token: str
```

## backend/app/models/schemas.py — IdentityEventResponse

[backend/app/models/schemas.py:3976](../../backend/app/models/schemas.py#L3976)

Bases: `BaseModel`.



```python
id: uuid.UUID
event_type: IdentityEventType
user_id: Optional[uuid.UUID] = None
sso_config_id: Optional[uuid.UUID] = None
actor_id: Optional[uuid.UUID] = None
actor_name: Optional[str] = None
detail: Optional[dict] = None
ip_address: Optional[str] = None
success: bool
error_message: Optional[str] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — IdentityEventListResponse

[backend/app/models/schemas.py:3991](../../backend/app/models/schemas.py#L3991)

Bases: `BaseModel`.



```python
total: int
items: List[IdentityEventResponse]
```

## backend/app/models/schemas.py — IdentitySyncStatus

[backend/app/models/schemas.py:3996](../../backend/app/models/schemas.py#L3996)

Bases: `BaseModel`.

Aggregated sync health for the admin dashboard.

```python
sso_config_id: Optional[uuid.UUID] = None
sso_display_name: Optional[str] = None
total_federated_users: int = 0
last_sso_login_at: Optional[datetime] = None
last_scim_sync_at: Optional[datetime] = None
recent_failures: int = 0
recent_events: List[IdentityEventResponse] = []
```

## backend/app/models/schemas.py — PolicyThresholds

[backend/app/models/schemas.py:4010](../../backend/app/models/schemas.py#L4010)

Bases: `BaseModel`.

Risk score thresholds for GO/NO_GO classification.

```python
go_threshold: float = Field(default=20.0, ge=0, le=100)
no_go_threshold: float = Field(default=55.0, ge=0, le=100)
pass_rate_minimum: float = Field(default=90.0, ge=0, le=100)
pass_rate_hard_floor_factor: float = Field(default=0.7, ge=0, le=1.0)
```

## backend/app/models/schemas.py — PolicyDimensionWeights

[backend/app/models/schemas.py:4018](../../backend/app/models/schemas.py#L4018)

Bases: `BaseModel`.

Weights for 7-dimension risk scoring (should sum to 1.0).

```python
user_impact: float = Field(default=0.25, ge=0, le=1.0)
env_sensitivity: float = Field(default=0.1, ge=0, le=1.0)
reproducibility: float = Field(default=0.15, ge=0, le=1.0)
regression_likely: float = Field(default=0.2, ge=0, le=1.0)
hist_recurrence: float = Field(default=0.1, ge=0, le=1.0)
blast_radius: float = Field(default=0.15, ge=0, le=1.0)
diagnosis_conf: float = Field(default=0.05, ge=0, le=1.0)
```

## backend/app/models/schemas.py — PolicyRule

[backend/app/models/schemas.py:4029](../../backend/app/models/schemas.py#L4029)

Bases: `BaseModel`.

A single rule within a release gate policy.

```python
id: str = Field(..., min_length=1, max_length=100)
name: str = Field(..., min_length=1, max_length=255)
type: str
enabled: bool = True
params: dict = Field(default_factory=dict)
```

## backend/app/models/schemas.py — PolicyPassRateBands

[backend/app/models/schemas.py:4038](../../backend/app/models/schemas.py#L4038)

Bases: `BaseModel`.

Project-level 4-band classification for the build colour and verdict.

Bands are defined by the *lower* edge of each colour and must be strictly
increasing: ``orange_min < yellow_min < green_min``. A pass rate below
``orange_min`` is red; ``[orange_min, yellow_min)`` is orange;
``[yellow_min, green_min)`` is yellow; ``>= green_min`` is green.

Defaults match the user-requested levels (red <90, orange 90-95,
yellow 95-99, green >=99). Verdict mapping is fixed: green = GO,
yellow = GO with watch, orange = CONDITIONAL, red = NO_GO.

A breached hard cap (``PolicyHardCaps``) does **not** step the band down
one or two notches — it pins the band straight to red and forces NO_GO,
whatever the pass rate was. The stepped behaviour was abandoned because
yellow still mapped to GO, which made a "hard cap" advisory at best.

```python
orange_min: float = Field(default=90.0, ge=0, le=100)
yellow_min: float = Field(default=95.0, ge=0, le=100)
green_min: float = Field(default=99.0, ge=0, le=100)
```

## backend/app/models/schemas.py — PolicyHardCaps

[backend/app/models/schemas.py:4060](../../backend/app/models/schemas.py#L4060)

Bases: `BaseModel`.

Hard caps that downgrade the pass-rate band before the verdict map.

Each cap is a (count) threshold. **Breaching any cap pins the band to red
and forces NO_GO**, regardless of how green the pass rate was — a hard cap
is a hard blocker, not a one-step downgrade. ``downgrades`` records which
caps fired.

**Zero does not mean the same thing for every cap**, and the difference is
load-bearing, so it is stated per field below rather than summarised here.
``max_p0_defects=0`` means "no P0 defects allowed" and blocks on the first
one; ``max_flaky_count=0`` and ``max_new_failures_24h=0`` *disable* their
caps, because a literal zero would over-fire on real projects. That
asymmetry is deliberate and pinned by
``tests/test_classify_with_policy.py``.

```python
max_p0_defects: int = Field(default=0, ge=0, description='Active P0 defects allowed before the gate blocks. 0 means NONE allowed — one open P0 forces red/NO_GO. This cap cannot be disabled by setting it to 0; raise it to permit P0 defects.')
max_flaky_count: int = Field(default=10, ge=0, description='Flaky tests allowed before the gate blocks. 0 DISABLES this cap (any flaky count passes) rather than forbidding flakiness — set 1 to block on the first flaky test.')
max_new_failures_24h: int = Field(default=20, ge=0, description='New failures in the last 24h allowed before the gate blocks. 0 DISABLES this cap rather than forbidding new failures — set 1 to block on the first one.')
```

## backend/app/models/schemas.py — PolicyKindBudget

[backend/app/models/schemas.py:4105](../../backend/app/models/schemas.py#L4105)

Bases: `BaseModel`.

Failure budget for one excludable failure kind (US-9.3).

``max_failures`` is the count of failures of this kind the gate will
excuse from the NO_GO trigger. ``downgrade_to`` is deliberately a
single-value Literal: a NO_GO may be softened at most to CONDITIONAL_GO —
never to GO — so the schema itself makes the hard rule unrepresentable.

``min_confidence_to_excuse`` (AI-4, optional): when set, a failure only
counts toward this kind's excusable budget if its per-failure kind
confidence (the evidence-checklist confidence, falling back to the
classifier confidence) is at or above the floor. Below-floor and
unknown-confidence failures count as product — conservative. ``None``
(the default) applies no floor: verdicts are byte-identical to the
pre-AI-4 US-9.3 behaviour (pinned by tests/test_kind_gate_policy.py).

```python
max_failures: int = Field(default=0, ge=0)
downgrade_to: Literal['CONDITIONAL_GO'] = 'CONDITIONAL_GO'
min_confidence_to_excuse: Optional[int] = Field(default=None, ge=0, le=100)
```

## backend/app/models/schemas.py — PolicyKindRules

[backend/app/models/schemas.py:4126](../../backend/app/models/schemas.py#L4126)

Bases: `BaseModel`.

Opt-in failure-kind weighting for the release gate (US-9.3).

STRICTLY OPT-IN: ``enabled`` defaults to False and the evaluator treats
a disabled/absent block as byte-identical to today's behaviour (pinned by
``tests/test_kind_gate_policy.py``). Kinds come from the derived triad in
``app/services/failure_kind.py`` (AI-classified — never ground truth).

Only ``infrastructure`` and ``test_code`` may carry budgets. ``product``
failures always count and ``unknown`` failures are conservatively counted
as product — neither is representable here on purpose.

```python
enabled: bool = False
infrastructure: Optional[PolicyKindBudget] = None
test_code: Optional[PolicyKindBudget] = None
```

## backend/app/models/schemas.py — PolicyDocument

[backend/app/models/schemas.py:4143](../../backend/app/models/schemas.py#L4143)

Bases: `BaseModel`.

The full policy rule document stored as JSON in release_gate_policies.rules.

```python
schema_version: int = 1
thresholds: PolicyThresholds = Field(default_factory=PolicyThresholds)
dimension_weights: PolicyDimensionWeights = Field(default_factory=PolicyDimensionWeights)
rules: List[PolicyRule] = Field(default_factory=list)
pass_rate_bands: PolicyPassRateBands = Field(default_factory=PolicyPassRateBands)
hard_caps: PolicyHardCaps = Field(default_factory=PolicyHardCaps)
kind_rules: PolicyKindRules = Field(default_factory=PolicyKindRules)
```

## backend/app/models/schemas.py — ReleaseGatePolicyCreate

[backend/app/models/schemas.py:4158](../../backend/app/models/schemas.py#L4158)

Bases: `BaseModel`.

Create a new draft policy.

```python
project_id: Optional[uuid.UUID] = None
name: str = Field(..., min_length=2, max_length=255)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
rules: PolicyDocument = Field(default_factory=PolicyDocument)
```

## backend/app/models/schemas.py — ReleaseGatePolicyUpdate

[backend/app/models/schemas.py:4166](../../backend/app/models/schemas.py#L4166)

Bases: `BaseModel`.

Update a draft policy (fails if already published).

```python
name: Optional[str] = Field(None, min_length=2, max_length=255)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
rules: Optional[PolicyDocument] = None
```

## backend/app/models/schemas.py — ReleaseGatePolicyResponse

[backend/app/models/schemas.py:4173](../../backend/app/models/schemas.py#L4173)

Bases: `BaseModel`.

Policy summary response.

```python
id: uuid.UUID
project_id: Optional[uuid.UUID] = None
version: int
name: str
description: Optional[str] = None
rules: dict
is_active: bool
is_draft: bool
created_by: uuid.UUID
activated_by: Optional[uuid.UUID] = None
activated_at: Optional[datetime] = None
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — RuleEvaluationResponse

[backend/app/models/schemas.py:4191](../../backend/app/models/schemas.py#L4191)

Bases: `BaseModel`.

Result of evaluating a single policy rule.

```python
rule_id: str
rule_name: str
rule_type: str
passed: bool
action: str
message: str
actual_value: Optional[float] = None
threshold_value: Optional[float] = None
```

## backend/app/models/schemas.py — PolicySimulateRequest

[backend/app/models/schemas.py:4203](../../backend/app/models/schemas.py#L4203)

Bases: `BaseModel`.

Simulate a draft policy against a past run.

```python
run_id: uuid.UUID
policy_document: PolicyDocument
```

## backend/app/models/schemas.py — PolicySimulateResponse

[backend/app/models/schemas.py:4209](../../backend/app/models/schemas.py#L4209)

Bases: `BaseModel`.

Side-by-side comparison: original vs simulated policy result.

```python
original_recommendation: str
simulated_recommendation: str
original_composite: float
simulated_composite: float
rule_evaluations: List[RuleEvaluationResponse] = []
diff_summary: str
```

## backend/app/models/schemas.py — OwnershipRuleCreate

[backend/app/models/schemas.py:4222](../../backend/app/models/schemas.py#L4222)

Bases: `BaseModel`.

Create a new ownership rule.

```python
match_type: str = Field(..., pattern='^(suite_name|component|package|path|label)$')
match_pattern: str = Field(..., min_length=1, max_length=500)
service_name: str = Field(..., min_length=1, max_length=255)
team_name: str = Field(..., min_length=1, max_length=255)
team_contact: Optional[str] = Field(None, max_length=500)
priority: int = Field(default=0, ge=0, le=1000)
```

## backend/app/models/schemas.py — OwnershipRuleUpdate

[backend/app/models/schemas.py:4232](../../backend/app/models/schemas.py#L4232)

Bases: `BaseModel`.

Partial update for an ownership rule.

```python
match_type: Optional[str] = Field(None, pattern='^(suite_name|component|package|path|label)$')
match_pattern: Optional[str] = Field(None, min_length=1, max_length=500)
service_name: Optional[str] = Field(None, min_length=1, max_length=255)
team_name: Optional[str] = Field(None, min_length=1, max_length=255)
team_contact: Optional[str] = Field(None, max_length=500)
priority: Optional[int] = Field(None, ge=0, le=1000)
is_active: Optional[bool] = None
```

## backend/app/models/schemas.py — OwnershipRuleResponse

[backend/app/models/schemas.py:4243](../../backend/app/models/schemas.py#L4243)

Bases: `BaseModel`.

Ownership rule response.

```python
id: uuid.UUID
project_id: uuid.UUID
match_type: str
match_pattern: str
service_name: str
team_name: str
team_contact: Optional[str] = None
priority: int
is_active: bool
created_by: Optional[uuid.UUID] = None
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — OwnershipBulkImportItem

[backend/app/models/schemas.py:4260](../../backend/app/models/schemas.py#L4260)

Bases: `BaseModel`.

Single item for bulk import.

```python
match_type: str = Field(..., pattern='^(suite_name|component|package|path|label)$')
match_pattern: str = Field(..., min_length=1, max_length=500)
service_name: str = Field(..., min_length=1, max_length=255)
team_name: str = Field(..., min_length=1, max_length=255)
team_contact: Optional[str] = None
priority: int = 0
```

## backend/app/models/schemas.py — OwnershipBulkImportRequest

[backend/app/models/schemas.py:4270](../../backend/app/models/schemas.py#L4270)

Bases: `BaseModel`.

Bulk import of ownership rules.

```python
rules: List[OwnershipBulkImportItem] = Field(..., min_length=1, max_length=500)
replace_existing: bool = False
```

## backend/app/models/schemas.py — CodeownersImportRequest

[backend/app/models/schemas.py:4276](../../backend/app/models/schemas.py#L4276)

Bases: `BaseModel`.

Import a CODEOWNERS file into ``path`` ownership rules (US-8.3).

``source == "text"`` carries the raw file body in ``text`` (air-gapped /
paste-upload). ``source == "github"`` fetches it over the configured
GitHub connector and ``text`` is ignored.

```python
source: str = Field(..., pattern='^(github|text)$')
text: Optional[str] = Field(None, max_length=1000000)
```

## backend/app/models/schemas.py — CodeownersCoverage

[backend/app/models/schemas.py:4287](../../backend/app/models/schemas.py#L4287)

Bases: `BaseModel`.

Coverage of recent failing-test paths by ``path`` ownership rules.

```python
path_rules: int = 0
codeowners_rules: int = 0
sampled: int = 0
located: int = 0
matched: int = 0
coverage_pct: Optional[float] = None
lookback_days: int = 30
```

## backend/app/models/schemas.py — CodeownersImportResponse

[backend/app/models/schemas.py:4301](../../backend/app/models/schemas.py#L4301)

Bases: `BaseModel`.

Summary of a CODEOWNERS import.

```python
imported: int
rules_created: int
rules_replaced: int
source: str
coverage: CodeownersCoverage
```

## backend/app/models/schemas.py — OwnershipResolution

[backend/app/models/schemas.py:4310](../../backend/app/models/schemas.py#L4310)

Bases: `BaseModel`.

Result of resolving ownership for a test/cluster.

```python
service_name: Optional[str] = None
team_name: Optional[str] = None
team_contact: Optional[str] = None
confidence: str = 'none'
matched_rule_id: Optional[str] = None
match_source: Optional[str] = None
fallback_reason: Optional[str] = None
```

## backend/app/models/schemas.py — TeamChannelUpsert

[backend/app/models/schemas.py:4321](../../backend/app/models/schemas.py#L4321)

Bases: `BaseModel`.

Create/replace the notification channel for one ownership team
(PMF US-7.3). The team is keyed by name in the URL path.

```python
channel_type: str = Field(..., pattern='^(email|slack|teams)$')
target: str = Field(..., min_length=1, max_length=2000)
is_active: bool = True
```

## backend/app/models/schemas.py — TeamChannelResponse

[backend/app/models/schemas.py:4329](../../backend/app/models/schemas.py#L4329)

Bases: `BaseModel`.

Team → notification channel mapping (PMF US-7.3).

```python
id: uuid.UUID
project_id: uuid.UUID
team_name: str
channel_type: str
target: str
is_active: bool
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SavedViewCreate

[backend/app/models/schemas.py:4382](../../backend/app/models/schemas.py#L4382)

Bases: `BaseModel`.



```python
project_id: Optional[uuid.UUID] = None
name: str = Field(..., min_length=2, max_length=255)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
page: Optional[SAVED_VIEW_PAGES] = None
filters: dict = Field(default_factory=dict)
is_shared: bool = False
is_default: bool = False
```

- Validator/serializer `validate_filters`: [backend/app/models/schemas.py:4393](../../backend/app/models/schemas.py#L4393). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — SavedViewUpdate

[backend/app/models/schemas.py:4397](../../backend/app/models/schemas.py#L4397)

Bases: `BaseModel`.



```python
name: Optional[str] = Field(None, min_length=2, max_length=255)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
page: Optional[SAVED_VIEW_PAGES] = None
filters: Optional[dict] = None
is_shared: Optional[bool] = None
is_default: Optional[bool] = None
```

- Validator/serializer `validate_filters`: [backend/app/models/schemas.py:4407](../../backend/app/models/schemas.py#L4407). Read source for the cross-field or conversion rule.
## backend/app/models/schemas.py — SavedViewRelease

[backend/app/models/schemas.py:4411](../../backend/app/models/schemas.py#L4411)

Bases: `BaseModel`.

Whether THIS reader may apply the release stored in a view (S5-3f-ii).

A saved view outlives the state it was saved in, and a shared view is read
by people who did not save it — so the stored id is an id somebody else
supplied. The verdict is reported rather than the view being refused:
losing one filter is recoverable, refusing to open a view because one field
went stale is not. ``reason`` is what lets the UI say the result set is
wider than the view's author intended.

```python
release_id: Optional[str] = None
applied: bool = False
reason: Optional[str] = None
```

## backend/app/models/schemas.py — SavedViewResponse

[backend/app/models/schemas.py:4427](../../backend/app/models/schemas.py#L4427)

Bases: `BaseModel`.



```python
id: uuid.UUID
user_id: uuid.UUID
project_id: Optional[uuid.UUID] = None
name: str
description: Optional[str] = None
page: Optional[str] = None
filters: dict
is_shared: bool
is_default: bool
created_at: datetime
updated_at: Optional[datetime] = None
release: Optional[SavedViewRelease] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — DigestSubscriptionCreate

[backend/app/models/schemas.py:4445](../../backend/app/models/schemas.py#L4445)

Bases: `BaseModel`.



```python
project_id: Optional[uuid.UUID] = None
saved_view_id: Optional[uuid.UUID] = None
name: str = Field(..., min_length=2, max_length=255)
schedule: str = Field(default='WEEKLY', pattern='^(DAILY|WEEKLY|WEEKLY_RETRO|PER_RUN|PER_RELEASE|PER_SUITE)$')
channel: str = Field(default='email', pattern='^(email|slack|teams)$')
scope_type: Optional[str] = Field(default='project', pattern='^(project|release|suite|global)$')
scope_value: Optional[str] = Field(None, max_length=255)
trigger_filter: Optional[str] = Field(default='all', pattern='^(all|failed_only|degraded_only)$')
send_when_unchanged: bool = True
report_attachment: bool = False
```

## backend/app/models/schemas.py — DigestSubscriptionUpdate

[backend/app/models/schemas.py:4468](../../backend/app/models/schemas.py#L4468)

Bases: `BaseModel`.



```python
name: Optional[str] = Field(None, min_length=2, max_length=255)
schedule: Optional[str] = Field(None, pattern='^(DAILY|WEEKLY|WEEKLY_RETRO|PER_RUN|PER_RELEASE|PER_SUITE)$')
channel: Optional[str] = Field(None, pattern='^(email|slack|teams)$')
saved_view_id: Optional[uuid.UUID] = None
scope_type: Optional[str] = Field(None, pattern='^(project|release|suite|global)$')
scope_value: Optional[str] = Field(None, max_length=255)
trigger_filter: Optional[str] = Field(None, pattern='^(all|failed_only|degraded_only)$')
is_active: Optional[bool] = None
is_paused: Optional[bool] = None
send_when_unchanged: Optional[bool] = None
report_attachment: Optional[bool] = None
```

## backend/app/models/schemas.py — DigestSubscriptionResponse

[backend/app/models/schemas.py:4505](../../backend/app/models/schemas.py#L4505)

Bases: `BaseModel`.



```python
id: uuid.UUID
user_id: uuid.UUID
project_id: Optional[uuid.UUID] = None
saved_view_id: Optional[uuid.UUID] = None
name: str
schedule: str
channel: str
is_active: bool
is_paused: bool
scope_type: Optional[str] = 'project'
scope_value: Optional[str] = None
trigger_filter: Optional[str] = 'all'
send_when_unchanged: bool = True
report_attachment: bool = False
last_delivered_at: Optional[datetime] = None
next_delivery_at: Optional[datetime] = None
delivery_count: int = 0
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — DigestContentResponse

[backend/app/models/schemas.py:4528](../../backend/app/models/schemas.py#L4528)

Bases: `BaseModel`.

Actionable digest content for a project or saved view scope.

```python
project_name: Optional[str] = None
period: str
generated_at: str
total_runs: int = 0
avg_pass_rate: Optional[float] = None
pass_rate_trend: Optional[float] = None
new_regressions: int = 0
top_blockers: List[str] = []
top_clusters: List[dict] = []
flaky_test_count: int = 0
release_decisions: List[dict] = []
action_items: List[str] = []
changes_since: Optional[str] = None
delta: Optional[dict] = None
is_zero_change: Optional[bool] = None
latest_run_total_tests: Optional[int] = None
```

## backend/app/models/schemas.py — AIEvalDatasetCreate

[backend/app/models/schemas.py:4556](../../backend/app/models/schemas.py#L4556)

Bases: `BaseModel`.



```python
name: str = Field(..., min_length=2, max_length=255)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
task_type: str = Field(..., pattern='^(classification|root_cause|release_decision|duplicate_detection)$')
items: List[dict] = Field(default_factory=list)
```

## backend/app/models/schemas.py — AIEvalDatasetResponse

[backend/app/models/schemas.py:4563](../../backend/app/models/schemas.py#L4563)

Bases: `BaseModel`.



```python
id: uuid.UUID
name: str
description: Optional[str] = None
task_type: str
item_count: int
is_active: bool
created_by: Optional[uuid.UUID] = None
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AIEvalRunResponse

[backend/app/models/schemas.py:4576](../../backend/app/models/schemas.py#L4576)

Bases: `BaseModel`.



```python
id: uuid.UUID
dataset_id: uuid.UUID
model_name: str
model_version_id: Optional[uuid.UUID] = None
task_type: str
precision: Optional[float] = None
recall: Optional[float] = None
f1_score: Optional[float] = None
accuracy: Optional[float] = None
agreement_rate: Optional[float] = None
total_items: int = 0
correct_items: int = 0
fallback_used: bool = False
evaluated_at: datetime
duration_ms: Optional[int] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AIEvalGateRunResponse

[backend/app/models/schemas.py:4595](../../backend/app/models/schemas.py#L4595)

Bases: `BaseModel`.



```python
id: uuid.UUID
change_id: str
status: str
manifest_checksum_sha256: str
manifest: Dict[str, Any]
gate_results: List[Dict[str, Any]]
blocking_gates: List[Dict[str, Any]]
version_changes: List[Dict[str, Any]]
gate_type: Optional[str] = None
project_id: Optional[uuid.UUID] = None
agent_id: Optional[str] = None
baseline_tier: Optional[str] = None
candidate_tier: Optional[str] = None
sample_count: Optional[int] = None
evaluated_by: Optional[uuid.UUID] = None
evaluated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AIEvalManifestResponse

[backend/app/models/schemas.py:4615](../../backend/app/models/schemas.py#L4615)

Bases: `BaseModel`.



```python
eval_manifest_checksum: str
source: str
status: str
manifest: Dict[str, Any]
gate_run_id: Optional[uuid.UUID] = None
evaluated_at: Optional[datetime] = None
```

## backend/app/models/schemas.py — EvalProvenanceHealthResponse

[backend/app/models/schemas.py:4624](../../backend/app/models/schemas.py#L4624)

Bases: `BaseModel`.



```python
window_days: int
window_start: datetime
window_end: datetime
total_runs: int
stamped_runs: int
resolved_runs: int
missing_checksum_count: int
unresolvable_run_count: int
unresolvable_checksums: List[str]
has_unresolvable_checksums: bool
```

## backend/app/models/schemas.py — AIQualityDashboardResponse

[backend/app/models/schemas.py:4637](../../backend/app/models/schemas.py#L4637)

Bases: `BaseModel`.

Combined dashboard data for AI quality metrics.

```python
agreement: Optional[dict] = None
drift: Optional[dict] = None
recent_eval_runs: List[AIEvalRunResponse] = []
model_versions: List[dict] = []
feedback_summary: Optional[dict] = None
label_health: Optional[dict] = None
eval_provenance: Optional[EvalProvenanceHealthResponse] = None
```

## backend/app/models/schemas.py — AgentMemoryEntryResponse

[backend/app/models/schemas.py:4653](../../backend/app/models/schemas.py#L4653)

Bases: `BaseModel`.

Single memory entry returned to the client.

```python
id: uuid.UUID
project_id: uuid.UUID
run_id: uuid.UUID
pipeline_run_id: Optional[uuid.UUID] = None
entity_type: str
entity_id: str
error_signature: Optional[str] = None
failure_category: Optional[str] = None
root_cause_summary: Optional[str] = None
payload: Optional[dict] = None
confidence: Optional[int] = None
resolution: Optional[str] = None
source_type: str = 'pipeline_agent'
trust_level: str = 'derived'
lifecycle_status: str = 'active'
source_snapshot_id: Optional[str] = None
source_hash: Optional[str] = None
expires_at: Optional[datetime] = None
superseded_by_id: Optional[uuid.UUID] = None
superseded_at: Optional[datetime] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — AgentMemoryListResponse

[backend/app/models/schemas.py:4679](../../backend/app/models/schemas.py#L4679)

Bases: `BaseModel`.

Paginated memory entry list.

```python
total: int
items: List[AgentMemoryEntryResponse]
page: int
size: int
```

## backend/app/models/schemas.py — SimilarMemoryResponse

[backend/app/models/schemas.py:4687](../../backend/app/models/schemas.py#L4687)

Bases: `BaseModel`.

A memory entry with a similarity score from vector recall.

```python
memory: AgentMemoryEntryResponse
similarity: float = Field(ge=0.0, le=1.0)
retrieval_audit: Optional[Dict[str, Any]] = None
memory_reference: Optional[MemoryReference] = None
```

## backend/app/models/schemas.py — SimilarMemoryRecallRequest

[backend/app/models/schemas.py:4695](../../backend/app/models/schemas.py#L4695)

Bases: `BaseModel`.

Request body for semantic similarity recall.

```python
error_signature: str = Field(..., min_length=5, max_length=5000)
entity_type: Optional[str] = None
limit: int = Field(default=10, ge=1, le=50)
```

## backend/app/models/schemas.py — SimilarMemoryRecallResponse

[backend/app/models/schemas.py:4702](../../backend/app/models/schemas.py#L4702)

Bases: `BaseModel`.

Response with ranked similar memories.

```python
query_signature: str
results: List[SimilarMemoryResponse]
total_found: int
retrieval_audit: Optional[Dict[str, Any]] = None
```

## backend/app/models/schemas.py — MemoryTimelineResponse

[backend/app/models/schemas.py:4710](../../backend/app/models/schemas.py#L4710)

Bases: `BaseModel`.

Timeline of memory entries for a run, grouped by entity type.

```python
run_id: uuid.UUID
project_id: uuid.UUID
entries_by_type: dict
total_entries: int
```

## backend/app/models/schemas.py — KnowledgeSourceCreate

[backend/app/models/schemas.py:4721](../../backend/app/models/schemas.py#L4721)

Bases: `BaseModel`.



```python
source_type: str = Field(..., max_length=30)
title: str = Field(..., min_length=1, max_length=500)
canonical_url: str = Field(..., min_length=1, max_length=2000)
external_id: Optional[str] = Field(None, max_length=500)
classification: str = Field('internal', max_length=20)
```

## backend/app/models/schemas.py — KnowledgeSourceUpdate

[backend/app/models/schemas.py:4729](../../backend/app/models/schemas.py#L4729)

Bases: `BaseModel`.



```python
title: Optional[str] = Field(None, min_length=1, max_length=500)
classification: Optional[str] = Field(None, max_length=20)
is_archived: Optional[bool] = None
```

## backend/app/models/schemas.py — KnowledgeSourceResponse

[backend/app/models/schemas.py:4735](../../backend/app/models/schemas.py#L4735)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
source_type: str
title: str
canonical_url: str
external_id: Optional[str] = None
owner_id: Optional[uuid.UUID] = None
sync_status: str
last_synced_at: Optional[datetime] = None
sync_error: Optional[str] = None
content_hash: Optional[str] = None
classification: str
is_archived: bool
storage_path: Optional[str] = None
created_at: datetime
updated_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — KnowledgeSourceListResponse

[backend/app/models/schemas.py:4756](../../backend/app/models/schemas.py#L4756)

Bases: `BaseModel`.



```python
items: List[KnowledgeSourceResponse]
total: int
page: int
page_size: int
```

## backend/app/models/schemas.py — KnowledgeSourceSyncResponse

[backend/app/models/schemas.py:4763](../../backend/app/models/schemas.py#L4763)

Bases: `BaseModel`.



```python
source_id: uuid.UUID
task_id: str
sync_status: str
```

## backend/app/models/schemas.py — ConnectorTestResult

[backend/app/models/schemas.py:4769](../../backend/app/models/schemas.py#L4769)

Bases: `BaseModel`.



```python
success: bool
latency_ms: Optional[int] = None
error: Optional[str] = None
detail: Optional[str] = None
```

## backend/app/models/schemas.py — ConnectorConfigTestRequest

[backend/app/models/schemas.py:4776](../../backend/app/models/schemas.py#L4776)

Bases: `BaseModel`.



```python
source_type: str
params: dict = Field(default_factory=dict)
```

## backend/app/models/schemas.py — KnowledgeDomainAllowlistUpdate

[backend/app/models/schemas.py:4781](../../backend/app/models/schemas.py#L4781)

Bases: `BaseModel`.



```python
domains: List[str] = Field(..., description="FQDN list, e.g. ['confluence.corp.com', 'jira.corp.com']")
```

- Validator/serializer `_validate_domains`: [backend/app/models/schemas.py:4789](../../backend/app/models/schemas.py#L4789). S4-audit S8: the allowlist is the domain gate that complements the
url_connector SSRF guard, so each entry must be a real FQDN. Reject
wildcards / schemes / ports / paths / IP addresses — none of which the
``hostname == d or hostname.endswith('.'+d)`` matcher honours anyway, so
rejecting them is behaviour-preserving. An empty list is allowed (it
clears the allowlist → permissive, the existing semantics).
## backend/app/models/schemas.py — KnowledgeSyncEventResponse

[backend/app/models/schemas.py:4834](../../backend/app/models/schemas.py#L4834)

Bases: `BaseModel`.



```python
id: uuid.UUID
source_id: uuid.UUID
project_id: uuid.UUID
trigger: str
status: str
content_hash: Optional[str] = None
previous_hash: Optional[str] = None
content_changed: bool
chunk_count: Optional[int] = None
duration_ms: Optional[int] = None
error_message: Optional[str] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — KnowledgeChunkResponse

[backend/app/models/schemas.py:4854](../../backend/app/models/schemas.py#L4854)

Bases: `BaseModel`.



```python
id: uuid.UUID
source_id: uuid.UUID
project_id: uuid.UUID
vector_id: str
section_heading: Optional[str] = None
requirement_id: Optional[str] = None
chunk_index: int
chunk_text_preview: Optional[str] = None
token_count: Optional[int] = None
sync_version: int
is_active: bool
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — KnowledgeSourceFreshnessResponse

[backend/app/models/schemas.py:4874](../../backend/app/models/schemas.py#L4874)

Bases: `BaseModel`.



```python
source_id: uuid.UUID
is_stale: bool
stale_since: Optional[datetime] = None
hours_since_sync: Optional[float] = None
staleness_threshold_hours: int
content_changed_on_last_sync: bool
active_chunk_count: int
last_sync_status: Optional[str] = None
sync_event_count: int
```

## backend/app/models/schemas.py — RagRetrieveRequest

[backend/app/models/schemas.py:4889](../../backend/app/models/schemas.py#L4889)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
query_text: str = Field(..., min_length=1, max_length=5000)
source_ids: Optional[List[uuid.UUID]] = None
top_k: int = Field(10, ge=1, le=50)
min_score: float = Field(0.0, ge=0.0, le=1.0)
```

## backend/app/models/schemas.py — RetrievedChunkSchema

[backend/app/models/schemas.py:4897](../../backend/app/models/schemas.py#L4897)

Bases: `BaseModel`.



```python
vector_id: str
source_id: uuid.UUID
source_title: str
section_heading: Optional[str] = None
chunk_text: str
relevance_score: float
requirement_id: Optional[str] = None
canonical_url: Optional[str] = None
```

## backend/app/models/schemas.py — RagRetrieveResponse

[backend/app/models/schemas.py:4908](../../backend/app/models/schemas.py#L4908)

Bases: `BaseModel`.



```python
chunks: List[RetrievedChunkSchema]
total: int
```

## backend/app/models/schemas.py — RagGenerateRequest

[backend/app/models/schemas.py:4916](../../backend/app/models/schemas.py#L4916)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
prompt_text: str = Field('', max_length=10000)
source_ids: List[uuid.UUID] = Field(default_factory=list)
persist: bool = False
generation_config: Optional[dict] = None
```

## backend/app/models/schemas.py — CitationSchema

[backend/app/models/schemas.py:4924](../../backend/app/models/schemas.py#L4924)

Bases: `BaseModel`.



```python
case_index: int
vector_id: str
source_id: uuid.UUID
source_title: str
section_heading: Optional[str] = None
chunk_text_preview: Optional[str] = None
relevance_score: Optional[float] = None
canonical_url: Optional[str] = None
```

## backend/app/models/schemas.py — RagGenerateResponse

[backend/app/models/schemas.py:4935](../../backend/app/models/schemas.py#L4935)

Bases: `BaseModel`.



```python
batch_id: uuid.UUID
generation_mode: str
test_cases: List[dict]
citations: List[CitationSchema]
coverage_summary: Optional[str] = None
gaps_noted: List[str] = Field(default_factory=list)
created_ids: List[str] = Field(default_factory=list)
```

## backend/app/models/schemas.py — RequirementCoverageSchema

[backend/app/models/schemas.py:4948](../../backend/app/models/schemas.py#L4948)

Bases: `BaseModel`.



```python
id: uuid.UUID
batch_id: uuid.UUID
project_id: uuid.UUID
requirement_id: str
requirement_text: Optional[str] = None
coverage_status: str
covered_by_case_ids: Optional[List[str]] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — GenerationBatchResponse

[backend/app/models/schemas.py:4964](../../backend/app/models/schemas.py#L4964)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
created_by_id: Optional[uuid.UUID] = None
generation_mode: str
cases_generated: int
cases_accepted: int
cases_rejected: int
coverage_score: Optional[int] = None
status: str
llm_model_used: Optional[str] = None
created_at: datetime
completed_at: Optional[datetime] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — RagCaseAcceptEdits

[backend/app/models/schemas.py:4981](../../backend/app/models/schemas.py#L4981)

Bases: `BaseModel`.

Content fields a reviewer may change while accepting generated work.

```python
title: Optional[str] = Field(None, min_length=3, max_length=500)
description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
steps: Optional[List[TestCaseStepSchema]] = None
parameters: Optional[List[TestCaseParameterSchema]] = None
expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_data: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
test_type: Optional[str] = Field(None, max_length=50)
priority: Optional[str] = Field(None, max_length=30)
severity: Optional[str] = Field(None, max_length=30)
feature_area: Optional[str] = Field(None, max_length=500)
tags: Optional[List[str]] = None
estimated_duration_minutes: Optional[int] = Field(None, ge=0)
is_automated: Optional[bool] = None
automation_status: Optional[str] = Field(None, max_length=30)
model_config = ConfigDict(extra='forbid')
```

## backend/app/models/schemas.py — BatchAcceptRequest

[backend/app/models/schemas.py:5004](../../backend/app/models/schemas.py#L5004)

Bases: `BaseModel`.



```python
case_ids: List[uuid.UUID]
edits: Optional[Dict[uuid.UUID, RagCaseAcceptEdits]] = None
```

## backend/app/models/schemas.py — RejectCaseRequest

[backend/app/models/schemas.py:5009](../../backend/app/models/schemas.py#L5009)

Bases: `BaseModel`.



```python
reason: Optional[str] = Field(None, max_length=500)
```

## backend/app/models/schemas.py — AcceptCaseRequest

[backend/app/models/schemas.py:5013](../../backend/app/models/schemas.py#L5013)

Bases: `BaseModel`.



```python
edits: Optional[RagCaseAcceptEdits] = None
```

## backend/app/models/schemas.py — RagStatusResponse

[backend/app/models/schemas.py:5027](../../backend/app/models/schemas.py#L5027)

Bases: `BaseModel`.



```python
enabled: bool
feature_flag: str = 'KNOWLEDGE_RAG_ENABLED'
total_sources: int = 0
total_batches: int = 0
total_chunks: int = 0
```

## backend/app/models/schemas.py — FeatureFlagCreate

[backend/app/models/schemas.py:5038](../../backend/app/models/schemas.py#L5038)

Bases: `BaseModel`.



```python
key: str = Field(..., min_length=2, max_length=80, pattern='^[a-z][a-z0-9_]*$')
description: Optional[str] = Field(None, max_length=2000)
enabled_global: bool = False
enabled_projects: Optional[List[uuid.UUID]] = None
enabled_roles: Optional[List[str]] = None
rollout_percent: int = Field(100, ge=0, le=100)
```

## backend/app/models/schemas.py — FeatureFlagUpdate

[backend/app/models/schemas.py:5047](../../backend/app/models/schemas.py#L5047)

Bases: `BaseModel`.

Partial update.

Omitted fields keep their value. Explicit null clears project/role
allow-lists; for the scalar fields null is ignored.

```python
description: Optional[str] = Field(None, max_length=2000)
enabled_global: Optional[bool] = None
enabled_projects: Optional[List[uuid.UUID]] = None
enabled_roles: Optional[List[str]] = None
rollout_percent: Optional[int] = Field(None, ge=0, le=100)
```

## backend/app/models/schemas.py — FeatureFlagResponse

[backend/app/models/schemas.py:5060](../../backend/app/models/schemas.py#L5060)

Bases: `BaseModel`.



```python
id: uuid.UUID
key: str
description: Optional[str] = None
enabled_global: bool
enabled_projects: Optional[List[uuid.UUID]] = None
enabled_roles: Optional[List[str]] = None
rollout_percent: int
created_at: datetime
updated_at: datetime
updated_by_user_id: Optional[uuid.UUID] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — DecisionLogEntry

[backend/app/models/schemas.py:5077](../../backend/app/models/schemas.py#L5077)

Bases: `BaseModel`.

A single structured decision made by an agent — mirror of
``BaseAgent.log_decision`` output stored on AgentStageResult.decision_log.

```python
at: str
decision_point: str
chosen: str
rationale: str
alternatives: Optional[List[str]] = None
test_case_id: Optional[str] = None
context: Optional[Dict[str, Any]] = None
```

## backend/app/models/schemas.py — StageDecisionSummary

[backend/app/models/schemas.py:5089](../../backend/app/models/schemas.py#L5089)

Bases: `BaseModel`.



```python
stage_name: str
status: str
started_at: Optional[datetime] = None
completed_at: Optional[datetime] = None
duration_seconds: Optional[float] = None
analysis_mode: Optional[str] = None
fallback_used: Optional[bool] = None
fallback_reason: Optional[str] = None
route_rationale: Optional[str] = None
error_category: Optional[str] = None
skipped_reason: Optional[str] = None
execution_path: Optional[str] = None
confidence_score: Optional[int] = None
evidence_count: Optional[int] = None
input_tokens: Optional[int] = None
output_tokens: Optional[int] = None
cost_usd: Optional[float] = None
decision_log: List[DecisionLogEntry] = Field(default_factory=list)
```

## backend/app/models/schemas.py — PerTestRouting

[backend/app/models/schemas.py:5110](../../backend/app/models/schemas.py#L5110)

Bases: `BaseModel`.



```python
test_case_id: uuid.UUID
test_name: Optional[str] = None
analysis_mode: Optional[str] = None
mode_requested: Optional[str] = None
fallback_from: Optional[str] = None
fallback_reason: Optional[str] = None
confidence_adjustments: Optional[List[Dict[str, Any]]] = None
retry_count: Optional[int] = None
duration_seconds: Optional[float] = None
threshold_check: Optional[ThresholdCheck] = None
```

## backend/app/models/schemas.py — WorkflowDecisionEvent

[backend/app/models/schemas.py:5125](../../backend/app/models/schemas.py#L5125)

Bases: `BaseModel`.



```python
at: str
decision_point: str
chosen: str
rationale: str
alternatives: Optional[List[str]] = None
context: Optional[Dict[str, Any]] = None
```

## backend/app/models/schemas.py — DecisionTrailResponse

[backend/app/models/schemas.py:5134](../../backend/app/models/schemas.py#L5134)

Bases: `BaseModel`.

Full decision trail for a pipeline run — the user-facing audit surface.

```python
run_id: uuid.UUID
pipeline_run_id: Optional[uuid.UUID] = None
workflow_type: Optional[str] = None
pipeline_status: Optional[str] = None
started_at: Optional[datetime] = None
completed_at: Optional[datetime] = None
total_cost_usd: float = 0.0
total_tokens: int = 0
stages: List[StageDecisionSummary] = Field(default_factory=list)
workflow_events: List[WorkflowDecisionEvent] = Field(default_factory=list)
per_test: List[PerTestRouting] = Field(default_factory=list)
mode_distribution: Dict[str, int] = Field(default_factory=dict)
fallback_count: int = 0
below_threshold_count: int = 0
```

## backend/app/models/schemas.py — LlmQuotaWrite

[backend/app/models/schemas.py:5161](../../backend/app/models/schemas.py#L5161)

Bases: `BaseModel`.

Admin-editable billing config for a project.

```python
enabled: bool = True
period_type: str = Field('MONTHLY', pattern='^(MONTHLY)$')
included_usd: float = Field(0.0, ge=0)
overage_rate_usd: float = Field(1.0, ge=0)
hard_cap_usd: float = Field(0.0, ge=0)
soft_warn_threshold_pct: int = Field(100, ge=1, le=100)
at_cap_action: str = Field('AUTO_DOWNGRADE_TO_ML', pattern='^(SOFT_WARN|AUTO_DOWNGRADE_TO_ML|AUTO_DOWNGRADE_TO_RULES|HARD_BLOCK)$')
```

## backend/app/models/schemas.py — LlmQuotaRead

[backend/app/models/schemas.py:5175](../../backend/app/models/schemas.py#L5175)

Bases: `LlmQuotaWrite`.



```python
id: uuid.UUID
project_id: uuid.UUID
created_at: datetime
updated_at: datetime
updated_by_user_id: Optional[uuid.UUID] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — LlmUsageRead

[backend/app/models/schemas.py:5184](../../backend/app/models/schemas.py#L5184)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
period_start: datetime
period_end: datetime
total_cost_usd: float
total_input_tokens: int
total_output_tokens: int
total_llm_calls: int
cap_hits: int
included_usd: Optional[float] = None
hard_cap_usd: Optional[float] = None
utilization_pct: Optional[float] = None
status: Optional[str] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — LlmUsageHistoryEntry

[backend/app/models/schemas.py:5201](../../backend/app/models/schemas.py#L5201)

Bases: `BaseModel`.



```python
period_start: datetime
period_end: datetime
total_cost_usd: float
total_llm_calls: int
cap_hits: int
```

## backend/app/models/schemas.py — BillingOverviewProject

[backend/app/models/schemas.py:5209](../../backend/app/models/schemas.py#L5209)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
project_name: str
current_cost_usd: float
hard_cap_usd: Optional[float] = None
utilization_pct: Optional[float] = None
status: str
cap_hits: int
```

## backend/app/models/schemas.py — BillingOverviewResponse

[backend/app/models/schemas.py:5219](../../backend/app/models/schemas.py#L5219)

Bases: `BaseModel`.



```python
period_start: datetime
period_end: datetime
total_cost_usd: float
total_llm_calls: int
projects: List[BillingOverviewProject]
price_table_updated: Optional[str] = None
pricing_is_estimated: bool = True
```

## backend/app/models/schemas.py — FlakyQuarantineRead

[backend/app/models/schemas.py:5236](../../backend/app/models/schemas.py#L5236)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
test_fingerprint: str
test_name: Optional[str] = None
suite_name: Optional[str] = None
status: str
detection_method: str
flip_rate: Optional[float] = None
flip_window_size: Optional[int] = None
pass_count: Optional[int] = None
fail_count: Optional[int] = None
detected_at: datetime
last_failure_at: Optional[datetime] = None
proposed_at: Optional[datetime] = None
approved_at: Optional[datetime] = None
approved_by_user_id: Optional[uuid.UUID] = None
rejected_at: Optional[datetime] = None
rejected_by_user_id: Optional[uuid.UUID] = None
quarantine_start: Optional[datetime] = None
quarantine_expires_at: Optional[datetime] = None
quarantine_duration_days: int
recheck_at: Optional[datetime] = None
rationale: Optional[Dict[str, Any]] = None
reviewer_notes: Optional[str] = None
owner_user_id: Optional[uuid.UUID] = None
owner_name: Optional[str] = None
defect_id: Optional[uuid.UUID] = None
defect_jira_key: Optional[str] = None
defect_jira_url: Optional[str] = None
defect_external_status: Optional[str] = None
defect_external_status_conflict: bool = False
sla_days: Optional[int] = None
stale_at: Optional[datetime] = None
stale: bool = False
consecutive_passes: int = 0
ready_to_promote: bool = False
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — QuarantineDecisionRequest

[backend/app/models/schemas.py:5283](../../backend/app/models/schemas.py#L5283)

Bases: `BaseModel`.

Body for approve / reject / release endpoints.

```python
notes: Optional[str] = Field(None, max_length=2000)
quarantine_duration_days: Optional[int] = Field(None, ge=1, le=90)
```

## backend/app/models/schemas.py — QuarantineProposeRequest

[backend/app/models/schemas.py:5289](../../backend/app/models/schemas.py#L5289)

Bases: `BaseModel`.

Manual proposal — rarely used. Detection agent is the primary path.

```python
project_id: uuid.UUID
test_fingerprint: str = Field(..., min_length=1, max_length=64)
test_name: Optional[str] = Field(None, max_length=500)
suite_name: Optional[str] = Field(None, max_length=500)
detection_method: str = Field('manual', max_length=50)
flip_rate: Optional[float] = Field(None, ge=0, le=1)
flip_window_size: Optional[int] = Field(None, ge=1)
pass_count: Optional[int] = Field(None, ge=0)
fail_count: Optional[int] = Field(None, ge=0)
rationale: Optional[Dict[str, Any]] = None
quarantine_duration_days: int = Field(14, ge=1, le=90)
```

## backend/app/models/schemas.py — QuarantineManifestEntry

[backend/app/models/schemas.py:5304](../../backend/app/models/schemas.py#L5304)

Bases: `BaseModel`.

One currently-quarantined test in the CI manifest (US-5.1).

Identity tuple for CI-side matching: ``fingerprint`` (primary key —
``sha256(class_name::test_name)[:16]``, same formula as ingestion's
``make_test_fingerprint``) plus the human-readable ``test_name`` /
``suite_name`` / ``class_name`` for name-based fallback matching.

```python
fingerprint: str
test_name: Optional[str] = None
suite_name: Optional[str] = None
class_name: Optional[str] = None
status: str
quarantined_at: Optional[datetime] = None
expires_at: Optional[datetime] = None
reason: Optional[str] = None
stale: bool = False
ready_to_promote: bool = False
```

## backend/app/models/schemas.py — QuarantineManifestResponse

[backend/app/models/schemas.py:5326](../../backend/app/models/schemas.py#L5326)

Bases: `BaseModel`.

Versioned quarantine manifest consumed by CI (``testlookup ci-verdict``).

Contains ONLY currently-effective quarantines (QUARANTINED /
RECHECK_SCHEDULED / RE_QUARANTINED) — released, rejected, and expired
rows never appear, nor do un-reviewed proposals.

```python
version: int = 1
project_id: uuid.UUID
generated_at: datetime
etag: str
count: int
entries: List[QuarantineManifestEntry]
```

## backend/app/models/schemas.py — QuarantineLifecyclePolicyUpdate

[backend/app/models/schemas.py:5341](../../backend/app/models/schemas.py#L5341)

Bases: `BaseModel`.

Per-project quarantine lifecycle policy (PMF US-5.4 / US-5.5 / US-5.6).

```python
sla_days: int = Field(14, ge=1, le=365)
auto_create_defect: bool = False
auto_promote: bool = False
promote_after_passes: int = Field(20, ge=1, le=1000)
detection_flip_rate_threshold: float = Field(0.2, ge=0.0, le=1.0)
detection_min_runs: int = Field(10, ge=1, le=1000)
```

## backend/app/models/schemas.py — QuarantineLifecyclePolicyResponse

[backend/app/models/schemas.py:5351](../../backend/app/models/schemas.py#L5351)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
sla_days: int
auto_create_defect: bool
auto_promote: bool
promote_after_passes: int
detection_flip_rate_threshold: float
detection_min_runs: int
is_default: bool = False
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — QuarantineStatsResponse

[backend/app/models/schemas.py:5365](../../backend/app/models/schemas.py#L5365)

Bases: `BaseModel`.

Counts per status for the /quarantine page header tiles.

```python
proposed: int = 0
approved: int = 0
quarantined: int = 0
recheck_scheduled: int = 0
released: int = 0
rejected: int = 0
expired: int = 0
re_quarantined: int = 0
detected: int = 0
total_live: int = 0
```

## backend/app/models/schemas.py — CompliancePackGenerateRequest

[backend/app/models/schemas.py:5382](../../backend/app/models/schemas.py#L5382)

Bases: `BaseModel`.

Body for POST /api/v1/releases/{id}/compliance-pack.

```python
notes: Optional[str] = Field(None, max_length=2000)
retention_days: Optional[int] = Field(None, ge=1, le=3650, description='Override retention window (default 2557 = ~7 years)')
```

## backend/app/models/schemas.py — CompliancePackRead

[backend/app/models/schemas.py:5393](../../backend/app/models/schemas.py#L5393)

Bases: `BaseModel`.



```python
id: uuid.UUID
release_id: Optional[uuid.UUID] = None
project_id: uuid.UUID
test_run_id: Optional[uuid.UUID] = None
minio_key: str
manifest_sha256: str
file_count: int
bytes: int
retention_expires_at: datetime
generated_at: datetime
generated_by_user_id: Optional[uuid.UUID] = None
metadata_snapshot: Optional[Dict[str, Any]] = None
notes: Optional[str] = None
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — CompliancePackDownloadResponse

[backend/app/models/schemas.py:5410](../../backend/app/models/schemas.py#L5410)

Bases: `BaseModel`.

Response for the download endpoint — either a presigned URL or
a streaming hint.

```python
pack_id: uuid.UUID
download_url: Optional[str] = None
expires_in_seconds: Optional[int] = None
bytes: int
manifest_sha256: str
```

## backend/app/models/schemas.py — GitHubIntegrationWrite

[backend/app/models/schemas.py:5423](../../backend/app/models/schemas.py#L5423)

Bases: `BaseModel`.



```python
enabled: bool = True
repo_owner: str = Field(..., min_length=1, max_length=255, pattern='^[A-Za-z0-9._-]+$')
repo_name: str = Field(..., min_length=1, max_length=255, pattern='^[A-Za-z0-9._-]+$')
api_base_url: str = Field('https://api.github.com', max_length=500)
pat: Optional[str] = Field(None, max_length=200)
pr_comment_mode: Literal['off', 'failures_only', 'always'] = 'failures_only'
```

## backend/app/models/schemas.py — GitHubIntegrationRead

[backend/app/models/schemas.py:5438](../../backend/app/models/schemas.py#L5438)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
enabled: bool
repo_owner: str
repo_name: str
api_base_url: str
has_pat: bool
pr_comment_mode: str = 'failures_only'
last_posted_at: Optional[datetime] = None
last_error: Optional[str] = None
last_error_at: Optional[datetime] = None
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — GitHubConnectionTestResponse

[backend/app/models/schemas.py:5455](../../backend/app/models/schemas.py#L5455)

Bases: `BaseModel`.



```python
success: bool
status_code: Optional[int] = None
message: str
repo_html_url: Optional[str] = None
```

## backend/app/models/schemas.py — ValueMetricAssumptionsWrite

[backend/app/models/schemas.py:5469](../../backend/app/models/schemas.py#L5469)

Bases: `BaseModel`.

PUT body for ``/projects/{id}/value-metrics/assumptions`` (US-12.1).

All fields optional — omitted fields keep their current (or default)
value. Bounds: 0 < x <= 480 minutes; anything outside 422s.

```python
triage_minutes_per_failure: Optional[float] = Field(None, gt=0, le=480)
blocked_run_wait_minutes: Optional[float] = Field(None, gt=0, le=480)
defect_filing_minutes: Optional[float] = Field(None, gt=0, le=480)
```

## backend/app/models/schemas.py — ValueMetricAssumptionsRead

[backend/app/models/schemas.py:5480](../../backend/app/models/schemas.py#L5480)

Bases: `BaseModel`.

GET/PUT response — the EFFECTIVE assumptions plus their source
(``default`` = no row, ``custom`` = project row exists).

```python
triage_minutes_per_failure: float
blocked_run_wait_minutes: float
defect_filing_minutes: float
source: str = 'default'
```

## backend/app/models/schemas.py — GitLabConfigWrite

[backend/app/models/schemas.py:5489](../../backend/app/models/schemas.py#L5489)

Bases: `BaseModel`.

PUT body for ``/projects/{id}/integrations/gitlab``.

``token`` is the optional write-only field: ``None`` leaves the stored
secret alone, ``""`` clears it, any value sets/rotates it. The PAT is
NEVER present on the read side (see ``GitLabConfigRead``).

```python
enabled: bool = False
base_url: str = Field('https://gitlab.com', max_length=500, pattern='^https?://')
project_path: str = Field('', max_length=500)
mr_comment_mode: Literal['off', 'failures_only', 'always'] = 'failures_only'
commit_status_enabled: bool = True
token: Optional[str] = Field(None, max_length=200)
```

## backend/app/models/schemas.py — GitLabConfigRead

[backend/app/models/schemas.py:5506](../../backend/app/models/schemas.py#L5506)

Bases: `BaseModel`.

GET/PUT response for ``/projects/{id}/integrations/gitlab``.

Structurally token-free — the PAT can never leak through this model;
``has_token`` is the only token signal. ``mr_comment_mode`` is a plain
``str`` on the read side (GitHub-sibling pattern): the column is an
unconstrained ``String(20)``, and a drifted row value must degrade
gracefully instead of turning GET into a ResponseValidationError 500.

```python
enabled: bool = False
base_url: str = Field('https://gitlab.com', max_length=500)
project_path: str = Field('', max_length=500)
mr_comment_mode: str = 'failures_only'
commit_status_enabled: bool = True
has_token: bool = False
last_error: Optional[str] = None
last_error_at: Optional[datetime] = None
```

## backend/app/models/schemas.py — GitLabConnectionTestResponse

[backend/app/models/schemas.py:5525](../../backend/app/models/schemas.py#L5525)

Bases: `BaseModel`.



```python
ok: bool
detail: str
project_id_resolved: Optional[str] = None
```

## backend/app/models/schemas.py — WebhookSubscriptionWrite

[backend/app/models/schemas.py:5534](../../backend/app/models/schemas.py#L5534)

Bases: `BaseModel`.



```python
name: str = Field(..., min_length=1, max_length=255)
target_url: str = Field(..., min_length=8, max_length=1000, pattern='^https?://')
events: List[str] = Field(..., min_length=1)
enabled: bool = True
max_retries: int = Field(5, ge=0, le=10)
secret: Optional[str] = Field(None, max_length=200)
```

## backend/app/models/schemas.py — WebhookSubscriptionRead

[backend/app/models/schemas.py:5544](../../backend/app/models/schemas.py#L5544)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
name: str
target_url: str
events: List[str]
enabled: bool
has_secret: bool
max_retries: int
last_delivered_at: Optional[datetime] = None
last_failure_at: Optional[datetime] = None
last_error: Optional[str] = None
failure_count: int
total_delivered: int
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — WebhookDeliveryRead

[backend/app/models/schemas.py:5563](../../backend/app/models/schemas.py#L5563)

Bases: `BaseModel`.



```python
id: uuid.UUID
subscription_id: uuid.UUID
event_type: str
event_payload: Optional[Dict[str, Any]] = None
status: str
attempt_count: int
http_status: Optional[int] = None
response_preview: Optional[str] = None
error: Optional[str] = None
delivered_at: Optional[datetime] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — WebhookTestResponse

[backend/app/models/schemas.py:5578](../../backend/app/models/schemas.py#L5578)

Bases: `BaseModel`.



```python
success: bool
status_code: Optional[int] = None
message: str
latency_ms: Optional[int] = None
```

## backend/app/models/schemas.py — WebhookDeliveryReplayResponse

[backend/app/models/schemas.py:5585](../../backend/app/models/schemas.py#L5585)

Bases: `BaseModel`.

Result of POST /webhooks/{sub_id}/deliveries/{delivery_id}/replay.

```python
delivery_id: uuid.UUID
status: str = 'PENDING'
```

## backend/app/models/schemas.py — WebhookEventCatalogEntry

[backend/app/models/schemas.py:5594](../../backend/app/models/schemas.py#L5594)

Bases: `BaseModel`.



```python
event_type: str
description: str
```

## backend/app/models/schemas.py — WebhookEventCatalogResponse

[backend/app/models/schemas.py:5599](../../backend/app/models/schemas.py#L5599)

Bases: `BaseModel`.



```python
events: List[WebhookEventCatalogEntry]
```

## backend/app/models/schemas.py — RunCompareSummary

[backend/app/models/schemas.py:5606](../../backend/app/models/schemas.py#L5606)

Bases: `BaseModel`.

One side of the compare view — the subset of TestRun fields used
by the diff UI. Kept tiny so the JSON payload is fast even on big runs.

```python
id: uuid.UUID
project_id: uuid.UUID
build_number: Optional[str] = None
branch: Optional[str] = None
commit_hash: Optional[str] = None
status: Optional[str] = None
total_tests: int = 0
passed_tests: int = 0
failed_tests: int = 0
broken_tests: int = 0
skipped_tests: int = 0
unknown_tests: int = 0
pass_rate: Optional[float] = None
duration_ms: Optional[int] = None
start_time: Optional[datetime] = None
end_time: Optional[datetime] = None
primary_suite_name: Optional[str] = None
suite_names: Optional[List[str]] = None
```

## backend/app/models/schemas.py — RunCompareSelection

[backend/app/models/schemas.py:5629](../../backend/app/models/schemas.py#L5629)

Bases: `BaseModel`.



```python
mode: Literal['latest_vs_previous', 'explicit'] = 'explicit'
scope: Literal['run', 'suite'] = 'run'
suite_name: Optional[str] = None
selection_reason: str = ''
project_id: uuid.UUID
branch: Optional[str] = None
branch_mismatch: bool = False
release_name: Optional[str] = None
```

## backend/app/models/schemas.py — RunCompareAIReport

[backend/app/models/schemas.py:5640](../../backend/app/models/schemas.py#L5640)

Bases: `BaseModel`.



```python
status: Literal['ready', 'queued', 'failed'] = 'ready'
executive_summary: str = ''
markdown_report: str = ''
risk_level: Literal['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] = 'LOW'
key_differences: List[str] = Field(default_factory=list)
new_risks: List[str] = Field(default_factory=list)
resolved_risks: List[str] = Field(default_factory=list)
duration_concerns: List[str] = Field(default_factory=list)
recommended_actions: List[str] = Field(default_factory=list)
confidence: int = 0
confidence_reason: str = ''
fallback_used: bool = False
message: Optional[str] = None
```

## backend/app/models/schemas.py — RunCompareTestDelta

[backend/app/models/schemas.py:5656](../../backend/app/models/schemas.py#L5656)

Bases: `BaseModel`.

A single test whose status or duration differed between the two runs.

```python
test_fingerprint: str
test_name: Optional[str] = None
suite_name: Optional[str] = None
left_status: Optional[str] = None
right_status: Optional[str] = None
left_duration_ms: Optional[int] = None
right_duration_ms: Optional[int] = None
delta_duration_ms: Optional[int] = None
classification: str
paired_by: Optional[str] = None
previous_test_name: Optional[str] = None
previous_test_fingerprint: Optional[str] = None
```

## backend/app/models/schemas.py — RunCompareResponse

[backend/app/models/schemas.py:5681](../../backend/app/models/schemas.py#L5681)

Bases: `BaseModel`.



```python
left: RunCompareSummary
right: RunCompareSummary
scope: Literal['run', 'suite'] = 'run'
suite_name: Optional[str] = None
selection: Optional[RunCompareSelection] = None
ai_report: Optional[RunCompareAIReport] = None
delta_total: int = 0
delta_passed: int = 0
delta_failed: int = 0
delta_broken: int = 0
delta_skipped: int = 0
delta_pass_rate: Optional[float] = None
delta_duration_ms: Optional[int] = None
new_failures: int = 0
fixed: int = 0
still_failing: int = 0
regressed: int = 0
improved: int = 0
new_tests: int = 0
removed_tests: int = 0
duration_spikes: int = 0
renamed: int = 0
test_deltas: List[RunCompareTestDelta] = Field(default_factory=list)
truncated: bool = False
```

## backend/app/models/schemas.py — SuiteOwnerUpdate

[backend/app/models/schemas.py:5720](../../backend/app/models/schemas.py#L5720)

Bases: `BaseModel`.

PUT body for setting/clearing a suite's explicit owner.

```python
owner_user_id: Optional[uuid.UUID] = None
```

## backend/app/models/schemas.py — SuiteOwnerResponse

[backend/app/models/schemas.py:5725](../../backend/app/models/schemas.py#L5725)

Bases: `BaseModel`.



```python
project_id: uuid.UUID
suite_name: str
owner_user_id: Optional[uuid.UUID] = None
owner_email: Optional[str] = None
owner_full_name: Optional[str] = None
is_fallback: bool = False
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — SuiteReviewUpdate

[backend/app/models/schemas.py:5735](../../backend/app/models/schemas.py#L5735)

Bases: `BaseModel`.



```python
state: SuiteReviewStateLiteral
note: Optional[str] = Field(None, max_length=4000)
```

## backend/app/models/schemas.py — SuiteReviewResponse

[backend/app/models/schemas.py:5740](../../backend/app/models/schemas.py#L5740)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
suite_name: str
test_run_id: uuid.UUID
state: str
note: Optional[str] = None
reviewer_user_id: Optional[uuid.UUID] = None
reviewer_email: Optional[str] = None
reviewed_at: Optional[datetime] = None
created_at: datetime
updated_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — NotifyTestOwnerRequest

[backend/app/models/schemas.py:5755](../../backend/app/models/schemas.py#L5755)

Bases: `BaseModel`.

POST body for /api/v1/analytics/notify-owner — fires an email at the
suite owner of the test that's been failing repeatedly.

```python
project_id: uuid.UUID
test_name: str = Field(..., min_length=1, max_length=1000)
days: int = Field(30, ge=1, le=365)
fail_count: Optional[int] = Field(None, ge=0, le=10000)
```

## backend/app/models/schemas.py — NotifyTestOwnerResponse

[backend/app/models/schemas.py:5764](../../backend/app/models/schemas.py#L5764)

Bases: `BaseModel`.



```python
queued: bool
sent_to: Optional[str] = None
owner_name: Optional[str] = None
suite_name: Optional[str] = None
is_fallback_owner: bool = False
reason: Optional[str] = None
```

## backend/app/models/schemas.py — ClassifyUncategorizedRequest

[backend/app/models/schemas.py:5775](../../backend/app/models/schemas.py#L5775)

Bases: `BaseModel`.

POST body for /api/v1/analytics/classify-uncategorized — bulk-assign a
failure category to every test case currently labelled UNKNOWN (or
NULL) in the requested project + window.

```python
project_id: uuid.UUID
category: FailureCategory
days: int = Field(30, ge=1, le=365)
suite_name: Optional[str] = Field(None, max_length=500)
```

## backend/app/models/schemas.py — ClassifyUncategorizedResponse

[backend/app/models/schemas.py:5787](../../backend/app/models/schemas.py#L5787)

Bases: `BaseModel`.



```python
updated: int
category: str
project_id: uuid.UUID
days: int
suite_name: Optional[str] = None
```

## backend/app/models/schemas.py — DefectIntakeRequest

[backend/app/models/schemas.py:5795](../../backend/app/models/schemas.py#L5795)

Bases: `BaseModel`.

POST body for /api/v1/analytics/defects — manual defect intake.

Severity uses the P0–P3 vocabulary the Defects UI renders; it maps to the
`defects.severity` column's CRITICAL/HIGH/MEDIUM/LOW values server-side.
`test_name`/`suite_name` are optional — when both are supplied the service
will try to attach the new defect to the most-recent matching TestCase row,
otherwise the defect is created standalone (test_case_id NULL).

```python
project_id: uuid.UUID
title: str = Field(..., min_length=3, max_length=255)
description: Optional[str] = Field(None, max_length=10000)
severity: Literal['P0', 'P1', 'P2', 'P3']
failure_category: FailureCategory = FailureCategory.PRODUCT_BUG
component: Optional[str] = Field(None, max_length=255)
test_name: Optional[str] = Field(None, max_length=1000)
suite_name: Optional[str] = Field(None, max_length=500)
jira_ticket_url: Optional[str] = Field(None, max_length=1000)
affects_releases: Optional[list[str]] = Field(None, max_length=100)
```

## backend/app/models/schemas.py — DefectIntakeResponse

[backend/app/models/schemas.py:5822](../../backend/app/models/schemas.py#L5822)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
title: str
severity: str
failure_category: Optional[str] = None
component: Optional[str] = None
test_name: Optional[str] = None
suite_name: Optional[str] = None
jira_ticket_id: Optional[str] = None
jira_ticket_url: Optional[str] = None
resolution_status: str
ai_confidence_score: Optional[int] = None
created_at: datetime
model_config = ConfigDict(from_attributes=True)
```

## backend/app/models/schemas.py — RetentionPolicyWrite

[backend/app/models/schemas.py:5842](../../backend/app/models/schemas.py#L5842)

Bases: `BaseModel`.

PUT body for ``/projects/{id}/retention-policy``.

All fields optional — omitted fields keep their current (or default)
value. Per-field bounds 422 here; the cross-field ``audit_days >=
runs_days`` check runs in the service on the MERGED values (a partial
body can't be validated field-locally).

```python
enabled: Optional[bool] = None
raw_events_days: Optional[int] = Field(None, ge=7, le=3650)
runs_days: Optional[int] = Field(None, ge=30, le=3650)
artifacts_days: Optional[int] = Field(None, ge=7, le=3650)
audit_days: Optional[int] = Field(None, ge=365, le=3650)
```

## backend/app/models/schemas.py — RetentionLastPurge

[backend/app/models/schemas.py:5857](../../backend/app/models/schemas.py#L5857)

Bases: `BaseModel`.

Latest execute-mode purge, parsed from the settings_audit_log
purge-audit rows (``setting_key = "retention_purge:{project_id}"``).

```python
at: datetime
mode: str = 'execute'
counts: dict = Field(default_factory=dict)
```

## backend/app/models/schemas.py — RetentionPolicyRead

[backend/app/models/schemas.py:5865](../../backend/app/models/schemas.py#L5865)

Bases: `BaseModel`.

GET/PUT response — the EFFECTIVE policy plus its source
(``default`` = no row, ``custom`` = project row exists).

```python
enabled: bool
raw_events_days: int
runs_days: int
artifacts_days: int
audit_days: int
source: str = 'default'
last_purge: Optional[RetentionLastPurge] = None
```

## backend/app/models/schemas.py — RetentionPreviewCandidates

[backend/app/models/schemas.py:5877](../../backend/app/models/schemas.py#L5877)

Bases: `BaseModel`.

Per-class candidate counts a purge WOULD delete right now.

**Every class ``run_purge`` counts must have a field here.** FastAPI
filters the handler's return through this model, so a class the service
counts but the model omits is dropped from the response *silently* — no
error, no warning, just a smaller object.

That happened: the service computed twelve counts and this model declared
eight, so ``evidence_artifact_rows``, ``analysis_cache_entries``,
``memory_entries_expired`` and ``search_index_documents`` never reached
the operator. The preview is what an ADMIN authorises an irreversible
cross-store purge from, so under-reporting it is not cosmetic — four
categories of data were deleted by execute without ever appearing in the
dry run.

```python
runs: int
test_cases: int
mongo_docs: dict[str, int] = Field(default_factory=dict)
minio_objects: int
event_archive_rows: int
audit_rows: int
revoked_share_links: int = 0
provenance_rows: int
compliance_packs_expired: int
evidence_artifact_rows: int = 0
memory_entries_expired: int = 0
analysis_cache_entries: Optional[int] = None
search_index_documents: Optional[int] = None
```

## backend/app/models/schemas.py — RetentionPreviewResponse

[backend/app/models/schemas.py:5916](../../backend/app/models/schemas.py#L5916)

Bases: `BaseModel`.

POST ``.../retention-policy/preview`` — dry-run, writes nothing.

```python
cutoffs: dict[str, datetime]
candidates: RetentionPreviewCandidates
unmeasured: List[str] = Field(default_factory=list)
```

## backend/app/models/schemas.py — RetentionPurgeRequest

[backend/app/models/schemas.py:5926](../../backend/app/models/schemas.py#L5926)

Bases: `BaseModel`.

POST ``.../retention-policy/purge`` — typed-name confirmation
(``project_reset`` convention): must equal the project name exactly.

```python
confirmation_name: str = Field(..., min_length=1, max_length=255)
```

## backend/app/models/schemas.py — RetentionPurgeQueued

[backend/app/models/schemas.py:5932](../../backend/app/models/schemas.py#L5932)

Bases: `BaseModel`.

202 body — the execute-mode purge was enqueued to Celery.

```python
queued: bool = True
```

## backend/app/models/viz_contracts.py — VizContract

[backend/app/models/viz_contracts.py:343](../../backend/app/models/viz_contracts.py#L343)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='ignore', strict=True, validate_by_alias=True, validate_by_name=False, serialize_by_alias=True)
```

## backend/app/models/viz_contracts.py — ScopeWindow

[backend/app/models/viz_contracts.py:368](../../backend/app/models/viz_contracts.py#L368)

Bases: `VizContract`.



```python
days: WindowDays | None = None
from_: str | None = Field(default=None, alias='from')
to: str | None = None
```

- Validator/serializer `_one_form_in_order`: [backend/app/models/viz_contracts.py:375](../../backend/app/models/viz_contracts.py#L375). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — Scope

[backend/app/models/viz_contracts.py:397](../../backend/app/models/viz_contracts.py#L397)

Bases: `VizPayload`.

What a report is filtered by. OR within a dimension, AND across dimensions.

```python
project_id: str | None
release_ids: list[str] = Field(max_length=MAX_RELEASES)
suite_names: list[Annotated[str, Field(min_length=1, max_length=MAX_SUITE_NAME_LENGTH)]] = Field(max_length=MAX_SUITES)
window: ScopeWindow
```

- Validator/serializer `_project_id_format`: [backend/app/models/viz_contracts.py:409](../../backend/app/models/viz_contracts.py#L409). Read source for the cross-field or conversion rule.
- Validator/serializer `_release_ids`: [backend/app/models/viz_contracts.py:416](../../backend/app/models/viz_contracts.py#L416). Read source for the cross-field or conversion rule.
- Validator/serializer `_unique_suites`: [backend/app/models/viz_contracts.py:435](../../backend/app/models/viz_contracts.py#L435). Read source for the cross-field or conversion rule.
- Validator/serializer `_release_requires_project`: [backend/app/models/viz_contracts.py:444](../../backend/app/models/viz_contracts.py#L444). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — ScopeProject

[backend/app/models/viz_contracts.py:457](../../backend/app/models/viz_contracts.py#L457)

Bases: `VizContract`.



```python
id: str
name: str
```

## backend/app/models/viz_contracts.py — ScopeRelease

[backend/app/models/viz_contracts.py:462](../../backend/app/models/viz_contracts.py#L462)

Bases: `VizContract`.



```python
id: str
name: str
status: str
```

## backend/app/models/viz_contracts.py — AppliedWindow

[backend/app/models/viz_contracts.py:468](../../backend/app/models/viz_contracts.py#L468)

Bases: `VizContract`.



```python
from_: str = Field(alias='from')
to: str
days: Count
timezone: Literal['UTC']
_days = field_validator('from_', 'to')(_check_day)
```

## backend/app/models/viz_contracts.py — AppliedScope

[backend/app/models/viz_contracts.py:477](../../backend/app/models/viz_contracts.py#L477)

Bases: `VizContract`.

What the server applied, not what was asked.

```python
projects: list[ScopeProject]
releases: list[ScopeRelease]
suites: list[str]
window: AppliedWindow
```

## backend/app/models/viz_contracts.py — Totals

[backend/app/models/viz_contracts.py:486](../../backend/app/models/viz_contracts.py#L486)

Bases: `VizContract`.



```python
matched_runs: Count
total_runs: Count
matched_executions: Count
total_executions: Count
```

- Validator/serializer `_totals_subset`: [backend/app/models/viz_contracts.py:493](../../backend/app/models/viz_contracts.py#L493). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — IgnoredFilter

[backend/app/models/viz_contracts.py:503](../../backend/app/models/viz_contracts.py#L503)

Bases: `VizContract`.



```python
dimension: Literal['release', 'suite', 'window']
reason: str
```

## backend/app/models/viz_contracts.py — EnvelopeMeta

[backend/app/models/viz_contracts.py:508](../../backend/app/models/viz_contracts.py#L508)

Bases: `VizPayload`.

The additive ``meta`` object on an analytics response.

Every key is required. A nullable one is sent as ``null``, never left out:
"not measured" and "the server forgot" must not look the same.

```python
schema_version: PositiveCount
scope: AppliedScope
totals: Totals
pass_rate_basis: Literal['executions', 'unique_tests'] | None
ignored_filters: list[IgnoredFilter]
truncated: bool
truncated_total: Count | None
measured: bool
reason: str | None
includes_in_progress: Count
partial_day: str | None
generated_at: str
as_of: str
_partial_day = field_validator('partial_day')(_check_day)
_instants = field_validator('generated_at', 'as_of')(_check_utc_instant)
```

- Validator/serializer `_conditional_fields`: [backend/app/models/viz_contracts.py:533](../../backend/app/models/viz_contracts.py#L533). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — SeriesPoint

[backend/app/models/viz_contracts.py:563](../../backend/app/models/viz_contracts.py#L563)

Bases: `VizContract`.



```python
x: str
y: Number | None
n: Count
```

## backend/app/models/viz_contracts.py — Series

[backend/app/models/viz_contracts.py:570](../../backend/app/models/viz_contracts.py#L570)

Bases: `VizContract`.



```python
key: str
label: str
points: list[SeriesPoint] = Field(max_length=MAX_POINTS_PER_SERIES)
_point_cap = field_validator('points', mode='before')(_cap('point_cap', MAX_POINTS_PER_SERIES, 'points in one series'))
```

## backend/app/models/viz_contracts.py — SeriesChart

[backend/app/models/viz_contracts.py:580](../../backend/app/models/viz_contracts.py#L580)

Bases: `VizContract`.



```python
kind: Literal['series']
dimensions: list[str]
x_type: Literal['time', 'category']
series: list[Series] = Field(max_length=MAX_SERIES)
```

- Validator/serializer `_unique_series_key`: [backend/app/models/viz_contracts.py:588](../../backend/app/models/viz_contracts.py#L588). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — MatrixCell

[backend/app/models/viz_contracts.py:597](../../backend/app/models/viz_contracts.py#L597)

Bases: `VizContract`.



```python
x: Count
y: Count
value: Number | str | None
n: Count
```

## backend/app/models/viz_contracts.py — MatrixChart

[backend/app/models/viz_contracts.py:605](../../backend/app/models/viz_contracts.py#L605)

Bases: `VizContract`.



```python
kind: Literal['matrix']
value_type: Literal['rate', 'count', 'status']
x_labels: list[str]
y_labels: list[str]
cells: list[MatrixCell] = Field(max_length=MAX_MATRIX_CELLS)
_cell_cap = field_validator('cells', mode='before')(_cap('cell_cap', MAX_MATRIX_CELLS, 'cells'))
```

- Validator/serializer `_cells`: [backend/app/models/viz_contracts.py:617](../../backend/app/models/viz_contracts.py#L617). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — TreeNode

[backend/app/models/viz_contracts.py:643](../../backend/app/models/viz_contracts.py#L643)

Bases: `VizContract`.



```python
id: str
parent_id: str | None
label: str
value: NonNegativeNumber
measure: Number | None
```

## backend/app/models/viz_contracts.py — TreeChart

[backend/app/models/viz_contracts.py:651](../../backend/app/models/viz_contracts.py#L651)

Bases: `VizContract`.



```python
kind: Literal['tree']
nodes: list[TreeNode] = Field(max_length=MAX_TREE_NODES)
_node_cap = field_validator('nodes', mode='before')(_cap('node_cap', MAX_TREE_NODES, 'tree nodes'))
```

- Validator/serializer `_forest`: [backend/app/models/viz_contracts.py:661](../../backend/app/models/viz_contracts.py#L661). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — GraphNode

[backend/app/models/viz_contracts.py:696](../../backend/app/models/viz_contracts.py#L696)

Bases: `VizContract`.



```python
id: str
label: str
size: NonNegativeNumber
```

## backend/app/models/viz_contracts.py — GraphEdge

[backend/app/models/viz_contracts.py:702](../../backend/app/models/viz_contracts.py#L702)

Bases: `VizContract`.



```python
source: str
target: str
weight: UnitNumber
```

## backend/app/models/viz_contracts.py — GraphChart

[backend/app/models/viz_contracts.py:708](../../backend/app/models/viz_contracts.py#L708)

Bases: `VizContract`.



```python
kind: Literal['graph']
nodes: list[GraphNode] = Field(max_length=MAX_GRAPH_NODES)
edges: list[GraphEdge]
_node_cap = field_validator('nodes', mode='before')(_cap('node_cap', MAX_GRAPH_NODES, 'graph nodes'))
```

- Validator/serializer `_edges_join_nodes`: [backend/app/models/viz_contracts.py:718](../../backend/app/models/viz_contracts.py#L718). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — WidgetInstance

[backend/app/models/viz_contracts.py:757](../../backend/app/models/viz_contracts.py#L757)

Bases: `VizContract`.

One placed visualisation inside ``saved_views.filters``.

Attribute names ARE the wire keys. The frontend writes this same stored
object, so a dump that forgot ``by_alias`` must not be able to write
``instance_id`` beside the ``instanceId`` the other writer reads.

```python
model_config = ConfigDict(extra='allow')
instanceId: str = Field(min_length=1)
templateId: str = Field(min_length=1)
title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
chartType: ChartType | None = None
metricVariant: str | None = None
filters: dict[str, Any] | None = None
groupBy: list[Dimension] | None = Field(default=None, max_length=MAX_GROUP_BY)
topN: Literal[5, 10, 25, 50] | None = None
scale: Literal['linear', 'log'] | None = None
stack: Literal['none', 'absolute', 'percent'] | None = None
bucket: Literal['day', 'week'] | None = None
```

- Validator/serializer `_optional_is_not_nullable`: [backend/app/models/viz_contracts.py:780](../../backend/app/models/viz_contracts.py#L780). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — WidgetConfig

[backend/app/models/viz_contracts.py:796](../../backend/app/models/viz_contracts.py#L796)

Bases: `VizPayload`.

``saved_views.filters`` for an analytics page. Unknown keys survive a round-trip.

```python
model_config = ConfigDict(extra='allow')
page: str
version: PositiveCount
instances: list[WidgetInstance] = Field(max_length=MAX_INSTANCES)
```

- Validator/serializer `_unique_instance_id`: [backend/app/models/viz_contracts.py:807](../../backend/app/models/viz_contracts.py#L807). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — DrillLevel

[backend/app/models/viz_contracts.py:819](../../backend/app/models/viz_contracts.py#L819)

Bases: `VizContract`.



```python
dimension: Dimension
value: str = Field(min_length=1, max_length=MAX_DRILL_VALUE_LENGTH)
```

- Validator/serializer `_status_vocab`: [backend/app/models/viz_contracts.py:824](../../backend/app/models/viz_contracts.py#L824). Read source for the cross-field or conversion rule.
## backend/app/models/viz_contracts.py — DrillPath

[backend/app/models/viz_contracts.py:830](../../backend/app/models/viz_contracts.py#L830)

Bases: `VizPayload`.

Ordered from the top level down.

```python
path: list[DrillLevel] = Field(max_length=MAX_DRILL_DEPTH)
```

- Validator/serializer `_unique_dimension`: [backend/app/models/viz_contracts.py:837](../../backend/app/models/viz_contracts.py#L837). Read source for the cross-field or conversion rule.
## backend/app/routers/admin_maintenance.py — OutboxRequeueRequest

[backend/app/routers/admin_maintenance.py:292](../../backend/app/routers/admin_maintenance.py#L292)

Bases: `BaseModel`.

Which failed run-outbox intents to put back.

```python
operation: str = Field(..., description='The post-ingestion operation, for example agent_pipeline.')
last_error: str = Field(..., min_length=1, max_length=200, description='Only intents that failed with exactly this error, for example broker_TypeError. Required: a requeue without it re-ran every failed intent of the operation, including notifications and webhooks that had executed and failed for their own reasons (code review round 3).')
run_id: uuid.UUID | None = Field(None, description="Only this run's intents.")
limit: int = Field(100, ge=1, le=500, description='At most this many, oldest first.')
dry_run: bool = Field(True, description='List what would be requeued, and change nothing. The default.')
```

## backend/app/routers/agent_actions.py — ActionTransitionRequest

[backend/app/routers/agent_actions.py:20](../../backend/app/routers/agent_actions.py#L20)

Bases: `BaseModel`.



```python
status: Literal['approved', 'rejected', 'rolled_back']
error_code: str | None = Field(default=None, max_length=100)
```

## backend/app/routers/agent_investigations.py — AgentPolicyBudgets

[backend/app/routers/agent_investigations.py:122](../../backend/app/routers/agent_investigations.py#L122)

Bases: `BaseModel`.



```python
max_runs_per_day: int = Field(default=10, ge=0, le=10000)
max_llm_calls_per_run: int = Field(default=30, ge=0, le=100000)
max_tokens_per_run: int = Field(default=60000, ge=0, le=100000000)
max_cost_usd_per_run: float = Field(default=5.0, ge=0, le=1000000)
max_seconds_per_run: int = Field(default=300, ge=0, le=86400)
max_cluster_children_per_run: int = Field(default=1, ge=0, le=20)
max_cluster_members_per_child: int = Field(default=50, ge=1, le=500)
max_cluster_child_llm_calls_per_parent: int = Field(default=6, ge=0, le=1000)
max_cluster_child_tokens_per_parent: int = Field(default=12000, ge=0, le=10000000)
max_cluster_child_cost_usd_per_parent: float = Field(default=2.0, ge=0, le=1000000)
max_cluster_child_seconds_per_parent: int = Field(default=180, ge=0, le=86400)
max_active_cluster_children_per_project: int = Field(default=2, ge=0, le=20)
max_cluster_children_per_day: int = Field(default=20, ge=0, le=1000)
```

## backend/app/routers/agent_investigations.py — AgentPolicyPromotion

[backend/app/routers/agent_investigations.py:138](../../backend/app/routers/agent_investigations.py#L138)

Bases: `BaseModel`.



```python
shadow_runs_completed: int = Field(default=0, ge=0)
note: Optional[str] = Field(default=None, max_length=2000)
```

## backend/app/routers/agent_investigations.py — AgentPolicyUpdate

[backend/app/routers/agent_investigations.py:144](../../backend/app/routers/agent_investigations.py#L144)

Bases: `BaseModel`.

PUT body — AgentPolicy sans ``agent_id`` (pinned contract).

```python
enabled: bool = True
mode: str = 'shadow'
budgets: AgentPolicyBudgets = Field(default_factory=AgentPolicyBudgets)
promotion: Optional[AgentPolicyPromotion] = None
```

- Validator/serializer `_validate_mode`: [backend/app/routers/agent_investigations.py:154](../../backend/app/routers/agent_investigations.py#L154). Read source for the cross-field or conversion rule.
## backend/app/routers/agent_invoke.py — AgentInvokeRequest

[backend/app/routers/agent_invoke.py:100](../../backend/app/routers/agent_invoke.py#L100)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
project_id: uuid.UUID = Field(description='Assertion: must equal the project of the test run named in input, else 400.')
input: dict[str, Any] = Field(description="The agent's <StageName>InvokeInput, as published by GET /api/v1/agents/catalog/{agent_id}.")
mode: Literal['async', 'sync'] = Field(default='async', description='sync waits for the result, and only for sync-eligible agents (see the catalog); any other agent runs async and answers 202.')
config_overrides: Optional[AgentConfigPatch] = Field(default=None, description='Per-invocation tighten-only overrides. Provider, endpoint, mode, temperature, max_tokens, escalation, and other ambiguous fields are rejected.')
correlation_id: Optional[str] = Field(default=None, max_length=128, pattern='^[A-Za-z0-9._:-]+$')
```

## backend/app/routers/agent_invoke.py — AgentInvocationResponse

[backend/app/routers/agent_invoke.py:126](../../backend/app/routers/agent_invoke.py#L126)

Bases: `BaseModel`.



```python
id: uuid.UUID
project_id: uuid.UUID
agent_id: str
test_run_id: uuid.UUID
pipeline_run_id: uuid.UUID
mode: Literal['sync', 'async']
status: Literal['in_progress', 'completed', 'failed', 'passed']
attempt: int
max_attempts: int
next_retry_at: Optional[datetime] = None
error: Optional[str] = None
output: Optional[dict[str, Any]] = Field(default=None, description="The invoked agent's stored stage output, once that stage has completed.")
config_snapshot: Optional[dict[str, Any]] = Field(default=None, description='Credential-free resolved configuration accepted for this invocation.')
requires_human_review: bool
review: ReviewBlock
created_at: Optional[datetime] = None
links: dict[str, str]
```

## backend/app/routers/agent_invoke.py — StreamTicketResponse

[backend/app/routers/agent_invoke.py:152](../../backend/app/routers/agent_invoke.py#L152)

Bases: `BaseModel`.



```python
ticket: str
expires_in: int
links: dict[str, str]
```

## backend/app/routers/agents.py — RetryPipelineResponse

[backend/app/routers/agents.py:416](../../backend/app/routers/agents.py#L416)

Bases: `BaseModel`.

What a manual retry did. ``mode`` is ``resume`` or ``rerun``.

```python
mode: str
pipeline_run_id: Optional[str] = None
rerun_of: Optional[str] = None
attempt: int
max_attempts: int
reason: str
status: str
public_status: str
links: dict = Field(default_factory=dict)
```

## backend/app/routers/agents.py — BulkTriggerRequest

[backend/app/routers/agents.py:685](../../backend/app/routers/agents.py#L685)

Bases: `BaseModel`.



```python
run_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=2000)
workflow_type: Literal['offline', 'deep'] = 'offline'
```

## backend/app/routers/agents.py — BulkTriggerResponse

[backend/app/routers/agents.py:690](../../backend/app/routers/agents.py#L690)

Bases: `BaseModel`.



```python
queued: int
not_found: int
workflow_type: str
not_found_ids: List[str] = Field(default_factory=list)
```

## backend/app/routers/ai_evaluation.py — PreReleaseGateRequest

[backend/app/routers/ai_evaluation.py:277](../../backend/app/routers/ai_evaluation.py#L277)

Bases: `BaseModel`.



```python
task_type: str = 'classification'
agent_name: str = 'AnalysisAgent'
dataset_id: Optional[str] = None
```

## backend/app/routers/ai_evaluation.py — ReportEvalCycleRequest

[backend/app/routers/ai_evaluation.py:283](../../backend/app/routers/ai_evaluation.py#L283)

Bases: `BaseModel`.



```python
corpus_version: str = Field(min_length=1, max_length=120)
reports: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
authorized_evidence_ids: list[str] = Field(default_factory=list, max_length=5000)
project_id: uuid.UUID | None = None
test_run_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
cycle_key: str | None = Field(default=None, max_length=128)
```

## backend/app/routers/ai_evaluation.py — AgentStackReleaseGateRequest

[backend/app/routers/ai_evaluation.py:417](../../backend/app/routers/ai_evaluation.py#L417)

Bases: `BaseModel`.



```python
change_id: str
prompt_versions: dict[str, str] = Field(default_factory=dict)
model_versions: dict[str, str] = Field(default_factory=dict)
routing_versions: dict[str, str] = Field(default_factory=dict)
required_gates: list[dict[str, str]] | None = None
persist: bool = True
```

## backend/app/routers/ai_evaluation.py — TierOutputPairRequest

[backend/app/routers/ai_evaluation.py:426](../../backend/app/routers/ai_evaluation.py#L426)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
sample_id: str = Field(min_length=1, max_length=160)
incumbent_output: Any
candidate_output: Any
incumbent_cost_usd: float = Field(default=0.0, ge=0)
candidate_cost_usd: float = Field(default=0.0, ge=0)
incumbent_latency_ms: int = Field(default=0, ge=0)
candidate_latency_ms: int = Field(default=0, ge=0)
incumbent_tokens: int = Field(default=0, ge=0)
candidate_tokens: int = Field(default=0, ge=0)
```

## backend/app/routers/ai_evaluation.py — TierComparisonRequest

[backend/app/routers/ai_evaluation.py:440](../../backend/app/routers/ai_evaluation.py#L440)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
project_id: uuid.UUID
agent_id: str = Field(min_length=1, max_length=80)
capability: str = Field(min_length=1, max_length=80)
incumbent_tier: str = Field(pattern='^(deterministic|slm|llm|auto)$')
candidate_tier: str = Field(pattern='^(deterministic|slm|llm|auto)$')
delta: float = Field(default=0.05, ge=0, le=1)
pairs: list[TierOutputPairRequest] = Field(default_factory=list, max_length=1000)
persist: bool = True
```

## backend/app/routers/ai_evaluation.py — ReviewerQualityRequest

[backend/app/routers/ai_evaluation.py:453](../../backend/app/routers/ai_evaluation.py#L453)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
project_id: uuid.UUID
agent_id: str = Field(default='agent.reviewer.v1', min_length=1, max_length=80)
mutations: list[ReviewerMutationObservation] = Field(default_factory=list, max_length=10000)
clean: list[ReviewerCleanObservation] = Field(default_factory=list, max_length=10000)
human_outcomes: list[ReviewerHumanOutcome] = Field(default_factory=list, max_length=10000)
persist: bool = True
auto_disable: bool = True
```

## backend/app/routers/ai_evaluation.py — SetBaselineRequest

[backend/app/routers/ai_evaluation.py:465](../../backend/app/routers/ai_evaluation.py#L465)

Bases: `BaseModel`.



```python
task_type: str = 'classification'
agent_name: str = 'AnalysisAgent'
prompt_version: str = 'v1'
model_name: Optional[str] = None
dataset_id: Optional[str] = None
min_accuracy: float = 0.8
min_f1: float = 0.75
max_regression_pct: float = 5.0
```

## backend/app/routers/app_settings.py — RequiredModel

[backend/app/routers/app_settings.py:395](../../backend/app/routers/app_settings.py#L395)

Bases: `BaseModel`.

One model the effective configuration depends on.

```python
name: str
purpose: str
present: bool
remedy: Optional[str] = None
```

## backend/app/routers/app_settings.py — FallbackChainEntry

[backend/app/routers/app_settings.py:407](../../backend/app/routers/app_settings.py#L407)

Bases: `BaseModel`.

One analysis tier and whether it can actually run right now.

```python
mode: str
available: bool
reason: Optional[str] = None
reason_code: Optional[str] = None
```

## backend/app/routers/app_settings.py — AIModelStatusRead

[backend/app/routers/app_settings.py:422](../../backend/app/routers/app_settings.py#L422)

Bases: `BaseModel`.

Live model presence + fallback-chain state (US-13.2).

``ollama_reachable=False`` and "model missing" are deliberately
separate signals: an unreachable daemon is a connectivity problem,
an empty/incomplete model list on a reachable daemon is a model-pack
import problem. The UI must not collapse them.

```python
ollama_reachable: bool
ollama_error: Optional[str] = None
ollama_base_url: str
installed_models: list[str]
required: list[RequiredModel]
fallback_chain: list[FallbackChainEntry]
offline_mode: bool
llm_provider: str
analysis_mode: str
checked_at: str
```

## backend/app/routers/app_settings.py — FeatureFlagUpdate

[backend/app/routers/app_settings.py:868](../../backend/app/routers/app_settings.py#L868)

Bases: `BaseModel`.



```python
enabled: bool
scope: str = 'global'
config: Optional[dict] = None
description: Optional[str] = None
```

## backend/app/routers/deep_investigation.py — TriggerDeepRequest

[backend/app/routers/deep_investigation.py:49](../../backend/app/routers/deep_investigation.py#L49)

Bases: `BaseModel`.



```python
mode: str = 'deep'
```

## backend/app/routers/deep_investigation.py — TriggerDeepResponse

[backend/app/routers/deep_investigation.py:53](../../backend/app/routers/deep_investigation.py#L53)

Bases: `BaseModel`.



```python
task_id: Optional[str] = None
pipeline_run_id: Optional[str] = None
message: str
run_id: str
```

## backend/app/routers/deep_investigation.py — ClusterResponse

[backend/app/routers/deep_investigation.py:60](../../backend/app/routers/deep_investigation.py#L60)

Bases: `BaseModel`.



```python
cluster_id: str
label: str
representative_error: Optional[str]
member_test_ids: list[str]
size: int
cohesion_score: Optional[float] = None
```

## backend/app/routers/deep_investigation.py — DeepFindingResponse

[backend/app/routers/deep_investigation.py:69](../../backend/app/routers/deep_investigation.py#L69)

Bases: `BaseModel`.



```python
cluster_id: str
root_cause: Optional[str]
failure_category: Optional[str]
confidence_score: Optional[int]
causal_chain: Optional[list]
evidence: Optional[list]
affected_services: Optional[list]
contract_violations: Optional[list]
recommended_actions: Optional[list]
origin: str = 'unknown'
confidence_basis: Optional[str] = None
```

## backend/app/routers/feedback.py — FeedbackRequest

[backend/app/routers/feedback.py:172](../../backend/app/routers/feedback.py#L172)

Bases: `BaseModel`.



```python
rating: FeedbackRating
corrected_category: Optional[FailureCategory] = None
corrected_root_cause: Optional[str] = None
comment: Optional[str] = None
```

## backend/app/routers/feedback.py — DecisionReportFeedbackRequest

[backend/app/routers/feedback.py:180](../../backend/app/routers/feedback.py#L180)

Bases: `BaseModel`.

Utility rating or claim correction for one immutable report version.

```python
report_version: int = Field(..., ge=1)
feedback_kind: Literal['utility', 'claim_correction']
utility_rating: Optional[Literal['useful', 'partially_useful', 'not_useful']] = None
claim_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
correction_type: Optional[Literal['category', 'cause', 'flaky', 'release']] = None
corrected_value: Optional[object] = None
reason: Optional[str] = Field(default=None, max_length=4000)
evidence_ids: list[str] = Field(default_factory=list, max_length=5)
idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)
```

- Validator/serializer `validate_evidence_ids`: [backend/app/routers/feedback.py:194](../../backend/app/routers/feedback.py#L194). Read source for the cross-field or conversion rule.
- Validator/serializer `validate_feedback_shape`: [backend/app/routers/feedback.py:200](../../backend/app/routers/feedback.py#L200). Read source for the cross-field or conversion rule.
## backend/app/routers/feedback.py — PromoteModelRequest

[backend/app/routers/feedback.py:213](../../backend/app/routers/feedback.py#L213)

Bases: `BaseModel`.



```python
track: str
model_name: str
eval_accuracy: Optional[float] = None
baseline_accuracy: Optional[float] = None
```

## backend/app/routers/feedback.py — FixOutcomeRequest

[backend/app/routers/feedback.py:220](../../backend/app/routers/feedback.py#L220)

Bases: `BaseModel`.

AI-5: outcome of a fix informed by TestLookup's diagnosis.

``outcome`` is a closed vocabulary validated here (the values map onto
FeedbackRating in the service); ``fingerprint`` is the stable test
identity (sha256(class::test)[:16]) the analytics surface uses.

```python
fingerprint: str = Field(..., min_length=1, max_length=64)
outcome: Literal['fixed', 'not_fixed', 'reverted']
reference: Optional[str] = Field(default=None, max_length=500)
comment: Optional[str] = Field(default=None, max_length=4000)
```

## backend/app/routers/feedback.py — AnalysisLookupResponse

[backend/app/routers/feedback.py:233](../../backend/app/routers/feedback.py#L233)

Bases: `BaseModel`.

US-2.4: latest AI analysis for a (project, fingerprint) pair.

All fields are ``None`` when the test has never been analysed — the UI
renders a "no AI analysis recorded yet" empty state instead of a 404
(which axios would surface as a scary error toast).

``failure_category`` is a plain string, NOT the ``FailureCategory``
enum: the backing column is ``String(30)`` and a strict enum here would
silently 422 the response if a stored value ever drifts out of vocab
(backend/CLAUDE.md pitfall).

```python
analysis_id: Optional[uuid.UUID] = None
failure_category: Optional[str] = None
analyzed_at: Optional[datetime] = None
```

## backend/app/routers/fixer.py — FixerRunner

[backend/app/routers/fixer.py:102](../../backend/app/routers/fixer.py#L102)

Bases: `BaseModel`.



```python
type: str = 'none'
runner_image: Optional[str] = None
command_template: Optional[str] = None
workflow_ref: Optional[str] = None
```

- Validator/serializer `_valid_type`: [backend/app/routers/fixer.py:110](../../backend/app/routers/fixer.py#L110). Read source for the cross-field or conversion rule.
- Validator/serializer `_valid_image`: [backend/app/routers/fixer.py:117](../../backend/app/routers/fixer.py#L117). Read source for the cross-field or conversion rule.
## backend/app/routers/fixer.py — FixerBudgets

[backend/app/routers/fixer.py:128](../../backend/app/routers/fixer.py#L128)

Bases: `BaseModel`.



```python
max_tests_per_run: int = Field(default=3, ge=0, le=1000)
max_attempts_per_test: int = Field(default=2, ge=0, le=100)
validation_reruns: int = Field(default=5, ge=1, le=100)
max_concurrent_open_prs: int = Field(default=2, ge=0, le=100)
```

## backend/app/routers/fixer.py — FixerConfigUpdate

[backend/app/routers/fixer.py:135](../../backend/app/routers/fixer.py#L135)

Bases: `BaseModel`.



```python
enabled: bool = False
mode: str = 'shadow'
runner: FixerRunner = Field(default_factory=FixerRunner)
test_globs: list[str] = Field(default_factory=lambda: ['tests/**', '**/*.spec.*', '**/*.test.*'])
budgets: FixerBudgets = Field(default_factory=FixerBudgets)
schedule: str = 'off'
```

- Validator/serializer `_valid_mode`: [backend/app/routers/fixer.py:145](../../backend/app/routers/fixer.py#L145). Read source for the cross-field or conversion rule.
- Validator/serializer `_valid_schedule`: [backend/app/routers/fixer.py:152](../../backend/app/routers/fixer.py#L152). Read source for the cross-field or conversion rule.
## backend/app/routers/live.py — WsSession

[backend/app/routers/live.py:52](../../backend/app/routers/live.py#L52)

Bases: ``.

Per-connection auth state held for the lifetime of the WebSocket.

```python
user_id: _uuid.UUID
user_name: Optional[str]
token_exp: float
last_event_id: Optional[str] = None
```

## backend/app/routers/observability.py — FrontendError

[backend/app/routers/observability.py:27](../../backend/app/routers/observability.py#L27)

Bases: `BaseModel`.



```python
type: str
message: str
stack: Optional[str] = None
component_stack: Optional[str] = None
url: str
user_agent: Optional[str] = None
timestamp: Optional[str] = None
context: Optional[dict[str, Any]] = None
```

- Validator/serializer `truncate_message`: [backend/app/routers/observability.py:39](../../backend/app/routers/observability.py#L39). Read source for the cross-field or conversion rule.
- Validator/serializer `truncate_stack`: [backend/app/routers/observability.py:44](../../backend/app/routers/observability.py#L44). Read source for the cross-field or conversion rule.
## backend/app/routers/observability.py — WebVitalReport

[backend/app/routers/observability.py:48](../../backend/app/routers/observability.py#L48)

Bases: `BaseModel`.



```python
name: str
value: float
rating: str
url: str
delta: Optional[float] = None
```

## backend/app/routers/observability.py — FrontendTelemetryBatch

[backend/app/routers/observability.py:56](../../backend/app/routers/observability.py#L56)

Bases: `BaseModel`.



```python
errors: List[FrontendError] = []
vitals: List[WebVitalReport] = []
```

## backend/app/routers/onboarding.py — StepAction

[backend/app/routers/onboarding.py:33](../../backend/app/routers/onboarding.py#L33)

Bases: `BaseModel`.



```python
step_key: str
```

## backend/app/routers/onboarding.py — TrackEventRequest

[backend/app/routers/onboarding.py:37](../../backend/app/routers/onboarding.py#L37)

Bases: `BaseModel`.



```python
event_name: str
project_id: Optional[str] = None
payload: Optional[dict] = None
```

## backend/app/routers/release_attribution_rules.py — AttributionRuleIn

[backend/app/routers/release_attribution_rules.py:66](../../backend/app/routers/release_attribution_rules.py#L66)

Bases: `BaseModel`.



```python
name: str = Field(..., min_length=1, max_length=255)
match_field: str
match_pattern: str = Field(..., min_length=1, max_length=255)
target_release_name: str = Field(..., min_length=1, max_length=255)
priority: int = Field(100, ge=0, le=10000)
is_enabled: bool = True
```

## backend/app/routers/release_attribution_rules.py — AttributionRuleUpdate

[backend/app/routers/release_attribution_rules.py:84](../../backend/app/routers/release_attribution_rules.py#L84)

Bases: `BaseModel`.



```python
name: Optional[str] = Field(None, min_length=1, max_length=255)
match_field: Optional[str] = None
match_pattern: Optional[str] = Field(None, min_length=1, max_length=255)
target_release_name: Optional[str] = Field(None, min_length=1, max_length=255)
priority: Optional[int] = Field(None, ge=0, le=10000)
is_enabled: Optional[bool] = None
```

## backend/app/routers/release_readiness.py — ReleaseDecisionResponse

[backend/app/routers/release_readiness.py:32](../../backend/app/routers/release_readiness.py#L32)

Bases: `BaseModel`.



```python
run_id: str
recommendation: str
risk_score: int
blocking_issues: list[str]
conditions_for_go: list[str]
reasoning: Optional[str]
human_override: Optional[str]
pass_rate: Optional[float] = None
build_number: Optional[str] = None
```

## backend/app/routers/releases.py — PhaseIn

[backend/app/routers/releases.py:42](../../backend/app/routers/releases.py#L42)

Bases: `BaseModel`.



```python
name: str
phase_type: str = 'qa_testing'
status: str = 'pending'
description: Optional[str] = None
order_index: int = 0
planned_start: Optional[datetime] = None
planned_end: Optional[datetime] = None
actual_start: Optional[datetime] = None
actual_end: Optional[datetime] = None
exit_criteria: Optional[dict] = None
notes: Optional[str] = None
```

## backend/app/routers/releases.py — PhaseUpdate

[backend/app/routers/releases.py:56](../../backend/app/routers/releases.py#L56)

Bases: `BaseModel`.



```python
name: Optional[str] = None
phase_type: Optional[str] = None
status: Optional[str] = None
gate_override_reason: Optional[str] = Field(None, min_length=3, max_length=1000)
skip_reason: Optional[str] = Field(None, min_length=3, max_length=1000)
description: Optional[str] = None
order_index: Optional[int] = None
planned_start: Optional[datetime] = None
planned_end: Optional[datetime] = None
actual_start: Optional[datetime] = None
actual_end: Optional[datetime] = None
exit_criteria: Optional[dict] = None
notes: Optional[str] = None
```

## backend/app/routers/releases.py — ReleaseIn

[backend/app/routers/releases.py:89](../../backend/app/routers/releases.py#L89)

Bases: `BaseModel`.



```python
project_id: str
name: str
version: Optional[str] = None
description: Optional[str] = None
status: str = 'planning'
planned_date: Optional[datetime] = None
phases: list[PhaseIn] = []
release_type: Optional[ReleaseType] = None
target_environment: Optional[str] = Field(None, max_length=100)
cutoff_start_at: Optional[datetime] = None
cutoff_end_at: Optional[datetime] = None
baseline_release_id: Optional[str] = None
```

## backend/app/routers/releases.py — ReleaseUpdate

[backend/app/routers/releases.py:116](../../backend/app/routers/releases.py#L116)

Bases: `BaseModel`.



```python
name: Optional[str] = None
version: Optional[str] = None
description: Optional[str] = None
status: Optional[str] = None
planned_date: Optional[datetime] = None
released_at: Optional[datetime] = None
release_type: Optional[ReleaseType] = None
target_environment: Optional[str] = Field(None, max_length=100)
cutoff_start_at: Optional[datetime] = None
cutoff_end_at: Optional[datetime] = None
baseline_release_id: Optional[str] = None
```

## backend/app/routers/releases.py — LinkRunRequest

[backend/app/routers/releases.py:130](../../backend/app/routers/releases.py#L130)

Bases: `BaseModel`.



```python
test_run_id: str
phase_id: Optional[str] = None
```

## backend/app/routers/releases.py — ReleaseSyncIn

[backend/app/routers/releases.py:135](../../backend/app/routers/releases.py#L135)

Bases: `BaseModel`.



```python
project_id: str
source: Literal['github', 'jira']
jira_project_key: Optional[str] = None
```

## backend/app/routers/releases.py — ReleaseOutcomeIn

[backend/app/routers/releases.py:145](../../backend/app/routers/releases.py#L145)

Bases: `BaseModel`.

A production outcome reported by a release owner for G5 drift.

```python
model_config = ConfigDict(extra='forbid')
outcome: Literal['incident', 'rollback']
reason: str = Field(min_length=3, max_length=2000)
```

- Validator/serializer `reason_must_contain_text`: [backend/app/routers/releases.py:155](../../backend/app/routers/releases.py#L155). Read source for the cross-field or conversion rule.
## backend/app/routers/reports.py — EmailTrendsRequest

[backend/app/routers/reports.py:32](../../backend/app/routers/reports.py#L32)

Bases: `BaseModel`.



```python
project_id: str
days: int = 30
release_id: str | None = None
recipient_email: EmailStr
chart_ids: list[str] = []
```

## backend/app/routers/reports.py — CreateShareLinkRequest

[backend/app/routers/reports.py:180](../../backend/app/routers/reports.py#L180)

Bases: `BaseModel`.



```python
layout: str = Field(default='executive', pattern='^(executive|engineering)$')
expiry_days: int = Field(default=7, ge=1, le=30)
```

## backend/app/routers/reports.py — ShareLinkResponse

[backend/app/routers/reports.py:185](../../backend/app/routers/reports.py#L185)

Bases: `BaseModel`.



```python
id: str
token: Optional[str] = None
share_url: Optional[str] = None
report_layout: str
expires_at: str
created_by_name: Optional[str] = None
access_count: int = 0
is_revoked: bool = False
created_at: str
snapshot_ready: Optional[bool] = None
warning: Optional[str] = None
```

## backend/app/routers/reviews.py — ReviewResponse

[backend/app/routers/reviews.py:55](../../backend/app/routers/reviews.py#L55)

Bases: `BaseModel`.

A review request as clients see it. No reviewer identity (section 8.2).

```python
model_config = ConfigDict(from_attributes=True)
id: uuid.UUID
project_id: uuid.UUID
kind: str
subject_type: str
subject_id: str
pipeline_run_id: Optional[uuid.UUID] = None
test_run_id: Optional[uuid.UUID] = None
workflow_type: Optional[str] = None
state: ReviewState
reviewed: bool
reviewed_at: Optional[datetime] = None
reason_code: Optional[str] = None
notes: Optional[str] = None
evidence_bundle_sha256: Optional[str] = None
superseded_by: Optional[uuid.UUID] = None
created_at: datetime
requires_human_review: bool = True
ai_disclaimer: str
ai_disclaimer_version: str
```

## backend/app/routers/reviews.py — AcceptReviewRequest

[backend/app/routers/reviews.py:81](../../backend/app/routers/reviews.py#L81)

Bases: `BaseModel`.



```python
notes: Optional[str] = Field(default=None, max_length=4000)
```

## backend/app/routers/reviews.py — RejectReviewRequest

[backend/app/routers/reviews.py:85](../../backend/app/routers/reviews.py#L85)

Bases: `BaseModel`.



```python
reason_code: ReasonCode
notes: Optional[str] = Field(default=None, max_length=4000)
```

## backend/app/services/action_policy.py — ActionStatus

[backend/app/services/action_policy.py:50](../../backend/app/services/action_policy.py#L50)

Bases: `str, PyEnum`.



```python
SUGGESTED = 'suggested'
PENDING_REVIEW = 'pending_review'
APPROVED = 'approved'
EXECUTED = 'executed'
REJECTED = 'rejected'
```

## backend/app/services/action_policy.py — ActionType

[backend/app/services/action_policy.py:58](../../backend/app/services/action_policy.py#L58)

Bases: `str, PyEnum`.



```python
DEFECT_PROMOTION = 'defect_promotion'
JIRA_TICKET_CREATION = 'jira_ticket_creation'
RELEASE_OVERRIDE = 'release_override'
```

## backend/app/services/activity/events.py — ActivityEventSpec

[backend/app/services/activity/events.py:134](../../backend/app/services/activity/events.py#L134)

Bases: ``.

One registered event.

``write_mode`` picks the durability contract, and the choice is per event
rather than per caller:

* ``outcome`` — the ledger row shares the caller's transaction, so it dies
  with a rollback. Right when the row only makes sense if the primary
  mutation actually succeeded ("policy updated").
* ``attempt`` — the ledger row commits on its own session and survives a
  caller rollback. Right when the ATTEMPT is the thing worth recording
  ("a full project reset was issued"), even if it later fails midway.

```python
event_type: str
category: str
entity_type: str
summary_template: str
write_mode: str = 'outcome'
actor_types: frozenset[str] = _ALL_ACTORS
```

## backend/app/services/activity/query.py — ActivityFilters

[backend/app/services/activity/query.py:54](../../backend/app/services/activity/query.py#L54)

Bases: ``.

The feed's filter set.

A dataclass rather than twelve ``Query(...)`` parameters on the handler,
and that is deliberate: a ``Query(None)`` default is returned as the
``Query`` OBJECT (not ``None``) when a test calls the handler directly,
which has broken direct-call tests in this repo five separate times. One
dependency object has one shape whether it is built by FastAPI or by a test.

```python
categories: Sequence[str] = field(default_factory=tuple)
event_types: Sequence[str] = field(default_factory=tuple)
actor_id: Optional[uuid.UUID] = None
actor_type: Optional[str] = None
entity_type: Optional[str] = None
entity_id: Optional[str] = None
release_id: Optional[uuid.UUID] = None
since: Optional[datetime] = None
until: Optional[datetime] = None
q: Optional[str] = None
limit: int = DEFAULT_LIMIT
cursor: Optional[str] = None
```

## backend/app/services/activity/service.py — ActorRef

[backend/app/services/activity/service.py:81](../../backend/app/services/activity/service.py#L81)

Bases: ``.

Who did the thing.

Deliberately a plain class rather than a Pydantic model: this is built on
every mutation, including inside Celery tasks that have no request context,
and it must never be able to raise a validation error on the write path.

```python
__slots__ = ('actor_type', 'actor_id', 'actor_name', 'actor_ref')
```

## backend/app/services/agent_catalog.py — SubjectRef

[backend/app/services/agent_catalog.py:56](../../backend/app/services/agent_catalog.py#L56)

Bases: `BaseModel`.

Invoke an agent on a stored subject; the server assembles the input from it.

```python
model_config = ConfigDict(extra='forbid')
test_run_id: uuid.UUID
```

## backend/app/services/agent_catalog.py — AgentCatalogEntry

[backend/app/services/agent_catalog.py:64](../../backend/app/services/agent_catalog.py#L64)

Bases: `BaseModel`.



```python
agent_id: str
version: int
stage_name: str
permission: str
execution: str
default_tier: Literal['deterministic', 'slm', 'llm']
escalation: list[Literal['validation_failure', 'low_confidence', 'not_enough_evidence', 'contradictions', 'multi_artifact_evidence']]
sync_eligible: bool = Field(description='May be invoked synchronously: deterministic and expected to finish in 5 s or less.')
invokable: bool = Field(description='Can be invoked independently; false means the capability requires a surrounding workflow.')
produces_report: bool = Field(description='Its output is a report contract, so a run of it needs human review (E8).')
input_schema: str
output_schema: str
input_schema_resolved: bool = Field(description='False when the input schema is a label with no model yet; the wrapper then accepts only a SubjectRef.')
output_schema_resolved: bool
dependencies: list[str]
required_evidence: list[str]
expected_latency_ms: int
expected_cost_usd: float
timeout_seconds: int
max_retries: int
fallback: str
concurrency_class: str
```

## backend/app/services/agent_catalog.py — AgentCatalogDetail

[backend/app/services/agent_catalog.py:105](../../backend/app/services/agent_catalog.py#L105)

Bases: `AgentCatalogEntry`.



```python
input_wrapper: str
input_json_schema: dict[str, Any]
output_json_schema: Optional[dict[str, Any]] = None
```

## backend/app/services/agent_config_resolver.py — Clamp

[backend/app/services/agent_config_resolver.py:66](../../backend/app/services/agent_config_resolver.py#L66)

Bases: `BaseModel`.

One value the resolver changed, and which layer forced the change.

```python
field: str
layer: str
requested: Any = None
effective: Any = None
reason: str
```

## backend/app/services/agent_config_resolver.py — ResolvedEndpoint

[backend/app/services/agent_config_resolver.py:76](../../backend/app/services/agent_config_resolver.py#L76)

Bases: `BaseModel`.



```python
provider: str
model: str
temperature: float
max_tokens: int
base_url: Optional[str] = None
source: str = Field(description="project: the agent config's own block; ai_config: inherited from the global config")
```

## backend/app/services/agent_config_resolver.py — ResolvedAgentConfig

[backend/app/services/agent_config_resolver.py:85](../../backend/app/services/agent_config_resolver.py#L85)

Bases: `BaseModel`.



```python
agent_id: str
source: str = Field(description='default when the project has no row')
config_version: int
patched: bool = False
config: AgentConfigV1
endpoints: dict[str, Optional[ResolvedEndpoint]]
offline_mode: bool
offline_mode_source: str
offline_mode_env_pinned: bool
clamps: list[Clamp] = Field(default_factory=list)
```

## backend/app/services/agent_config_resolver.py — FrozenAgentConfig

[backend/app/services/agent_config_resolver.py:98](../../backend/app/services/agent_config_resolver.py#L98)

Bases: `BaseModel`.

Durable, credential-free project/request configuration for an invocation.

```python
model_config = ConfigDict(extra='forbid')
schema_version: Literal[1] = 1
agent_id: str
source: str
config_version: int
patched: bool
config: dict[str, Any]
unavailable_tiers: list[Literal['slm', 'llm']] = Field(default_factory=list)
clamps: list[Clamp] = Field(default_factory=list)
```

## backend/app/services/agent_config_service.py — _Strict

[backend/app/services/agent_config_service.py:144](../../backend/app/services/agent_config_service.py#L144)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
```

## backend/app/services/agent_config_service.py — ModelEndpointConfig

[backend/app/services/agent_config_service.py:148](../../backend/app/services/agent_config_service.py#L148)

Bases: `_Strict`.

One tier's model. Provider endpoints come only from the environment.

```python
provider: str = Field(min_length=1, max_length=40)
model: str = Field(min_length=1, max_length=200)
temperature: float = Field(default=0.0, ge=0.0, le=2.0)
max_tokens: int = Field(default=1024, ge=1, le=200000)
```

- Validator/serializer `_known_provider`: [backend/app/services/agent_config_service.py:158](../../backend/app/services/agent_config_service.py#L158). Read source for the cross-field or conversion rule.
## backend/app/services/agent_config_service.py — EscalationConfig

[backend/app/services/agent_config_service.py:165](../../backend/app/services/agent_config_service.py#L165)

Bases: `_Strict`.



```python
on_validation_failure: bool = True
on_confidence_below: int = Field(default=70, ge=0, le=100)
max_escalations: int = Field(default=1, ge=0, le=3)
```

## backend/app/services/agent_config_service.py — ModelConfig

[backend/app/services/agent_config_service.py:171](../../backend/app/services/agent_config_service.py#L171)

Bases: `_Strict`.



```python
tier: Tier = 'auto'
slm: Optional[ModelEndpointConfig] = None
llm: Optional[ModelEndpointConfig] = None
escalation: EscalationConfig = Field(default_factory=EscalationConfig)
```

## backend/app/services/agent_config_service.py — ThresholdsConfig

[backend/app/services/agent_config_service.py:178](../../backend/app/services/agent_config_service.py#L178)

Bases: `_Strict`.



```python
confidence_min: int = Field(default=80, ge=0, le=100)
max_failures_analyzed: int = Field(default=50, ge=1, le=1000)
degraded_ratio: float = Field(default=0.3, ge=0.0, le=1.0)
```

## backend/app/services/agent_config_service.py — RetryConfig

[backend/app/services/agent_config_service.py:184](../../backend/app/services/agent_config_service.py#L184)

Bases: `_Strict`.



```python
max_attempts: int = Field(default_factory=lambda: settings.AGENT_PIPELINE_MAX_ATTEMPTS, ge=1)
base_seconds: int = Field(default_factory=lambda: settings.AGENT_RETRY_BASE_SECONDS, ge=1)
cap_seconds: int = Field(default_factory=lambda: settings.AGENT_RETRY_CAP_SECONDS, ge=1)
jitter: float = Field(default=0.2, ge=0.0, lt=1.0)
retry_on: list[str] = Field(default_factory=lambda: ['model_unavailable', 'timeout', 'tool_error'])
```

- Validator/serializer `_retryable_codes`: [backend/app/services/agent_config_service.py:193](../../backend/app/services/agent_config_service.py#L193). Read source for the cross-field or conversion rule.
- Validator/serializer `_ceiling_and_cap`: [backend/app/services/agent_config_service.py:200](../../backend/app/services/agent_config_service.py#L200). Read source for the cross-field or conversion rule.
## backend/app/services/agent_config_service.py — ToolsConfig

[backend/app/services/agent_config_service.py:214](../../backend/app/services/agent_config_service.py#L214)

Bases: `_Strict`.



```python
allowlist: list[str] = Field(default_factory=list)
```

- Validator/serializer `_known_tools`: [backend/app/services/agent_config_service.py:219](../../backend/app/services/agent_config_service.py#L219). Read source for the cross-field or conversion rule.
## backend/app/services/agent_config_service.py — BudgetConfig

[backend/app/services/agent_config_service.py:226](../../backend/app/services/agent_config_service.py#L226)

Bases: `_Strict`.

Budget names are the existing ``DEFAULT_BUDGETS`` keys (section 4.2).

```python
max_llm_calls_per_run: int = Field(default=int(DEFAULT_BUDGETS['max_llm_calls_per_run']), ge=0)
max_tokens_per_run: int = Field(default=int(DEFAULT_BUDGETS['max_tokens_per_run']), ge=0)
max_cost_usd_per_run: float = Field(default=float(DEFAULT_BUDGETS['max_cost_usd_per_run']), ge=0.0)
max_runs_per_day: int = Field(default=int(DEFAULT_BUDGETS['max_runs_per_day']), ge=0)
```

## backend/app/services/agent_config_service.py — ShadowConfig

[backend/app/services/agent_config_service.py:235](../../backend/app/services/agent_config_service.py#L235)

Bases: `_Strict`.



```python
sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
daily_token_budget: int = Field(default=200000, ge=0)
```

## backend/app/services/agent_config_service.py — ReviewConfig

[backend/app/services/agent_config_service.py:240](../../backend/app/services/agent_config_service.py#L240)

Bases: `_Strict`.



```python
policy: Literal['human_required', 'human_required_plus_auto_reviewer'] = 'human_required'
auto_reviewer: bool = False
second_model_check: bool = False
```

- Validator/serializer `_policy_names_the_reviewer`: [backend/app/services/agent_config_service.py:246](../../backend/app/services/agent_config_service.py#L246). Read source for the cross-field or conversion rule.
## backend/app/services/agent_config_service.py — OverridePolicyConfig

[backend/app/services/agent_config_service.py:252](../../backend/app/services/agent_config_service.py#L252)

Bases: `_Strict`.



```python
allow_tier_downgrade: bool = True
allow_retry_decrease: bool = True
allow_tool_narrowing: bool = True
```

## backend/app/services/agent_config_service.py — InvestigatorPolicyBudgets

[backend/app/services/agent_config_service.py:258](../../backend/app/services/agent_config_service.py#L258)

Bases: `_Strict`.

The complete legacy Investigator budget contract preserved by E4.4.

```python
max_runs_per_day: int = Field(default=10, ge=0, le=10000)
max_llm_calls_per_run: int = Field(default=30, ge=0, le=100000)
max_tokens_per_run: int = Field(default=60000, ge=0, le=100000000)
max_cost_usd_per_run: float = Field(default=5.0, ge=0, le=1000000)
max_seconds_per_run: int = Field(default=300, ge=0, le=86400)
max_cluster_children_per_run: int = Field(default=1, ge=0, le=20)
max_cluster_members_per_child: int = Field(default=50, ge=1, le=500)
max_cluster_child_llm_calls_per_parent: int = Field(default=6, ge=0, le=1000)
max_cluster_child_tokens_per_parent: int = Field(default=12000, ge=0, le=10000000)
max_cluster_child_cost_usd_per_parent: float = Field(default=2.0, ge=0, le=1000000)
max_cluster_child_seconds_per_parent: int = Field(default=180, ge=0, le=86400)
max_active_cluster_children_per_project: int = Field(default=2, ge=0, le=20)
max_cluster_children_per_day: int = Field(default=20, ge=0, le=1000)
```

## backend/app/services/agent_config_service.py — InvestigatorConfigExtension

[backend/app/services/agent_config_service.py:276](../../backend/app/services/agent_config_service.py#L276)

Bases: `_Strict`.



```python
budgets: InvestigatorPolicyBudgets = Field(default_factory=InvestigatorPolicyBudgets)
shadow_runs_completed: int = Field(default=0, ge=0)
promotion_note: Optional[str] = Field(default=None, max_length=2000)
```

## backend/app/services/agent_config_service.py — FixerRunnerExtension

[backend/app/services/agent_config_service.py:282](../../backend/app/services/agent_config_service.py#L282)

Bases: `_Strict`.



```python
type: str = 'none'
runner_image: Optional[str] = None
command_template: Optional[str] = None
workflow_ref: Optional[str] = None
```

- Validator/serializer `_known_runner_type`: [backend/app/services/agent_config_service.py:290](../../backend/app/services/agent_config_service.py#L290). Read source for the cross-field or conversion rule.
- Validator/serializer `_safe_runner_image`: [backend/app/services/agent_config_service.py:297](../../backend/app/services/agent_config_service.py#L297). Read source for the cross-field or conversion rule.
## backend/app/services/agent_config_service.py — FixerPolicyBudgets

[backend/app/services/agent_config_service.py:303](../../backend/app/services/agent_config_service.py#L303)

Bases: `_Strict`.



```python
max_tests_per_run: int = Field(default=int(DEFAULT_FIXER_BUDGETS['max_tests_per_run']), ge=0, le=1000)
max_attempts_per_test: int = Field(default=int(DEFAULT_FIXER_BUDGETS['max_attempts_per_test']), ge=0, le=100)
validation_reruns: int = Field(default=int(DEFAULT_FIXER_BUDGETS['validation_reruns']), ge=1, le=100)
max_concurrent_open_prs: int = Field(default=int(DEFAULT_FIXER_BUDGETS['max_concurrent_open_prs']), ge=0, le=100)
```

## backend/app/services/agent_config_service.py — FixerConfigExtension

[backend/app/services/agent_config_service.py:312](../../backend/app/services/agent_config_service.py#L312)

Bases: `_Strict`.



```python
runner: FixerRunnerExtension = Field(default_factory=FixerRunnerExtension)
test_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))
budgets: FixerPolicyBudgets = Field(default_factory=FixerPolicyBudgets)
schedule: str = 'off'
```

- Validator/serializer `_non_empty_globs`: [backend/app/services/agent_config_service.py:320](../../backend/app/services/agent_config_service.py#L320). Read source for the cross-field or conversion rule.
- Validator/serializer `_known_schedule`: [backend/app/services/agent_config_service.py:328](../../backend/app/services/agent_config_service.py#L328). Read source for the cross-field or conversion rule.
## backend/app/services/agent_config_service.py — AgentConfigExtensions

[backend/app/services/agent_config_service.py:334](../../backend/app/services/agent_config_service.py#L334)

Bases: `_Strict`.

Typed homes for pre-registry contracts migrated from agent_policies.

```python
investigator: Optional[InvestigatorConfigExtension] = None
fixer: Optional[FixerConfigExtension] = None
```

## backend/app/services/agent_config_service.py — AgentConfigV1

[backend/app/services/agent_config_service.py:341](../../backend/app/services/agent_config_service.py#L341)

Bases: `_Strict`.

A project's configuration of one agent (section 4.2).

```python
agent_id: str
enabled: bool = True
mode: Mode = 'shadow'
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
```

- Validator/serializer `_registered`: [backend/app/services/agent_config_service.py:360](../../backend/app/services/agent_config_service.py#L360). Read source for the cross-field or conversion rule.
- Validator/serializer `_section_4_3`: [backend/app/services/agent_config_service.py:366](../../backend/app/services/agent_config_service.py#L366). Read source for the cross-field or conversion rule.
## backend/app/services/agent_config_service.py — _TierPatch

[backend/app/services/agent_config_service.py:457](../../backend/app/services/agent_config_service.py#L457)

Bases: `_Strict`.



```python
tier: Tier
```

## backend/app/services/agent_config_service.py — _RetryPatch

[backend/app/services/agent_config_service.py:461](../../backend/app/services/agent_config_service.py#L461)

Bases: `_Strict`.



```python
max_attempts: int = Field(ge=1)
```

## backend/app/services/agent_config_service.py — _ThresholdsPatch

[backend/app/services/agent_config_service.py:465](../../backend/app/services/agent_config_service.py#L465)

Bases: `_Strict`.



```python
max_failures_analyzed: int = Field(ge=1)
```

## backend/app/services/agent_config_service.py — _ToolsPatch

[backend/app/services/agent_config_service.py:469](../../backend/app/services/agent_config_service.py#L469)

Bases: `_Strict`.



```python
allowlist: list[str]
```

## backend/app/services/agent_config_service.py — _BudgetPatch

[backend/app/services/agent_config_service.py:473](../../backend/app/services/agent_config_service.py#L473)

Bases: `_Strict`.



```python
max_llm_calls_per_run: Optional[int] = Field(default=None, ge=0)
max_tokens_per_run: Optional[int] = Field(default=None, ge=0)
max_cost_usd_per_run: Optional[float] = Field(default=None, ge=0.0)
max_runs_per_day: Optional[int] = Field(default=None, ge=0)
```

## backend/app/services/agent_config_service.py — _ReviewPatch

[backend/app/services/agent_config_service.py:480](../../backend/app/services/agent_config_service.py#L480)

Bases: `_Strict`.



```python
add_auto_reviewer: bool = False
```

## backend/app/services/agent_config_service.py — AgentConfigPatch

[backend/app/services/agent_config_service.py:484](../../backend/app/services/agent_config_service.py#L484)

Bases: `_Strict`.

What one request may override. Every field may only tighten the project layer.

``mode``, ``temperature``, ``max_tokens``, ``escalation``, most thresholds and
any provider or endpoint are absent on purpose: ``extra="forbid"`` rejects them.

```python
model: Optional[_TierPatch] = None
retry: Optional[_RetryPatch] = None
timeout_seconds: Optional[int] = Field(default=None, ge=1)
thresholds: Optional[_ThresholdsPatch] = None
tools: Optional[_ToolsPatch] = None
budget: Optional[_BudgetPatch] = None
review: Optional[_ReviewPatch] = None
```

## backend/app/services/agent_eval_harness.py — AgentEvalSample

[backend/app/services/agent_eval_harness.py:75](../../backend/app/services/agent_eval_harness.py#L75)

Bases: `BaseModel`.

A single recorded agent output paired with its ground truth.

```python
model_config = ConfigDict(extra='ignore')
sample_id: str = ''
agent_name: str = ''
verdict: str = ''
confidence_score: int = 0
evidence_count: int = 0
decision_reason: str = ''
recommended_actions: list[str] = Field(default_factory=list)
ground_truth_verdict: str = ''
is_flaky_truth: bool = False
```

- Validator/serializer `_coerce_str`: [backend/app/services/agent_eval_harness.py:92](../../backend/app/services/agent_eval_harness.py#L92). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_verdict`: [backend/app/services/agent_eval_harness.py:99](../../backend/app/services/agent_eval_harness.py#L99). Read source for the cross-field or conversion rule.
- Validator/serializer `_clamp_confidence`: [backend/app/services/agent_eval_harness.py:109](../../backend/app/services/agent_eval_harness.py#L109). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_evidence_count`: [backend/app/services/agent_eval_harness.py:122](../../backend/app/services/agent_eval_harness.py#L122). Read source for the cross-field or conversion rule.
- Validator/serializer `_coerce_actions`: [backend/app/services/agent_eval_harness.py:131](../../backend/app/services/agent_eval_harness.py#L131). Read source for the cross-field or conversion rule.
## backend/app/services/agent_eval_harness.py — AgentEvalReport

[backend/app/services/agent_eval_harness.py:324](../../backend/app/services/agent_eval_harness.py#L324)

Bases: `BaseModel`.

Aggregated agent-output quality report with per-metric pass/fail.

```python
model_config = ConfigDict(extra='ignore')
coherence: float | None = None
completeness: float | None = None
actionability: float | None = None
accuracy: float | None = None
brier: float | None = None
ece: float | None = None
sample_count: int = 0
per_metric_pass: dict[str, bool] = Field(default_factory=dict)
passed: bool = False
verdict: EvalVerdict = EvalVerdict.INSUFFICIENT_SAMPLES
detail: dict[str, Any] = Field(default_factory=dict)
```

## backend/app/services/agent_eval_samples.py — LabelKind

[backend/app/services/agent_eval_samples.py:32](../../backend/app/services/agent_eval_samples.py#L32)

Bases: `StrEnum`.



```python
CLASSIFICATION = 'classification'
STRUCTURED = 'structured'
NARRATIVE = 'narrative'
```

## backend/app/services/agent_eval_samples.py — SemanticClass

[backend/app/services/agent_eval_samples.py:38](../../backend/app/services/agent_eval_samples.py#L38)

Bases: `StrEnum`.



```python
PRODUCT_DEFECT = 'product_defect'
INFRASTRUCTURE = 'infrastructure'
AUTOMATION_DEFECT = 'automation_defect'
TEST_DATA = 'test_data'
FLAKY = 'flaky'
```

## backend/app/services/agent_eval_samples.py — MutationClass

[backend/app/services/agent_eval_samples.py:46](../../backend/app/services/agent_eval_samples.py#L46)

Bases: `StrEnum`.



```python
WRONG_LABEL = 'wrong_label'
MISSING_REQUIRED_FIELD = 'missing_required_field'
UNSUPPORTED_CLAIM = 'unsupported_claim'
UNSUPPORTED_CAUSAL_CLAIM = 'unsupported_causal_claim'
PLAUSIBLE_WRONG_CATEGORY = 'plausible_wrong_category'
CORRECT_NUMBERS_WRONG_CONCLUSION = 'correct_numbers_wrong_conclusion'
```

## backend/app/services/agent_eval_samples.py — EvalContract

[backend/app/services/agent_eval_samples.py:55](../../backend/app/services/agent_eval_samples.py#L55)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid', frozen=True)
```

## backend/app/services/agent_eval_samples.py — ClassificationLabelV1

[backend/app/services/agent_eval_samples.py:59](../../backend/app/services/agent_eval_samples.py#L59)

Bases: `EvalContract`.



```python
kind: Literal[LabelKind.CLASSIFICATION] = LabelKind.CLASSIFICATION
value: str = Field(min_length=1)
confidence_floor: float = Field(default=0.7, ge=0, le=1)
```

## backend/app/services/agent_eval_samples.py — StructuredLabelV1

[backend/app/services/agent_eval_samples.py:65](../../backend/app/services/agent_eval_samples.py#L65)

Bases: `EvalContract`.



```python
kind: Literal[LabelKind.STRUCTURED] = LabelKind.STRUCTURED
required_fields: tuple[str, ...] = Field(min_length=1)
exact_values: dict[str, Any] = Field(default_factory=dict)
```

## backend/app/services/agent_eval_samples.py — NarrativeLabelV1

[backend/app/services/agent_eval_samples.py:71](../../backend/app/services/agent_eval_samples.py#L71)

Bases: `EvalContract`.



```python
kind: Literal[LabelKind.NARRATIVE] = LabelKind.NARRATIVE
required_claims: tuple[str, ...] = Field(min_length=1)
prohibited_claims: tuple[str, ...] = ()
```

## backend/app/services/agent_eval_samples.py — CapabilityEvalSampleV1

[backend/app/services/agent_eval_samples.py:83](../../backend/app/services/agent_eval_samples.py#L83)

Bases: `EvalContract`.



```python
schema_version: Literal[1] = 1
sample_id: str = Field(min_length=1)
capability: str = Field(min_length=1)
input_schema: str = Field(min_length=1)
output_schema: str = Field(min_length=1)
semantic_class: SemanticClass
input_payload: dict[str, Any]
label: CapabilityLabelV1
source: str = Field(min_length=1)
```

- Validator/serializer `_matches_registered_contract`: [backend/app/services/agent_eval_samples.py:95](../../backend/app/services/agent_eval_samples.py#L95). Read source for the cross-field or conversion rule.
## backend/app/services/agent_eval_samples.py — EvalMutationV1

[backend/app/services/agent_eval_samples.py:106](../../backend/app/services/agent_eval_samples.py#L106)

Bases: `EvalContract`.



```python
sample_id: str
capability: str
mutation_class: MutationClass
mutated_label: dict[str, Any]
```

## backend/app/services/agent_step_tracing.py — _StepObservation

[backend/app/services/agent_step_tracing.py:30](../../backend/app/services/agent_step_tracing.py#L30)

Bases: ``.



```python
span: Any
tier: str
model: str = 'deterministic'
provider: str | None = None
input_tokens: int = 0
output_tokens: int = 0
llm_calls: int = 0
outcome: str = 'success'
```

## backend/app/services/analysis_router.py — AnalysisMode

[backend/app/services/analysis_router.py:42](../../backend/app/services/analysis_router.py#L42)

Bases: ``.



```python
LLM = 'llm'
ML = 'ml'
RULES = 'rules'
AUTO = 'auto'
```

## backend/app/services/codeowners_service.py — CodeownersEntry

[backend/app/services/codeowners_service.py:100](../../backend/app/services/codeowners_service.py#L100)

Bases: ``.

One effective CODEOWNERS line: the raw pattern and its owner tokens
(in file order; first is the primary owner).

```python
pattern: str
owners: list[str] = field(default_factory=list)
```

## backend/app/services/commit_attribution_service.py — _ConnectorTarget

[backend/app/services/commit_attribution_service.py:531](../../backend/app/services/commit_attribution_service.py#L531)

Bases: ``.

Plain-value connector coordinates read out of the DB up front so the
HTTP phase runs with no session open.

```python
api_base: str
repo: str
pat: str
```

## backend/app/services/compliance_pack_service.py — ExportScope

[backend/app/services/compliance_pack_service.py:632](../../backend/app/services/compliance_pack_service.py#L632)

Bases: ``.

What an archive covers.

The compliance pack was release-shaped throughout: the entry point took a
``Release``, the storage key was built from ``release_id``, and the
manifest named release fields directly. A retention export covers a set of
runs instead. This is the one description both understand, so there is one
builder rather than two — the epic's explicit warning, because a second
copy of the manifest chain is a second thing that can drift from the
verification steps the README tells an auditor to follow.

```python
kind: str
project_id: uuid.UUID
run_ids: tuple[uuid.UUID, ...]
tier: str
release_id: Optional[uuid.UUID] = None
```

## backend/app/services/confidence_bands.py — ConfidenceBand

[backend/app/services/confidence_bands.py:52](../../backend/app/services/confidence_bands.py#L52)

Bases: ``.

A named confidence level with a documented basis.

```python
rule_id: str
confidence: int
basis: str
provenance: str
```

## backend/app/services/connectors/base.py — FetchedContent

[backend/app/services/connectors/base.py:11](../../backend/app/services/connectors/base.py#L11)

Bases: ``.

Normalized output from any connector's fetch_content() call.

```python
raw_text: str
content_hash: str
title: str
source_url: str
metadata: dict[str, Any] = field(default_factory=dict)
attachments: list[str] = field(default_factory=list)
```

## backend/app/services/connectors/confluence_connector.py — ConfluenceKnowledgeConnector

[backend/app/services/connectors/confluence_connector.py:28](../../backend/app/services/connectors/confluence_connector.py#L28)

Bases: `KnowledgeConnectorBase`.

Handles confluence_page source type via Confluence REST API.

```python
connector_type = 'confluence_page'
```

## backend/app/services/connectors/document_connector.py — DocumentConnector

[backend/app/services/connectors/document_connector.py:20](../../backend/app/services/connectors/document_connector.py#L20)

Bases: `KnowledgeConnectorBase`.

Handles uploaded_document source type — downloads from MinIO and extracts text.

```python
connector_type = 'uploaded_document'
```

## backend/app/services/connectors/jira_connector.py — JiraKnowledgeConnector

[backend/app/services/connectors/jira_connector.py:25](../../backend/app/services/connectors/jira_connector.py#L25)

Bases: `KnowledgeConnectorBase`.

Handles jira_issue and jira_epic source types via Jira REST API v3.

```python
connector_type = 'jira_issue'
```

## backend/app/services/connectors/url_connector.py — URLConnector

[backend/app/services/connectors/url_connector.py:76](../../backend/app/services/connectors/url_connector.py#L76)

Bases: `KnowledgeConnectorBase`.

Handles internal_url and external_url source types via HTTP GET.

```python
connector_type = 'internal_url'
```

## backend/app/services/decision_report_eval_service.py — ReportEvalCheck

[backend/app/services/decision_report_eval_service.py:34](../../backend/app/services/decision_report_eval_service.py#L34)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
name: str = Field(min_length=1, max_length=100)
status: str = Field(pattern='^(pass|warn|fail|not_evaluated)$')
detail: dict[str, Any] = Field(default_factory=dict)
```

## backend/app/services/decision_report_eval_service.py — DecisionReportEvalResult

[backend/app/services/decision_report_eval_service.py:42](../../backend/app/services/decision_report_eval_service.py#L42)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
schema_version: int = REPORT_EVAL_SCHEMA_VERSION
status: str = Field(pattern='^(pass|warn|fail)$')
metrics: dict[str, Any] = Field(default_factory=dict)
thresholds: dict[str, float] = Field(default_factory=lambda: dict(REPORT_EVAL_THRESHOLDS))
checks: list[ReportEvalCheck] = Field(default_factory=list, max_length=20)
unavailable_metrics: list[str] = Field(default_factory=list, max_length=20)
```

## backend/app/services/decision_report_service.py — DecisionReportV1

[backend/app/services/decision_report_service.py:71](../../backend/app/services/decision_report_service.py#L71)

Bases: `BaseModel`.

Immutable, tenant/run/pipeline-bound published report.

```python
model_config = ConfigDict(frozen=True, extra='forbid')
report_id: str
project_id: str
test_run_id: str
pipeline_run_id: str
report_version: int = Field(ge=1)
status: Literal['published'] = 'published'
generated_at: datetime
supersedes_report_id: str | None = None
decision_intelligence: dict[str, Any]
verification: dict[str, Any]
markdown_report: str = Field(max_length=500000)
evidence_snapshot_id: str | None = None
evidence_bundle_sha256: str | None = None
schema_version: int = REPORT_SCHEMA_VERSION
```

## backend/app/services/decision_report_service.py — DecisionReportAttemptV1

[backend/app/services/decision_report_service.py:92](../../backend/app/services/decision_report_service.py#L92)

Bases: `BaseModel`.

Immutable rejected/failed terminal attempt; never a published report.

```python
model_config = ConfigDict(frozen=True, extra='forbid')
attempt_id: str
project_id: str
test_run_id: str
pipeline_run_id: str
status: Literal['rejected', 'failed']
attempted_at: datetime
reason: str = Field(max_length=8000)
verification: dict[str, Any]
supersedes_report_id: str | None = None
schema_version: int = REPORT_SCHEMA_VERSION
```

## backend/app/services/deletion_criteria.py — RetentionCriteria

[backend/app/services/deletion_criteria.py:63](../../backend/app/services/deletion_criteria.py#L63)

Bases: `BaseModel`.

What to delete. Every field is optional; at least one must be given.

``suite_match`` decides what "in this suite" means for a run that contains
several. ``only`` — the default — matches the run's own suite label and is
the conservative reading: it will not delete a multi-suite run because one
of its suites was selected. ``any`` matches a run with any test case in the
suite, and is the destructive reading, so it is opt-in.

```python
model_config = ConfigDict(extra='forbid')
date_from: Optional[datetime] = None
date_to: Optional[datetime] = None
older_than_days: Optional[int] = Field(None, ge=1, le=3650)
run_ids: Optional[list[uuid.UUID]] = Field(None, max_length=MAX_RUN_IDS)
statuses: Optional[list[str]] = None
suite_names: Optional[list[str]] = None
suite_match: Literal['only', 'any'] = 'only'
branches: Optional[list[str]] = None
environments: Optional[list[str]] = None
```

- Validator/serializer `_at_least_one_narrowing_field`: [backend/app/services/deletion_criteria.py:86](../../backend/app/services/deletion_criteria.py#L86). Read source for the cross-field or conversion rule.
- Validator/serializer `_statuses_use_the_columns_vocabulary`: [backend/app/services/deletion_criteria.py:95](../../backend/app/services/deletion_criteria.py#L95). Read source for the cross-field or conversion rule.
- Validator/serializer `_date_range_is_ordered`: [backend/app/services/deletion_criteria.py:108](../../backend/app/services/deletion_criteria.py#L108). Read source for the cross-field or conversion rule.
## backend/app/services/demo_dataset.py — DemoRun

[backend/app/services/demo_dataset.py:50](../../backend/app/services/demo_dataset.py#L50)

Bases: ``.

One synthetic run: an IngestPayload-shaped body plus its backdated time.

```python
build_number: str
started_at: datetime
branch: str
framework: str
results: list[dict[str, Any]] = field(default_factory=list)
```

## backend/app/services/duplicate_detection_service.py — _CaseView

[backend/app/services/duplicate_detection_service.py:308](../../backend/app/services/duplicate_detection_service.py#L308)

Bases: ``.

Pre-computed view of a ManagedTestCase used during scoring/blocking.

```python
id: uuid.UUID
title: str
suite_name: Optional[str]
tags: list[str]
status: Optional[str]
fingerprint: str
norm_title: str
norm_text: str
steps_text: str
step_set: frozenset[str]
title_tokens: set[str]
text_tokens: set[str]
has_body: bool
```

## backend/app/services/duplicate_detection_service.py — _BlockingResult

[backend/app/services/duplicate_detection_service.py:501](../../backend/app/services/duplicate_detection_service.py#L501)

Bases: ``.



```python
pairs: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)
truncated_blocks: int = 0
budget_exhausted: bool = False
```

## backend/app/services/eval_verdict.py — EvalVerdict

[backend/app/services/eval_verdict.py:7](../../backend/app/services/eval_verdict.py#L7)

Bases: `str, Enum`.

A conclusive pass/fail, or an honest absence of enough evidence.

```python
PASS = 'pass'
FAIL = 'fail'
INSUFFICIENT_SAMPLES = 'insufficient_samples'
```

## backend/app/services/evidence_sanitizer.py — SanitizationStats

[backend/app/services/evidence_sanitizer.py:22](../../backend/app/services/evidence_sanitizer.py#L22)

Bases: ``.



```python
redacted_strings: int = 0
truncated_strings: int = 0
omitted_items: int = 0
```

## backend/app/services/failed_test_assignment_service.py — ProjectAssignmentContext

[backend/app/services/failed_test_assignment_service.py:114](../../backend/app/services/failed_test_assignment_service.py#L114)

Bases: ``.

Project-invariant lookups for the per-run resolver.

Built once per project so ``backfill_unassigned_failures`` doesn't re-run
the same 4-7 queries for every one of up to 200 runs (audit 2026-07 #13).
``path_rules is None`` means "not loaded yet" — the resolver loads them
lazily the first time a failure actually reaches the path-owner branch
and caches them back onto the context.

```python
default_qa_lead_id: Optional[uuid.UUID]
manager_id: Optional[uuid.UUID]
qa_lead_pool: list[uuid.UUID]
admin_pool: list[uuid.UUID]
path_rules: Optional[list[ServiceOwnershipRule]] = None
handle_map: dict[str, uuid.UUID] = field(default_factory=dict)
```

## backend/app/services/failure_attribution_service.py — AttributionVerdict

[backend/app/services/failure_attribution_service.py:67](../../backend/app/services/failure_attribution_service.py#L67)

Bases: `str, Enum`.

The four answers. ``UNCERTAIN`` is an answer, not an absence of one.

```python
LIKELY_YOUR_CHANGE = 'LIKELY_YOUR_CHANGE'
LIKELY_FLAKY = 'LIKELY_FLAKY'
LIKELY_INFRA = 'LIKELY_INFRA'
UNCERTAIN = 'UNCERTAIN'
```

## backend/app/services/failure_attribution_service.py — AttributionInputs

[backend/app/services/failure_attribution_service.py:126](../../backend/app/services/failure_attribution_service.py#L126)

Bases: ``.

The five signals, exactly as they were when the verdict was composed.

```python
is_new_failure: bool = False
last_green_run_id: Optional[str] = None
flaky_score: Optional[float] = None
flaky_confidence: str = 'none'
cluster_key: Optional[str] = None
cluster_cause_family: Optional[str] = None
cluster_size: int = 0
change_overlap: Optional[float] = None
changed_files: tuple[str, ...] = ()
calibration_mode: str = 'hint'
calibration_specificity: Optional[float] = None
```

## backend/app/services/failure_attribution_service.py — Attribution

[backend/app/services/failure_attribution_service.py:163](../../backend/app/services/failure_attribution_service.py#L163)

Bases: ``.

A verdict, its confidence, and every input behind it.

```python
verdict: AttributionVerdict
confidence: float
rationale: str
inputs: AttributionInputs
votes: dict[str, float] = field(default_factory=dict)
```

## backend/app/services/flake_load_service.py — FlakeLoad

[backend/app/services/flake_load_service.py:55](../../backend/app/services/flake_load_service.py#L55)

Bases: ``.

Share of runs carrying flake noise, or an honest refusal.

```python
project_id: Any
window_days: int
total_runs: int
runs_with_flake_noise: int
flake_load: Optional[float]
insufficient_reason: Optional[str]
```

## backend/app/services/flaky_classifier_calibration.py — CalibrationResult

[backend/app/services/flaky_classifier_calibration.py:62](../../backend/app/services/flaky_classifier_calibration.py#L62)

Bases: ``.

Measured classifier quality for one project, or an honest refusal.

```python
project_id: Any
method: str
specificity: Optional[float]
sample_count: int
true_negatives: int
false_positives: int
insufficient_reason: Optional[str]
window_days: int
```

## backend/app/services/flaky_detection_timing_service.py — Cadence

[backend/app/services/flaky_detection_timing_service.py:96](../../backend/app/services/flaky_detection_timing_service.py#L96)

Bases: ``.



```python
screen_interval_minutes: int
sweep_interval_minutes: int
basis: str
runs_per_day: float
```

## backend/app/services/flaky_detection_timing_service.py — DetectionTiming

[backend/app/services/flaky_detection_timing_service.py:383](../../backend/app/services/flaky_detection_timing_service.py#L383)

Bases: ``.



```python
project_id: str
available: bool
insufficient_data_reason: Optional[str] = None
tracked: int = 0
measurable: int = 0
excluded_not_observed: int = 0
scored: int = 0
awaiting_evidence: int = 0
latency_hours: dict[str, Optional[float]] = field(default_factory=dict)
coverage: dict[str, Any] = field(default_factory=dict)
cadence: dict[str, Any] = field(default_factory=dict)
bottleneck: Optional[str] = None
notes: list[str] = field(default_factory=list)
```

## backend/app/services/flaky_investigator.py — FailureClusters

[backend/app/services/flaky_investigator.py:47](../../backend/app/services/flaky_investigator.py#L47)

Bases: ``.

Explainable clustering of a test's recent failures.

```python
total_failures: int = 0
distinct_error_signatures: int = 0
distinct_stack_fingerprints: int = 0
dominant_error: str = ''
dominant_error_count: int = 0
examples: list[str] = field(default_factory=list)
```

## backend/app/services/flaky_quarantine_service.py — EffectiveLifecyclePolicy

[backend/app/services/flaky_quarantine_service.py:100](../../backend/app/services/flaky_quarantine_service.py#L100)

Bases: ``.

Resolved per-project quarantine lifecycle policy.

```python
sla_days: int = _DEFAULT_SLA_DAYS
auto_create_defect: bool = False
auto_promote: bool = False
promote_after_passes: int = _DEFAULT_PROMOTE_AFTER_PASSES
detection_flip_rate_threshold: float = _DEFAULT_DETECTION_FLIP_RATE
detection_min_runs: int = _DEFAULT_DETECTION_MIN_RUNS
max_active_quarantined: int = _DEFAULT_MAX_ACTIVE_QUARANTINED
```

## backend/app/services/flaky_readiness_service.py — FlakyReadiness

[backend/app/services/flaky_readiness_service.py:54](../../backend/app/services/flaky_readiness_service.py#L54)

Bases: ``.

Per-project verdict on whether scoring has data to work with.

```python
project_id: Any
window_days: int
total_fingerprints: int
qualifying_fingerprints: int
median_runs_per_fingerprint: float
max_runs_per_fingerprint: int
qualifying_share: float
available: bool
insufficient_data_reason: Optional[str]
```

## backend/app/services/flaky_score_service.py — FlakinessScore

[backend/app/services/flaky_score_service.py:175](../../backend/app/services/flaky_score_service.py#L175)

Bases: ``.

A decomposable score, or an honest refusal to score.

```python
test_fingerprint: str
score: Optional[float]
test_name: Optional[str] = None
components: dict[str, float] = field(default_factory=dict)
weights: dict[str, float] = field(default_factory=dict)
observation_count: int = 0
confidence: str = CONFIDENCE_NONE
insufficient_reason: Optional[str] = None
```

## backend/app/services/flaky_screening_service.py — ScreeningCandidate

[backend/app/services/flaky_screening_service.py:86](../../backend/app/services/flaky_screening_service.py#L86)

Bases: ``.

One fingerprint tier 1 looked at, and why.

Carries no verdict by design: with a single observation the only honest
statement is that the test is not yet observable.

```python
test_fingerprint: str
test_name: Optional[str]
reason: str
first_seen_at: datetime
first_seen_is_exact: bool
observation_count: int
```

## backend/app/services/flaky_signals.py — IntermittencySignals

[backend/app/services/flaky_signals.py:57](../../backend/app/services/flaky_signals.py#L57)

Bases: ``.

Structured flakiness-intermittency signal for one test fingerprint.

```python
runs: int = 0
fail_count: int = 0
flip_count: int = 0
status_volatility: float = 0.0
error_signature_diversity: float = 0.0
stack_trace_diversity: float = 0.0
in_run_retry_rate: float = 0.0
intermittency_label: str = 'insufficient_data'
```

## backend/app/services/flaky_statistics.py — FailureRatioConfidence

[backend/app/services/flaky_statistics.py:37](../../backend/app/services/flaky_statistics.py#L37)

Bases: ``.

Wilson confidence band on the failure ratio for one test fingerprint.

```python
failures: int = 0
total: int = 0
point: float = 0.0
low: float = 0.0
high: float = 0.0
z: float = Z_95
```

## backend/app/services/flaky_step_analysis.py — StepFailureAttribution

[backend/app/services/flaky_step_analysis.py:39](../../backend/app/services/flaky_step_analysis.py#L39)

Bases: ``.

Surgical attribution of a flaky/failing test to one step.

```python
has_failing_step: bool = False
ordinal: int = -1
step_name: str = ''
keyword: str = ''
is_assertion_failure: bool = False
assertion_summary: str = ''
step_fingerprint: str = ''
total_steps: int = 0
failing_step_count: int = 0
surgical_recommendation: str = ''
```

## backend/app/services/flaky_step_flip.py — StepFlip

[backend/app/services/flaky_step_flip.py:60](../../backend/app/services/flaky_step_flip.py#L60)

Bases: ``.

One adjacent-run transition of a single step between PASSED and FAILED.

```python
ordinal: int
step_name: str
from_run_id: str
to_run_id: str
from_status: str
to_status: str
direction: str
```

## backend/app/services/flaky_step_flip.py — StepFlipSummary

[backend/app/services/flaky_step_flip.py:76](../../backend/app/services/flaky_step_flip.py#L76)

Bases: ``.

Per-step roll-up across the analysed window — the actionable unit.

```python
ordinal: int
step_name: str
flip_count: int
runs_observed: int
last_status: str
is_flaky: bool
```

## backend/app/services/flaky_step_flip.py — StepFlipReport

[backend/app/services/flaky_step_flip.py:91](../../backend/app/services/flaky_step_flip.py#L91)

Bases: ``.

Result of :func:`compute_step_flips` over a per-run step window.

```python
has_step_flip: bool = False
runs_analyzed: int = 0
total_flips: int = 0
flips: tuple[StepFlip, ...] = ()
flipping_steps: tuple[StepFlipSummary, ...] = ()
summary: str = ''
```

## backend/app/services/flaky_suppression_gate.py — SuppressionDecision

[backend/app/services/flaky_suppression_gate.py:58](../../backend/app/services/flaky_suppression_gate.py#L58)

Bases: ``.

How much authority a flaky verdict carries on this project.

```python
project_id: Any
mode: str
may_suppress: bool
specificity: Optional[float]
sample_count: int
reason: str
```

## backend/app/services/github_checks_service.py — _CheckEnrichment

[backend/app/services/github_checks_service.py:470](../../backend/app/services/github_checks_service.py#L470)

Bases: ``.

The run's failures partitioned with the same semantics as the PR
summary comment (``github_pr_comment_service._partition_tests``) but
keeping the ``(fingerprint, TestCase)`` pairs — annotations need the
stack trace and still-failing rows, which the comment partition
discards.

```python
newly_failed: list[tuple[str, Any]] = field(default_factory=list)
still_failing: list[tuple[str, Any]] = field(default_factory=list)
known_flaky: list[tuple[str, Any]] = field(default_factory=list)
fixed_count: int = 0
has_baseline: bool = False
kind_labels: dict[str, str] = field(default_factory=dict)
```

## backend/app/services/github_pr_comment_service.py — _Partition

[backend/app/services/github_pr_comment_service.py:177](../../backend/app/services/github_pr_comment_service.py#L177)

Bases: ``.

PR-run failures split into newly-failed vs known-flaky, plus the
fixed-vs-baseline rows and the still-failing-on-baseline count.

```python
newly_failed: list[dict[str, str]] = field(default_factory=list)
known_flaky: list[dict[str, str]] = field(default_factory=list)
fixed: list[dict[str, str]] = field(default_factory=list)
still_failing: int = 0
has_baseline: bool = False
```

## backend/app/services/github_pr_comment_service.py — _PRCommentContext

[backend/app/services/github_pr_comment_service.py:491](../../backend/app/services/github_pr_comment_service.py#L491)

Bases: ``.



```python
integration_id: uuid.UUID
api_base: str
repo: str
pr_number: int
pat: str
mode: str
marker: str
body: str
has_failures: bool
has_fixed: bool
```

## backend/app/services/gitlab_integration_service.py — _MRNoteContext

[backend/app/services/gitlab_integration_service.py:426](../../backend/app/services/gitlab_integration_service.py#L426)

Bases: ``.



```python
__slots__ = ('integration_id', 'api_root', 'project_ref', 'mr_iid', 'pat', 'mode', 'marker', 'body', 'has_failures', 'has_fixed')
```

## backend/app/services/gitlab_integration_service.py — _CommitStatusContext

[backend/app/services/gitlab_integration_service.py:759](../../backend/app/services/gitlab_integration_service.py#L759)

Bases: ``.



```python
__slots__ = ('integration_id', 'project_id', 'url', 'pat', 'payload')
```

## backend/app/services/ingestion_backpressure.py — _MemorySnapshot

[backend/app/services/ingestion_backpressure.py:46](../../backend/app/services/ingestion_backpressure.py#L46)

Bases: ``.



```python
used_bytes: int
max_bytes: int
captured_at: float
```

## backend/app/services/integration_probe_service.py — ProbeResult

[backend/app/services/integration_probe_service.py:31](../../backend/app/services/integration_probe_service.py#L31)

Bases: ``.



```python
provider: str
status: str
response_ms: int = 0
message: str = ''
auth_valid: bool | None = None
payload_valid: bool | None = None
```

## backend/app/services/knowledge_chunking_service.py — Chunk

[backend/app/services/knowledge_chunking_service.py:38](../../backend/app/services/knowledge_chunking_service.py#L38)

Bases: ``.



```python
text: str
section_heading: Optional[str] = None
requirement_id: Optional[str] = None
chunk_index: int = 0
token_count: int = 0
```

## backend/app/services/llm_circuit_breaker.py — CircuitScope

[backend/app/services/llm_circuit_breaker.py:42](../../backend/app/services/llm_circuit_breaker.py#L42)

Bases: ``.



```python
provider: str
base_url: str
scope_id: str
```

## backend/app/services/llm_circuit_breaker.py — LLMCircuitBreaker

[backend/app/services/llm_circuit_breaker.py:163](../../backend/app/services/llm_circuit_breaker.py#L163)

Bases: ``.

Redis-backed breaker isolated to one provider endpoint.

```python
_known_open_until: dict[str, float] = {}
```

## backend/app/services/llm_cost_budget.py — CapDecision

[backend/app/services/llm_cost_budget.py:175](../../backend/app/services/llm_cost_budget.py#L175)

Bases: ``.

Structured result returned from :func:`check_and_apply_cap`.

Consumers use ``mode_override`` to force a specific analysis engine
("ml" or "rules") or ``block`` to raise a hard HTTP 402/503 at the
caller's discretion. ``rationale`` is intended for
``BaseAgent.log_decision`` so the decision trail explains the
degrade to end users.

```python
__slots__ = ('action', 'mode_override', 'block', 'rationale', 'utilization_pct')
```

## backend/app/services/llm_cost_reservation.py — Reservation

[backend/app/services/llm_cost_reservation.py:126](../../backend/app/services/llm_cost_reservation.py#L126)

Bases: ``.



```python
key: str
project_id: str
estimated_usd: float
ttl_seconds: int
settled: bool = False
```

## backend/app/services/llm_policy_service.py — ProviderProfile

[backend/app/services/llm_policy_service.py:35](../../backend/app/services/llm_policy_service.py#L35)

Bases: ``.



```python
provider: str
residency: str
supports_offline: bool
requires_secret: bool
```

## backend/app/services/llm_pricing.py — ModelPrice

[backend/app/services/llm_pricing.py:63](../../backend/app/services/llm_pricing.py#L63)

Bases: ``.

USD per **one million** tokens.

```python
input_per_mtok: float
output_per_mtok: float
cached_input_per_mtok: Optional[float] = None
cache_write_per_mtok: Optional[float] = None
```

## backend/app/services/llm_pricing.py — CostEstimate

[backend/app/services/llm_pricing.py:138](../../backend/app/services/llm_pricing.py#L138)

Bases: ``.

What a call cost, and how much to trust that number.

```python
cost_usd: float
source: CostSource
provider: str
model: str
batch: bool = False
note: Optional[str] = None
```

## backend/app/services/llm_pricing.py — TokenUsage

[backend/app/services/llm_pricing.py:301](../../backend/app/services/llm_pricing.py#L301)

Bases: ``.

Tokens a call used, with the input/output split preserved.

The split matters: output tokens cost several times more than input on
every cloud provider, so collapsing both into a total — which every call
site in this codebase used to do — cannot be priced correctly.

```python
input_tokens: int = 0
output_tokens: int = 0
cached_input_tokens: int = 0
cache_write_tokens: int = 0
source: UsageSource = 'none'
details: dict[str, Any] = field(default_factory=dict)
```

## backend/app/services/mfa_service.py — SecretState

[backend/app/services/mfa_service.py:99](../../backend/app/services/mfa_service.py#L99)

Bases: `str, Enum`.

Why :func:`load_totp_secret` did or did not return a seed.

```python
OK = 'ok'
MISSING = 'missing'
UNREADABLE = 'unreadable'
```

## backend/app/services/mfa_service.py — LoadedSecret

[backend/app/services/mfa_service.py:108](../../backend/app/services/mfa_service.py#L108)

Bases: ``.



```python
state: SecretState
secret: Optional[str] = None
```

## backend/app/services/mfa_service.py — MfaPolicy

[backend/app/services/mfa_service.py:389](../../backend/app/services/mfa_service.py#L389)

Bases: ``.

Workspace-wide MFA + lockout policy.

Stored as a single ``app_settings`` row keyed ``mfa_policy`` rather than in
a dedicated table. The obvious alternative — mirroring
``SSOConfiguration.enforcement_mode`` — exists because a SAML config is a
large object (certificates, URLs, role maps) that has to be modelled
anyway, and its enforcement mode is one field on it. This policy is four
scalars with no accompanying object; a table would buy a single-active-row
lifecycle we have no use for. The ``app_settings`` route also gets
``settings_audit_service.log_settings_change`` for free, which is the
audit trail an auditor will ask for.

```python
require_mfa: bool = False
required_for_role: Optional[str] = None
lockout_enabled: bool = True
lockout_threshold: int = 10
lockout_duration_minutes: int = 15
```

## backend/app/services/mfa_service.py — LoginRequirement

[backend/app/services/mfa_service.py:530](../../backend/app/services/mfa_service.py#L530)

Bases: `str, Enum`.

What the login path must do after the password checks out.

```python
NONE = 'none'
CHALLENGE = 'challenge'
ENROLL = 'enroll'
BROKEN = 'broken'
```

## backend/app/services/model_registry.py — ModelRegistry

[backend/app/services/model_registry.py:29](../../backend/app/services/model_registry.py#L29)

Bases: ``.

Redis-backed registry for active model versions per track.

```python
TRACKS = ('classifier', 'reasoning', 'embedding')
```

## backend/app/services/model_router.py — ModelChoice

[backend/app/services/model_router.py:33](../../backend/app/services/model_router.py#L33)

Bases: ``.



```python
tier: ModelTier
endpoint: ResolvedEndpoint | None
reason: str
fallback: str | None = None
```

## backend/app/services/model_router.py — EscalationDecision

[backend/app/services/model_router.py:41](../../backend/app/services/model_router.py#L41)

Bases: ``.



```python
action: Literal['accept', 'escalate', 'fallback']
choice: ModelChoice
escalations: int
step_llm_calls_remaining: int
stage_quality: Literal['normal', 'degraded']
reason: str
```

## backend/app/services/notification_routing.py — TeamChannelInfo

[backend/app/services/notification_routing.py:59](../../backend/app/services/notification_routing.py#L59)

Bases: ``.

Resolved notification target for one team.

```python
team_name: str
channel_type: str
target: str
```

## backend/app/services/notification_routing.py — TransitionBatch

[backend/app/services/notification_routing.py:67](../../backend/app/services/notification_routing.py#L67)

Bases: ``.

One routed batch of transition content (per team, or the default).

``groups`` / ``singles`` / ``recovered`` / ``newly_flaky`` mirror the
arguments of ``notification_transitions.render_transition_message`` —
this module treats them as opaque beyond ``suite_name`` /
``test_name`` / ``event`` / ``tests`` attributes to avoid an import
cycle with the transition engine.

```python
groups: list = field(default_factory=list)
singles: list = field(default_factory=list)
recovered: list = field(default_factory=list)
newly_flaky: list = field(default_factory=list)
fallback_counts: dict[str, int] = field(default_factory=dict)
```

## backend/app/services/notification_routing.py — TeamBatch

[backend/app/services/notification_routing.py:119](../../backend/app/services/notification_routing.py#L119)

Bases: `TransitionBatch`.

A batch bound for one team's channel.

```python
channel: Optional[TeamChannelInfo] = None
```

## backend/app/services/notification_transitions.py — TransitionOrderPending

[backend/app/services/notification_transitions.py:73](../../backend/app/services/notification_transitions.py#L73)

Bases: `RuntimeError`.

A newer run must be deferred until an older transition is durable.

```python
defer_downstream_without_failure = True
```

## backend/app/services/notification_transitions.py — EffectivePolicy

[backend/app/services/notification_transitions.py:149](../../backend/app/services/notification_transitions.py#L149)

Bases: ``.

Resolved per-project transition-notification policy.

A MISSING ``NotificationTransitionPolicy`` row resolves to the
new-project default (transitions ON, per-run spam OFF). Migration 0103
backfilled an explicit row (transitions OFF, per-run ON) for every
project that existed at upgrade time, so existing projects keep their
old behaviour until someone edits the policy.

```python
transitions_enabled: bool = True
per_run_events_enabled: bool = False
enabled_events: tuple[str, ...] = TRANSITION_EVENT_VALUES
consecutive_failure_threshold: int = DEFAULT_CONSECUTIVE_FAILURE_THRESHOLD
```

## backend/app/services/notification_transitions.py — TransitionEvent

[backend/app/services/notification_transitions.py:301](../../backend/app/services/notification_transitions.py#L301)

Bases: ``.

One detected transition, pre-rendering.

```python
event: str
fingerprint: str
test_name: str
suite_name: Optional[str] = None
test_case_id: Optional[uuid.UUID] = None
consecutive_failures: int = 0
```

## backend/app/services/notification_transitions.py — CaseOutcome

[backend/app/services/notification_transitions.py:312](../../backend/app/services/notification_transitions.py#L312)

Bases: ``.

Minimal view of one TestCase row of the finalized run.

```python
fingerprint: str
status: str
test_name: str
suite_name: Optional[str] = None
test_case_id: Optional[uuid.UUID] = None
```

## backend/app/services/notification_transitions.py — ClusterGroup

[backend/app/services/notification_transitions.py:427](../../backend/app/services/notification_transitions.py#L427)

Bases: ``.

Newly-failing tests of one run that share a FailureCluster.

```python
cluster_id: str
label: str
tests: list[TransitionEvent] = field(default_factory=list)
```

## backend/app/services/ownership_resolver_service.py — OwnershipResult

[backend/app/services/ownership_resolver_service.py:28](../../backend/app/services/ownership_resolver_service.py#L28)

Bases: ``.



```python
service_name: str | None = None
team_name: str | None = None
team_contact: str | None = None
confidence: str = 'none'
matched_rule_id: str | None = None
match_source: str | None = None
fallback_reason: str | None = None
```

## backend/app/services/performance_budgets.py — LatencyBudget

[backend/app/services/performance_budgets.py:30](../../backend/app/services/performance_budgets.py#L30)

Bases: ``.

Max acceptable latency for an operation.

``http_handler`` is the FastAPI route template (with ``{path_param}``
placeholders) so the budget can be looked up against the
``http_request_duration_seconds_bucket{handler="..."}`` series in
Prometheus. ``None`` means the operation is internal (e.g. a Celery
task) and not directly observable on the HTTP histogram.

```python
operation: str
p50_ms: int
p95_ms: int
p99_ms: int
description: str
http_handler: Optional[str] = None
http_method: str = 'GET'
```

## backend/app/services/performance_budgets.py — ThroughputBudget

[backend/app/services/performance_budgets.py:49](../../backend/app/services/performance_budgets.py#L49)

Bases: ``.

Minimum acceptable throughput for a workload.

```python
operation: str
min_rps: float
description: str
```

## backend/app/services/pipeline_budget_service.py — PipelineBudgetReservation

[backend/app/services/pipeline_budget_service.py:52](../../backend/app/services/pipeline_budget_service.py#L52)

Bases: ``.



```python
reservation_id: str | None
allowed: bool
stop_reason: str | None = None
legacy_unbounded: bool = False
```

## backend/app/services/pipeline_cancellation.py — PipelineCancelled

[backend/app/services/pipeline_cancellation.py:75](../../backend/app/services/pipeline_cancellation.py#L75)

Bases: `RuntimeError`.

The run was cancelled; the worker must stop without scheduling a retry.

Deliberately NOT in ``RetryPolicy.DEFAULT_RETRYABLE`` -- its error code is
``cancelled``, which ``retry_policy.NON_RETRYABLE`` lists. A cancelled run
that retried would defeat the cancellation.

```python
error_code = 'cancelled'
```

## backend/app/services/pipeline_cancellation.py — CancelOutcome

[backend/app/services/pipeline_cancellation.py:92](../../backend/app/services/pipeline_cancellation.py#L92)

Bases: ``.

What a cancel request did.

``accepted`` distinguishes "stopped it now" (``terminal=True``) from
"asked the running worker to stop" (``terminal=False``); ``already`` means
the run had already finished and nothing was changed.

```python
__slots__ = ('accepted', 'terminal', 'status', 'reason')
```

## backend/app/services/pipeline_lease.py — PipelineLease

[backend/app/services/pipeline_lease.py:94](../../backend/app/services/pipeline_lease.py#L94)

Bases: ``.

One holder's claim on one pipeline row.

```python
pipeline_run_id: str
owner: str
token: str
```

## backend/app/services/pipeline_retry_config.py — RetryPlan

[backend/app/services/pipeline_retry_config.py:84](../../backend/app/services/pipeline_retry_config.py#L84)

Bases: ``.

How a manual retry should be carried out.

``mode`` is ``"resume"`` (same id, keep completed stages) or ``"rerun"``
(new id, ``rerun_of`` set, nothing replayed).

```python
__slots__ = ('mode', 'reason', 'frozen_fingerprint', 'current_fingerprint')
```

## backend/app/services/policy_evaluator_service.py — RuleEvaluation

[backend/app/services/policy_evaluator_service.py:34](../../backend/app/services/policy_evaluator_service.py#L34)

Bases: ``.



```python
rule_id: str
rule_name: str
rule_type: str
passed: bool
action: str
message: str
actual_value: float | int | None = None
threshold_value: float | int | None = None
```

## backend/app/services/policy_evaluator_service.py — PolicyEvaluationResult

[backend/app/services/policy_evaluator_service.py:46](../../backend/app/services/policy_evaluator_service.py#L46)

Bases: ``.



```python
policy_id: str | None
policy_version: int | None
policy_level: str
overall_result: str
recommendation: str
effective_composite: float
rule_evaluations: list[RuleEvaluation] = field(default_factory=list)
effective_thresholds: dict = field(default_factory=dict)
effective_weights: dict = field(default_factory=dict)
evaluated_at: str = ''
kind_breakdown: dict | None = None
kind_rule_applied: bool = False
kind_counterfactual: str | None = None
policy_snapshot: dict = field(default_factory=dict)
evaluator_version: str = POLICY_EVALUATOR_VERSION
```

## backend/app/services/prompt_registry.py — PromptDef

[backend/app/services/prompt_registry.py:84](../../backend/app/services/prompt_registry.py#L84)

Bases: ``.

One versioned prompt. ``version`` is bumped on ANY text change.

```python
id: str
version: int
text: str
```

## backend/app/services/quarantine_health_service.py — QuarantineWarning

[backend/app/services/quarantine_health_service.py:46](../../backend/app/services/quarantine_health_service.py#L46)

Bases: ``.

Something a human should look at. Never an action taken on their behalf.

```python
kind: str
severity: str
message: str
subject: Optional[str] = None
```

## backend/app/services/quarantine_health_service.py — QuarantineHealth

[backend/app/services/quarantine_health_service.py:64](../../backend/app/services/quarantine_health_service.py#L64)

Bases: ``.

The state of a project's quarantine, and what is worth noticing.

```python
project_id: Any
active_count: int
max_active: int
over_cap: bool
stale_count: int
warnings: list[QuarantineWarning] = field(default_factory=list)
```

## backend/app/services/rag_generation_service.py — CitationGroup

[backend/app/services/rag_generation_service.py:47](../../backend/app/services/rag_generation_service.py#L47)

Bases: ``.



```python
case_index: int
chunks: list[RetrievedChunk] = field(default_factory=list)
```

## backend/app/services/rag_generation_service.py — GroundedGenerationResult

[backend/app/services/rag_generation_service.py:53](../../backend/app/services/rag_generation_service.py#L53)

Bases: ``.



```python
batch_id: uuid.UUID
generation_mode: str
test_cases: list[dict]
citations: list[dict]
coverage_summary: Optional[str] = None
gaps_noted: list[str] = field(default_factory=list)
created_ids: list[str] = field(default_factory=list)
```

## backend/app/services/rag_retrieval_service.py — RetrievedChunk

[backend/app/services/rag_retrieval_service.py:20](../../backend/app/services/rag_retrieval_service.py#L20)

Bases: ``.



```python
vector_id: str
source_id: uuid.UUID
source_title: str
section_heading: Optional[str]
chunk_text: str
relevance_score: float
requirement_id: Optional[str]
chunk_text_preview: Optional[str] = None
canonical_url: Optional[str] = None
```

## backend/app/services/release_rollup_service.py — ReleaseRollup

[backend/app/services/release_rollup_service.py:137](../../backend/app/services/release_rollup_service.py#L137)

Bases: ``.

What the gate saw. Every field is snapshotted onto the decision row.

```python
latest_by_test: dict[str, str] = field(default_factory=dict)
status_counts: dict[str, int] = field(default_factory=dict)
run_ids: list[str] = field(default_factory=list)
attribution_mix: dict[str, int] = field(default_factory=dict)
incomplete_runs: dict[str, int] = field(default_factory=dict)
truncated: bool = False
```

## backend/app/services/report_composition_service.py — ReportSection

[backend/app/services/report_composition_service.py:27](../../backend/app/services/report_composition_service.py#L27)

Bases: ``.



```python
title: str
content: Any
```

## backend/app/services/report_composition_service.py — ReportData

[backend/app/services/report_composition_service.py:33](../../backend/app/services/report_composition_service.py#L33)

Bases: ``.

Structured report data ready for rendering.

```python
run_id: str
build_number: str
branch: str
project_name: str
generated_at: str
layout: str
pass_rate: float
total_tests: int
passed_tests: int
failed_tests: int
skipped_tests: int
executive_summary: str = ''
release_recommendation: str = ''
risk_score: int = 0
composite_risk: float = 0.0
blocking_issues: list[str] = field(default_factory=list)
conditions_for_go: list[str] = field(default_factory=list)
release_reasoning: str = ''
dimension_scores: list[dict] = field(default_factory=list)
category_breakdown: dict[str, int] = field(default_factory=dict)
affected_suites: list[dict] = field(default_factory=list)
action_plan: dict = field(default_factory=dict)
baseline_diff: dict = field(default_factory=dict)
provenance: dict = field(default_factory=dict)
executive_panel: dict = field(default_factory=dict)
failure_clusters: list[dict] = field(default_factory=list)
top_analyses: list[dict] = field(default_factory=list)
defect_candidates: list[dict] = field(default_factory=list)
evidence_artifacts: list[dict] = field(default_factory=list)
draft_watermark: str = ''
```

## backend/app/services/report_distribution_policy.py — DistributionDecision

[backend/app/services/report_distribution_policy.py:86](../../backend/app/services/report_distribution_policy.py#L86)

Bases: ``.



```python
allowed: bool
reason: str
envelope: ReviewEnvelope
watermark: Optional[str] = None
enforced: bool = False
```

## backend/app/services/resilience.py — TruncationReport

[backend/app/services/resilience.py:206](../../backend/app/services/resilience.py#L206)

Bases: ``.

What a budget truncation actually dropped.

Exists because the old helper returned a bare string, so a caller could not
tell a truncated context from an intact one. Six call sites truncate and not
one recorded it -- the only trace was a marker appended INTO the prompt,
which reaches the model rather than any metric. That is the "silent" in
silent data loss.

```python
__slots__ = ('text', 'truncated', 'original_chars', 'dropped_chars')
```

## backend/app/services/retention_service.py — EffectiveRetentionPolicy

[backend/app/services/retention_service.py:189](../../backend/app/services/retention_service.py#L189)

Bases: ``.

Resolved per-project retention policy (missing row → defaults).

```python
enabled: bool = False
raw_events_days: int = DEFAULT_RAW_EVENTS_DAYS
runs_days: int = DEFAULT_RUNS_DAYS
artifacts_days: int = DEFAULT_ARTIFACTS_DAYS
audit_days: int = DEFAULT_AUDIT_DAYS
source: str = 'default'
```

## backend/app/services/retention_service.py — _ExecutionPlan

[backend/app/services/retention_service.py:510](../../backend/app/services/retention_service.py#L510)

Bases: ``.

The non-run-scoped filters the execute phase needs.

``_Candidates`` holds what is derived FROM runs. These eight are not: seven
never reference a run at all, and ``event_archive_where`` keys on a
different column (``event_archive_at``) from the one the runs clock uses.
They come from four independent clocks, which is exactly why a single
``older_than_days`` criterion could never express the policy purge — and why
the executor takes them as data rather than closing over them.

```python
event_archive_where: tuple
access_audit_where: tuple
tc_audit_where: tuple
activity_where: tuple
deletion_job_where: tuple
revoked_share_link_where: tuple
provenance_where: tuple
memory_expired_ids: list
expired_packs: list
```

## backend/app/services/retention_service.py — _Candidates

[backend/app/services/retention_service.py:533](../../backend/app/services/retention_service.py#L533)

Bases: ``.

Everything materialized from Postgres BEFORE any delete (the
ordering trap: the CASCADE destroys the only run→doc/key mapping).

```python
purge_run_ids: list[uuid.UUID]
purge_run_strs: list[str]
purge_tc_strs: list[str]
raw_run_strs: list[str]
raw_tc_strs: list[str]
raw_live_run_keys: list[str]
artifact_prefixes: list[str]
artifact_pipeline_strs: set[str]
evidence_artifact_ids: list[uuid.UUID]
audit_pipeline_strs: list[str]
audit_run_strs: list[str]
```

## backend/app/services/retry_policy.py — RetryPolicy

[backend/app/services/retry_policy.py:47](../../backend/app/services/retry_policy.py#L47)

Bases: ``.

Exponential backoff with symmetric jitter and a bounded attempt count.

``attempt`` everywhere in this module is **1-based and counts the attempt
that just failed**: ``delay(1)`` is the wait after the first attempt.

```python
max_attempts: int = 5
base_seconds: float = 30.0
cap_seconds: float = 600.0
jitter: float = 0.2
retry_on: frozenset[str] = field(default_factory=lambda: DEFAULT_RETRYABLE)
```

## backend/app/services/review_envelope.py — ReviewEnvelope

[backend/app/services/review_envelope.py:67](../../backend/app/services/review_envelope.py#L67)

Bases: ``.



```python
ai_generated: bool
state: str
message: str
review_id: Optional[str] = None
reviewed_at: Optional[str] = None
```

## backend/app/services/reviewer_quality_service.py — _Strict

[backend/app/services/reviewer_quality_service.py:49](../../backend/app/services/reviewer_quality_service.py#L49)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
```

## backend/app/services/reviewer_quality_service.py — ReviewerMutationObservation

[backend/app/services/reviewer_quality_service.py:66](../../backend/app/services/reviewer_quality_service.py#L66)

Bases: `_Strict`.



```python
sample_id: str = Field(min_length=1, max_length=160)
mutation_class: MutationClass
detected_families: frozenset[int] = Field(default_factory=frozenset)
observed_at: datetime
_validate_families = field_validator('detected_families')(_families)
_validate_time = field_validator('observed_at')(_aware)
```

## backend/app/services/reviewer_quality_service.py — ReviewerCleanObservation

[backend/app/services/reviewer_quality_service.py:76](../../backend/app/services/reviewer_quality_service.py#L76)

Bases: `_Strict`.



```python
sample_id: str = Field(min_length=1, max_length=160)
flagged_families: frozenset[int] = Field(default_factory=frozenset)
observed_at: datetime
_validate_families = field_validator('flagged_families')(_families)
_validate_time = field_validator('observed_at')(_aware)
```

## backend/app/services/reviewer_quality_service.py — ReviewerHumanOutcome

[backend/app/services/reviewer_quality_service.py:85](../../backend/app/services/reviewer_quality_service.py#L85)

Bases: `_Strict`.



```python
review_id: str = Field(min_length=1, max_length=160)
reviewer_passed: bool
human_rejected: bool
observed_at: datetime
_validate_time = field_validator('observed_at')(_aware)
```

## backend/app/services/run_environment.py — ResolvedEnvironment

[backend/app/services/run_environment.py:38](../../backend/app/services/run_environment.py#L38)

Bases: ``.

An environment answer plus how much to trust it.

```python
key: Optional[str]
source: str
```

## backend/app/services/scim_projection.py — SCIMProjection

[backend/app/services/scim_projection.py:20](../../backend/app/services/scim_projection.py#L20)

Bases: ``.



```python
included: frozenset[str] | None = None
excluded: frozenset[str] | None = None
```

## backend/app/services/search_service.py — _Bound

[backend/app/services/search_service.py:45](../../backend/app/services/search_service.py#L45)

Bases: ``.

The named bind parameters a cached statement varies by.

Every value the query differs on must travel through one of these. A value
left inline is baked into the cached statement object and then served to the
NEXT caller — for ``project_id`` or the allow-list that is not a stale
number, it is one tenant reading another's rows. ``test_search_statement_cache.py``
executes one cached statement for two projects in turn and fails if the
second sees the first's scope.

```python
__slots__ = ('pattern', 'project_id', 'allowed_ids', 'status', 'since')
```

## backend/app/services/share_link_service.py — CreatedShareLink

[backend/app/services/share_link_service.py:25](../../backend/app/services/share_link_service.py#L25)

Bases: `NamedTuple`.



```python
link: ReportShareLink
raw_token: str
```

## backend/app/services/step_llm_budget.py — StepLLMBudget

[backend/app/services/step_llm_budget.py:9](../../backend/app/services/step_llm_budget.py#L9)

Bases: ``.

One counter shared by every extra model call for a workflow step.

```python
limit: int
used: int = 0
reasons: list[str] = field(default_factory=list)
```

## backend/app/services/storage_accounting_service.py — StoreFootprint

[backend/app/services/storage_accounting_service.py:78](../../backend/app/services/storage_accounting_service.py#L78)

Bases: ``.

One store's contribution, with its own honesty flags.

``bytes_`` and the count fields are ``None`` when ``measured`` is False —
deliberately not ``0``, so a caller cannot render an outage as an empty
project without noticing.

```python
store: str
measured: bool
exact: bool
complete: bool = True
bytes_: Optional[int] = None
items: Optional[int] = None
estimate_basis: Optional[str] = None
unreachable_reason: Optional[str] = None
```

## backend/app/services/storage_accounting_service.py — ProjectStorageFootprint

[backend/app/services/storage_accounting_service.py:104](../../backend/app/services/storage_accounting_service.py#L104)

Bases: ``.



```python
project_id: str
computed_at: datetime
stores: list[StoreFootprint] = field(default_factory=list)
```

## backend/app/services/storage_accounting_service.py — DeletedProjectFootprint

[backend/app/services/storage_accounting_service.py:331](../../backend/app/services/storage_accounting_service.py#L331)

Bases: ``.



```python
project_id: str
name: str
reachable_by_retention: bool
footprint: ProjectStorageFootprint
```

## backend/app/services/storage_accounting_service.py — DeletedProjectsReport

[backend/app/services/storage_accounting_service.py:352](../../backend/app/services/storage_accounting_service.py#L352)

Bases: ``.



```python
computed_at: datetime
projects_total: int
projects: list[DeletedProjectFootprint] = field(default_factory=list)
```

## backend/app/services/summary_report_service.py — _Totals

[backend/app/services/summary_report_service.py:57](../../backend/app/services/summary_report_service.py#L57)

Bases: ``.



```python
total: int
passed: int
failed: int
skipped: int
broken: int
```

## backend/app/services/systemic_cluster_service.py — SystemicCluster

[backend/app/services/systemic_cluster_service.py:122](../../backend/app/services/systemic_cluster_service.py#L122)

Bases: ``.

A group of tests that fail together, with the evidence for it.

```python
cluster_key: str
members: tuple[str, ...]
cohesion: float
co_failure_runs: int
cause_family: str = CAUSE_UNKNOWN
label: str = ''
member_names: dict[str, str] = field(default_factory=dict)
```

## backend/app/services/test_case_lifecycle_service.py — LifecycleAction

[backend/app/services/test_case_lifecycle_service.py:34](../../backend/app/services/test_case_lifecycle_service.py#L34)

Bases: `str, Enum`.



```python
REQUEST_REVIEW = 'request_review'
CLAIM_REVIEW = 'claim_review'
WITHDRAW_REVIEW = 'withdraw_review'
UNCLAIM = 'unclaim'
APPROVE = 'approve'
REJECT = 'reject'
REQUEST_CHANGES = 'request_changes'
ACTIVATE = 'activate'
FLAG_STALE = 'flag_stale'
REVISE = 'revise'
DEPRECATE = 'deprecate'
REINSTATE = 'reinstate'
ARCHIVE = 'archive'
```

## backend/app/services/test_case_lifecycle_service.py — LifecycleTransitionResult

[backend/app/services/test_case_lifecycle_service.py:118](../../backend/app/services/test_case_lifecycle_service.py#L118)

Bases: ``.



```python
case: ManagedTestCase
review: Optional[TestCaseReview] = None
```

## backend/app/services/tier_comparison_service.py — TierOutputPair

[backend/app/services/tier_comparison_service.py:51](../../backend/app/services/tier_comparison_service.py#L51)

Bases: ``.



```python
sample_id: str
incumbent_output: Any
candidate_output: Any
incumbent_cost_usd: float = 0.0
candidate_cost_usd: float = 0.0
incumbent_latency_ms: int = 0
candidate_latency_ms: int = 0
incumbent_tokens: int = 0
candidate_tokens: int = 0
```

## backend/app/services/tool_call_idempotency.py — ToolCallOutcome

[backend/app/services/tool_call_idempotency.py:109](../../backend/app/services/tool_call_idempotency.py#L109)

Bases: ``.

What ``run_once`` did.

``executed``        -- this attempt made the call.
``replayed``        -- an earlier attempt made it; ``result`` is its answer.
``outcome_unknown`` -- an earlier attempt started it and never recorded
                       how it ended. The call was not repeated.

```python
status: Literal['executed', 'replayed', 'outcome_unknown']
key: str
result: dict[str, Any] = field(default_factory=dict)
```

## backend/app/services/tool_call_idempotency.py — PriorToolCall

[backend/app/services/tool_call_idempotency.py:124](../../backend/app/services/tool_call_idempotency.py#L124)

Bases: ``.

A call an earlier attempt already made (or started).

```python
status: Literal['executed', 'executing']
result: dict[str, Any] = field(default_factory=dict)
```

## backend/app/services/upload_limits.py — TooManyResults

[backend/app/services/upload_limits.py:104](../../backend/app/services/upload_limits.py#L104)

Bases: `Exception`.

A report carries more results than one upload may.

Deliberately not a ``ValueError``: the archive parser skips an entry that
raises an ordinary parse error, and must never skip this one.

```python
code = 'too_many_results'
```

## backend/app/services/value_metrics_service.py — EffectiveAssumptions

[backend/app/services/value_metrics_service.py:97](../../backend/app/services/value_metrics_service.py#L97)

Bases: ``.

Resolved per-project hours-saved assumptions (missing row → defaults).

```python
triage_minutes_per_failure: float = DEFAULT_TRIAGE_MINUTES_PER_FAILURE
blocked_run_wait_minutes: float = DEFAULT_BLOCKED_RUN_WAIT_MINUTES
defect_filing_minutes: float = DEFAULT_DEFECT_FILING_MINUTES
source: str = 'default'
```

## backend/app/services/workflow_definition_service.py — _Strict

[backend/app/services/workflow_definition_service.py:21](../../backend/app/services/workflow_definition_service.py#L21)

Bases: `BaseModel`.



```python
model_config = ConfigDict(extra='forbid')
```

## backend/app/services/workflow_definition_service.py — WorkflowStepV1

[backend/app/services/workflow_definition_service.py:25](../../backend/app/services/workflow_definition_service.py#L25)

Bases: `_Strict`.



```python
id: str = Field(min_length=1, max_length=80, pattern='^[a-z][a-z0-9_]*$')
agent_id: str = Field(min_length=1, max_length=80)
config_ref: Optional[str] = Field(default=None, max_length=80)
tools: list[str] = Field(default_factory=list, max_length=64)
reviews: list[str] = Field(default_factory=list, max_length=64)
model: Optional[dict[str, Any]] = None
```

## backend/app/services/workflow_definition_service.py — WorkflowEdgeV1

[backend/app/services/workflow_definition_service.py:34](../../backend/app/services/workflow_definition_service.py#L34)

Bases: `_Strict`.



```python
source: str | list[str] = Field(alias='from')
to: str = Field(min_length=1, max_length=80)
when: Optional[dict[str, Any]] = None
join: Optional[Literal['all', 'any']] = None
```

## backend/app/services/workflow_definition_service.py — WorkflowLoopV1

[backend/app/services/workflow_definition_service.py:41](../../backend/app/services/workflow_definition_service.py#L41)

Bases: `_Strict`.



```python
source: str = Field(alias='from', min_length=1, max_length=80)
to: str = Field(min_length=1, max_length=80)
when: dict[str, Any]
max_iterations: int = Field(ge=1, le=100)
```

## backend/app/services/workflow_definition_service.py — WorkflowRetryPolicyV1

[backend/app/services/workflow_definition_service.py:48](../../backend/app/services/workflow_definition_service.py#L48)

Bases: `_Strict`.



```python
max_attempts: int = Field(default=5, ge=1, le=10)
base_seconds: int = Field(default=30, ge=0, le=3600)
cap_seconds: int = Field(default=600, ge=1, le=86400)
```

- Validator/serializer `cap_not_below_base`: [backend/app/services/workflow_definition_service.py:54](../../backend/app/services/workflow_definition_service.py#L54). Read source for the cross-field or conversion rule.
## backend/app/services/workflow_definition_service.py — WorkflowBodyV1

[backend/app/services/workflow_definition_service.py:60](../../backend/app/services/workflow_definition_service.py#L60)

Bases: `_Strict`.



```python
workflow_id: str = Field(min_length=3, max_length=80, pattern='^(?:wf\\.[a-z0-9_.-]+|offline|deep|live)$')
name: str = Field(min_length=1, max_length=120)
description: Optional[str] = Field(default=None, max_length=4000)
base: Literal['offline', 'deep', 'live']
steps: list[WorkflowStepV1] = Field(min_length=1, max_length=100)
edges: list[WorkflowEdgeV1] = Field(default_factory=list, max_length=300)
loops: list[WorkflowLoopV1] = Field(default_factory=list, max_length=50)
retry_policy: WorkflowRetryPolicyV1 = Field(default_factory=WorkflowRetryPolicyV1)
review_policy: Literal['human_required', 'human_required_plus_auto_reviewer'] = 'human_required'
deadline_seconds: int = Field(default=1500, ge=1, le=86400)
```

- Validator/serializer `unique_step_ids`: [backend/app/services/workflow_definition_service.py:78](../../backend/app/services/workflow_definition_service.py#L78). Read source for the cross-field or conversion rule.
## backend/app/services/workflow_definition_service.py — WorkflowForkV1

[backend/app/services/workflow_definition_service.py:85](../../backend/app/services/workflow_definition_service.py#L85)

Bases: `_Strict`.



```python
workflow_id: str = Field(min_length=3, max_length=80, pattern='^wf\\.[a-z0-9_.-]+$')
name: str = Field(min_length=1, max_length=120)
description: Optional[str] = Field(default=None, max_length=4000)
```

## backend/app/services/workflow_definition_service.py — WorkflowEvaluateV1

[backend/app/services/workflow_definition_service.py:91](../../backend/app/services/workflow_definition_service.py#L91)

Bases: `_Strict`.



```python
version: Optional[int] = Field(default=None, ge=1)
sample_limit: int = Field(default=20, ge=20, le=100)
```

## backend/app/services/workflow_definition_service.py — WorkflowPublishV1

[backend/app/services/workflow_definition_service.py:96](../../backend/app/services/workflow_definition_service.py#L96)

Bases: `_Strict`.



```python
version: int = Field(ge=1)
definition_sha256: str = Field(min_length=64, max_length=64, pattern='^[0-9a-f]{64}$')
accept_regression: bool = False
reason: Optional[str] = Field(default=None, max_length=2000)
eval_manifest_checksum: Optional[str] = Field(default=None, min_length=64, max_length=64, pattern='^[0-9a-f]{64}$')
```

- Validator/serializer `regression_reason_required`: [backend/app/services/workflow_definition_service.py:113](../../backend/app/services/workflow_definition_service.py#L113). Read source for the cross-field or conversion rule.
## backend/app/services/workflow_evaluation_service.py — ReplayEntry

[backend/app/services/workflow_evaluation_service.py:43](../../backend/app/services/workflow_evaluation_service.py#L43)

Bases: ``.



```python
agent_id: str
prompt_version: str
input_hash: str
output: Mapping[str, Any]
step_id: str = ''
authority_sha256: str = ''
input_checksum_sha256: str = ''
output_checksum_sha256: str = ''
stage_status: str = 'completed'
degraded: bool = False
cost_usd: float = 0.0
latency_ms: int = 0
```

## backend/app/services/workflow_evaluation_service.py — ReplayCase

[backend/app/services/workflow_evaluation_service.py:59](../../backend/app/services/workflow_evaluation_service.py#L59)

Bases: ``.



```python
case_id: str
project_id: uuid.UUID
test_run_id: uuid.UUID
prompt_version: str
entries: tuple[ReplayEntry, ...]
baseline_plan_passed: bool
baseline_degraded: bool
reviewer_rejected: bool
baseline_cost_usd: float
baseline_latency_ms: int
```

## backend/app/services/ws_event_ingest.py — WsIngestOutcome

[backend/app/services/ws_event_ingest.py:62](../../backend/app/services/ws_event_ingest.py#L62)

Bases: ``.

What the route needs once :func:`ingest_one` has staged an event.

```python
session_id: str
cache_key: str = ''
completes: bool = False
staged: bool = False
```

## backend/app/tools/chat_read_tools.py — ChatToolState

[backend/app/tools/chat_read_tools.py:60](../../backend/app/tools/chat_read_tools.py#L60)

Bases: ``.

Server-side state for one chat tool loop run.

```python
project_id: str
token_budget_remaining: int
per_call_token_cap: int
trace: list[dict[str, str]] = field(default_factory=list)
quarantine_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
jira_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
```

## backend/app/tools/investigation_context.py — InvestigationContext

[backend/app/tools/investigation_context.py:14](../../backend/app/tools/investigation_context.py#L14)

Bases: ``.



```python
project_id: str
run_id: str
test_case_id: str
test_name: str
test_fingerprint: str | None = None
service_name: str | None = None
timestamp: str | None = None
ocp_pod_name: str | None = None
ocp_namespace: str | None = None
```

## backend/app/worker/tasks.py — DownstreamTrackedTask

[backend/app/worker/tasks.py:106](../../backend/app/worker/tasks.py#L106)

Bases: `Task`.

Tie outbox publication to the consumer's durable business outcome.

Direct callers have no tracking headers and retain the task's historical
Celery retry behaviour.  Outbox deliveries let PostgreSQL own retries so a
Celery retry and the relay cannot race one another.

```python
abstract = True
```
