# PostgreSQL data dictionary

[Documentation home](../README.md) · [Regeneration](../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../handoff/verification.md).

Current SQLAlchemy metadata, without a database connection. All columns, server/application defaults, foreign keys, indexes and constraints below describe the model snapshot; [migrations](migrations.md) determine deployed schema history. Application validation and serializers can impose additional rules. JSON/JSONB types alone do not enforce a payload schema.

| Table | Model | Columns |
|---|---|---|
| [access_audit_logs](#access_audit_logs) | AccessAuditLog | 9 |
| [agent_action_dispatch_outbox](#agent_action_dispatch_outbox) | AgentActionDispatchOutbox | 12 |
| [agent_action_ledger](#agent_action_ledger) | AgentActionLedger | 22 |
| [agent_child_dispatch_outbox](#agent_child_dispatch_outbox) | AgentChildDispatchOutbox | 13 |
| [agent_configs](#agent_configs) | AgentConfig | 10 |
| [agent_investigations](#agent_investigations) | AgentInvestigation | 30 |
| [agent_invocations](#agent_invocations) | AgentInvocation | 16 |
| [agent_memory_entries](#agent_memory_entries) | AgentMemoryEntry | 21 |
| [agent_pipeline_runs](#agent_pipeline_runs) | AgentPipelineRun | 24 |
| [agent_runs](#agent_runs) | AgentRun | 16 |
| [agent_stage_results](#agent_stage_results) | AgentStageResult | 36 |
| [ai_analysis](#ai_analysis) | AIAnalysis | 17 |
| [ai_eval_baselines](#ai_eval_baselines) | AIEvalBaseline | 18 |
| [ai_eval_datasets](#ai_eval_datasets) | AIEvalDataset | 10 |
| [ai_eval_gate_runs](#ai_eval_gate_runs) | AIEvalGateRun | 16 |
| [ai_eval_reviewer_quality](#ai_eval_reviewer_quality) | AIEvalReviewerQuality | 25 |
| [ai_eval_runs](#ai_eval_runs) | AIEvalRun | 16 |
| [ai_eval_shadow_pairs](#ai_eval_shadow_pairs) | AIEvalShadowPair | 16 |
| [ai_feedback](#ai_feedback) | AIFeedback | 12 |
| [ai_provenance_records](#ai_provenance_records) | AIProvenanceRecord | 13 |
| [api_keys](#api_keys) | ApiKey | 12 |
| [app_settings](#app_settings) | AppSetting | 6 |
| [canonical_test_cases](#canonical_test_cases) | CanonicalTestCase | 20 |
| [chat_messages](#chat_messages) | ChatMessage | 6 |
| [chat_sessions](#chat_sessions) | ChatSession | 9 |
| [compliance_packs](#compliance_packs) | CompliancePack | 13 |
| [contract_violations](#contract_violations) | ContractViolation | 10 |
| [coverage_snapshots](#coverage_snapshots) | CoverageSnapshot | 7 |
| [decision_report_eval_cycles](#decision_report_eval_cycles) | DecisionReportEvalCycle | 13 |
| [decision_report_feedback](#decision_report_feedback) | DecisionReportFeedback | 17 |
| [decision_report_supersession_requests](#decision_report_supersession_requests) | DecisionReportSupersessionRequest | 14 |
| [deep_findings](#deep_findings) | DeepFinding | 13 |
| [defect_candidates](#defect_candidates) | DefectCandidate | 19 |
| [defects](#defects) | Defect | 34 |
| [deletion_jobs](#deletion_jobs) | DeletionJob | 16 |
| [digest_subscriptions](#digest_subscriptions) | DigestSubscription | 19 |
| [dismissed_duplicate_pairs](#dismissed_duplicate_pairs) | DismissedDuplicatePair | 6 |
| [duplicate_test_case_candidates](#duplicate_test_case_candidates) | DuplicateTestCaseCandidate | 11 |
| [evidence_artifacts](#evidence_artifacts) | EvidenceArtifact | 23 |
| [failure_attribution](#failure_attribution) | FailureAttribution | 10 |
| [failure_clusters](#failure_clusters) | FailureCluster | 11 |
| [feature_flags](#feature_flags) | FeatureFlag | 10 |
| [federated_identities](#federated_identities) | FederatedIdentity | 10 |
| [fix_attempts](#fix_attempts) | FixAttempt | 21 |
| [flaky_classifier_calibration](#flaky_classifier_calibration) | FlakyClassifierCalibration | 10 |
| [flaky_coach_results](#flaky_coach_results) | FlakyCoachResult | 19 |
| [flaky_detection_state](#flaky_detection_state) | FlakyDetectionState | 13 |
| [flaky_quarantine_requests](#flaky_quarantine_requests) | FlakyQuarantineRequest | 35 |
| [flaky_score](#flaky_score) | FlakyScore | 11 |
| [generation_batches](#generation_batches) | GenerationBatch | 17 |
| [generation_case_sources](#generation_case_sources) | GenerationCaseSource | 12 |
| [github_integrations](#github_integrations) | GitHubIntegration | 14 |
| [gitlab_integrations](#gitlab_integrations) | GitLabIntegration | 14 |
| [identity_events](#identity_events) | IdentityEvent | 11 |
| [integration_health_checks](#integration_health_checks) | IntegrationHealthCheck | 7 |
| [integration_probe_results](#integration_probe_results) | IntegrationProbeResult | 8 |
| [knowledge_chunks](#knowledge_chunks) | KnowledgeChunk | 12 |
| [knowledge_sources](#knowledge_sources) | KnowledgeSource | 16 |
| [knowledge_sync_events](#knowledge_sync_events) | KnowledgeSyncEvent | 12 |
| [live_event_receipts](#live_event_receipts) | LiveEventReceipt | 9 |
| [live_ingestion_attempts](#live_ingestion_attempts) | LiveIngestionAttempt | 11 |
| [live_projection_checkpoints](#live_projection_checkpoints) | LiveProjectionCheckpoint | 6 |
| [live_sessions](#live_sessions) | LiveSession | 21 |
| [managed_test_cases](#managed_test_cases) | ManagedTestCase | 51 |
| [mfa_recovery_codes](#mfa_recovery_codes) | MfaRecoveryCode | 5 |
| [model_versions](#model_versions) | ModelVersion | 16 |
| [notification_logs](#notification_logs) | NotificationLog | 24 |
| [notification_preferences](#notification_preferences) | NotificationPreference | 12 |
| [notification_test_states](#notification_test_states) | NotificationTestState | 10 |
| [notification_transition_policies](#notification_transition_policies) | NotificationTransitionPolicy | 8 |
| [perf_baselines](#perf_baselines) | PerfBaseline | 14 |
| [product_usage_events](#product_usage_events) | ProductUsageEvent | 6 |
| [project_activity_events](#project_activity_events) | ProjectActivityEvent | 24 |
| [project_llm_quota](#project_llm_quota) | ProjectLlmQuota | 12 |
| [project_llm_usage](#project_llm_usage) | ProjectLlmUsage | 10 |
| [project_members](#project_members) | ProjectMember | 5 |
| [project_retention_policies](#project_retention_policies) | ProjectRetentionPolicy | 10 |
| [projects](#projects) | Project | 18 |
| [quality_gates](#quality_gates) | QualityGate | 6 |
| [quarantine_lifecycle_policies](#quarantine_lifecycle_policies) | QuarantineLifecyclePolicy | 11 |
| [refresh_token_records](#refresh_token_records) | RefreshTokenRecord | 8 |
| [release_attribution_rules](#release_attribution_rules) | ReleaseAttributionRule | 11 |
| [release_decisions](#release_decisions) | ReleaseDecision | 21 |
| [release_gate_decisions](#release_gate_decisions) | ReleaseGateDecision | 19 |
| [release_gate_policies](#release_gate_policies) | ReleaseGatePolicy | 13 |
| [release_outcomes](#release_outcomes) | ReleaseOutcome | 7 |
| [release_phases](#release_phases) | ReleasePhase | 15 |
| [release_test_run_links](#release_test_run_links) | ReleaseTestRunLink | 9 |
| [releases](#releases) | Release | 26 |
| [report_share_links](#report_share_links) | ReportShareLink | 12 |
| [requirement_coverage](#requirement_coverage) | RequirementCoverage | 8 |
| [review_requests](#review_requests) | ReviewRequest | 22 |
| [run_baselines](#run_baselines) | RunBaseline | 10 |
| [run_commit_ranges](#run_commit_ranges) | RunCommitRange | 11 |
| [run_comparison_reports](#run_comparison_reports) | RunComparisonReport | 16 |
| [run_diffs](#run_diffs) | RunDiff | 5 |
| [run_downstream_outbox](#run_downstream_outbox) | RunDownstreamOutbox | 23 |
| [run_intelligence_snapshots](#run_intelligence_snapshots) | RunIntelligenceSnapshot | 9 |
| [run_tombstones](#run_tombstones) | RunTombstone | 6 |
| [saved_views](#saved_views) | SavedView | 11 |
| [scim_tokens](#scim_tokens) | SCIMToken | 10 |
| [secret_refs](#secret_refs) | SecretRef | 10 |
| [semantic_reindex_jobs](#semantic_reindex_jobs) | SemanticReindexJob | 17 |
| [service_ownership_rules](#service_ownership_rules) | ServiceOwnershipRule | 12 |
| [settings_audit_log](#settings_audit_log) | SettingsAuditLog | 7 |
| [sso_configurations](#sso_configurations) | SSOConfiguration | 20 |
| [suite_membership_events](#suite_membership_events) | SuiteMembershipEvent | 11 |
| [suite_memberships](#suite_memberships) | SuiteMembership | 16 |
| [suite_run_reviews](#suite_run_reviews) | SuiteRunReview | 10 |
| [systemic_flake_cluster](#systemic_flake_cluster) | SystemicFlakeCluster | 10 |
| [systemic_flake_cluster_member](#systemic_flake_cluster_member) | SystemicFlakeClusterMember | 5 |
| [team_notification_channels](#team_notification_channels) | TeamNotificationChannel | 8 |
| [tenant_metric_snapshots](#tenant_metric_snapshots) | TenantMetricSnapshot | 10 |
| [tenant_onboarding_status](#tenant_onboarding_status) | TenantOnboardingStatus | 7 |
| [test_attachments](#test_attachments) | TestAttachment | 8 |
| [test_case_audit_logs](#test_case_audit_logs) | TestCaseAuditLog | 15 |
| [test_case_comments](#test_case_comments) | TestCaseComment | 10 |
| [test_case_history](#test_case_history) | TestCaseHistory | 8 |
| [test_case_reviews](#test_case_reviews) | TestCaseReview | 13 |
| [test_case_versions](#test_case_versions) | TestCaseVersion | 28 |
| [test_cases](#test_cases) | TestCase | 44 |
| [test_execution_reviews](#test_execution_reviews) | TestExecutionReview | 10 |
| [test_health_recommendations](#test_health_recommendations) | TestHealthRecommendation | 11 |
| [test_plan_items](#test_plan_items) | TestPlanItem | 12 |
| [test_plans](#test_plans) | TestPlan | 22 |
| [test_runs](#test_runs) | TestRun | 43 |
| [test_step_runs](#test_step_runs) | TestStepRun | 10 |
| [test_steps](#test_steps) | TestStep | 17 |
| [test_strategies](#test_strategies) | TestStrategy | 24 |
| [test_suite_owners](#test_suite_owners) | TestSuiteOwner | 6 |
| [test_suites](#test_suites) | TestSuite | 8 |
| [user_invitations](#user_invitations) | UserInvitation | 8 |
| [user_ui_dismissals](#user_ui_dismissals) | UserUIDismissal | 4 |
| [users](#users) | User | 19 |
| [value_metric_assumptions](#value_metric_assumptions) | ValueMetricAssumptions | 8 |
| [webhook_deliveries](#webhook_deliveries) | WebhookDelivery | 18 |
| [webhook_subscriptions](#webhook_subscriptions) | WebhookSubscription | 16 |
| [workflow_definitions](#workflow_definitions) | WorkflowDefinition | 22 |
| [workflow_replay_corpus](#workflow_replay_corpus) | WorkflowReplayCorpus | 14 |

## access_audit_logs

[backend/app/models/postgres.py:4100](../../backend/app/models/postgres.py#L4100)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `actor_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `actor_name` | `VARCHAR(200)` | True | False | `—` |  |
| `target_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `project_id` | `UUID` | True | False | `—` | projects.id / SET NULL |
| `action` | `VARCHAR(50)` | False | False | `—` |  |
| `before_value` | `JSON` | True | False | `—` |  |
| `after_value` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `actor_user_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `target_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_aal_actor` (unique=False): `access_audit_logs.actor_user_id`; options `{}`
- Index `ix_aal_created` (unique=False): `access_audit_logs.created_at`; options `{}`
- Index `ix_aal_target` (unique=False): `access_audit_logs.target_user_id`; options `{}`

## agent_action_dispatch_outbox

[backend/app/models/postgres.py:5560](../../backend/app/models/postgres.py#L5560)

Durable delivery intent for approved actions; never contains prompts.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `action_id` | `UUID` | False | False | `—` | agent_action_ledger.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `idempotency_key` | `VARCHAR(128)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `server=pending; application=pending` |  |
| `attempts` | `INTEGER` | False | False | `server=0; application=0` |  |
| `next_attempt_at` | `DATETIME` | True | False | `—` |  |
| `lease_expires_at` | `DATETIME` | True | False | `—` |  |
| `sent_at` | `DATETIME` | True | False | `—` |  |
| `last_error` | `VARCHAR(200)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_action_outbox_attempts_nonnegative`: `attempts >= 0`
- `CheckConstraint` `ck_agent_action_outbox_status`: `status IN ('pending', 'sending', 'sent', 'failed')`
- `ForeignKeyConstraint` `unnamed`: `action_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `idempotency_key`
- `UniqueConstraint` `uq_agent_action_outbox_action`: `action_id`
- Index `ix_agent_action_outbox_status_next` (unique=False): `agent_action_dispatch_outbox.status, agent_action_dispatch_outbox.next_attempt_at`; options `{}`

## agent_action_ledger

[backend/app/models/postgres.py:5156](../../backend/app/models/postgres.py#L5156)

Append-only proposal/execution ledger for agent-originated actions.

The ledger is the durable approval boundary for external mutations. The
request payload is sanitized and hashed at proposal time; execution and
rollback outcomes are recorded separately so a caller can never replace a
proposal with a different payload under the same idempotency key.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `actor_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `action_type` | `VARCHAR(60)` | False | False | `—` |  |
| `target_type` | `VARCHAR(60)` | False | False | `—` |  |
| `target_id` | `VARCHAR(255)` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `server=proposed; application=proposed` |  |
| `approval_required` | `BOOLEAN` | False | False | `server=true; application=True` |  |
| `idempotency_key` | `VARCHAR(128)` | False | False | `—` |  |
| `request_sha256` | `VARCHAR(64)` | False | False | `—` |  |
| `request_payload` | `JSONB` | False | False | `application=dict` |  |
| `approved_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `approved_at` | `DATETIME` | True | False | `—` |  |
| `execution_started_at` | `DATETIME` | True | False | `—` |  |
| `execution_completed_at` | `DATETIME` | True | False | `—` |  |
| `result_payload` | `JSONB` | True | False | `—` |  |
| `rollback_payload` | `JSONB` | True | False | `—` |  |
| `error_code` | `VARCHAR(100)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_action_request_hash`: `request_sha256 ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_agent_action_status`: `status IN ('proposed', 'pending_review', 'approved', 'executing', 'executed', 'failed', 'rejected', 'rolled_back')`
- `ForeignKeyConstraint` `unnamed`: `actor_user_id`
- `ForeignKeyConstraint` `unnamed`: `approved_by`
- `ForeignKeyConstraint` `unnamed`: `pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_agent_action_project_idempotency`: `project_id, idempotency_key`
- Index `ix_agent_action_project_created` (unique=False): `agent_action_ledger.project_id, agent_action_ledger.created_at`; options `{}`
- Index `ix_agent_action_status` (unique=False): `agent_action_ledger.project_id, agent_action_ledger.status`; options `{}`
- Index `ix_agent_action_target` (unique=False): `agent_action_ledger.project_id, agent_action_ledger.target_type, agent_action_ledger.target_id`; options `{}`

## agent_child_dispatch_outbox

[backend/app/models/postgres.py:1809](../../backend/app/models/postgres.py#L1809)

Durable dispatch record for a planner-spawned Investigator child.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `spawn_key` | `VARCHAR(64)` | False | False | `—` |  |
| `investigation_id` | `UUID` | False | False | `—` | agent_investigations.id / CASCADE |
| `parent_pipeline_run_id` | `UUID` | False | False | `—` | agent_pipeline_runs.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `status` | `VARCHAR(20)` | False | False | `server=pending; application=pending` |  |
| `attempts` | `INTEGER` | False | False | `server=0; application=0` |  |
| `next_attempt_at` | `DATETIME` | True | False | `—` |  |
| `sent_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `last_error` | `TEXT` | True | False | `—` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_child_outbox_attempts_nonnegative`: `attempts >= 0`
- `CheckConstraint` `ck_agent_child_outbox_spawn_key_sha256`: `spawn_key ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_agent_child_outbox_status`: `status IN ('pending', 'sending', 'sent', 'failed')`
- `ForeignKeyConstraint` `unnamed`: `investigation_id`
- `ForeignKeyConstraint` `unnamed`: `parent_pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `investigation_id`
- `UniqueConstraint` `unnamed`: `spawn_key`
- Index `ix_agent_child_dispatch_status_next_attempt` (unique=False): `agent_child_dispatch_outbox.status, agent_child_dispatch_outbox.next_attempt_at`; options `{}`

## agent_configs

[backend/app/models/postgres.py:5385](../../backend/app/models/postgres.py#L5385)

A project's configuration of one agent (architecture E4.1, section 4.2).

``mode`` and ``enabled`` are columns; ``config`` holds the rest of the
validated ``AgentConfigV1`` document. A missing row means the defaults.
Every write bumps ``config_version``.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `agent_id` | `VARCHAR(80)` | False | False | `—` |  |
| `enabled` | `BOOLEAN` | False | False | `server=true; application=True` |  |
| `mode` | `VARCHAR(10)` | False | False | `server=shadow; application=shadow` |  |
| `config` | `JSONB` | False | False | `server='{}'::jsonb; application=dict` |  |
| `config_version` | `INTEGER` | False | False | `server=1; application=1` |  |
| `updated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_configs_mode`: `mode IN ('shadow', 'suggest', 'act')`
- `CheckConstraint` `ck_agent_configs_version_positive`: `config_version >= 1`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_agent_configs_project_agent`: `project_id, agent_id`

## agent_investigations

[backend/app/models/postgres.py:1665](../../backend/app/models/postgres.py#L1665)

One Investigator run against a test run (Agentic plan AI-1, shadow).

Persists the full InvestigationDetail wire shape served by
``GET /api/v1/investigations/{id}``: lifecycle status, mode, trigger,
budget + spend JSONB, the per-hypothesis results (written incrementally
as hypothesis nodes complete — the UI polls), the synthesis verdict, the
prompt-version snapshot, and the cooperative-cancel flag.

One ACTIVE investigation per run is enforced by the partial unique index
``uq_agent_investigations_one_active_per_run`` (migration 0108) over
``ACTIVE_STATUSES`` — the API's 409 is backed by a real constraint.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `scope_type` | `VARCHAR(20)` | False | False | `server=run; application=run` |  |
| `failure_cluster_id` | `UUID` | True | False | `—` | failure_clusters.id / CASCADE |
| `cluster_scope_sha256` | `VARCHAR(64)` | True | False | `—` |  |
| `cluster_member_test_ids` | `JSONB` | False | False | `server='[]'::jsonb; application=list` |  |
| `parent_pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / CASCADE |
| `parent_task_id` | `VARCHAR(255)` | True | False | `—` |  |
| `spawn_lineage_id` | `UUID` | True | False | `—` |  |
| `spawn_depth` | `INTEGER` | False | False | `server=0; application=0` |  |
| `spawn_key` | `VARCHAR(64)` | True | False | `—` |  |
| `selection_reason` | `TEXT` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=queued` |  |
| `mode` | `VARCHAR(10)` | False | False | `application=shadow` |  |
| `triggered_by` | `VARCHAR(40)` | False | False | `application=manual` |  |
| `requested_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `budget` | `JSONB` | False | False | `application=dict` |  |
| `spend` | `JSONB` | False | False | `application=dict` |  |
| `hypotheses` | `JSONB` | False | False | `application=list` |  |
| `verdict` | `JSONB` | True | False | `—` |  |
| `prompt_versions` | `JSONB` | True | False | `—` |  |
| `model_info` | `JSONB` | True | False | `—` |  |
| `cancel_requested` | `BOOLEAN` | False | False | `application=False` |  |
| `cancelled_by` | `VARCHAR(255)` | True | False | `—` |  |
| `error` | `TEXT` | True | False | `—` |  |
| `started_at` | `DATETIME` | True | False | `—` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_investigation_cluster_members_array`: `jsonb_typeof(cluster_member_test_ids) = 'array'`
- `CheckConstraint` `ck_agent_investigation_cluster_scope_sha256`: `cluster_scope_sha256 IS NULL OR cluster_scope_sha256 ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_agent_investigation_scope_consistency`: `(scope_type = 'run' AND failure_cluster_id IS NULL AND cluster_scope_sha256 IS NULL AND parent_pipeline_run_id IS NULL AND parent_task_id IS NULL AND spawn_lineage_id IS NULL AND spawn_key IS NULL AND spawn_depth = 0 AND jsonb_array_length(cluster_member_test_ids) = 0) OR (scope_type = 'failure_cluster' AND failure_cluster_id IS NOT NULL AND cluster_scope_sha256 IS NOT NULL AND parent_pipeline_run_id IS NOT NULL AND parent_task_id IS NOT NULL AND length(parent_task_id) > 0 AND spawn_lineage_id IS NOT NULL AND spawn_key IS NOT NULL AND spawn_depth >= 1 AND jsonb_array_length(cluster_member_test_ids) > 0)`
- `CheckConstraint` `ck_agent_investigation_scope_type`: `scope_type IN ('run', 'failure_cluster')`
- `CheckConstraint` `ck_agent_investigation_spawn_depth`: `spawn_depth >= 0 AND spawn_depth <= 8`
- `CheckConstraint` `ck_agent_investigation_spawn_key_sha256`: `spawn_key IS NULL OR spawn_key ~ '^[0-9a-f]{64}$'`
- `ForeignKeyConstraint` `unnamed`: `failure_cluster_id`
- `ForeignKeyConstraint` `unnamed`: `parent_pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `requested_by`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_agent_investigations_project_created` (unique=False): `agent_investigations.project_id, agent_investigations.created_at`; options `{}`
- Index `ix_agent_investigations_run` (unique=False): `agent_investigations.run_id`; options `{}`
- Index `uq_agent_investigations_active_failure_cluster` (unique=True): `agent_investigations.parent_pipeline_run_id, agent_investigations.failure_cluster_id`; options `{"postgresql_where": "scope_type = 'failure_cluster' AND status IN ('queued', 'running', 'synthesizing')"}`
- Index `uq_agent_investigations_active_run_scope` (unique=True): `agent_investigations.run_id`; options `{"postgresql_where": "scope_type = 'run' AND status IN ('queued', 'running', 'synthesizing')"}`
- Index `ux_agent_investigations_spawn_key` (unique=True): `agent_investigations.spawn_key`; options `{"postgresql_where": "spawn_key IS NOT NULL"}`

## agent_invocations

[backend/app/models/postgres.py:5322](../../backend/app/models/postgres.py#L5322)

One call of a single agent through the public API (architecture E1.2).

Carries no status of its own: the invocation runs as the pipeline run named
by ``pipeline_run_id`` (minted here before the worker creates that run, so
it is not a foreign key), and status, attempts and review are read from it.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `agent_id` | `VARCHAR(80)` | False | False | `—` |  |
| `stage_name` | `VARCHAR(60)` | False | False | `—` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `pipeline_run_id` | `UUID` | False | False | `—` |  |
| `workflow_type` | `VARCHAR(20)` | False | False | `—` |  |
| `mode` | `VARCHAR(10)` | False | False | `server=async; application=async` |  |
| `requested_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `correlation_id` | `VARCHAR(128)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `dispatched_at` | `DATETIME` | True | False | `—` |  |
| `cancel_requested` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `idempotency_key` | `VARCHAR(128)` | True | False | `—` |  |
| `request_sha256` | `VARCHAR(64)` | True | False | `—` |  |
| `resolved_config_snapshot` | `JSONB` | True | False | `—` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_invocations_mode`: `mode IN ('sync', 'async')`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `requested_by`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_agent_invocations_project_created` (unique=False): `agent_invocations.project_id, agent_invocations.created_at`; options `{}`
- Index `ix_agent_invocations_run_agent` (unique=False): `agent_invocations.test_run_id, agent_invocations.agent_id, agent_invocations.created_at`; options `{}`
- Index `ux_agent_invocations_pipeline_run` (unique=True): `agent_invocations.pipeline_run_id`; options `{}`
- Index `ux_agent_invocations_scoped_idempotency_key` (unique=True): `agent_invocations.requested_by, agent_invocations.project_id, agent_invocations.agent_id, agent_invocations.idempotency_key`; options `{"postgresql_where": "idempotency_key IS NOT NULL"}`

## agent_memory_entries

[backend/app/models/postgres.py:5101](../../backend/app/models/postgres.py#L5101)

Unified memory linking a run/snapshot to related entities (clusters, defects,
release decisions, ownership) for project-scoped historical recall.

Each entry represents a single relationship discovered during a pipeline run.
Agents query these entries to retrieve similar historical failures, prior
defect decisions, and release outcomes for the same project.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `entity_type` | `VARCHAR(50)` | False | False | `—` |  |
| `entity_id` | `VARCHAR(200)` | False | False | `—` |  |
| `error_signature` | `TEXT` | True | False | `—` |  |
| `failure_category` | `VARCHAR(50)` | True | False | `—` |  |
| `root_cause_summary` | `TEXT` | True | False | `—` |  |
| `payload` | `JSON` | True | False | `—` |  |
| `confidence` | `INTEGER` | True | False | `—` |  |
| `resolution` | `VARCHAR(50)` | True | False | `—` |  |
| `source_type` | `VARCHAR(40)` | False | False | `application=pipeline_agent` |  |
| `trust_level` | `VARCHAR(30)` | False | False | `application=derived` |  |
| `lifecycle_status` | `VARCHAR(20)` | False | False | `application=active` |  |
| `source_snapshot_id` | `VARCHAR(128)` | True | False | `—` |  |
| `source_hash` | `VARCHAR(64)` | True | False | `—` |  |
| `expires_at` | `DATETIME` | True | False | `—` |  |
| `superseded_by_id` | `UUID` | True | False | `—` | agent_memory_entries.id / SET NULL |
| `superseded_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `ForeignKeyConstraint` `unnamed`: `superseded_by_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_ame_created` (unique=False): `agent_memory_entries.created_at`; options `{}`
- Index `ix_ame_entity` (unique=False): `agent_memory_entries.entity_type, agent_memory_entries.entity_id`; options `{}`
- Index `ix_ame_project_entity` (unique=False): `agent_memory_entries.project_id, agent_memory_entries.entity_type`; options `{}`
- Index `ix_ame_project_id` (unique=False): `agent_memory_entries.project_id`; options `{}`
- Index `ix_ame_run_id` (unique=False): `agent_memory_entries.run_id`; options `{}`

## agent_pipeline_runs

[backend/app/models/postgres.py:1977](../../backend/app/models/postgres.py#L1977)

Tracks a single execution of the multi-agent pipeline for a test run.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `requested_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `parent_pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `parent_task_id` | `VARCHAR(255)` | True | False | `—` |  |
| `spawn_depth` | `INTEGER` | False | False | `server=0; application=0` |  |
| `workflow_type` | `VARCHAR(20)` | False | False | `application=offline` |  |
| `status` | `VARCHAR(20)` | False | False | `application=pending` |  |
| `started_at` | `DATETIME` | True | False | `—` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |
| `error` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `attempt` | `INTEGER` | False | False | `server=1; application=1` |  |
| `max_attempts` | `INTEGER` | False | False | `server=5; application=5` |  |
| `next_retry_at` | `DATETIME` | True | False | `—` |  |
| `lease_owner` | `VARCHAR(255)` | True | False | `—` |  |
| `lease_expires_at` | `DATETIME` | True | False | `—` |  |
| `fencing_token` | `VARCHAR(64)` | True | False | `—` |  |
| `heartbeat_at` | `DATETIME` | True | False | `—` |  |
| `cancel_requested` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `review_policy` | `VARCHAR(40)` | False | False | `server=human_required; application=human_required` |  |
| `rerun_of` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `execution_metadata` | `JSON` | True | False | `—` |  |
| `provenance_metadata` | `JSON` | True | False | `—` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_pipeline_spawn_depth`: `spawn_depth >= 0 AND spawn_depth <= 8`
- `CheckConstraint` `ck_agent_pipeline_status`: `status IN ('pending', 'running', 'retry_wait', 'completed', 'passed', 'failed')`
- `ForeignKeyConstraint` `unnamed`: `parent_pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `requested_by`
- `ForeignKeyConstraint` `unnamed`: `rerun_of`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_pipeline_runs_status` (unique=False): `agent_pipeline_runs.status`; options `{}`
- Index `ix_pipeline_runs_test_run` (unique=False): `agent_pipeline_runs.test_run_id`; options `{}`

ORM navigation and cascade declarations:

```python
stages: Mapped[list['AgentStageResult']] = relationship('AgentStageResult', back_populates='pipeline_run', cascade='all, delete-orphan')
```

## agent_runs

[backend/app/models/postgres.py:1875](../../backend/app/models/postgres.py#L1875)

Agent activity ledger (Agentic plan AI-3): one row per agent
execution — what ran, why, what it proposed, what it actually did
(always nothing in shadow/suggest), and what it cost.

Written on every investigation completion/cancel/failure; mirrored as a
durable event to the Mongo pipeline event log; snapshotted into release
compliance packs (``agent_activity.json``).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `agent_id` | `VARCHAR(50)` | False | False | `—` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `mode` | `VARCHAR(10)` | False | False | `application=shadow` |  |
| `trigger` | `VARCHAR(40)` | False | False | `application=manual` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `summary` | `TEXT` | False | False | `application=` |  |
| `actions_proposed` | `JSONB` | False | False | `application=list` |  |
| `actions_taken` | `JSONB` | False | False | `application=list` |  |
| `tokens` | `INTEGER` | False | False | `application=0` |  |
| `cost_usd` | `FLOAT` | False | False | `application=0.0` |  |
| `duration_ms` | `INTEGER` | False | False | `application=0` |  |
| `prompt_registry_digest` | `VARCHAR(64)` | True | False | `—` |  |
| `details_path` | `VARCHAR(500)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_agent_runs_project_agent_created` (unique=False): `agent_runs.project_id, agent_runs.agent_id, agent_runs.created_at`; options `{}`

## agent_stage_results

[backend/app/models/postgres.py:2046](../../backend/app/models/postgres.py#L2046)

Per-stage result for an AgentPipelineRun.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `pipeline_run_id` | `UUID` | False | False | `—` | agent_pipeline_runs.id / CASCADE |
| `task_key` | `VARCHAR(255)` | True | False | `—` |  |
| `capability_id` | `VARCHAR(120)` | True | False | `—` |  |
| `parent_task_key` | `VARCHAR(255)` | True | False | `—` |  |
| `failure_cluster_id` | `UUID` | True | False | `—` | failure_clusters.id / SET NULL |
| `attempt` | `INTEGER` | False | False | `server=1; application=1` |  |
| `selected` | `BOOLEAN` | True | False | `—` |  |
| `required` | `BOOLEAN` | True | False | `—` |  |
| `dependencies` | `JSON` | True | False | `—` |  |
| `allocated_budget` | `JSON` | True | False | `—` |  |
| `stop_reason` | `VARCHAR(100)` | True | False | `—` |  |
| `idempotency_key` | `VARCHAR(64)` | True | False | `—` |  |
| `lease_expires_at` | `DATETIME` | True | False | `—` |  |
| `stage_name` | `VARCHAR(50)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=pending` |  |
| `started_at` | `DATETIME` | True | False | `—` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |
| `result_data` | `JSON` | True | False | `—` |  |
| `error` | `TEXT` | True | False | `—` |  |
| `skipped_reason` | `TEXT` | True | False | `—` |  |
| `execution_path` | `VARCHAR(50)` | True | False | `—` |  |
| `fallback_used` | `BOOLEAN` | True | False | `—` |  |
| `checkpoint_data` | `JSON` | True | False | `—` |  |
| `input_tokens` | `INTEGER` | True | False | `—` |  |
| `output_tokens` | `INTEGER` | True | False | `—` |  |
| `total_tokens` | `INTEGER` | True | False | `—` |  |
| `llm_calls_count` | `INTEGER` | True | False | `—` |  |
| `cost_usd` | `FLOAT` | True | False | `—` |  |
| `error_category` | `VARCHAR(30)` | True | False | `—` |  |
| `confidence_score` | `INTEGER` | True | False | `—` |  |
| `evidence_count` | `INTEGER` | True | False | `—` |  |
| `route_rationale` | `TEXT` | True | False | `—` |  |
| `decision_log` | `JSON` | True | False | `—` |  |
| `fallback_reason` | `VARCHAR(200)` | True | False | `—` |  |
| `analysis_mode` | `VARCHAR(20)` | True | False | `—` |  |

Constraints and indexes:

- `CheckConstraint` `ck_agent_stage_attempt_positive`: `attempt >= 1`
- `CheckConstraint` `ck_agent_stage_idempotency_sha256`: `idempotency_key IS NULL OR idempotency_key ~ '^[0-9a-f]{64}$'`
- `ForeignKeyConstraint` `unnamed`: `failure_cluster_id`
- `ForeignKeyConstraint` `unnamed`: `pipeline_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_agent_stage_results_stage_status` (unique=False): `agent_stage_results.stage_name, agent_stage_results.status`; options `{}`
- Index `ix_stage_results_pipeline` (unique=False): `agent_stage_results.pipeline_run_id`; options `{}`
- Index `ux_agent_stage_pipeline_idempotency` (unique=True): `agent_stage_results.pipeline_run_id, agent_stage_results.idempotency_key`; options `{"postgresql_where": "idempotency_key IS NOT NULL"}`
- Index `ux_agent_stage_pipeline_task_key` (unique=True): `agent_stage_results.pipeline_run_id, agent_stage_results.task_key`; options `{"postgresql_where": "task_key IS NOT NULL"}`

ORM navigation and cascade declarations:

```python
pipeline_run: Mapped['AgentPipelineRun'] = relationship('AgentPipelineRun', back_populates='stages')
```

## ai_analysis

[backend/app/models/postgres.py:1308](../../backend/app/models/postgres.py#L1308)

Stored AI triage results per test case.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_case_id` | `UUID` | False | False | `—` | test_cases.id / CASCADE |
| `root_cause_summary` | `TEXT` | True | False | `—` |  |
| `failure_category` | `VARCHAR(30)` | True | False | `—` |  |
| `backend_error_found` | `BOOLEAN` | False | False | `application=False` |  |
| `pod_issue_found` | `BOOLEAN` | False | False | `application=False` |  |
| `is_flaky` | `BOOLEAN` | False | False | `application=False` |  |
| `confidence_score` | `INTEGER` | True | False | `—` |  |
| `recommended_actions` | `JSON` | True | False | `—` |  |
| `evidence_references` | `JSON` | True | False | `—` |  |
| `tools_used` | `JSON` | True | False | `—` |  |
| `role_actions` | `JSON` | True | False | `—` |  |
| `llm_provider` | `VARCHAR(50)` | True | False | `—` |  |
| `llm_model` | `VARCHAR(100)` | True | False | `—` |  |
| `requires_human_review` | `BOOLEAN` | False | False | `application=False` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `routing_metadata` | `JSONB` | True | False | `—` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `test_case_id`

ORM navigation and cascade declarations:

```python
test_case: Mapped['TestCase'] = relationship('TestCase', back_populates='ai_analysis')
```

## ai_eval_baselines

[backend/app/models/postgres.py:4824](../../backend/app/models/postgres.py#L4824)

Baseline metrics per agent/task_type for comparison gating.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `task_type` | `VARCHAR(50)` | False | False | `—` |  |
| `agent_name` | `VARCHAR(100)` | False | False | `—` |  |
| `prompt_version` | `VARCHAR(50)` | False | False | `application=v1` |  |
| `model_name` | `VARCHAR(200)` | True | False | `—` |  |
| `baseline_accuracy` | `FLOAT` | True | False | `—` |  |
| `baseline_precision` | `FLOAT` | True | False | `—` |  |
| `baseline_recall` | `FLOAT` | True | False | `—` |  |
| `baseline_f1` | `FLOAT` | True | False | `—` |  |
| `min_accuracy` | `FLOAT` | False | False | `application=0.8` |  |
| `min_f1` | `FLOAT` | False | False | `application=0.75` |  |
| `max_regression_pct` | `FLOAT` | False | False | `application=5.0` |  |
| `eval_run_id` | `UUID` | True | False | `—` | ai_eval_runs.id / SET NULL |
| `dataset_id` | `UUID` | True | False | `—` | ai_eval_datasets.id / SET NULL |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `created_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by`
- `ForeignKeyConstraint` `unnamed`: `dataset_id`
- `ForeignKeyConstraint` `unnamed`: `eval_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_aeb_task_agent_prompt`: `task_type, agent_name, prompt_version`
- Index `ix_aeb_task_agent` (unique=False): `ai_eval_baselines.task_type, ai_eval_baselines.agent_name`; options `{}`

## ai_eval_datasets

[backend/app/models/postgres.py:4768](../../backend/app/models/postgres.py#L4768)

Labeled evaluation dataset for measuring AI quality over time.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `task_type` | `VARCHAR(50)` | False | False | `—` |  |
| `items` | `JSON` | False | False | `application=list` |  |
| `item_count` | `INTEGER` | False | False | `application=0` |  |
| `created_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_aed_task_type` (unique=False): `ai_eval_datasets.task_type`; options `{}`

## ai_eval_gate_runs

[backend/app/models/postgres.py:4855](../../backend/app/models/postgres.py#L4855)

Historical record of an agent-stack release gate decision.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `change_id` | `VARCHAR(200)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `manifest_checksum_sha256` | `VARCHAR(64)` | False | False | `—` |  |
| `manifest` | `JSONB` | False | False | `—` |  |
| `gate_results` | `JSONB` | False | False | `application=list` |  |
| `blocking_gates` | `JSONB` | False | False | `application=list` |  |
| `version_changes` | `JSONB` | False | False | `application=list` |  |
| `gate_type` | `VARCHAR(40)` | True | False | `—` |  |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `agent_id` | `VARCHAR(80)` | True | False | `—` |  |
| `baseline_tier` | `VARCHAR(20)` | True | False | `—` |  |
| `candidate_tier` | `VARCHAR(20)` | True | False | `—` |  |
| `sample_count` | `INTEGER` | True | False | `—` |  |
| `evaluated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `evaluated_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `evaluated_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_aeg_change_id` (unique=False): `ai_eval_gate_runs.change_id`; options `{}`
- Index `ix_aeg_evaluated_at` (unique=False): `ai_eval_gate_runs.evaluated_at`; options `{}`
- Index `ix_aeg_manifest_checksum` (unique=False): `ai_eval_gate_runs.manifest_checksum_sha256`; options `{}`
- Index `ix_aeg_status` (unique=False): `ai_eval_gate_runs.status`; options `{}`
- Index `ix_aeg_tier_comparison_lookup` (unique=False): `ai_eval_gate_runs.project_id, ai_eval_gate_runs.agent_id, ai_eval_gate_runs.candidate_tier, ai_eval_gate_runs.evaluated_at`; options `{"postgresql_where": "gate_type = 'tier_comparison'"}`

## ai_eval_reviewer_quality

[backend/app/models/postgres.py:4922](../../backend/app/models/postgres.py#L4922)

A G3 reviewer-quality observation batch or scheduled 30-day rollup.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `agent_id` | `VARCHAR(80)` | False | False | `—` |  |
| `source` | `VARCHAR(24)` | False | False | `—` |  |
| `status` | `VARCHAR(24)` | False | False | `—` |  |
| `manifest_checksum_sha256` | `VARCHAR(64)` | False | False | `—` |  |
| `window_started_at` | `DATETIME` | False | False | `—` |  |
| `window_ended_at` | `DATETIME` | False | False | `—` |  |
| `mutation_observations` | `JSONB` | False | False | `application=list` |  |
| `clean_observations` | `JSONB` | False | False | `application=list` |  |
| `human_outcomes` | `JSONB` | False | False | `application=list` |  |
| `metrics` | `JSONB` | False | False | `application=list` |  |
| `regressions` | `JSONB` | False | False | `application=list` |  |
| `semantic_sample_counts` | `JSONB` | False | False | `application=dict` |  |
| `clean_sample_count` | `INTEGER` | False | False | `application=0` |  |
| `human_outcome_count` | `INTEGER` | False | False | `application=0` |  |
| `deterministic_recall` | `FLOAT` | True | False | `—` |  |
| `second_model_recall` | `FLOAT` | True | False | `—` |  |
| `recall_delta` | `FLOAT` | True | False | `—` |  |
| `false_flag_rate` | `FLOAT` | True | False | `—` |  |
| `false_omission_rate` | `FLOAT` | True | False | `—` |  |
| `auto_disable_eligible` | `BOOLEAN` | False | False | `application=False` |  |
| `auto_disable_applied` | `BOOLEAN` | False | False | `application=False` |  |
| `evaluated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `evaluated_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_aerq_sample_counts_nonnegative`: `clean_sample_count >= 0 AND human_outcome_count >= 0`
- `CheckConstraint` `ck_aerq_source`: `source IN ('observation_batch', 'scheduled')`
- `CheckConstraint` `ck_aerq_status`: `status IN ('pass', 'fail', 'insufficient_samples')`
- `CheckConstraint` `ck_aerq_window_order`: `window_started_at <= window_ended_at`
- `ForeignKeyConstraint` `unnamed`: `evaluated_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_aerq_project_agent_evaluated` (unique=False): `ai_eval_reviewer_quality.project_id, ai_eval_reviewer_quality.agent_id, ai_eval_reviewer_quality.evaluated_at`; options `{}`

## ai_eval_runs

[backend/app/models/postgres.py:4788](../../backend/app/models/postgres.py#L4788)

Record of running an evaluation dataset against a model version.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `dataset_id` | `UUID` | False | False | `—` | ai_eval_datasets.id / CASCADE |
| `model_name` | `VARCHAR(200)` | False | False | `—` |  |
| `model_version_id` | `UUID` | True | False | `—` | model_versions.id / SET NULL |
| `task_type` | `VARCHAR(50)` | False | False | `—` |  |
| `precision` | `FLOAT` | True | False | `—` |  |
| `recall` | `FLOAT` | True | False | `—` |  |
| `f1_score` | `FLOAT` | True | False | `—` |  |
| `accuracy` | `FLOAT` | True | False | `—` |  |
| `agreement_rate` | `FLOAT` | True | False | `—` |  |
| `item_results` | `JSONB` | True | False | `—` |  |
| `total_items` | `INTEGER` | False | False | `application=0` |  |
| `correct_items` | `INTEGER` | False | False | `application=0` |  |
| `fallback_used` | `BOOLEAN` | False | False | `application=False` |  |
| `evaluated_at` | `DATETIME` | False | False | `server=now()` |  |
| `duration_ms` | `INTEGER` | True | False | `—` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `dataset_id`
- `ForeignKeyConstraint` `unnamed`: `model_version_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_aer_dataset` (unique=False): `ai_eval_runs.dataset_id`; options `{}`
- Index `ix_aer_time` (unique=False): `ai_eval_runs.evaluated_at`; options `{}`

## ai_eval_shadow_pairs

[backend/app/models/postgres.py:4892](../../backend/app/models/postgres.py#L4892)

A bounded live tier pair awaiting a human or golden-input label.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `agent_id` | `VARCHAR(80)` | False | False | `—` |  |
| `sample_key` | `VARCHAR(128)` | False | False | `—` |  |
| `incumbent_tier` | `VARCHAR(20)` | False | False | `—` |  |
| `candidate_tier` | `VARCHAR(20)` | False | False | `—` |  |
| `incumbent_output` | `JSONB` | False | False | `—` |  |
| `candidate_output` | `JSONB` | False | False | `—` |  |
| `incumbent_tokens` | `INTEGER` | False | False | `application=0` |  |
| `candidate_tokens` | `INTEGER` | False | False | `application=0` |  |
| `total_tokens` | `INTEGER` | False | False | `application=0` |  |
| `label_status` | `VARCHAR(20)` | False | False | `application=pending` |  |
| `label` | `JSONB` | True | False | `—` |  |
| `labelled_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `labelled_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_aesp_label_status`: `label_status IN ('pending', 'human_labelled', 'golden_match')`
- `CheckConstraint` `ck_aesp_tokens_nonnegative`: `total_tokens >= 0`
- `ForeignKeyConstraint` `unnamed`: `labelled_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_ai_eval_shadow_pair_sample`: `project_id, agent_id, sample_key`
- Index `ix_aesp_project_agent_created` (unique=False): `ai_eval_shadow_pairs.project_id, ai_eval_shadow_pairs.agent_id, ai_eval_shadow_pairs.created_at`; options `{}`

## ai_feedback

[backend/app/models/postgres.py:2177](../../backend/app/models/postgres.py#L2177)

Human feedback on AI triage results — the primary training signal.

Sources:
  - manual: engineer rates analysis card in the UI (explicit)
  - jira_resolved: Jira ticket created by AI was resolved (implicit positive)
  - jira_invalid: Jira ticket closed as invalid/won't-fix (implicit negative)
  - category_correction: engineer changed the failure_category in the UI

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `analysis_id` | `UUID` | False | False | `—` | ai_analysis.id / CASCADE |
| `test_case_id` | `UUID` | False | False | `—` | test_cases.id / CASCADE |
| `user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `rating` | `VARCHAR(25)` | False | False | `—` |  |
| `corrected_category` | `VARCHAR(30)` | True | False | `—` |  |
| `corrected_root_cause` | `TEXT` | True | False | `—` |  |
| `comment` | `TEXT` | True | False | `—` |  |
| `source` | `VARCHAR(50)` | False | False | `application=manual` |  |
| `exported` | `BOOLEAN` | False | False | `application=False` |  |
| `eval_manifest_checksum` | `VARCHAR(64)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_ai_feedback_eval_manifest_checksum`: `eval_manifest_checksum IS NULL OR eval_manifest_checksum ~ '^[0-9a-f]{64}$'`
- `ForeignKeyConstraint` `unnamed`: `analysis_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_ai_feedback_analysis` (unique=False): `ai_feedback.analysis_id`; options `{}`
- Index `ix_ai_feedback_created` (unique=False): `ai_feedback.created_at`; options `{}`
- Index `ix_ai_feedback_created_at` (unique=False): `ai_feedback.created_at`; options `{}`
- Index `ix_ai_feedback_exported` (unique=False): `ai_feedback.exported`; options `{}`
- Index `ix_ai_feedback_rating` (unique=False): `ai_feedback.rating`; options `{}`
- Index `ix_ai_feedback_test_case_id` (unique=False): `ai_feedback.test_case_id`; options `{}`

## ai_provenance_records

[backend/app/models/postgres.py:3931](../../backend/app/models/postgres.py#L3931)

Tracks which model/method produced each AI conclusion.

Retention (US-11.4, migration 0113): provenance is AUDIT-class data —
it must outlive the run it describes. ``run_id`` therefore detaches
(``SET NULL``) when the run is purged on the runs clock, and the row
itself is deleted only by the retention audit clock via the dedicated
``project_id`` scope column (backfilled from test_runs in 0113).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `entity_type` | `VARCHAR(50)` | False | False | `—` |  |
| `entity_id` | `UUID` | False | False | `—` |  |
| `run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `model_name` | `VARCHAR(200)` | True | False | `—` |  |
| `fallback_used` | `BOOLEAN` | False | False | `application=False` |  |
| `confidence` | `INTEGER` | True | False | `—` |  |
| `confidence_reason` | `TEXT` | True | False | `—` |  |
| `evidence_count` | `INTEGER` | False | False | `application=0` |  |
| `sources_used` | `JSON` | True | False | `—` |  |
| `deterministic_checks_used` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_provenance_entity` (unique=False): `ai_provenance_records.entity_type, ai_provenance_records.entity_id`; options `{}`
- Index `ix_provenance_project` (unique=False): `ai_provenance_records.project_id`; options `{}`
- Index `ix_provenance_run` (unique=False): `ai_provenance_records.run_id`; options `{}`

## api_keys

[backend/app/models/postgres.py:3810](../../backend/app/models/postgres.py#L3810)

Scoped personal access token (PAT) for CI/CD and API access.

When ``project_id`` is NULL the key is **user-scoped** and inherits the
owning user's project permissions.  When set, the key is
**project-scoped** — requests using this key are restricted to the
specified project.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(100)` | False | False | `—` |  |
| `key_hash` | `VARCHAR(255)` | False | False | `—` |  |
| `key_hint` | `VARCHAR(12)` | False | False | `—` |  |
| `scopes` | `JSON` | False | False | `application=list` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `expires_at` | `DATETIME` | True | False | `—` |  |
| `last_used_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `minted_by_key_id` | `UUID` | True | False | `—` | api_keys.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `ForeignKeyConstraint` `fk_api_keys_minted_by_key_id`: `minted_by_key_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `key_hash`
- Index `ix_api_keys_minted_by_key_id` (unique=False): `api_keys.minted_by_key_id`; options `{}`
- Index `ix_api_keys_project_id` (unique=False): `api_keys.project_id`; options `{}`

## app_settings

[backend/app/models/postgres.py:3741](../../backend/app/models/postgres.py#L3741)

Key-value store for application-level configuration (e.g. SMTP settings).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `key` | `VARCHAR(100)` | False | True | `—` |  |
| `value` | `JSON` | True | False | `—` |  |
| `secret_ref_id` | `UUID` | True | False | `—` | secret_refs.id / SET NULL |
| `is_secret_backed` | `BOOLEAN` | False | False | `application=False` |  |
| `updated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `secret_ref_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by`
- `PrimaryKeyConstraint` `unnamed`: `key`

## canonical_test_cases

[backend/app/models/postgres.py:1033](../../backend/app/models/postgres.py#L1033)

Project-scoped test case identity.

Merges the prior ``SuiteMembership`` lifecycle model with a relational
anchor that the per-run ``test_cases`` table FKs to. Identified by
``(project_id, test_fingerprint)`` — one row per logical test per project.
Every CanonicalTestCase belongs to exactly one TestSuite; manual re-linking
moves the row between suites without losing run history.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_suite_id` | `UUID` | False | False | `—` | test_suites.id / RESTRICT |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(1000)` | False | False | `—` |  |
| `class_name` | `VARCHAR(500)` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `server=active; application=active` |  |
| `source` | `VARCHAR(20)` | False | False | `server=execution; application=execution` |  |
| `first_seen_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `last_seen_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `deleted_at_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `managed_test_case_id` | `UUID` | True | False | `—` | managed_test_cases.id / SET NULL |
| `retirement_confirmed_at` | `DATETIME` | True | False | `—` |  |
| `retirement_confirmed_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `retirement_reason` | `VARCHAR(500)` | True | False | `—` |  |
| `deleted_observed_at` | `DATETIME` | True | False | `—` |  |
| `review_tag` | `VARCHAR(50)` | True | False | `—` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `deleted_at_run_id`
- `ForeignKeyConstraint` `unnamed`: `first_seen_run_id`
- `ForeignKeyConstraint` `unnamed`: `last_seen_run_id`
- `ForeignKeyConstraint` `unnamed`: `managed_test_case_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `retirement_confirmed_by_id`
- `ForeignKeyConstraint` `unnamed`: `test_suite_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_canonical_test_cases_project_fp`: `project_id, test_fingerprint`
- Index `ix_ctc_fingerprint` (unique=False): `canonical_test_cases.test_fingerprint`; options `{}`
- Index `ix_ctc_project_id` (unique=False): `canonical_test_cases.project_id`; options `{}`
- Index `ix_ctc_project_status` (unique=False): `canonical_test_cases.project_id, canonical_test_cases.status`; options `{}`
- Index `ix_ctc_test_suite_id` (unique=False): `canonical_test_cases.test_suite_id`; options `{}`
- Index `uq_ctc_managed_test_case_id` (unique=True): `canonical_test_cases.managed_test_case_id`; options `{"postgresql_where": "managed_test_case_id IS NOT NULL"}`

ORM navigation and cascade declarations:

```python
project: Mapped['Project'] = relationship('Project', back_populates='canonical_test_cases')
test_suite: Mapped['TestSuite'] = relationship('TestSuite', back_populates='canonical_test_cases')
test_cases: Mapped[list['TestCase']] = relationship('TestCase', back_populates='canonical_test_case')
managed_test_case: Mapped[Optional['ManagedTestCase']] = relationship('ManagedTestCase')
steps: Mapped[list['TestStep']] = relationship('TestStep', back_populates='canonical_test_case', cascade='all, delete-orphan', lazy='select')
attachments: Mapped[list['TestAttachment']] = relationship('TestAttachment', back_populates='canonical_test_case', cascade='all, delete-orphan', lazy='select')
```

## chat_messages

[backend/app/models/postgres.py:2153](../../backend/app/models/postgres.py#L2153)

A single message in a ChatSession.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `session_id` | `UUID` | False | False | `—` | chat_sessions.id / CASCADE |
| `role` | `VARCHAR(20)` | False | False | `—` |  |
| `content` | `TEXT` | False | False | `—` |  |
| `sources` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `session_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_chat_messages_session` (unique=False): `chat_messages.session_id, chat_messages.created_at`; options `{}`

ORM navigation and cascade declarations:

```python
session: Mapped['ChatSession'] = relationship('ChatSession', back_populates='messages')
```

## chat_sessions

[backend/app/models/postgres.py:2131](../../backend/app/models/postgres.py#L2131)

A conversation session between a user and the Conversation Agent.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `project_id` | `UUID` | True | False | `—` | projects.id / SET NULL |
| `active_test_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `active_report_id` | `VARCHAR(64)` | True | False | `—` |  |
| `active_report_version` | `INTEGER` | True | False | `—` |  |
| `title` | `VARCHAR(500)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `active_test_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_chat_sessions_active_test_run_id` (unique=False): `chat_sessions.active_test_run_id`; options `{}`
- Index `ix_chat_sessions_user` (unique=False): `chat_sessions.user_id`; options `{}`

ORM navigation and cascade declarations:

```python
messages: Mapped[list['ChatMessage']] = relationship('ChatMessage', back_populates='session', cascade='all, delete-orphan')
```

## compliance_packs

[backend/app/models/postgres.py:6327](../../backend/app/models/postgres.py#L6327)

Generated compliance export pack (ZIP) for a release decision.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `release_id` | `UUID` | True | False | `—` | releases.id / SET NULL |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `minio_key` | `VARCHAR(500)` | False | False | `—` |  |
| `manifest_sha256` | `VARCHAR(64)` | False | False | `—` |  |
| `file_count` | `INTEGER` | False | False | `application=0` |  |
| `bytes` | `INTEGER` | False | False | `application=0` |  |
| `retention_expires_at` | `DATETIME` | False | False | `—` |  |
| `generated_at` | `DATETIME` | False | False | `server=now()` |  |
| `generated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `metadata_snapshot` | `JSONB` | True | False | `—` |  |
| `notes` | `TEXT` | True | False | `—` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `generated_by_user_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `release_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_compliance_packs_project` (unique=False): `compliance_packs.project_id, compliance_packs.generated_at`; options `{}`
- Index `ix_compliance_packs_release` (unique=False): `compliance_packs.release_id`; options `{}`

## contract_violations

[backend/app/models/postgres.py:2426](../../backend/app/models/postgres.py#L2426)

API contract violation detected by ContractAgent.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `test_case_id` | `UUID` | False | False | `—` | test_cases.id / CASCADE |
| `endpoint` | `VARCHAR(500)` | True | False | `—` |  |
| `violation_type` | `VARCHAR(50)` | False | False | `—` |  |
| `field_path` | `VARCHAR(500)` | True | False | `—` |  |
| `expected` | `VARCHAR(500)` | True | False | `—` |  |
| `actual` | `VARCHAR(500)` | True | False | `—` |  |
| `severity` | `VARCHAR(20)` | False | False | `application=warning` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_contract_violations_run` (unique=False): `contract_violations.test_run_id`; options `{}`
- Index `ix_contract_violations_tc` (unique=False): `contract_violations.test_case_id`; options `{}`

## coverage_snapshots

[backend/app/models/postgres.py:1509](../../backend/app/models/postgres.py#L1509)

Daily test coverage snapshots for trend charts.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `snapshot_date` | `DATETIME` | False | False | `server=now()` |  |
| `total_suites` | `INTEGER` | False | False | `application=0` |  |
| `total_tests` | `INTEGER` | False | False | `application=0` |  |
| `automated_count` | `INTEGER` | False | False | `application=0` |  |
| `suite_coverage` | `JSON` | True | False | `—` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_coverage_project_date`: `project_id, snapshot_date`

## decision_report_eval_cycles

[backend/app/models/postgres.py:4984](../../backend/app/models/postgres.py#L4984)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `cycle_key` | `VARCHAR(128)` | False | False | `—` |  |
| `corpus_version` | `VARCHAR(120)` | False | False | `—` |  |
| `corpus_sha256` | `VARCHAR(64)` | False | False | `—` |  |
| `report_count` | `INTEGER` | False | False | `application=0` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `metrics` | `JSONB` | False | False | `application=dict` |  |
| `checks` | `JSONB` | False | False | `application=list` |  |
| `unavailable_metrics` | `JSONB` | False | False | `application=list` |  |
| `consecutive_passes` | `INTEGER` | False | False | `application=0` |  |
| `evaluated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `evaluated_at` | `DATETIME` | False | False | `server=now()` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_drec_consecutive_nonnegative`: `consecutive_passes >= 0`
- `CheckConstraint` `ck_drec_corpus_hash`: `corpus_sha256 ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_drec_report_count`: `report_count >= 0`
- `CheckConstraint` `ck_drec_status`: `status IN ('pass', 'warn', 'fail')`
- `ForeignKeyConstraint` `unnamed`: `evaluated_by`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_drec_cycle_key`: `cycle_key`
- Index `ix_drec_corpus_evaluated` (unique=False): `decision_report_eval_cycles.corpus_version, decision_report_eval_cycles.evaluated_at`; options `{}`
- Index `ix_drec_status` (unique=False): `decision_report_eval_cycles.status`; options `{}`

## decision_report_feedback

[backend/app/models/postgres.py:2217](../../backend/app/models/postgres.py#L2217)

Structured human feedback bound to one immutable DecisionReport version.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `report_id` | `VARCHAR(64)` | False | False | `—` |  |
| `report_version` | `INTEGER` | False | False | `—` |  |
| `report_evidence_sha256` | `VARCHAR(64)` | False | False | `—` |  |
| `user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `feedback_kind` | `VARCHAR(30)` | False | False | `—` |  |
| `utility_rating` | `VARCHAR(25)` | True | False | `—` |  |
| `claim_id` | `VARCHAR(128)` | True | False | `—` |  |
| `claim_kind` | `VARCHAR(20)` | True | False | `—` |  |
| `correction_type` | `VARCHAR(30)` | True | False | `—` |  |
| `corrected_value` | `JSON` | True | False | `—` |  |
| `reason` | `TEXT` | True | False | `—` |  |
| `evidence_refs` | `JSON` | False | False | `application=list` |  |
| `idempotency_key` | `VARCHAR(128)` | False | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_decision_report_feedback_kind`: `feedback_kind IN ('utility', 'claim_correction')`
- `CheckConstraint` `ck_decision_report_feedback_report_version_positive`: `report_version >= 1`
- `CheckConstraint` `ck_decision_report_feedback_utility_rating`: `utility_rating IS NULL OR utility_rating IN ('useful', 'partially_useful', 'not_useful')`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_decision_report_feedback_user_idempotency`: `user_id, idempotency_key`
- Index `ix_decision_report_feedback_created` (unique=False): `decision_report_feedback.created_at`; options `{}`
- Index `ix_decision_report_feedback_created_at` (unique=False): `decision_report_feedback.created_at`; options `{}`
- Index `ix_decision_report_feedback_report` (unique=False): `decision_report_feedback.project_id, decision_report_feedback.test_run_id, decision_report_feedback.report_id, decision_report_feedback.report_version`; options `{}`

## decision_report_supersession_requests

[backend/app/models/postgres.py:5046](../../backend/app/models/postgres.py#L5046)

Durable, idempotent request to publish a child-enriched report version.

The parent deep pipeline never carries child prompts or evidence through a
broker payload.  It records this small, tenant-bound request instead; a
worker later re-resolves the immutable parent report and terminal child
rows before publishing a new DecisionReportV1 version.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `parent_pipeline_run_id` | `UUID` | False | False | `—` | agent_pipeline_runs.id / CASCADE |
| `status` | `VARCHAR(20)` | False | False | `server=pending; application=pending` |  |
| `reason` | `VARCHAR(120)` | False | False | `application=children_terminal` |  |
| `attempts` | `INTEGER` | False | False | `server=0; application=0` |  |
| `next_attempt_at` | `DATETIME` | True | False | `—` |  |
| `claimed_at` | `DATETIME` | True | False | `—` |  |
| `published_report_id` | `VARCHAR(64)` | True | False | `—` |  |
| `published_report_version` | `INTEGER` | True | False | `—` |  |
| `error` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_drsr_attempts_nonnegative`: `attempts >= 0`
- `CheckConstraint` `ck_drsr_status`: `status IN ('pending', 'processing', 'published', 'rejected', 'failed')`
- `ForeignKeyConstraint` `unnamed`: `parent_pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_drsr_parent_pipeline`: `parent_pipeline_run_id`
- Index `ix_drsr_status_next_attempt` (unique=False): `decision_report_supersession_requests.status, decision_report_supersession_requests.next_attempt_at`; options `{}`

## deep_findings

[backend/app/models/postgres.py:2359](../../backend/app/models/postgres.py#L2359)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `cluster_id` | `VARCHAR(20)` | False | False | `—` |  |
| `root_cause` | `TEXT` | True | False | `—` |  |
| `failure_category` | `VARCHAR(30)` | True | False | `—` |  |
| `confidence_score` | `INTEGER` | True | False | `—` |  |
| `causal_chain` | `JSON` | True | False | `—` |  |
| `evidence` | `JSON` | True | False | `—` |  |
| `affected_services` | `JSON` | True | False | `—` |  |
| `contract_violations` | `JSON` | True | False | `—` |  |
| `log_evidence` | `JSON` | True | False | `—` |  |
| `recommended_actions` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_deep_findings_run` (unique=False): `deep_findings.test_run_id`; options `{}`

## defect_candidates

[backend/app/models/postgres.py:1460](../../backend/app/models/postgres.py#L1460)

Staging area for defect candidates before promotion to full defects.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `cluster_id` | `VARCHAR(20)` | False | False | `—` |  |
| `severity` | `VARCHAR(20)` | False | False | `application=HIGH` |  |
| `owner_team` | `VARCHAR(255)` | True | False | `—` |  |
| `component` | `VARCHAR(255)` | True | False | `—` |  |
| `title` | `VARCHAR(500)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `duplicate_of` | `UUID` | True | False | `—` |  |
| `is_duplicate` | `BOOLEAN` | False | False | `application=False` |  |
| `evidence_bundle` | `JSON` | True | False | `—` |  |
| `criticality_scores` | `JSON` | True | False | `—` |  |
| `composite_score` | `FLOAT` | True | False | `—` |  |
| `failure_category` | `VARCHAR(30)` | True | False | `—` |  |
| `member_count` | `INTEGER` | False | False | `application=0` |  |
| `status` | `VARCHAR(30)` | False | False | `application=pending` |  |
| `promoted_defect_id` | `UUID` | True | False | `—` | defects.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `promoted_defect_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_defect_cand_run` (unique=False): `defect_candidates.run_id`; options `{}`
- Index `ix_defect_cand_status` (unique=False): `defect_candidates.status`; options `{}`

## defects

[backend/app/models/postgres.py:1344](../../backend/app/models/postgres.py#L1344)

Defect records linked to test cases or failure clusters.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_case_id` | `UUID` | True | False | `—` | test_cases.id / SET NULL |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `jira_ticket_id` | `VARCHAR(50)` | True | False | `—` |  |
| `jira_ticket_url` | `VARCHAR(1000)` | True | False | `—` |  |
| `jira_status` | `VARCHAR(50)` | True | False | `—` |  |
| `ai_confidence_score` | `INTEGER` | True | False | `—` |  |
| `failure_category` | `VARCHAR(30)` | True | False | `—` |  |
| `resolution_status` | `VARCHAR(50)` | False | False | `application=OPEN` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `resolved_at` | `DATETIME` | True | False | `—` |  |
| `cluster_id` | `VARCHAR(255)` | True | False | `—` |  |
| `title` | `VARCHAR(255)` | True | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `severity` | `VARCHAR(20)` | True | False | `—` |  |
| `component` | `VARCHAR(255)` | True | False | `—` |  |
| `owner_team` | `VARCHAR(255)` | True | False | `—` |  |
| `labels` | `JSON` | True | False | `—` |  |
| `criticality_scores` | `JSON` | True | False | `—` |  |
| `release_id` | `UUID` | True | False | `—` | releases.id / SET NULL |
| `affects_releases` | `JSON` | True | False | `—` |  |
| `evidence_bundle` | `JSON` | True | False | `—` |  |
| `duplicate_of` | `UUID` | True | False | `—` |  |
| `is_duplicate` | `BOOLEAN` | False | False | `application=False` |  |
| `promotion_source` | `VARCHAR(50)` | True | False | `—` |  |
| `approval_status` | `VARCHAR(20)` | False | False | `application=approved` |  |
| `approved_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `approved_at` | `DATETIME` | True | False | `—` |  |
| `policy_evaluation` | `JSON` | True | False | `—` |  |
| `signature_fingerprint` | `VARCHAR(64)` | True | False | `—` |  |
| `recurrence_count` | `INTEGER` | False | False | `server=0; application=0` |  |
| `last_recurrence_at` | `DATETIME` | True | False | `—` |  |
| `external_status_at` | `DATETIME` | True | False | `—` |  |
| `external_status_conflict` | `BOOLEAN` | False | False | `server=false; application=False` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `approved_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `release_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_defects_project_id` (unique=False): `defects.project_id`; options `{}`
- Index `ix_defects_project_signature` (unique=False): `defects.project_id, defects.signature_fingerprint`; options `{"postgresql_where": "signature_fingerprint IS NOT NULL"}`
- Index `ix_defects_project_status_severity` (unique=False): `defects.project_id, defects.resolution_status, defects.severity`; options `{}`
- Index `ix_defects_test_case_open_unique` (unique=True): `defects.test_case_id`; options `{"postgresql_where": "resolution_status = 'OPEN' AND test_case_id IS NOT NULL"}`

ORM navigation and cascade declarations:

```python
test_case: Mapped[Optional['TestCase']] = relationship('TestCase', back_populates='defects')
approver: Mapped[Optional['User']] = relationship('User', foreign_keys=[approved_by])
```

## deletion_jobs

[backend/app/models/postgres.py:6644](../../backend/app/models/postgres.py#L6644)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `job_kind` | `VARCHAR(20)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=queued` |  |
| `criteria` | `JSONB` | True | False | `—` |  |
| `resolved_run_ids` | `JSONB` | True | False | `—` |  |
| `candidate_hash` | `VARCHAR(64)` | True | False | `—` |  |
| `counts` | `JSONB` | True | False | `—` |  |
| `bytes_reclaimed` | `BIGINT` | True | False | `—` |  |
| `holds_honoured` | `JSONB` | True | False | `—` |  |
| `references_broken` | `JSONB` | True | False | `—` |  |
| `error` | `TEXT` | True | False | `—` |  |
| `requested_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `requested_at` | `DATETIME` | False | False | `server=now()` |  |
| `started_at` | `DATETIME` | True | False | `—` |  |
| `finished_at` | `DATETIME` | True | False | `—` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `requested_by_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_deletion_jobs_project_started` (unique=False): `deletion_jobs.project_id, deletion_jobs.started_at`; options `{}`
- Index `ix_deletion_jobs_status` (unique=False): `deletion_jobs.status`; options `{}`

## digest_subscriptions

[backend/app/models/postgres.py:4705](../../backend/app/models/postgres.py#L4705)

User subscription to a scheduled or event-driven report delivery.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `saved_view_id` | `UUID` | True | False | `—` | saved_views.id / SET NULL |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `schedule` | `VARCHAR(20)` | False | False | `application=WEEKLY` |  |
| `channel` | `VARCHAR(20)` | False | False | `application=email` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `is_paused` | `BOOLEAN` | False | False | `application=False` |  |
| `scope_type` | `VARCHAR(20)` | True | False | `application=project` |  |
| `scope_value` | `VARCHAR(255)` | True | False | `—` |  |
| `trigger_filter` | `VARCHAR(20)` | True | False | `application=all` |  |
| `send_when_unchanged` | `BOOLEAN` | False | False | `application=True` |  |
| `report_attachment` | `BOOLEAN` | False | False | `application=False` |  |
| `last_delivered_at` | `DATETIME` | True | False | `—` |  |
| `next_delivery_at` | `DATETIME` | True | False | `—` |  |
| `delivery_count` | `INTEGER` | False | False | `application=0` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `saved_view_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_ds_next` (unique=False): `digest_subscriptions.next_delivery_at`; options `{}`
- Index `ix_ds_schedule_active` (unique=False): `digest_subscriptions.schedule, digest_subscriptions.is_active, digest_subscriptions.is_paused`; options `{}`
- Index `ix_ds_user` (unique=False): `digest_subscriptions.user_id`; options `{}`

## dismissed_duplicate_pairs

[backend/app/models/postgres.py:2776](../../backend/app/models/postgres.py#L2776)

Suppression record so a dismissed duplicate pair stays dismissed (Phase 4).

Consulted by the detector before staging a candidate so a re-detection run
never resurfaces a pair the user already dismissed. Same canonical ordering
(``case_a_id < case_b_id``, DB CHECK) + uniqueness on
``(project_id, case_a_id, case_b_id)`` as the candidate table.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `case_a_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `case_b_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `dismissed_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `dismissed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_dismissed_dup_canonical_order`: `case_a_id < case_b_id`
- `ForeignKeyConstraint` `unnamed`: `case_a_id`
- `ForeignKeyConstraint` `unnamed`: `case_b_id`
- `ForeignKeyConstraint` `unnamed`: `dismissed_by_user_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_dismissed_dup_pair`: `project_id, case_a_id, case_b_id`
- Index `ix_dismissed_dup_project` (unique=False): `dismissed_duplicate_pairs.project_id`; options `{}`

## duplicate_test_case_candidates

[backend/app/models/postgres.py:2733](../../backend/app/models/postgres.py#L2733)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `case_a_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `case_b_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `band` | `VARCHAR(20)` | False | False | `—` |  |
| `score` | `FLOAT` | False | False | `—` |  |
| `reason` | `TEXT` | True | False | `—` |  |
| `method` | `VARCHAR(20)` | False | False | `—` |  |
| `component_scores` | `JSONB` | True | False | `—` |  |
| `detected_at` | `DATETIME` | False | False | `server=now()` |  |
| `status` | `VARCHAR(20)` | False | False | `server='open'; application=open` |  |

Constraints and indexes:

- `CheckConstraint` `ck_dup_candidate_canonical_order`: `case_a_id < case_b_id`
- `ForeignKeyConstraint` `unnamed`: `case_a_id`
- `ForeignKeyConstraint` `unnamed`: `case_b_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_dup_candidate_pair`: `project_id, case_a_id, case_b_id`
- Index `ix_dup_candidate_project_status` (unique=False): `duplicate_test_case_candidates.project_id, duplicate_test_case_candidates.status`; options `{}`

## evidence_artifacts

[backend/app/models/postgres.py:3863](../../backend/app/models/postgres.py#L3863)

Tenant-bound immutable evidence captured from an executed tool.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `producer_pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `cluster_id` | `VARCHAR(20)` | True | False | `—` |  |
| `test_case_id` | `UUID` | True | False | `—` | test_cases.id / SET NULL |
| `artifact_type` | `VARCHAR(50)` | False | False | `—` |  |
| `source_system` | `VARCHAR(100)` | False | False | `—` |  |
| `uri_or_ref` | `VARCHAR(1000)` | True | False | `—` |  |
| `summary_excerpt` | `TEXT` | True | False | `—` |  |
| `relevance_score` | `FLOAT` | True | False | `—` |  |
| `schema_version` | `INTEGER` | False | False | `application=2` |  |
| `source_version` | `VARCHAR(100)` | True | False | `—` |  |
| `content_sha256` | `VARCHAR(64)` | True | False | `—` |  |
| `content_size_bytes` | `BIGINT` | True | False | `—` |  |
| `media_type` | `VARCHAR(100)` | True | False | `—` |  |
| `sensitivity` | `VARCHAR(20)` | True | False | `—` |  |
| `freshness` | `VARCHAR(20)` | True | False | `—` |  |
| `observed_at` | `DATETIME` | True | False | `—` |  |
| `integrity_status` | `VARCHAR(30)` | False | False | `application=legacy_unverified` |  |
| `retention_class` | `VARCHAR(30)` | False | False | `application=artifacts` |  |
| `idempotency_key` | `VARCHAR(64)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_evidence_content_sha256`: `content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_evidence_content_size_nonnegative`: `content_size_bytes IS NULL OR content_size_bytes >= 0`
- `ForeignKeyConstraint` `unnamed`: `producer_pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_evidence_cluster` (unique=False): `evidence_artifacts.cluster_id`; options `{}`
- Index `ix_evidence_pipeline` (unique=False): `evidence_artifacts.producer_pipeline_run_id`; options `{}`
- Index `ix_evidence_project_run` (unique=False): `evidence_artifacts.project_id, evidence_artifacts.run_id`; options `{}`
- Index `ix_evidence_run_id` (unique=False): `evidence_artifacts.run_id`; options `{}`
- Index `ix_evidence_run_test` (unique=False): `evidence_artifacts.run_id, evidence_artifacts.test_case_id`; options `{}`
- Index `ux_evidence_idempotency` (unique=True): `evidence_artifacts.idempotency_key`; options `{}`

## failure_attribution

[backend/app/models/postgres.py:5800](../../backend/app/models/postgres.py#L5800)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_case_id` | `UUID` | False | False | `—` | test_cases.id / CASCADE |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `verdict` | `VARCHAR(32)` | False | False | `—` |  |
| `confidence` | `FLOAT` | False | False | `application=0.0` |  |
| `rationale` | `VARCHAR(1000)` | False | False | `application=` |  |
| `inputs` | `JSONB` | False | False | `application=dict` |  |
| `computed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_failure_attribution_project_verdict` (unique=False): `failure_attribution.project_id, failure_attribution.verdict`; options `{}`
- Index `ix_failure_attribution_run` (unique=False): `failure_attribution.test_run_id`; options `{}`
- Index `ux_failure_attribution_test_case` (unique=True): `failure_attribution.test_case_id`; options `{}`

## failure_clusters

[backend/app/models/postgres.py:2283](../../backend/app/models/postgres.py#L2283)

Semantic cluster of failures grouped by root-cause similarity.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `cluster_id` | `VARCHAR(20)` | False | False | `—` |  |
| `label` | `VARCHAR(500)` | False | False | `—` |  |
| `representative_error` | `TEXT` | True | False | `—` |  |
| `member_test_ids` | `JSON` | False | False | `application=list` |  |
| `size` | `INTEGER` | False | False | `application=1` |  |
| `cohesion_score` | `FLOAT` | True | False | `—` |  |
| `regression_classification` | `VARCHAR(50)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_failure_clusters_pipeline_cluster`: `pipeline_run_id, cluster_id`
- Index `ix_failure_clusters_run` (unique=False): `failure_clusters.test_run_id`; options `{}`

## feature_flags

[backend/app/models/postgres.py:5608](../../backend/app/models/postgres.py#L5608)

Named capability toggle gated by project, role, and rollout percent.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `key` | `VARCHAR(80)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `enabled_global` | `BOOLEAN` | False | False | `application=False` |  |
| `enabled_projects` | `JSONB` | True | False | `—` |  |
| `enabled_roles` | `JSONB` | True | False | `—` |  |
| `rollout_percent` | `INTEGER` | False | False | `application=100` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_feature_flags_key` (unique=True): `feature_flags.key`; options `{}`

## federated_identities

[backend/app/models/postgres.py:4309](../../backend/app/models/postgres.py#L4309)

Links an external IdP subject to a local User.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `sso_config_id` | `UUID` | False | False | `—` | sso_configurations.id / CASCADE |
| `external_id` | `VARCHAR(1000)` | False | False | `—` |  |
| `external_email` | `VARCHAR(255)` | True | False | `—` |  |
| `external_display_name` | `VARCHAR(500)` | True | False | `—` |  |
| `external_groups` | `JSON` | True | False | `application=list` |  |
| `last_login_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `sso_config_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_federated_identity`: `sso_config_id, external_id`
- Index `ix_federated_user` (unique=False): `federated_identities.user_id`; options `{}`

## fix_attempts

[backend/app/models/postgres.py:1913](../../backend/app/models/postgres.py#L1913)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `fixer_run_id` | `UUID` | False | False | `—` |  |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(500)` | True | False | `—` |  |
| `status` | `VARCHAR(30)` | False | False | `application=selected` |  |
| `attempt_no` | `INTEGER` | False | False | `application=1` |  |
| `patch_summary` | `TEXT` | True | False | `—` |  |
| `patch` | `TEXT` | True | False | `—` |  |
| `validation_reruns` | `INTEGER` | True | False | `—` |  |
| `validation_passed` | `INTEGER` | True | False | `—` |  |
| `runner_type` | `VARCHAR(30)` | True | False | `—` |  |
| `runner_log_digest` | `VARCHAR(128)` | True | False | `—` |  |
| `egress_opened` | `BOOLEAN` | False | False | `application=False` |  |
| `pr_url` | `VARCHAR(1000)` | True | False | `—` |  |
| `pr_number` | `INTEGER` | True | False | `—` |  |
| `pr_state` | `VARCHAR(20)` | True | False | `—` |  |
| `ledger_run_id` | `UUID` | True | False | `—` |  |
| `reason` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_fix_attempts_fixer_run` (unique=False): `fix_attempts.fixer_run_id`; options `{}`
- Index `ix_fix_attempts_project_created` (unique=False): `fix_attempts.project_id, fix_attempts.created_at`; options `{}`

## flaky_classifier_calibration

[backend/app/models/postgres.py:5927](../../backend/app/models/postgres.py#L5927)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `method` | `VARCHAR(50)` | False | False | `—` |  |
| `specificity` | `FLOAT` | True | False | `—` |  |
| `sample_count` | `INTEGER` | False | False | `application=0` |  |
| `true_negatives` | `INTEGER` | False | False | `application=0` |  |
| `false_positives` | `INTEGER` | False | False | `application=0` |  |
| `insufficient_reason` | `VARCHAR(200)` | True | False | `—` |  |
| `window_days` | `INTEGER` | False | False | `application=90` |  |
| `computed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ux_flaky_calibration_project_method` (unique=True): `flaky_classifier_calibration.project_id, flaky_classifier_calibration.method`; options `{}`

## flaky_coach_results

[backend/app/models/postgres.py:4175](../../backend/app/models/postgres.py#L4175)

Project-level flaky test coaching results with quarantine recommendations.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(1000)` | False | False | `—` |  |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `failure_rate` | `FLOAT` | False | False | `—` |  |
| `total_runs` | `INTEGER` | False | False | `application=0` |  |
| `failed_runs` | `INTEGER` | False | False | `application=0` |  |
| `flaky_since` | `DATETIME` | True | False | `—` |  |
| `last_failure_at` | `DATETIME` | True | False | `—` |  |
| `quarantine_recommendation` | `VARCHAR(30)` | False | False | `application=MONITOR` |  |
| `stabilization_actions` | `JSON` | True | False | `application=list` |  |
| `impact_score` | `FLOAT` | False | False | `application=0.0` |  |
| `status_history` | `JSON` | True | False | `application=list` |  |
| `flaky_confidence_low` | `FLOAT` | True | False | `—` |  |
| `flaky_confidence_high` | `FLOAT` | True | False | `—` |  |
| `is_flaky_confidence` | `FLOAT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_fcr_fingerprint` (unique=False): `flaky_coach_results.test_fingerprint`; options `{}`
- Index `ix_fcr_project` (unique=False): `flaky_coach_results.project_id`; options `{}`
- Index `ix_fcr_quarantine` (unique=False): `flaky_coach_results.quarantine_recommendation`; options `{}`

## flaky_detection_state

[backend/app/models/postgres.py:5963](../../backend/app/models/postgres.py#L5963)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(1000)` | True | False | `—` |  |
| `first_seen_at` | `DATETIME` | False | False | `—` |  |
| `first_seen_is_exact` | `BOOLEAN` | False | False | `application=False` |  |
| `screen_reason` | `VARCHAR(32)` | False | False | `—` |  |
| `first_screened_at` | `DATETIME` | True | False | `—` |  |
| `first_scored_at` | `DATETIME` | True | False | `—` |  |
| `first_scored_confidence` | `VARCHAR(20)` | True | False | `—` |  |
| `observation_count` | `INTEGER` | False | False | `application=0` |  |
| `last_swept_at` | `DATETIME` | True | False | `—` |  |
| `updated_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_flaky_detection_state_project_scored` (unique=False): `flaky_detection_state.project_id, flaky_detection_state.first_scored_at`; options `{}`
- Index `ix_flaky_detection_state_project_swept` (unique=False): `flaky_detection_state.project_id, flaky_detection_state.last_swept_at`; options `{}`
- Index `ux_flaky_detection_state_project_fingerprint` (unique=True): `flaky_detection_state.project_id, flaky_detection_state.test_fingerprint`; options `{}`

## flaky_quarantine_requests

[backend/app/models/postgres.py:6391](../../backend/app/models/postgres.py#L6391)

Workflow record for proposing, approving, and enforcing quarantine
of a flaky test.

One row per quarantine lifecycle. When an approved quarantine is
``RELEASED`` and the test flakes again, a fresh row is created rather
than reusing the old one — this keeps the history immutable and lets
QA leads see every past decision on the same test.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(500)` | True | False | `—` |  |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `status` | `VARCHAR(32)` | False | False | `application=PROPOSED` |  |
| `detection_method` | `VARCHAR(50)` | False | False | `application=pass_fail_ratio` |  |
| `flip_rate` | `FLOAT` | True | False | `—` |  |
| `flip_window_size` | `INTEGER` | True | False | `—` |  |
| `pass_count` | `INTEGER` | True | False | `—` |  |
| `fail_count` | `INTEGER` | True | False | `—` |  |
| `detected_at` | `DATETIME` | False | False | `server=now()` |  |
| `last_failure_at` | `DATETIME` | True | False | `—` |  |
| `proposed_at` | `DATETIME` | True | False | `—` |  |
| `approved_at` | `DATETIME` | True | False | `—` |  |
| `approved_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `rejected_at` | `DATETIME` | True | False | `—` |  |
| `rejected_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `quarantine_start` | `DATETIME` | True | False | `—` |  |
| `quarantine_expires_at` | `DATETIME` | True | False | `—` |  |
| `quarantine_duration_days` | `INTEGER` | False | False | `application=14` |  |
| `recheck_at` | `DATETIME` | True | False | `—` |  |
| `rationale` | `JSONB` | True | False | `—` |  |
| `reviewer_notes` | `TEXT` | True | False | `—` |  |
| `owner_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `defect_id` | `UUID` | True | False | `—` | defects.id / SET NULL |
| `sla_days` | `INTEGER` | True | False | `—` |  |
| `stale_at` | `DATETIME` | True | False | `—` |  |
| `stale_notified_at` | `DATETIME` | True | False | `—` |  |
| `consecutive_passes` | `INTEGER` | False | False | `application=0` |  |
| `last_stability_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `ready_to_promote` | `BOOLEAN` | False | False | `application=False` |  |
| `ready_notified_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `approved_by_user_id`
- `ForeignKeyConstraint` `unnamed`: `defect_id`
- `ForeignKeyConstraint` `unnamed`: `last_stability_run_id`
- `ForeignKeyConstraint` `unnamed`: `owner_user_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `rejected_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_fqr_fingerprint` (unique=False): `flaky_quarantine_requests.project_id, flaky_quarantine_requests.test_fingerprint`; options `{}`
- Index `ix_fqr_project_status` (unique=False): `flaky_quarantine_requests.project_id, flaky_quarantine_requests.status`; options `{}`

## flaky_score

[backend/app/models/postgres.py:5893](../../backend/app/models/postgres.py#L5893)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(1000)` | True | False | `—` |  |
| `score` | `FLOAT` | False | False | `application=0.0` |  |
| `components` | `JSONB` | False | False | `application=dict` |  |
| `weights` | `JSONB` | False | False | `application=dict` |  |
| `observation_count` | `INTEGER` | False | False | `application=0` |  |
| `confidence` | `VARCHAR(20)` | False | False | `application=none` |  |
| `window_days` | `INTEGER` | False | False | `application=30` |  |
| `computed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_flaky_score_project_score` (unique=False): `flaky_score.project_id, flaky_score.score`; options `{}`
- Index `ux_flaky_score_project_fingerprint` (unique=True): `flaky_score.project_id, flaky_score.test_fingerprint`; options `{}`

## generation_batches

[backend/app/models/postgres.py:3038](../../backend/app/models/postgres.py#L3038)

One row per AI test-case generation request (grounded or raw).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `prompt_text` | `TEXT` | True | False | `—` |  |
| `source_ids` | `JSON` | True | False | `—` |  |
| `generation_mode` | `VARCHAR(20)` | False | False | `application=raw` |  |
| `generation_config` | `JSON` | True | False | `—` |  |
| `cases_generated` | `INTEGER` | False | False | `application=0` |  |
| `cases_accepted` | `INTEGER` | False | False | `application=0` |  |
| `cases_rejected` | `INTEGER` | False | False | `application=0` |  |
| `coverage_score` | `INTEGER` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=pending` |  |
| `error_message` | `TEXT` | True | False | `—` |  |
| `prompt_redacted` | `BOOLEAN` | False | False | `application=False` |  |
| `llm_model_used` | `VARCHAR(200)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_gb_author` (unique=False): `generation_batches.created_by_id`; options `{}`
- Index `ix_gb_project_created` (unique=False): `generation_batches.project_id, generation_batches.created_at`; options `{}`

## generation_case_sources

[backend/app/models/postgres.py:3069](../../backend/app/models/postgres.py#L3069)

Maps a generated ManagedTestCase to the KnowledgeChunks cited for it.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `batch_id` | `UUID` | False | False | `—` | generation_batches.id / CASCADE |
| `case_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `source_id` | `UUID` | False | False | `—` | knowledge_sources.id / CASCADE |
| `chunk_vector_id` | `VARCHAR(64)` | False | False | `—` |  |
| `relevance_score` | `FLOAT` | True | False | `—` |  |
| `section_heading` | `VARCHAR(500)` | True | False | `—` |  |
| `chunk_text_preview` | `VARCHAR(500)` | True | False | `—` |  |
| `source_content_hash_at_generation` | `VARCHAR(64)` | True | False | `—` |  |
| `is_stale` | `BOOLEAN` | False | False | `application=False` |  |
| `stale_detected_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `batch_id`
- `ForeignKeyConstraint` `unnamed`: `case_id`
- `ForeignKeyConstraint` `unnamed`: `source_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_gcs_case_chunk`: `case_id, chunk_vector_id`
- Index `ix_gcs_batch_id` (unique=False): `generation_case_sources.batch_id`; options `{}`
- Index `ix_gcs_case_id` (unique=False): `generation_case_sources.case_id`; options `{}`
- Index `ix_gcs_source_id` (unique=False): `generation_case_sources.source_id`; options `{}`
- Index `ix_gcs_stale` (unique=False): `generation_case_sources.is_stale`; options `{}`

## github_integrations

[backend/app/models/postgres.py:6185](../../backend/app/models/postgres.py#L6185)

Per-project GitHub Checks API integration config.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `enabled` | `BOOLEAN` | False | False | `application=False` |  |
| `repo_owner` | `VARCHAR(255)` | False | False | `—` |  |
| `repo_name` | `VARCHAR(255)` | False | False | `—` |  |
| `api_base_url` | `VARCHAR(500)` | False | False | `application=https://api.github.com` |  |
| `has_pat` | `BOOLEAN` | False | False | `application=False` |  |
| `pr_comment_mode` | `VARCHAR(20)` | False | False | `server=failures_only; application=failures_only` |  |
| `last_posted_at` | `DATETIME` | True | False | `—` |  |
| `last_error` | `TEXT` | True | False | `—` |  |
| `last_error_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `project_id`
- Index `ix_github_integrations_project` (unique=True): `github_integrations.project_id`; options `{}`

## gitlab_integrations

[backend/app/models/postgres.py:6244](../../backend/app/models/postgres.py#L6244)

Per-project GitLab integration config (PMF Epic 3 US-3.1).

Mirror of ``GitHubIntegration`` for GitLab (self-managed or gitlab.com).
Drives the sticky **MR note** (US-3.2) and **commit status** (US-3.2)
outbound surfaces. The PAT lives in ``secret_service`` under scope
``gitlab_integration``, key ``project:{project_id}:pat`` — never a column,
so a DB dump cannot recover active tokens.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `enabled` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `base_url` | `VARCHAR(500)` | False | False | `server=https://gitlab.com; application=https://gitlab.com` |  |
| `project_path` | `VARCHAR(500)` | False | False | `server=; application=` |  |
| `mr_comment_mode` | `VARCHAR(20)` | False | False | `server=failures_only; application=failures_only` |  |
| `commit_status_enabled` | `BOOLEAN` | False | False | `server=true; application=True` |  |
| `has_pat` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `last_posted_at` | `DATETIME` | True | False | `—` |  |
| `last_error` | `TEXT` | True | False | `—` |  |
| `last_error_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `project_id`
- Index `ix_gitlab_integrations_project` (unique=True): `gitlab_integrations.project_id`; options `{}`

## identity_events

[backend/app/models/postgres.py:4348](../../backend/app/models/postgres.py#L4348)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `event_type` | `VARCHAR(40)` | False | False | `—` |  |
| `user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `sso_config_id` | `UUID` | True | False | `—` | sso_configurations.id / SET NULL |
| `actor_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `actor_name` | `VARCHAR(200)` | True | False | `—` |  |
| `detail` | `JSON` | True | False | `—` |  |
| `ip_address` | `VARCHAR(45)` | True | False | `—` |  |
| `success` | `BOOLEAN` | False | False | `application=True` |  |
| `error_message` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `actor_id`
- `ForeignKeyConstraint` `unnamed`: `sso_config_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_identity_event_created` (unique=False): `identity_events.created_at`; options `{}`
- Index `ix_identity_event_type` (unique=False): `identity_events.event_type`; options `{}`
- Index `ix_identity_event_user` (unique=False): `identity_events.user_id`; options `{}`

## integration_health_checks

[backend/app/models/postgres.py:4037](../../backend/app/models/postgres.py#L4037)

Integration provider health status (latest snapshot per provider).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `provider` | `VARCHAR(50)` | False | True | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=unknown` |  |
| `last_checked_at` | `DATETIME` | True | False | `—` |  |
| `message` | `TEXT` | True | False | `—` |  |
| `response_ms` | `INTEGER` | True | False | `—` |  |
| `consecutive_failures` | `INTEGER` | False | False | `application=0` |  |
| `last_success_at` | `DATETIME` | True | False | `—` |  |

Constraints and indexes:

- `PrimaryKeyConstraint` `unnamed`: `provider`

## integration_probe_results

[backend/app/models/postgres.py:4050](../../backend/app/models/postgres.py#L4050)

Historical record of each integration health probe (OPS-01).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `provider` | `VARCHAR(50)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `response_ms` | `INTEGER` | True | False | `—` |  |
| `message` | `TEXT` | True | False | `—` |  |
| `auth_valid` | `BOOLEAN` | True | False | `—` |  |
| `payload_valid` | `BOOLEAN` | True | False | `—` |  |
| `checked_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_ipr_provider_time` (unique=False): `integration_probe_results.provider, integration_probe_results.checked_at`; options `{}`

## knowledge_chunks

[backend/app/models/postgres.py:3008](../../backend/app/models/postgres.py#L3008)

PostgreSQL metadata for each chunk stored in ChromaDB knowledge_chunks collection.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `source_id` | `UUID` | False | False | `—` | knowledge_sources.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `vector_id` | `VARCHAR(64)` | False | False | `—` |  |
| `section_heading` | `VARCHAR(500)` | True | False | `—` |  |
| `requirement_id` | `VARCHAR(200)` | True | False | `—` |  |
| `chunk_index` | `INTEGER` | False | False | `—` |  |
| `chunk_text_preview` | `VARCHAR(500)` | True | False | `—` |  |
| `token_count` | `INTEGER` | True | False | `—` |  |
| `sync_version` | `INTEGER` | False | False | `application=1` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `source_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `vector_id`
- Index `ix_kc_project_active` (unique=False): `knowledge_chunks.project_id, knowledge_chunks.is_active`; options `{}`
- Index `ix_kc_source_active` (unique=False): `knowledge_chunks.source_id, knowledge_chunks.is_active`; options `{}`
- Index `ix_kc_sync_version` (unique=False): `knowledge_chunks.source_id, knowledge_chunks.sync_version`; options `{}`

## knowledge_sources

[backend/app/models/postgres.py:2947](../../backend/app/models/postgres.py#L2947)

Registry of external knowledge sources attached to a project for RAG-grounded test generation.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `source_type` | `VARCHAR(30)` | False | False | `—` |  |
| `title` | `VARCHAR(500)` | False | False | `—` |  |
| `canonical_url` | `VARCHAR(2000)` | False | False | `—` |  |
| `external_id` | `VARCHAR(500)` | True | False | `—` |  |
| `owner_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `sync_status` | `VARCHAR(20)` | False | False | `application=pending` |  |
| `last_synced_at` | `DATETIME` | True | False | `—` |  |
| `sync_error` | `TEXT` | True | False | `—` |  |
| `content_hash` | `VARCHAR(64)` | True | False | `—` |  |
| `classification` | `VARCHAR(20)` | False | False | `application=internal` |  |
| `is_archived` | `BOOLEAN` | False | False | `application=False` |  |
| `storage_path` | `VARCHAR(1000)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `owner_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_ks_project_url`: `project_id, canonical_url`
- Index `ix_ks_owner` (unique=False): `knowledge_sources.owner_id`; options `{}`
- Index `ix_ks_project_status` (unique=False): `knowledge_sources.project_id, knowledge_sources.sync_status`; options `{}`
- Index `ix_ks_project_type` (unique=False): `knowledge_sources.project_id, knowledge_sources.source_type`; options `{}`

## knowledge_sync_events

[backend/app/models/postgres.py:2982](../../backend/app/models/postgres.py#L2982)

Audit log for every sync attempt on a KnowledgeSource.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `source_id` | `UUID` | False | False | `—` | knowledge_sources.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `trigger` | `VARCHAR(20)` | False | False | `application=manual` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `content_hash` | `VARCHAR(64)` | True | False | `—` |  |
| `previous_hash` | `VARCHAR(64)` | True | False | `—` |  |
| `content_changed` | `BOOLEAN` | False | False | `application=False` |  |
| `chunk_count` | `INTEGER` | True | False | `—` |  |
| `duration_ms` | `INTEGER` | True | False | `—` |  |
| `error_message` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `source_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_kse_project_created` (unique=False): `knowledge_sync_events.project_id, knowledge_sync_events.created_at`; options `{}`
- Index `ix_kse_source_created` (unique=False): `knowledge_sync_events.source_id, knowledge_sync_events.created_at`; options `{}`

## live_event_receipts

[backend/app/models/postgres.py:487](../../backend/app/models/postgres.py#L487)

Per-event idempotency receipt and bounded sanitized attempt evidence.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `attempt_id` | `UUID` | False | False | `—` | live_ingestion_attempts.id / CASCADE |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `event_id` | `VARCHAR(64)` | False | False | `—` |  |
| `stream_id` | `VARCHAR(64)` | False | False | `—` |  |
| `event_index` | `INTEGER` | False | False | `—` |  |
| `event_type` | `VARCHAR(50)` | False | False | `—` |  |
| `payload` | `JSONB` | False | False | `application=dict` |  |
| `projected_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_live_event_receipt_index`: `event_index >= 0`
- `ForeignKeyConstraint` `unnamed`: `attempt_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_live_event_receipt_event`: `event_id`
- Index `ix_live_event_receipt_run_stream` (unique=False): `live_event_receipts.run_id, live_event_receipts.stream_id`; options `{}`

## live_ingestion_attempts

[backend/app/models/postgres.py:454](../../backend/app/models/postgres.py#L454)

Durable record that an accepted live batch reached projection.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `session_id` | `VARCHAR(255)` | False | False | `—` |  |
| `batch_id` | `VARCHAR(255)` | False | False | `—` |  |
| `event_count` | `INTEGER` | False | False | `—` |  |
| `first_event_id` | `VARCHAR(64)` | True | False | `—` |  |
| `last_event_id` | `VARCHAR(64)` | True | False | `—` |  |
| `first_stream_id` | `VARCHAR(64)` | True | False | `—` |  |
| `last_stream_id` | `VARCHAR(64)` | True | False | `—` |  |
| `projected_at` | `DATETIME` | False | False | `server=now()` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_live_ingestion_attempt_count`: `event_count >= 0`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_live_ingestion_attempt_session_batch`: `session_id, batch_id`
- Index `ix_live_ingestion_attempt_run_created` (unique=False): `live_ingestion_attempts.run_id, live_ingestion_attempts.created_at`; options `{}`

## live_projection_checkpoints

[backend/app/models/postgres.py:516](../../backend/app/models/postgres.py#L516)

Committed per-run high watermark for Redis evidence projection.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `run_id` | `UUID` | False | True | `—` | test_runs.id / CASCADE |
| `stream_key` | `VARCHAR(500)` | False | False | `—` |  |
| `consumer_group` | `VARCHAR(100)` | False | False | `—` |  |
| `last_stream_id` | `VARCHAR(64)` | False | False | `—` |  |
| `last_event_id` | `VARCHAR(64)` | False | False | `—` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `run_id`

## live_sessions

[backend/app/models/postgres.py:3116](../../backend/app/models/postgres.py#L3116)

Tracks an active live test execution session from a client machine.

Client machines register a session before streaming events.
The session_token (stored as a SHA-256 hash here; plaintext lives in Redis)
is used for lightweight authentication on the hot-path batch endpoint —
avoiding JWT decode + DB query overhead at 10k+ concurrent sessions.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `run_id` | `VARCHAR(100)` | False | False | `—` |  |
| `client_name` | `VARCHAR(255)` | False | False | `—` |  |
| `machine_id` | `VARCHAR(255)` | True | False | `—` |  |
| `build_number` | `VARCHAR(100)` | True | False | `—` |  |
| `framework` | `VARCHAR(50)` | True | False | `—` |  |
| `branch` | `VARCHAR(255)` | True | False | `—` |  |
| `commit_hash` | `VARCHAR(64)` | True | False | `—` |  |
| `session_token_hash` | `VARCHAR(64)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=active` |  |
| `release_name` | `VARCHAR(255)` | True | False | `—` |  |
| `launch_name` | `VARCHAR(255)` | True | False | `—` |  |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `total_tests` | `INTEGER` | False | False | `application=0` |  |
| `events_received` | `INTEGER` | False | False | `application=0` |  |
| `extra_metadata` | `JSON` | True | False | `—` |  |
| `started_at` | `DATETIME` | False | False | `—` |  |
| `last_event_at` | `DATETIME` | True | False | `—` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_live_sessions_project_status` (unique=False): `live_sessions.project_id, live_sessions.status`; options `{}`
- Index `ix_live_sessions_run_id` (unique=False): `live_sessions.run_id`; options `{}`
- Index `ix_live_sessions_session_token_hash` (unique=True): `live_sessions.session_token_hash`; options `{}`
- Index `ix_live_sessions_started_at` (unique=False): `live_sessions.started_at`; options `{}`
- Index `ix_live_sessions_status` (unique=False): `live_sessions.status`; options `{}`
- Index `ix_live_sessions_token_hash` (unique=False): `live_sessions.session_token_hash`; options `{}`
- Index `ux_live_sessions_active_project_run` (unique=True): `live_sessions.project_id, live_sessions.run_id`; options `{"postgresql_where": "status = 'active'"}`

## managed_test_cases

[backend/app/models/postgres.py:2510](../../backend/app/models/postgres.py#L2510)

Manually authored or AI-generated test case with full lifecycle management.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `title` | `VARCHAR(500)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `objective` | `TEXT` | True | False | `—` |  |
| `preconditions` | `TEXT` | True | False | `—` |  |
| `steps` | `JSON` | True | False | `—` |  |
| `expected_result` | `TEXT` | True | False | `—` |  |
| `test_data` | `TEXT` | True | False | `—` |  |
| `test_type` | `VARCHAR(50)` | False | False | `application=functional` |  |
| `priority` | `VARCHAR(20)` | False | False | `application=medium` |  |
| `severity` | `VARCHAR(20)` | False | False | `application=major` |  |
| `feature_area` | `VARCHAR(500)` | True | False | `—` |  |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `test_suite_id` | `UUID` | True | False | `—` | test_suites.id / SET NULL |
| `tags` | `JSON` | True | False | `—` |  |
| `parameters` | `JSON` | True | False | `—` |  |
| `status` | `VARCHAR(30)` | False | False | `application=draft` |  |
| `version` | `INTEGER` | False | False | `application=1` |  |
| `lifecycle_state_changed_at` | `DATETIME` | True | False | `—` |  |
| `approved_at` | `DATETIME` | True | False | `—` |  |
| `approved_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `needs_update_reason` | `VARCHAR(100)` | True | False | `—` |  |
| `deprecation_reason` | `VARCHAR(500)` | True | False | `—` |  |
| `deprecated_at` | `DATETIME` | True | False | `—` |  |
| `deprecated_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `archived_at` | `DATETIME` | True | False | `—` |  |
| `archived_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `author_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `assignee_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `reviewer_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `is_automated` | `BOOLEAN` | False | False | `application=False` |  |
| `automation_status` | `VARCHAR(30)` | False | False | `application=not_automated` |  |
| `test_fingerprint` | `VARCHAR(64)` | True | False | `—` |  |
| `dup_fingerprint` | `VARCHAR(64)` | True | False | `—` |  |
| `ai_generated` | `BOOLEAN` | False | False | `application=False` |  |
| `ai_generation_prompt` | `TEXT` | True | False | `—` |  |
| `ai_quality_score` | `INTEGER` | True | False | `—` |  |
| `ai_review_notes` | `JSON` | True | False | `—` |  |
| `estimated_duration_minutes` | `INTEGER` | True | False | `—` |  |
| `last_executed_at` | `DATETIME` | True | False | `—` |  |
| `last_execution_status` | `VARCHAR(20)` | True | False | `—` |  |
| `generation_batch_id` | `UUID` | True | False | `—` | generation_batches.id / SET NULL |
| `is_stale` | `BOOLEAN` | False | False | `application=False` |  |
| `stale_reason` | `TEXT` | True | False | `—` |  |
| `faithfulness_score` | `FLOAT` | True | False | `—` |  |
| `faithfulness_evaluator` | `VARCHAR(30)` | True | False | `—` |  |
| `faithfulness_evaluated_at` | `DATETIME` | True | False | `—` |  |
| `needs_review_reason` | `VARCHAR(500)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `approved_by_id`
- `ForeignKeyConstraint` `unnamed`: `archived_by_id`
- `ForeignKeyConstraint` `unnamed`: `assignee_id`
- `ForeignKeyConstraint` `unnamed`: `author_id`
- `ForeignKeyConstraint` `unnamed`: `deprecated_by_id`
- `ForeignKeyConstraint` `unnamed`: `generation_batch_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `reviewer_id`
- `ForeignKeyConstraint` `unnamed`: `test_suite_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_managed_test_cases_status` (unique=False): `managed_test_cases.status`; options `{}`
- Index `ix_mtc_author` (unique=False): `managed_test_cases.author_id`; options `{}`
- Index `ix_mtc_dup_fingerprint` (unique=False): `managed_test_cases.dup_fingerprint`; options `{}`
- Index `ix_mtc_fingerprint` (unique=False): `managed_test_cases.test_fingerprint`; options `{}`
- Index `ix_mtc_project_fingerprint` (unique=False): `managed_test_cases.project_id, managed_test_cases.test_fingerprint`; options `{}`
- Index `ix_mtc_project_last_executed` (unique=False): `managed_test_cases.project_id, managed_test_cases.last_executed_at`; options `{}`
- Index `ix_mtc_project_status` (unique=False): `managed_test_cases.project_id, managed_test_cases.status`; options `{}`

## mfa_recovery_codes

[backend/app/models/postgres.py:212](../../backend/app/models/postgres.py#L212)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `code_hash` | `VARCHAR(64)` | False | False | `—` |  |
| `used_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `code_hash`
- Index `ix_mfa_recovery_code_hash` (unique=True): `mfa_recovery_codes.code_hash`; options `{}`
- Index `ix_mfa_recovery_user` (unique=False): `mfa_recovery_codes.user_id`; options `{}`

## model_versions

[backend/app/models/postgres.py:2246](../../backend/app/models/postgres.py#L2246)

Registry of fine-tuned model versions per training track.

Tracks the full lifecycle: training → evaluation → active/retired.
The model_registry service uses this table + Redis for hot-swap lookups.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `track` | `VARCHAR(30)` | False | False | `—` |  |
| `model_name` | `VARCHAR(200)` | False | False | `—` |  |
| `provider` | `VARCHAR(30)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=training` |  |
| `training_examples` | `INTEGER` | False | False | `application=0` |  |
| `holdout_examples` | `INTEGER` | False | False | `application=0` |  |
| `eval_accuracy` | `FLOAT` | True | False | `—` |  |
| `baseline_accuracy` | `FLOAT` | True | False | `—` |  |
| `eval_details` | `JSON` | True | False | `—` |  |
| `provider_job_id` | `VARCHAR(200)` | True | False | `—` |  |
| `training_file_path` | `VARCHAR(1000)` | True | False | `—` |  |
| `promoted_at` | `DATETIME` | True | False | `—` |  |
| `retired_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_model_versions_track_status` (unique=False): `model_versions.track, model_versions.status`; options `{}`

## notification_logs

[backend/app/models/postgres.py:2446](../../backend/app/models/postgres.py#L2446)

Audit trail for every dispatched notification.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `project_id` | `UUID` | True | False | `—` | projects.id / SET NULL |
| `run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `preference_id` | `UUID` | True | False | `—` | notification_preferences.id / SET NULL |
| `delivery_key` | `VARCHAR(64)` | True | False | `—` |  |
| `delivery_metadata` | `JSONB` | True | False | `—` |  |
| `delivery_attempts` | `INTEGER` | False | False | `server=0; application=0` |  |
| `delivery_token` | `UUID` | True | False | `—` |  |
| `delivery_started_at` | `DATETIME` | True | False | `—` |  |
| `delivery_lease_expires_at` | `DATETIME` | True | False | `—` |  |
| `next_delivery_at` | `DATETIME` | True | False | `—` |  |
| `channel` | `VARCHAR(20)` | False | False | `—` |  |
| `event_type` | `VARCHAR(50)` | False | False | `—` |  |
| `title` | `VARCHAR(500)` | False | False | `—` |  |
| `body` | `TEXT` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=pending` |  |
| `error_detail` | `TEXT` | True | False | `—` |  |
| `routed_team` | `VARCHAR(255)` | True | False | `—` |  |
| `routing_fallback` | `VARCHAR(50)` | True | False | `—` |  |
| `is_read` | `BOOLEAN` | False | False | `application=False` |  |
| `sent_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `preference_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_notif_log_project` (unique=False): `notification_logs.project_id`; options `{}`
- Index `ix_notif_log_user_created` (unique=False): `notification_logs.user_id, notification_logs.created_at`; options `{}`
- Index `ix_notification_log_delivery_due` (unique=False): `notification_logs.status, notification_logs.next_delivery_at`; options `{}`
- Index `uq_notification_log_delivery_key` (unique=True): `notification_logs.delivery_key`; options `{}`

## notification_preferences

[backend/app/models/postgres.py:1525](../../backend/app/models/postgres.py#L1525)

Per-user, per-channel notification configuration.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `channel` | `VARCHAR(20)` | False | False | `—` |  |
| `enabled` | `BOOLEAN` | False | False | `application=True` |  |
| `events` | `JSONB` | False | False | `application=list` |  |
| `failure_rate_threshold` | `FLOAT` | True | False | `application=80.0` |  |
| `email_override` | `VARCHAR(255)` | True | False | `—` |  |
| `slack_webhook_url` | `VARCHAR(2000)` | True | False | `—` |  |
| `teams_webhook_url` | `VARCHAR(2000)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_notif_pref`: `user_id, project_id, channel`

## notification_test_states

[backend/app/models/postgres.py:1553](../../backend/app/models/postgres.py#L1553)

Per-(project, test_fingerprint) rolling state for transition-based
notifications (PMF US-7.1).

The transition engine (``services/notification_transitions.py``) updates
one row per logical test at run finalization and emits a notification
only when the tracked state CHANGES (pass→confirmed-failing,
failing→recovered, entered the known-flaky set). ``last_run_id`` is the
idempotency anchor: re-finalizing the same run skips rows already
stamped with that run, so transitions never double-fire (per-(entity,
run) idempotency convention).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `state` | `VARCHAR(20)` | False | False | `application=passing` |  |
| `consecutive_failures` | `INTEGER` | False | False | `application=0` |  |
| `last_notified_state` | `VARCHAR(20)` | True | False | `—` |  |
| `is_known_flaky` | `BOOLEAN` | False | False | `application=False` |  |
| `last_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `last_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_notif_test_state_project_fp`: `project_id, test_fingerprint`
- Index `ix_notif_test_states_project` (unique=False): `notification_test_states.project_id`; options `{}`

## notification_transition_policies

[backend/app/models/postgres.py:1599](../../backend/app/models/postgres.py#L1599)

Per-project policy for transition-based notifications (PMF US-7.1).

One row per project. Semantics of a MISSING row = the new-project
default: transitions ON, per-run spam OFF. Migration 0103 backfills an
explicit row (transitions OFF, per-run ON) for every project existing
at upgrade time so EXISTING projects keep their current behaviour with
no surprise change; projects created afterwards get the new defaults.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `transitions_enabled` | `BOOLEAN` | False | False | `application=True` |  |
| `per_run_events_enabled` | `BOOLEAN` | False | False | `application=False` |  |
| `enabled_events` | `JSONB` | False | False | `application=list` |  |
| `consecutive_failure_threshold` | `INTEGER` | False | False | `application=2` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `project_id`

## perf_baselines

[backend/app/models/postgres.py:6013](../../backend/app/models/postgres.py#L6013)

Per-test running duration statistics.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(500)` | True | False | `—` |  |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `sample_count` | `INTEGER` | False | False | `application=0` |  |
| `mean_ms` | `FLOAT` | False | False | `application=0.0` |  |
| `m2` | `FLOAT` | False | False | `application=0.0` |  |
| `stddev_ms` | `FLOAT` | False | False | `application=0.0` |  |
| `p95_ms` | `FLOAT` | True | False | `—` |  |
| `last_observed_ms` | `INTEGER` | True | False | `—` |  |
| `last_observed_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_perf_baseline_fingerprint`: `project_id, test_fingerprint`
- Index `ix_perf_baseline_project` (unique=False): `perf_baselines.project_id`; options `{}`

## product_usage_events

[backend/app/models/postgres.py:4083](../../backend/app/models/postgres.py#L4083)

Tracks product adoption events for analytics.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `project_id` | `UUID` | True | False | `—` | projects.id / SET NULL |
| `event_name` | `VARCHAR(100)` | False | False | `—` |  |
| `event_payload` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_pue_created` (unique=False): `product_usage_events.created_at`; options `{}`
- Index `ix_pue_event` (unique=False): `product_usage_events.event_name`; options `{}`
- Index `ix_pue_user` (unique=False): `product_usage_events.user_id`; options `{}`

## project_activity_events

[backend/app/models/postgres.py:6811](../../backend/app/models/postgres.py#L6811)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `release_id` | `UUID` | True | False | `—` | releases.id / SET NULL |
| `occurred_at` | `DATETIME` | False | False | `server=now()` |  |
| `recorded_at` | `DATETIME` | False | False | `server=now()` |  |
| `category` | `VARCHAR(20)` | False | False | `—` |  |
| `event_type` | `VARCHAR(60)` | False | False | `—` |  |
| `schema_version` | `SMALLINT` | False | False | `application=1` |  |
| `actor_type` | `VARCHAR(20)` | False | False | `—` |  |
| `actor_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `actor_name` | `VARCHAR(200)` | True | False | `—` |  |
| `actor_ref` | `VARCHAR(120)` | True | False | `—` |  |
| `entity_type` | `VARCHAR(40)` | False | False | `—` |  |
| `entity_id` | `VARCHAR(120)` | False | False | `—` |  |
| `entity_label` | `VARCHAR(300)` | True | False | `—` |  |
| `target_type` | `VARCHAR(40)` | True | False | `—` |  |
| `target_id` | `VARCHAR(120)` | True | False | `—` |  |
| `summary` | `VARCHAR(500)` | False | False | `—` |  |
| `diff` | `JSON` | True | False | `—` |  |
| `context` | `JSON` | True | False | `—` |  |
| `source_table` | `VARCHAR(40)` | True | False | `—` |  |
| `source_id` | `UUID` | True | False | `—` |  |
| `request_id` | `VARCHAR(64)` | True | False | `—` |  |
| `group_key` | `VARCHAR(120)` | True | False | `—` |  |

Constraints and indexes:

- `CheckConstraint` `ck_pae_actor_type`: `actor_type IN ('user', 'api_key', 'service_account', 'system', 'agent')`
- `CheckConstraint` `ck_pae_category`: `category IN ('runs', 'analysis', 'release', 'quality', 'configuration', 'membership', 'test_management', 'integration', 'agent', 'system')`
- `ForeignKeyConstraint` `unnamed`: `actor_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `release_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_pae_group_key` (unique=False): `project_activity_events.project_id, project_activity_events.group_key`; options `{}`
- Index `ix_pae_project_actor` (unique=False): `project_activity_events.project_id, project_activity_events.actor_id, project_activity_events.occurred_at`; options `{}`
- Index `ix_pae_project_category_time` (unique=False): `project_activity_events.project_id, project_activity_events.category, project_activity_events.occurred_at, project_activity_events.id`; options `{}`
- Index `ix_pae_project_entity` (unique=False): `project_activity_events.project_id, project_activity_events.entity_type, project_activity_events.entity_id, project_activity_events.occurred_at`; options `{}`
- Index `ix_pae_project_event_type` (unique=False): `project_activity_events.project_id, project_activity_events.event_type, project_activity_events.occurred_at`; options `{}`
- Index `ix_pae_project_release` (unique=False): `project_activity_events.project_id, project_activity_events.release_id, project_activity_events.occurred_at`; options `{"postgresql_where": "release_id IS NOT NULL"}`
- Index `ix_pae_project_time` (unique=False): `project_activity_events.project_id, project_activity_events.occurred_at, project_activity_events.id`; options `{}`

## project_llm_quota

[backend/app/models/postgres.py:5677](../../backend/app/models/postgres.py#L5677)

Per-project LLM spend config used by the usage-based billing gate.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `enabled` | `BOOLEAN` | False | False | `application=True` |  |
| `period_type` | `VARCHAR(20)` | False | False | `application=MONTHLY` |  |
| `included_usd` | `FLOAT` | False | False | `application=0.0` |  |
| `overage_rate_usd` | `FLOAT` | False | False | `application=1.0` |  |
| `hard_cap_usd` | `FLOAT` | False | False | `application=0.0` |  |
| `soft_warn_threshold_pct` | `INTEGER` | False | False | `application=100` |  |
| `at_cap_action` | `VARCHAR(32)` | False | False | `application=AUTO_DOWNGRADE_TO_ML` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `project_id`

## project_llm_usage

[backend/app/models/postgres.py:6771](../../backend/app/models/postgres.py#L6771)

Running per-period LLM cost meter for a project.

One row per ``(project_id, period_start)``. ``record_usage`` upserts with
atomic arithmetic increments so two workers writing concurrently don't
clobber each other. The ``(project_id, period_start)`` unique index is
what makes the upsert safe.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `period_start` | `DATETIME` | False | False | `—` |  |
| `period_end` | `DATETIME` | False | False | `—` |  |
| `total_cost_usd` | `FLOAT` | False | False | `application=0.0` |  |
| `total_input_tokens` | `INTEGER` | False | False | `application=0` |  |
| `total_output_tokens` | `INTEGER` | False | False | `application=0` |  |
| `total_llm_calls` | `INTEGER` | False | False | `application=0` |  |
| `cap_hits` | `INTEGER` | False | False | `application=0` |  |
| `last_updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_project_period`: `project_id, period_start`
- Index `ix_llm_usage_period` (unique=False): `project_llm_usage.period_start`; options `{}`

## project_members

[backend/app/models/postgres.py:3796](../../backend/app/models/postgres.py#L3796)

Per-project role assignment for a user.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `role` | `VARCHAR(20)` | False | False | `application=QA_ENGINEER` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_project_member`: `user_id, project_id`

## project_retention_policies

[backend/app/models/postgres.py:6715](../../backend/app/models/postgres.py#L6715)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `enabled` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `raw_events_days` | `INTEGER` | False | False | `server=90; application=90` |  |
| `runs_days` | `INTEGER` | False | False | `server=365; application=365` |  |
| `artifacts_days` | `INTEGER` | False | False | `server=180; application=180` |  |
| `audit_days` | `INTEGER` | False | False | `server=2555; application=2555` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `project_id`

## projects

[backend/app/models/postgres.py:239](../../backend/app/models/postgres.py#L239)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `slug` | `VARCHAR(100)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `jira_project_key` | `VARCHAR(50)` | True | False | `—` |  |
| `splunk_index` | `VARCHAR(255)` | True | False | `—` |  |
| `ocp_namespace` | `VARCHAR(255)` | True | False | `—` |  |
| `jenkins_job_pattern` | `VARCHAR(500)` | True | False | `—` |  |
| `component_owner_map` | `JSON` | True | False | `—` |  |
| `start_date` | `DATETIME` | True | False | `—` |  |
| `end_date` | `DATETIME` | True | False | `—` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `manager_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `default_qa_lead_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `allow_unreviewed_distribution` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `default_qa_lead_user_id`
- `ForeignKeyConstraint` `unnamed`: `manager_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_projects_default_qa_lead_user_id` (unique=False): `projects.default_qa_lead_user_id`; options `{}`
- Index `ix_projects_manager_user_id` (unique=False): `projects.manager_user_id`; options `{}`
- Index `ix_projects_name` (unique=False): `projects.name`; options `{}`
- Index `ix_projects_slug` (unique=True): `projects.slug`; options `{}`

ORM navigation and cascade declarations:

```python
test_runs: Mapped[list['TestRun']] = relationship('TestRun', back_populates='project', lazy='dynamic')
quality_gates: Mapped[list['QualityGate']] = relationship('QualityGate', back_populates='project')
test_suites: Mapped[list['TestSuite']] = relationship('TestSuite', back_populates='project', cascade='all, delete-orphan')
canonical_test_cases: Mapped[list['CanonicalTestCase']] = relationship('CanonicalTestCase', back_populates='project', cascade='all, delete-orphan')
```

## quality_gates

[backend/app/models/postgres.py:1489](../../backend/app/models/postgres.py#L1489)

Quality gate rule configuration per project.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `rules` | `JSON` | False | False | `application=list` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_quality_gates_project` (unique=False): `quality_gates.project_id`; options `{}`

ORM navigation and cascade declarations:

```python
project: Mapped['Project'] = relationship('Project', back_populates='quality_gates')
```

## quarantine_lifecycle_policies

[backend/app/models/postgres.py:6521](../../backend/app/models/postgres.py#L6521)

Per-project quarantine lifecycle configuration (PMF US-5.4/5.5/5.6).

One row per project; a MISSING row resolves to the defaults in
``flaky_quarantine_service.EffectiveLifecyclePolicy`` (SLA 14 days,
no auto-defect, no auto-promote, promote after 20 consecutive passes,
detection floor 20% flip rate over 10 runs) — no project-creation hook
needed.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `sla_days` | `INTEGER` | False | False | `application=14` |  |
| `auto_create_defect` | `BOOLEAN` | False | False | `application=False` |  |
| `auto_promote` | `BOOLEAN` | False | False | `application=False` |  |
| `promote_after_passes` | `INTEGER` | False | False | `application=20` |  |
| `max_active_quarantined` | `INTEGER` | False | False | `server=8; application=8` |  |
| `detection_flip_rate_threshold` | `FLOAT` | False | False | `application=0.2` |  |
| `detection_min_runs` | `INTEGER` | False | False | `application=10` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `project_id`

## refresh_token_records

[backend/app/models/postgres.py:4444](../../backend/app/models/postgres.py#L4444)

Server-side record of issued refresh tokens for rotation and replay detection.

Each refresh token carries a random ``jti`` claim; only the SHA-256 hex digest
is persisted. On refresh, the record is marked ``rotated_to_id`` and a new
record is created. Presenting an already-rotated or revoked token triggers
family-wide revocation for the owning user (``replay_detected = true``).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `jti_hash` | `VARCHAR(64)` | False | False | `—` |  |
| `issued_at` | `DATETIME` | False | False | `server=now()` |  |
| `expires_at` | `DATETIME` | False | False | `—` |  |
| `revoked_at` | `DATETIME` | True | False | `—` |  |
| `rotated_to_id` | `UUID` | True | False | `—` |  |
| `replay_detected` | `BOOLEAN` | False | False | `application=False` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_rtr_expires_at` (unique=False): `refresh_token_records.expires_at`; options `{}`
- Index `ix_rtr_jti_hash` (unique=True): `refresh_token_records.jti_hash`; options `{}`
- Index `ix_rtr_user_id` (unique=False): `refresh_token_records.user_id`; options `{}`

## release_attribution_rules

[backend/app/models/postgres.py:3578](../../backend/app/models/postgres.py#L3578)

Per-project rule mapping run metadata to a release (migration 0154).

The bridge for teams that cannot send an explicit ``release_name``: match
on what a run already carries and name the release it belongs to. Without
these, such projects fall straight to the active release, which cannot tell
a hotfix branch from a release candidate from trunk CI.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `priority` | `INTEGER` | False | False | `server=100; application=100` |  |
| `is_enabled` | `BOOLEAN` | False | False | `server=true; application=True` |  |
| `match_field` | `VARCHAR(30)` | False | False | `—` |  |
| `match_pattern` | `VARCHAR(255)` | False | False | `—` |  |
| `target_release_name` | `VARCHAR(255)` | False | False | `—` |  |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_attribution_rules_project_priority` (unique=False): `release_attribution_rules.project_id, release_attribution_rules.priority`; options `{}`

## release_decisions

[backend/app/models/postgres.py:2392](../../backend/app/models/postgres.py#L2392)

Release gate decision produced by ReleaseRiskAgent.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `recommendation` | `VARCHAR(20)` | False | False | `—` |  |
| `risk_score` | `INTEGER` | False | False | `application=50` |  |
| `blocking_issues` | `JSON` | True | False | `—` |  |
| `conditions_for_go` | `JSON` | True | False | `—` |  |
| `reasoning` | `TEXT` | True | False | `—` |  |
| `dimension_scores` | `JSON` | True | False | `—` |  |
| `composite_risk` | `FLOAT` | True | False | `—` |  |
| `score_model_version` | `INTEGER` | True | False | `—` |  |
| `human_override` | `TEXT` | True | False | `—` |  |
| `overridden_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `input_snapshot` | `JSON` | True | False | `—` |  |
| `override_audit` | `JSON` | True | False | `—` |  |
| `original_recommendation` | `VARCHAR(20)` | True | False | `—` |  |
| `original_risk_score` | `INTEGER` | True | False | `—` |  |
| `policy_id` | `UUID` | True | False | `—` | release_gate_policies.id / SET NULL |
| `policy_evaluation` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `overridden_by`
- `ForeignKeyConstraint` `unnamed`: `pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `policy_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `test_run_id`
- Index `ix_release_decisions_pipeline_run_id` (unique=False): `release_decisions.pipeline_run_id`; options `{}`

## release_gate_decisions

[backend/app/models/postgres.py:4527](../../backend/app/models/postgres.py#L4527)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `release_id` | `UUID` | False | False | `—` | releases.id / CASCADE |
| `phase_id` | `UUID` | True | False | `—` | release_phases.id / CASCADE |
| `is_current` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `verdict` | `VARCHAR(20)` | False | False | `—` |  |
| `denominator` | `INTEGER` | False | False | `application=0` |  |
| `evidence_count` | `INTEGER` | False | False | `application=0` |  |
| `run_ids` | `JSON` | True | False | `—` |  |
| `status_rollup` | `JSON` | True | False | `—` |  |
| `attribution_mix` | `JSON` | True | False | `—` |  |
| `ingestion_complete` | `BOOLEAN` | True | False | `—` |  |
| `incomplete_runs` | `JSON` | True | False | `—` |  |
| `policy_id` | `UUID` | True | False | `—` | release_gate_policies.id / SET NULL |
| `policy_snapshot` | `JSON` | True | False | `—` |  |
| `baseline_release_id` | `UUID` | True | False | `—` | releases.id / SET NULL |
| `blocking_reasons` | `JSON` | True | False | `—` |  |
| `conditions_for_go` | `JSON` | True | False | `—` |  |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `baseline_release_id`
- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `phase_id`
- `ForeignKeyConstraint` `unnamed`: `policy_id`
- `ForeignKeyConstraint` `unnamed`: `release_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_rgd_phase_current` (unique=True): `release_gate_decisions.release_id, release_gate_decisions.phase_id`; options `{"postgresql_where": "is_current IS TRUE AND phase_id IS NOT NULL"}`
- Index `ix_rgd_release_created` (unique=False): `release_gate_decisions.release_id, release_gate_decisions.created_at`; options `{}`
- Index `ix_rgd_release_current` (unique=True): `release_gate_decisions.release_id`; options `{"postgresql_where": "is_current IS TRUE AND phase_id IS NULL"}`

## release_gate_policies

[backend/app/models/postgres.py:4390](../../backend/app/models/postgres.py#L4390)

Versioned release gate policy — per-project or system-wide default.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `version` | `INTEGER` | False | False | `—` |  |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `rules` | `JSON` | False | False | `—` |  |
| `is_active` | `BOOLEAN` | False | False | `application=False` |  |
| `is_draft` | `BOOLEAN` | False | False | `application=True` |  |
| `created_by` | `UUID` | False | False | `—` | users.id / SET NULL |
| `activated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `activated_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `activated_by`
- `ForeignKeyConstraint` `unnamed`: `created_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_rgp_project_version`: `project_id, version`
- Index `ix_rgp_project_active` (unique=False): `release_gate_policies.project_id, release_gate_policies.is_active`; options `{}`

## release_outcomes

[backend/app/models/postgres.py:3528](../../backend/app/models/postgres.py#L3528)

A human-marked production incident or rollback for a release (E9.9).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `release_id` | `UUID` | False | False | `—` | releases.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `outcome_kind` | `VARCHAR(20)` | False | False | `—` |  |
| `reason` | `TEXT` | False | False | `—` |  |
| `marked_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `marked_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_release_outcomes_kind`: `outcome_kind IN ('incident', 'rollback')`
- `CheckConstraint` `ck_release_outcomes_reason`: `length(trim(reason)) >= 3`
- `ForeignKeyConstraint` `unnamed`: `marked_by_user_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `release_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_release_outcomes_project_marked` (unique=False): `release_outcomes.project_id, release_outcomes.marked_at`; options `{}`
- Index `ix_release_outcomes_release_marked` (unique=False): `release_outcomes.release_id, release_outcomes.marked_at`; options `{}`

## release_phases

[backend/app/models/postgres.py:3624](../../backend/app/models/postgres.py#L3624)

A phase / milestone within a Release lifecycle.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `release_id` | `UUID` | False | False | `—` | releases.id / CASCADE |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `phase_type` | `VARCHAR(50)` | False | False | `application=qa_testing` |  |
| `status` | `VARCHAR(30)` | False | False | `application=pending` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `order_index` | `INTEGER` | False | False | `application=0` |  |
| `planned_start` | `DATETIME` | True | False | `—` |  |
| `planned_end` | `DATETIME` | True | False | `—` |  |
| `actual_start` | `DATETIME` | True | False | `—` |  |
| `actual_end` | `DATETIME` | True | False | `—` |  |
| `exit_criteria` | `JSON` | True | False | `—` |  |
| `notes` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `release_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_release_phases_release` (unique=False): `release_phases.release_id`; options `{}`

ORM navigation and cascade declarations:

```python
release: Mapped['Release'] = relationship('Release', back_populates='phases')
```

## release_test_run_links

[backend/app/models/postgres.py:3659](../../backend/app/models/postgres.py#L3659)

Links a test run to a release for metrics aggregation.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `release_id` | `UUID` | False | False | `—` | releases.id / CASCADE |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `phase_id` | `UUID` | True | False | `—` | release_phases.id / SET NULL |
| `link_source` | `VARCHAR(30)` | True | False | `—` |  |
| `is_primary` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `linked_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `project_id` | `UUID` | True | False | `—` |  |
| `linked_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `linked_by_id`
- `ForeignKeyConstraint` `unnamed`: `phase_id`
- `ForeignKeyConstraint` `unnamed`: `release_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_release_test_run`: `release_id, test_run_id`
- Index `ix_rtr_links_primary` (unique=True): `release_test_run_links.test_run_id`; options `{"postgresql_where": "is_primary IS TRUE"}`
- Index `ix_rtr_links_release` (unique=False): `release_test_run_links.release_id`; options `{}`
- Index `ix_rtr_links_test_run` (unique=False): `release_test_run_links.test_run_id`; options `{}`

ORM navigation and cascade declarations:

```python
release: Mapped['Release'] = relationship('Release', back_populates='test_run_links')
```

## releases

[backend/app/models/postgres.py:3345](../../backend/app/models/postgres.py#L3345)

A software release tracked through the QA lifecycle.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `version` | `VARCHAR(100)` | True | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `status` | `VARCHAR(30)` | False | False | `application=planning` |  |
| `is_default` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `is_active` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `is_auto_named` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `activated_at` | `DATETIME` | True | False | `—` |  |
| `deactivated_at` | `DATETIME` | True | False | `—` |  |
| `release_type` | `VARCHAR(20)` | True | False | `—` |  |
| `sort_key` | `VARCHAR(64)` | True | False | `—` |  |
| `baseline_release_id` | `UUID` | True | False | `—` | releases.id / SET NULL |
| `cutoff_start_at` | `DATETIME` | True | False | `—` |  |
| `cutoff_end_at` | `DATETIME` | True | False | `—` |  |
| `target_environment` | `VARCHAR(100)` | True | False | `—` |  |
| `source_system` | `VARCHAR(20)` | True | False | `—` |  |
| `external_id` | `VARCHAR(255)` | True | False | `—` |  |
| `external_url` | `VARCHAR(1000)` | True | False | `—` |  |
| `last_synced_at` | `DATETIME` | True | False | `—` |  |
| `planned_date` | `DATETIME` | True | False | `—` |  |
| `released_at` | `DATETIME` | True | False | `—` |  |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `baseline_release_id`
- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_releases_external_identity` (unique=True): `releases.project_id, releases.source_system, releases.external_id`; options `{"postgresql_where": "external_id IS NOT NULL"}`
- Index `ix_releases_project_active` (unique=True): `releases.project_id`; options `{"postgresql_where": "is_active IS TRUE"}`
- Index `ix_releases_project_default` (unique=True): `releases.project_id`; options `{"postgresql_where": "is_default IS TRUE"}`
- Index `ix_releases_project_sort` (unique=False): `releases.project_id, releases.sort_key`; options `{}`
- Index `ix_releases_project_status` (unique=False): `releases.project_id, releases.status`; options `{}`

ORM navigation and cascade declarations:

```python
phases: Mapped[list['ReleasePhase']] = relationship('ReleasePhase', back_populates='release', cascade='all, delete-orphan', order_by='ReleasePhase.order_index')
test_run_links: Mapped[list['ReleaseTestRunLink']] = relationship('ReleaseTestRunLink', back_populates='release', cascade='all, delete-orphan')
```

## report_share_links

[backend/app/models/postgres.py:4416](../../backend/app/models/postgres.py#L4416)

Time-limited share token for run intelligence reports.

The raw token is shown once at creation and never persisted. Only the
SHA-256 hex digest (``token_hash``) is stored, so a DB compromise cannot
recover active share tokens.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `token_hash` | `VARCHAR(64)` | False | False | `—` |  |
| `report_layout` | `VARCHAR(20)` | False | False | `application=executive` |  |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_by_name` | `VARCHAR(200)` | True | False | `—` |  |
| `expires_at` | `DATETIME` | False | False | `—` |  |
| `is_revoked` | `BOOLEAN` | False | False | `application=False` |  |
| `access_count` | `INTEGER` | False | False | `application=0` |  |
| `last_accessed_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `token_hash`
- Index `ix_rsl_expires` (unique=False): `report_share_links.expires_at`; options `{}`
- Index `ix_rsl_run` (unique=False): `report_share_links.run_id`; options `{}`
- Index `ix_rsl_token_hash` (unique=True): `report_share_links.token_hash`; options `{}`

## requirement_coverage

[backend/app/models/postgres.py:3097](../../backend/app/models/postgres.py#L3097)

Tracks which requirements are covered/uncovered by a generation batch.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `batch_id` | `UUID` | False | False | `—` | generation_batches.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `requirement_id` | `VARCHAR(200)` | False | False | `—` |  |
| `requirement_text` | `TEXT` | True | False | `—` |  |
| `coverage_status` | `VARCHAR(20)` | False | False | `application=uncovered` |  |
| `covered_by_case_ids` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `batch_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_rc_batch_req`: `batch_id, requirement_id`
- Index `ix_rc_batch_id` (unique=False): `requirement_coverage.batch_id`; options `{}`
- Index `ix_rc_project_req` (unique=False): `requirement_coverage.project_id, requirement_coverage.requirement_id`; options `{}`

## review_requests

[backend/app/models/postgres.py:5224](../../backend/app/models/postgres.py#L5224)

One human review for one AI report-producing run (architecture section 8.1).

Every AI-generated report is a proposal until a human accepts it. There is
one LIVE request per subject -- the reports a run produced inherit its review
state -- enforced by a partial unique index; superseded rows are kept, so
the history of who accepted what survives a re-run.

``notes`` is free text and is never exported (section 8.1); ``reason_code``
is the closed vocabulary that becomes an eval label.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `kind` | `VARCHAR(20)` | False | False | `server=report; application=report` |  |
| `subject_type` | `VARCHAR(30)` | False | False | `—` |  |
| `subject_id` | `VARCHAR(128)` | False | False | `—` |  |
| `pipeline_run_id` | `UUID` | True | False | `—` | agent_pipeline_runs.id / SET NULL |
| `test_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `workflow_type` | `VARCHAR(20)` | True | False | `—` |  |
| `capability_id` | `VARCHAR(160)` | True | False | `—` |  |
| `state` | `VARCHAR(20)` | False | False | `server=pending_review; application=pending_review` |  |
| `requested_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_by` | `VARCHAR(40)` | False | False | `server=system; application=system` |  |
| `reviewed_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `reviewed_at` | `DATETIME` | True | False | `—` |  |
| `reason_code` | `VARCHAR(40)` | True | False | `—` |  |
| `notes` | `TEXT` | True | False | `—` |  |
| `evidence_bundle_sha256` | `VARCHAR(64)` | True | False | `—` |  |
| `eval_manifest_checksum` | `VARCHAR(64)` | True | False | `—` |  |
| `ai_disclaimer_version` | `VARCHAR(40)` | False | False | `—` |  |
| `superseded_by` | `UUID` | True | False | `—` | review_requests.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_review_requests_eval_manifest_checksum`: `eval_manifest_checksum IS NULL OR eval_manifest_checksum ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_review_requests_evidence_hash`: `evidence_bundle_sha256 IS NULL OR evidence_bundle_sha256 ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_review_requests_kind`: `kind IN ('report', 'eval_drift')`
- `CheckConstraint` `ck_review_requests_reason_code`: `reason_code IS NULL OR reason_code IN ('wrong_category', 'unsupported_claim', 'missing_evidence', 'contradiction', 'stale_data', 'other')`
- `CheckConstraint` `ck_review_requests_rejection_has_reason`: `state <> 'rejected' OR reason_code IS NOT NULL`
- `CheckConstraint` `ck_review_requests_settled_has_time`: `state NOT IN ('accepted', 'rejected') OR reviewed_at IS NOT NULL`
- `CheckConstraint` `ck_review_requests_state`: `state IN ('pending_review', 'accepted', 'rejected', 'superseded')`
- `CheckConstraint` `ck_review_requests_subject_type`: `subject_type IN ('pipeline_run', 'invocation', 'decision_report', 'summary', 'capability')`
- `ForeignKeyConstraint` `unnamed`: `pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `requested_by`
- `ForeignKeyConstraint` `unnamed`: `reviewed_by`
- `ForeignKeyConstraint` `unnamed`: `superseded_by`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_review_requests_pending_scope` (unique=False): `review_requests.project_id, review_requests.test_run_id, review_requests.workflow_type`; options `{"postgresql_where": "state = 'pending_review'"}`
- Index `ix_review_requests_project_state` (unique=False): `review_requests.project_id, review_requests.state, review_requests.created_at`; options `{}`
- Index `uq_review_requests_live_subject` (unique=True): `review_requests.kind, review_requests.subject_type, review_requests.subject_id`; options `{"postgresql_where": "state <> 'superseded'"}`

## run_baselines

[backend/app/models/postgres.py:3964](../../backend/app/models/postgres.py#L3964)

Records which baseline was selected for a run and why.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `baseline_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `selection_reason` | `VARCHAR(100)` | False | False | `—` |  |
| `classification` | `VARCHAR(50)` | False | False | `—` |  |
| `baseline_build_number` | `VARCHAR(100)` | True | False | `—` |  |
| `pass_rate_delta` | `FLOAT` | True | False | `—` |  |
| `commit_range` | `JSON` | True | False | `—` |  |
| `config_drift` | `JSON` | True | False | `—` |  |
| `computed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `baseline_run_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `run_id`
- Index `ix_run_baselines_baseline` (unique=False): `run_baselines.baseline_run_id`; options `{}`
- Index `ix_run_baselines_run_id` (unique=True): `run_baselines.run_id`; options `{}`

## run_commit_ranges

[backend/app/models/postgres.py:2308](../../backend/app/models/postgres.py#L2308)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `base_commit` | `VARCHAR(64)` | True | False | `—` |  |
| `head_commit` | `VARCHAR(64)` | True | False | `—` |  |
| `base_run_id` | `UUID` | True | False | `—` |  |
| `source` | `VARCHAR(20)` | False | False | `server=unavailable` |  |
| `base_source` | `VARCHAR(30)` | False | False | `server=unavailable; application=unavailable` |  |
| `commits` | `JSONB` | False | False | `server=[]; application=list` |  |
| `resolved_at` | `DATETIME` | False | False | `server=now()` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_run_commit_ranges_run`: `run_id`
- Index `ix_run_commit_ranges_project_resolved` (unique=False): `run_commit_ranges.project_id, run_commit_ranges.resolved_at`; options `{}`

## run_comparison_reports

[backend/app/models/postgres.py:4003](../../backend/app/models/postgres.py#L4003)

Cached AI report for a run or suite comparison.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `left_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `right_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `suite_name_normalized` | `VARCHAR(500)` | False | False | `application=` |  |
| `compare_payload` | `JSON` | False | False | `—` |  |
| `ai_report` | `JSON` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=queued` |  |
| `fallback_used` | `BOOLEAN` | False | False | `application=False` |  |
| `prompt_version` | `VARCHAR(50)` | False | False | `application=run_compare_v1` |  |
| `model_name` | `VARCHAR(200)` | True | False | `—` |  |
| `created_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `error_message` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by_user_id`
- `ForeignKeyConstraint` `unnamed`: `left_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `right_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_run_comparison_report_scope`: `project_id, left_run_id, right_run_id, suite_name_normalized, prompt_version`
- Index `ix_run_comparison_reports_project` (unique=False): `run_comparison_reports.project_id, run_comparison_reports.created_at`; options `{}`
- Index `ix_run_comparison_reports_runs` (unique=False): `run_comparison_reports.left_run_id, run_comparison_reports.right_run_id`; options `{}`

## run_diffs

[backend/app/models/postgres.py:3984](../../backend/app/models/postgres.py#L3984)

Persisted diff payload for a run — avoids recomputation.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `baseline_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `diff_payload` | `JSON` | False | False | `—` |  |
| `computed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `baseline_run_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `run_id`
- Index `ix_run_diffs_baseline_run_id` (unique=False): `run_diffs.baseline_run_id`; options `{}`
- Index `ix_run_diffs_run_id` (unique=True): `run_diffs.run_id`; options `{}`

## run_downstream_outbox

[backend/app/models/postgres.py:534](../../backend/app/models/postgres.py#L534)

Durable intent to publish one post-ingestion operation.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `operation` | `VARCHAR(60)` | False | False | `—` |  |
| `input_version` | `VARCHAR(64)` | False | False | `—` |  |
| `payload` | `JSONB` | False | False | `application=dict` |  |
| `queue` | `VARCHAR(80)` | False | False | `application=default` |  |
| `priority` | `INTEGER` | False | False | `application=5` |  |
| `status` | `VARCHAR(20)` | False | False | `server=pending; application=pending` |  |
| `attempts` | `INTEGER` | False | False | `server=0; application=0` |  |
| `dispatch_failures` | `INTEGER` | False | False | `server=0; application=0` |  |
| `execution_attempts` | `INTEGER` | False | False | `server=0; application=0` |  |
| `next_attempt_at` | `DATETIME` | True | False | `—` |  |
| `lease_expires_at` | `DATETIME` | True | False | `—` |  |
| `dispatch_token` | `UUID` | True | False | `—` |  |
| `processing_task_id` | `VARCHAR(80)` | True | False | `—` |  |
| `published_at` | `DATETIME` | True | False | `—` |  |
| `processing_started_at` | `DATETIME` | True | False | `—` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |
| `completion_detail` | `VARCHAR(200)` | True | False | `—` |  |
| `last_error` | `VARCHAR(200)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_run_downstream_outbox_attempts_nonnegative`: `attempts >= 0`
- `CheckConstraint` `ck_run_downstream_outbox_dispatch_failures_nonnegative`: `dispatch_failures >= 0`
- `CheckConstraint` `ck_run_downstream_outbox_execution_attempts_nonnegative`: `execution_attempts >= 0`
- `CheckConstraint` `ck_run_downstream_outbox_status`: `status IN ('waiting', 'pending', 'sending', 'published', 'processing', 'completed', 'failed')`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_run_downstream_operation_version`: `run_id, operation, input_version`
- Index `ix_run_downstream_outbox_due_project` (unique=False): `run_downstream_outbox.status, run_downstream_outbox.next_attempt_at, run_downstream_outbox.project_id, run_downstream_outbox.created_at`; options `{}`

## run_intelligence_snapshots

[backend/app/models/postgres.py:3844](../../backend/app/models/postgres.py#L3844)

Cached run intelligence payload for fast page loads.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `schema_version` | `INTEGER` | False | False | `application=1` |  |
| `payload` | `JSON` | False | False | `—` |  |
| `generated_at` | `DATETIME` | False | False | `server=now()` |  |
| `fallback_used` | `BOOLEAN` | False | False | `application=False` |  |
| `stale` | `BOOLEAN` | False | False | `application=False` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `run_id`
- Index `ix_ris_generated` (unique=False): `run_intelligence_snapshots.generated_at`; options `{}`
- Index `ix_ris_run_id` (unique=True): `run_intelligence_snapshots.run_id`; options `{}`

## run_tombstones

[backend/app/models/postgres.py:6603](../../backend/app/models/postgres.py#L6603)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `run_id` | `UUID` | False | True | `—` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `deleted_at` | `DATETIME` | False | False | `server=now()` |  |
| `deleted_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `reason` | `TEXT` | True | False | `—` |  |
| `deletion_job_id` | `UUID` | True | False | `—` | deletion_jobs.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `deleted_by_id`
- `ForeignKeyConstraint` `unnamed`: `deletion_job_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `run_id`
- Index `ix_run_tombstones_project_deleted` (unique=False): `run_tombstones.project_id, run_tombstones.deleted_at`; options `{}`

## saved_views

[backend/app/models/postgres.py:4500](../../backend/app/models/postgres.py#L4500)

Persisted filter/scope configuration — personal or shared.

The `filters` JSON field supports both legacy filter-only payloads and
analytics widget configurations (AC-2):
  Legacy: {"severity": "critical", "date_range": 7}
  Analytics: {"page": "dashboard", "widgets": ["w1", "w2"], "filters": {...}, "version": 1}

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `page` | `VARCHAR(50)` | True | False | `—` |  |
| `filters` | `JSON` | False | False | `—` |  |
| `is_shared` | `BOOLEAN` | False | False | `application=False` |  |
| `is_default` | `BOOLEAN` | False | False | `application=False` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_sv_project` (unique=False): `saved_views.project_id`; options `{}`
- Index `ix_sv_user` (unique=False): `saved_views.user_id`; options `{}`

## scim_tokens

[backend/app/models/postgres.py:4329](../../backend/app/models/postgres.py#L4329)

Bearer token for SCIM 2.0 provisioning endpoints.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `token_hash` | `VARCHAR(64)` | False | False | `—` |  |
| `token_hint` | `VARCHAR(12)` | False | False | `—` |  |
| `sso_config_id` | `UUID` | True | False | `—` | sso_configurations.id / SET NULL |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `last_used_at` | `DATETIME` | True | False | `—` |  |
| `expires_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `sso_config_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `token_hash`
- Index `ix_scim_token_hash` (unique=True): `scim_tokens.token_hash`; options `{}`

## secret_refs

[backend/app/models/postgres.py:3722](../../backend/app/models/postgres.py#L3722)

Stores sensitive values (API keys, tokens, passwords) separately from settings.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `scope` | `VARCHAR(100)` | False | False | `—` |  |
| `provider` | `VARCHAR(50)` | False | False | `application=db` |  |
| `key_name` | `VARCHAR(255)` | False | False | `—` |  |
| `encrypted_value` | `TEXT` | True | False | `—` |  |
| `masked_value` | `VARCHAR(50)` | True | False | `—` |  |
| `rotation_status` | `VARCHAR(30)` | False | False | `application=active` |  |
| `updated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `updated_by`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_secret_refs_scope_key` (unique=True): `secret_refs.scope, secret_refs.key_name`; options `{}`

## semantic_reindex_jobs

[backend/app/models/postgres.py:628](../../backend/app/models/postgres.py#L628)

Durable, fenced progress for one global or project semantic rebuild.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `scope_key` | `VARCHAR(80)` | False | True | `—` |  |
| `project_id` | `UUID` | True | False | `—` | projects.id / CASCADE |
| `job_id` | `UUID` | False | False | `application=uuid4` |  |
| `state_version` | `INTEGER` | False | False | `server=1; application=1` |  |
| `status` | `VARCHAR(20)` | False | False | `server=running; application=running` |  |
| `high_water_created_at` | `DATETIME` | False | False | `—` |  |
| `high_water_id` | `UUID` | False | False | `—` |  |
| `cursor_created_at` | `DATETIME` | True | False | `—` |  |
| `cursor_id` | `UUID` | True | False | `—` |  |
| `processed_count` | `BIGINT` | False | False | `server=0; application=0` |  |
| `lease_owner` | `UUID` | True | False | `—` |  |
| `lease_expires_at` | `DATETIME` | True | False | `—` |  |
| `fence_token` | `BIGINT` | False | False | `server=1; application=1` |  |
| `last_error` | `VARCHAR(500)` | True | False | `—` |  |
| `started_at` | `DATETIME` | False | False | `server=now()` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_semantic_reindex_job_processed_count`: `processed_count >= 0`
- `CheckConstraint` `ck_semantic_reindex_job_state_version`: `state_version = 1`
- `CheckConstraint` `ck_semantic_reindex_job_status`: `status IN ('running', 'finalizing', 'succeeded')`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `scope_key`
- `UniqueConstraint` `uq_semantic_reindex_jobs_job_id`: `job_id`
- Index `ix_semantic_reindex_jobs_status_lease` (unique=False): `semantic_reindex_jobs.status, semantic_reindex_jobs.lease_expires_at`; options `{}`

## service_ownership_rules

[backend/app/models/postgres.py:4472](../../backend/app/models/postgres.py#L4472)

Maps a matcher pattern (suite, component, package, path) to a team/service owner.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `match_type` | `VARCHAR(30)` | False | False | `—` |  |
| `match_pattern` | `VARCHAR(500)` | False | False | `—` |  |
| `service_name` | `VARCHAR(255)` | False | False | `—` |  |
| `team_name` | `VARCHAR(255)` | False | False | `—` |  |
| `team_contact` | `VARCHAR(500)` | True | False | `—` |  |
| `priority` | `INTEGER` | False | False | `application=0` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `created_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `created_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_sor_active` (unique=False): `service_ownership_rules.project_id, service_ownership_rules.is_active`; options `{}`
- Index `ix_sor_project` (unique=False): `service_ownership_rules.project_id`; options `{}`

## settings_audit_log

[backend/app/models/postgres.py:3758](../../backend/app/models/postgres.py#L3758)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `setting_key` | `VARCHAR(100)` | False | False | `—` |  |
| `action` | `VARCHAR(30)` | False | False | `—` |  |
| `actor_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `actor_name` | `VARCHAR(200)` | True | False | `—` |  |
| `changed_fields` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `actor_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_settings_audit_actor` (unique=False): `settings_audit_log.actor_id`; options `{}`
- Index `ix_settings_audit_key` (unique=False): `settings_audit_log.setting_key`; options `{}`

## sso_configurations

[backend/app/models/postgres.py:4264](../../backend/app/models/postgres.py#L4264)

Tenant/system-level SSO configuration for a SAML identity provider.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `display_name` | `VARCHAR(255)` | False | False | `—` |  |
| `provider_type` | `VARCHAR(20)` | False | False | `application=SAML` |  |
| `idp_entity_id` | `VARCHAR(1000)` | False | False | `—` |  |
| `idp_sso_url` | `VARCHAR(2000)` | False | False | `—` |  |
| `idp_slo_url` | `VARCHAR(2000)` | True | False | `—` |  |
| `idp_certificate` | `TEXT` | False | False | `—` |  |
| `sp_entity_id` | `VARCHAR(1000)` | False | False | `—` |  |
| `sp_acs_url` | `VARCHAR(2000)` | False | False | `—` |  |
| `audience` | `VARCHAR(1000)` | True | False | `—` |  |
| `role_mapping` | `JSON` | True | False | `application=dict` |  |
| `default_role` | `VARCHAR(20)` | False | False | `application=VIEWER` |  |
| `group_attribute` | `VARCHAR(255)` | True | False | `—` |  |
| `enforcement_mode` | `VARCHAR(20)` | False | False | `application=OPTIONAL` |  |
| `is_active` | `BOOLEAN` | False | False | `application=False` |  |
| `last_test_at` | `DATETIME` | True | False | `—` |  |
| `last_test_success` | `BOOLEAN` | True | False | `—` |  |
| `last_test_error` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_sso_config_active` (unique=False): `sso_configurations.is_active`; options `{}`
- Index `uq_sso_config_single_active` (unique=True): `sso_configurations.is_active`; options `{"postgresql_where": "is_active IS TRUE", "sqlite_where": "is_active IS TRUE"}`

## suite_membership_events

[backend/app/models/postgres.py:3269](../../backend/app/models/postgres.py#L3269)

Immutable audit log of suite membership changes detected during sync.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `suite_name` | `VARCHAR(500)` | False | False | `—` |  |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(1000)` | False | False | `application=` |  |
| `event_type` | `VARCHAR(20)` | False | False | `—` |  |
| `run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `old_values` | `JSON` | True | False | `—` |  |
| `new_values` | `JSON` | True | False | `—` |  |
| `details` | `TEXT` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_sme_project_suite` (unique=False): `suite_membership_events.project_id, suite_membership_events.suite_name, suite_membership_events.created_at`; options `{}`
- Index `ix_sme_run` (unique=False): `suite_membership_events.run_id`; options `{}`

## suite_memberships

[backend/app/models/postgres.py:3239](../../backend/app/models/postgres.py#L3239)

Tracks which test cases belong to which suite, linked to the run that confirmed membership.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `suite_name` | `VARCHAR(500)` | False | False | `—` |  |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(1000)` | False | False | `—` |  |
| `class_name` | `VARCHAR(500)` | True | False | `—` |  |
| `managed_test_case_id` | `UUID` | True | False | `—` | managed_test_cases.id / SET NULL |
| `source` | `VARCHAR(20)` | False | False | `application=execution` |  |
| `status` | `VARCHAR(20)` | False | False | `application=active` |  |
| `last_seen_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `first_seen_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `deleted_at_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `review_tag` | `VARCHAR(50)` | True | False | `—` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `deleted_at_run_id`
- `ForeignKeyConstraint` `unnamed`: `first_seen_run_id`
- `ForeignKeyConstraint` `unnamed`: `last_seen_run_id`
- `ForeignKeyConstraint` `unnamed`: `managed_test_case_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_suite_membership`: `project_id, suite_name, test_fingerprint`
- Index `ix_sm_fingerprint` (unique=False): `suite_memberships.test_fingerprint`; options `{}`
- Index `ix_sm_project_suite` (unique=False): `suite_memberships.project_id, suite_memberships.suite_name`; options `{}`
- Index `ix_sm_status` (unique=False): `suite_memberships.status`; options `{}`

## suite_run_reviews

[backend/app/models/postgres.py:918](../../backend/app/models/postgres.py#L918)

Human-in-the-loop overlay on AI analysis for a (run, suite) (migration 0076).

Non-gating: the AI pipeline still completes runs without waiting for a
review. State machine: pending → confirmed | acknowledged | review_later.
Unique on ``(test_run_id, suite_name)`` so each run+suite has at most one
review (later updates mutate the row instead of inserting).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `suite_name` | `VARCHAR(500)` | False | False | `—` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `reviewer_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `state` | `VARCHAR(20)` | False | False | `server='pending'; application=pending` |  |
| `note` | `TEXT` | True | False | `—` |  |
| `reviewed_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `reviewer_user_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_suite_run_reviews_run_suite`: `test_run_id, suite_name`
- Index `ix_suite_run_reviews_project_suite` (unique=False): `suite_run_reviews.project_id, suite_run_reviews.suite_name`; options `{}`
- Index `ix_suite_run_reviews_state` (unique=False): `suite_run_reviews.state`; options `{}`
- Index `ix_suite_run_reviews_test_run_id` (unique=False): `suite_run_reviews.test_run_id`; options `{}`

## systemic_flake_cluster

[backend/app/models/postgres.py:5838](../../backend/app/models/postgres.py#L5838)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `cluster_key` | `VARCHAR(32)` | False | False | `—` |  |
| `label` | `VARCHAR(500)` | False | False | `—` |  |
| `cause_family` | `VARCHAR(40)` | True | False | `—` |  |
| `size` | `INTEGER` | False | False | `application=0` |  |
| `cohesion` | `FLOAT` | True | False | `—` |  |
| `co_failure_runs` | `INTEGER` | False | False | `application=0` |  |
| `window_days` | `INTEGER` | False | False | `application=60` |  |
| `computed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ux_systemic_cluster_project_key` (unique=True): `systemic_flake_cluster.project_id, systemic_flake_cluster.cluster_key`; options `{}`

## systemic_flake_cluster_member

[backend/app/models/postgres.py:5863](../../backend/app/models/postgres.py#L5863)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `cluster_id` | `UUID` | False | False | `—` | systemic_flake_cluster.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `test_name` | `VARCHAR(1000)` | True | False | `—` |  |
| `failure_runs` | `INTEGER` | False | False | `application=0` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `cluster_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ux_systemic_member_cluster_fingerprint` (unique=True): `systemic_flake_cluster_member.cluster_id, systemic_flake_cluster_member.test_fingerprint`; options `{}`

## team_notification_channels

[backend/app/models/postgres.py:1629](../../backend/app/models/postgres.py#L1629)

Per-(project, team) notification target for ownership-routed
transition notifications (PMF US-7.3).

Teams exist only as free-text ``team_name`` strings on
``service_ownership_rules`` rows — many rules can share one team, so
the channel lives in its own table keyed by (project, team_name)
instead of being duplicated per rule. A transition event whose test
resolves (via the ownership rules) to a team with an active row here
is delivered directly to that team's channel; everything else falls
back to the project's default notification preferences.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `team_name` | `VARCHAR(255)` | False | False | `—` |  |
| `channel_type` | `VARCHAR(20)` | False | False | `—` |  |
| `target` | `VARCHAR(2000)` | False | False | `—` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_team_notif_channel_project_team`: `project_id, team_name`
- Index `ix_team_notif_channels_project` (unique=False): `team_notification_channels.project_id`; options `{}`

## tenant_metric_snapshots

[backend/app/models/postgres.py:4746](../../backend/app/models/postgres.py#L4746)

Per-project observability metric snapshot — aggregated periodically.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `recorded_at` | `DATETIME` | False | False | `server=now()` |  |
| `total_runs` | `INTEGER` | False | False | `application=0` |  |
| `total_tests` | `INTEGER` | False | False | `application=0` |  |
| `avg_pass_rate` | `FLOAT` | True | False | `—` |  |
| `failed_runs` | `INTEGER` | False | False | `application=0` |  |
| `ai_analyses_count` | `INTEGER` | False | False | `application=0` |  |
| `release_decisions_count` | `INTEGER` | False | False | `application=0` |  |
| `audit_events_count` | `INTEGER` | False | False | `application=0` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_tms_project_time` (unique=False): `tenant_metric_snapshots.project_id, tenant_metric_snapshots.recorded_at`; options `{}`

## tenant_onboarding_status

[backend/app/models/postgres.py:4067](../../backend/app/models/postgres.py#L4067)

Tracks onboarding wizard progress per project.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `step_key` | `VARCHAR(50)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=pending` |  |
| `completed_at` | `DATETIME` | True | False | `—` |  |
| `completed_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `completed_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_tos_project_step` (unique=True): `tenant_onboarding_status.project_id, tenant_onboarding_status.step_key`; options `{}`

## test_attachments

[backend/app/models/postgres.py:1243](../../backend/app/models/postgres.py#L1243)

Index-only attachment metadata for the latest-run snapshot.

Phase 1 stores references (``source_ref``) only — bytes are not proxied.
Anchored to the canonical test (CASCADE) like steps; ``test_step_id``
(CASCADE, nullable) links a step-scoped attachment, NULL = test-level.
Migration 0093.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `canonical_test_case_id` | `UUID` | False | False | `—` | canonical_test_cases.id / CASCADE |
| `test_step_id` | `UUID` | True | False | `—` | test_steps.id / CASCADE |
| `source_test_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `name` | `VARCHAR(500)` | False | False | `—` |  |
| `source_ref` | `VARCHAR(1000)` | True | False | `—` |  |
| `media_type` | `VARCHAR(100)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `canonical_test_case_id`
- `ForeignKeyConstraint` `unnamed`: `source_test_run_id`
- `ForeignKeyConstraint` `unnamed`: `test_step_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_test_attachments_canonical` (unique=False): `test_attachments.canonical_test_case_id`; options `{}`
- Index `ix_test_attachments_step` (unique=False): `test_attachments.test_step_id`; options `{}`

ORM navigation and cascade declarations:

```python
canonical_test_case: Mapped['CanonicalTestCase'] = relationship('CanonicalTestCase', back_populates='attachments')
test_step: Mapped[Optional['TestStep']] = relationship('TestStep', back_populates='attachments')
```

## test_case_audit_logs

[backend/app/models/postgres.py:3181](../../backend/app/models/postgres.py#L3181)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `entity_type` | `VARCHAR(30)` | False | False | `—` |  |
| `entity_id` | `UUID` | False | False | `—` |  |
| `project_id` | `UUID` | True | False | `—` | projects.id / SET NULL |
| `action` | `VARCHAR(50)` | False | False | `—` |  |
| `actor_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `actor_name` | `VARCHAR(200)` | True | False | `—` |  |
| `old_values` | `JSON` | True | False | `—` |  |
| `new_values` | `JSON` | True | False | `—` |  |
| `details` | `TEXT` | True | False | `—` |  |
| `reason` | `VARCHAR(500)` | True | False | `—` |  |
| `policy_snapshot` | `JSON` | True | False | `—` |  |
| `transition_from` | `VARCHAR(30)` | True | False | `—` |  |
| `transition_to` | `VARCHAR(30)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `actor_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_tcal_actor` (unique=False): `test_case_audit_logs.actor_id`; options `{}`
- Index `ix_tcal_entity` (unique=False): `test_case_audit_logs.entity_type, test_case_audit_logs.entity_id`; options `{}`
- Index `ix_tcal_project_created` (unique=False): `test_case_audit_logs.project_id, test_case_audit_logs.created_at`; options `{}`
- Index `ix_test_case_audit_logs_created_at` (unique=False): `test_case_audit_logs.created_at`; options `{}`

## test_case_comments

[backend/app/models/postgres.py:2712](../../backend/app/models/postgres.py#L2712)

Threaded comment on a ManagedTestCase.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_case_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `author_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `content` | `TEXT` | False | False | `—` |  |
| `comment_type` | `VARCHAR(30)` | False | False | `application=general` |  |
| `parent_id` | `UUID` | True | False | `—` | test_case_comments.id / SET NULL |
| `step_number` | `INTEGER` | True | False | `—` |  |
| `is_resolved` | `BOOLEAN` | False | False | `application=False` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `author_id`
- `ForeignKeyConstraint` `unnamed`: `parent_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_tcc_test_case` (unique=False): `test_case_comments.test_case_id`; options `{}`

## test_case_history

[backend/app/models/postgres.py:1280](../../backend/app/models/postgres.py#L1280)

Denormalized history for fast timeline queries.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_case_id` | `UUID` | False | False | `—` | test_cases.id / CASCADE |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `duration_ms` | `INTEGER` | True | False | `—` |  |
| `failure_category` | `VARCHAR(30)` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_history_fingerprint_date` (unique=False): `test_case_history.test_fingerprint, test_case_history.created_at`; options `{}`
- Index `ix_history_fingerprint_date_status` (unique=False): `test_case_history.test_fingerprint, test_case_history.created_at, test_case_history.status`; options `{}`
- Index `ix_history_test_case_id` (unique=False): `test_case_history.test_case_id`; options `{}`
- Index `ix_history_test_run_id` (unique=False): `test_case_history.test_run_id`; options `{}`

ORM navigation and cascade declarations:

```python
test_case: Mapped['TestCase'] = relationship('TestCase', back_populates='history')
```

## test_case_reviews

[backend/app/models/postgres.py:2676](../../backend/app/models/postgres.py#L2676)

Review cycle for a ManagedTestCase.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_case_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `reviewer_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `requested_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `status` | `VARCHAR(30)` | False | False | `application=pending` |  |
| `ai_review_completed` | `BOOLEAN` | False | False | `application=False` |  |
| `ai_quality_score` | `INTEGER` | True | False | `—` |  |
| `ai_review_notes` | `JSON` | True | False | `—` |  |
| `ai_reviewed_at` | `DATETIME` | True | False | `—` |  |
| `human_notes` | `TEXT` | True | False | `—` |  |
| `reviewed_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `requested_by_id`
- `ForeignKeyConstraint` `unnamed`: `reviewer_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_tcr_reviewer` (unique=False): `test_case_reviews.reviewer_id`; options `{}`
- Index `ix_tcr_test_case` (unique=False): `test_case_reviews.test_case_id`; options `{}`
- Index `uq_test_case_reviews_one_open_per_case` (unique=True): `test_case_reviews.test_case_id`; options `{"postgresql_where": "status IN ('pending', 'in_progress')"}`

## test_case_versions

[backend/app/models/postgres.py:2631](../../backend/app/models/postgres.py#L2631)

Immutable snapshot of a ManagedTestCase at each save.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_case_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `version` | `INTEGER` | False | False | `—` |  |
| `title` | `VARCHAR(500)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `objective` | `TEXT` | True | False | `—` |  |
| `preconditions` | `TEXT` | True | False | `—` |  |
| `steps` | `JSON` | True | False | `—` |  |
| `parameters` | `JSON` | True | False | `—` |  |
| `expected_result` | `TEXT` | True | False | `—` |  |
| `test_data` | `TEXT` | True | False | `—` |  |
| `test_type` | `VARCHAR(50)` | True | False | `—` |  |
| `priority` | `VARCHAR(20)` | True | False | `—` |  |
| `severity` | `VARCHAR(20)` | True | False | `—` |  |
| `feature_area` | `VARCHAR(500)` | True | False | `—` |  |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `test_suite_id` | `UUID` | True | False | `—` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `estimated_duration_minutes` | `INTEGER` | True | False | `—` |  |
| `is_automated` | `BOOLEAN` | True | False | `—` |  |
| `automation_status` | `VARCHAR(30)` | True | False | `—` |  |
| `test_fingerprint` | `VARCHAR(64)` | True | False | `—` |  |
| `status` | `VARCHAR(30)` | False | False | `—` |  |
| `changed_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `change_summary` | `VARCHAR(500)` | True | False | `—` |  |
| `change_type` | `VARCHAR(30)` | False | False | `application=updated` |  |
| `changed_fields` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `changed_by_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_test_case_versions_case_version`: `test_case_id, version`
- Index `ix_tcv_test_case` (unique=False): `test_case_versions.test_case_id`; options `{}`

## test_cases

[backend/app/models/postgres.py:707](../../backend/app/models/postgres.py#L707)

Individual test case result within a run.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `test_fingerprint` | `VARCHAR(64)` | False | False | `—` |  |
| `canonical_test_case_id` | `UUID` | True | False | `—` | canonical_test_cases.id / SET NULL |
| `test_name` | `VARCHAR(1000)` | False | False | `—` |  |
| `full_name` | `VARCHAR(2000)` | True | False | `—` |  |
| `suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `class_name` | `VARCHAR(500)` | True | False | `—` |  |
| `package_name` | `VARCHAR(500)` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=TestStatus.UNKNOWN` |  |
| `duration_ms` | `INTEGER` | True | False | `—` |  |
| `severity` | `VARCHAR(20)` | True | False | `—` |  |
| `feature` | `VARCHAR(500)` | True | False | `—` |  |
| `story` | `VARCHAR(500)` | True | False | `—` |  |
| `epic` | `VARCHAR(500)` | True | False | `—` |  |
| `owner` | `VARCHAR(255)` | True | False | `—` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `failure_category` | `VARCHAR(30)` | True | False | `—` |  |
| `error_message` | `TEXT` | True | False | `—` |  |
| `assigned_to_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `triage_status` | `VARCHAR(30)` | False | False | `server=PENDING_REVIEW; application=PENDING_REVIEW` |  |
| `triage_notes` | `VARCHAR(2000)` | True | False | `—` |  |
| `triage_updated_at` | `DATETIME` | True | False | `—` |  |
| `triage_updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `retry_count` | `INTEGER` | True | False | `—` |  |
| `is_flaky_run` | `BOOLEAN` | True | False | `—` |  |
| `stack_trace` | `TEXT` | True | False | `—` |  |
| `step_count` | `INTEGER` | True | False | `—` |  |
| `source_uuid` | `VARCHAR(255)` | True | False | `—` |  |
| `source_history_id` | `VARCHAR(255)` | True | False | `—` |  |
| `source_test_case_id` | `VARCHAR(255)` | True | False | `—` |  |
| `parser_format` | `VARCHAR(100)` | True | False | `—` |  |
| `parser_version` | `VARCHAR(100)` | True | False | `—` |  |
| `source_parameters` | `JSON` | True | False | `—` |  |
| `source_links` | `JSON` | True | False | `—` |  |
| `source_labels` | `JSON` | True | False | `—` |  |
| `source_extensions` | `JSON` | True | False | `—` |  |
| `service_name` | `VARCHAR(255)` | True | False | `—` |  |
| `component_names` | `JSON` | True | False | `—` |  |
| `steps_present` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `minio_s3_prefix` | `VARCHAR(1000)` | True | False | `—` |  |
| `has_attachments` | `BOOLEAN` | False | False | `application=False` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `assigned_to_user_id`
- `ForeignKeyConstraint` `unnamed`: `canonical_test_case_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `ForeignKeyConstraint` `unnamed`: `triage_updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_test_cases_run_fingerprint`: `test_run_id, test_fingerprint`
- Index `ix_test_cases_assigned_to_user_id` (unique=False): `test_cases.assigned_to_user_id`; options `{}`
- Index `ix_test_cases_assignee_triage` (unique=False): `test_cases.assigned_to_user_id, test_cases.triage_status`; options `{}`
- Index `ix_test_cases_canonical` (unique=False): `test_cases.canonical_test_case_id`; options `{}`
- Index `ix_test_cases_fingerprint` (unique=False): `test_cases.test_fingerprint`; options `{}`
- Index `ix_test_cases_run_status` (unique=False): `test_cases.test_run_id, test_cases.status`; options `{}`
- Index `ix_test_cases_run_suite` (unique=False): `test_cases.test_run_id, test_cases.suite_name`; options `{}`
- Index `ix_test_cases_semantic_reindex_keyset` (unique=False): `test_cases.created_at, test_cases.id`; options `{}`
- Index `ix_test_cases_test_fingerprint` (unique=False): `test_cases.test_fingerprint`; options `{}`

ORM navigation and cascade declarations:

```python
test_run: Mapped['TestRun'] = relationship('TestRun', back_populates='test_cases')
history: Mapped[list['TestCaseHistory']] = relationship('TestCaseHistory', back_populates='test_case')
ai_analysis: Mapped[Optional['AIAnalysis']] = relationship('AIAnalysis', back_populates='test_case', uselist=False)
defects: Mapped[list['Defect']] = relationship('Defect', back_populates='test_case')
canonical_test_case: Mapped[Optional['CanonicalTestCase']] = relationship('CanonicalTestCase', back_populates='test_cases')
```

## test_execution_reviews

[backend/app/models/postgres.py:957](../../backend/app/models/postgres.py#L957)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_case_id` | `UUID` | False | False | `—` | test_cases.id / CASCADE |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `state` | `VARCHAR(30)` | False | False | `server='pending_review'; application=pending_review` |  |
| `reviewed_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `defect_link` | `VARCHAR(2000)` | True | False | `—` |  |
| `note` | `TEXT` | True | False | `—` |  |
| `transitioned_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_ter_defect_link_required`: `state <> 'defect_filed' OR NULLIF(BTRIM(defect_link), '') IS NOT NULL`
- `CheckConstraint` `ck_ter_state_valid`: `state IN ('pending_review', 'reviewed', 'defect_filed', 'false_positive', 'reproducible')`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `reviewed_by_user_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_ter_test_case_id`: `test_case_id`
- Index `ix_ter_project_state` (unique=False): `test_execution_reviews.project_id, test_execution_reviews.state`; options `{}`
- Index `ix_ter_reviewed_by` (unique=False): `test_execution_reviews.reviewed_by_user_id`; options `{}`

## test_health_recommendations

[backend/app/models/postgres.py:4154](../../backend/app/models/postgres.py#L4154)

Persisted test health findings per run from TestHealthAgent.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `test_case_id` | `UUID` | False | False | `—` | test_cases.id / CASCADE |
| `test_name` | `VARCHAR(1000)` | False | False | `—` |  |
| `health_score` | `INTEGER` | False | False | `—` |  |
| `violations` | `JSON` | True | False | `application=list` |  |
| `critical_count` | `INTEGER` | False | False | `application=0` |  |
| `warning_count` | `INTEGER` | False | False | `application=0` |  |
| `recommendation` | `TEXT` | True | False | `—` |  |
| `anti_patterns` | `JSON` | True | False | `application=list` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_thr_run` (unique=False): `test_health_recommendations.test_run_id`; options `{}`
- Index `ix_thr_test_case` (unique=False): `test_health_recommendations.test_case_id`; options `{}`

## test_plan_items

[backend/app/models/postgres.py:2850](../../backend/app/models/postgres.py#L2850)

A test case entry within a TestPlan.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `plan_id` | `UUID` | False | False | `—` | test_plans.id / CASCADE |
| `test_case_id` | `UUID` | False | False | `—` | managed_test_cases.id / CASCADE |
| `order_index` | `INTEGER` | False | False | `application=0` |  |
| `priority_override` | `VARCHAR(20)` | True | False | `—` |  |
| `execution_status` | `VARCHAR(30)` | False | False | `application=not_run` |  |
| `executed_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `executed_at` | `DATETIME` | True | False | `—` |  |
| `execution_notes` | `TEXT` | True | False | `—` |  |
| `actual_duration_minutes` | `INTEGER` | True | False | `—` |  |
| `test_case_result_id` | `UUID` | True | False | `—` | test_cases.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `executed_by_id`
- `ForeignKeyConstraint` `unnamed`: `plan_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_id`
- `ForeignKeyConstraint` `unnamed`: `test_case_result_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_plan_test_case`: `plan_id, test_case_id`
- Index `ix_tpi_plan` (unique=False): `test_plan_items.plan_id`; options `{}`

## test_plans

[backend/app/models/postgres.py:2807](../../backend/app/models/postgres.py#L2807)

Named collection of test cases forming an executable test plan.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(500)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `objective` | `TEXT` | True | False | `—` |  |
| `status` | `VARCHAR(30)` | False | False | `application=draft` |  |
| `planned_start_date` | `DATETIME` | True | False | `—` |  |
| `planned_end_date` | `DATETIME` | True | False | `—` |  |
| `actual_start_date` | `DATETIME` | True | False | `—` |  |
| `actual_end_date` | `DATETIME` | True | False | `—` |  |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `assigned_to_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `ai_generated` | `BOOLEAN` | False | False | `application=False` |  |
| `ai_generation_context` | `TEXT` | True | False | `—` |  |
| `total_cases` | `INTEGER` | False | False | `application=0` |  |
| `executed_cases` | `INTEGER` | False | False | `application=0` |  |
| `passed_cases` | `INTEGER` | False | False | `application=0` |  |
| `failed_cases` | `INTEGER` | False | False | `application=0` |  |
| `blocked_cases` | `INTEGER` | False | False | `application=0` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `assigned_to_id`
- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_tp_project` (unique=False): `test_plans.project_id`; options `{}`
- Index `ix_tp_status` (unique=False): `test_plans.status`; options `{}`

## test_runs

[backend/app/models/postgres.py:292](../../backend/app/models/postgres.py#L292)

Represents a single CI/CD pipeline execution (Jenkins build).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `build_number` | `VARCHAR(100)` | False | False | `—` |  |
| `jenkins_job` | `VARCHAR(500)` | True | False | `—` |  |
| `trigger_source` | `VARCHAR(50)` | True | False | `—` |  |
| `branch` | `VARCHAR(255)` | True | False | `—` |  |
| `commit_hash` | `VARCHAR(64)` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=LaunchStatus.IN_PROGRESS` |  |
| `ingestion_source` | `VARCHAR(20)` | False | False | `server=unknown; application=unknown` |  |
| `ci_provider` | `VARCHAR(30)` | True | False | `—` |  |
| `ci_repo` | `VARCHAR(300)` | True | False | `—` |  |
| `pr_number` | `INTEGER` | True | False | `—` |  |
| `ci_actor` | `VARCHAR(120)` | True | False | `—` |  |
| `ci_run_url` | `VARCHAR(1000)` | True | False | `—` |  |
| `ingestion_identity` | `VARCHAR(64)` | True | False | `—` |  |
| `ingestion_attempted_tests` | `INTEGER` | True | False | `—` |  |
| `ingestion_rejected_tests` | `INTEGER` | False | False | `server=0; application=0` |  |
| `ingestion_complete` | `BOOLEAN` | True | False | `—` |  |
| `ingestion_rejection_reasons` | `JSONB` | True | False | `—` |  |
| `environment` | `VARCHAR(100)` | True | False | `—` |  |
| `primary_release_id` | `UUID` | True | False | `—` | releases.id / SET NULL |
| `total_tests` | `INTEGER` | False | False | `application=0` |  |
| `passed_tests` | `INTEGER` | False | False | `application=0` |  |
| `failed_tests` | `INTEGER` | False | False | `application=0` |  |
| `skipped_tests` | `INTEGER` | False | False | `application=0` |  |
| `broken_tests` | `INTEGER` | False | False | `application=0` |  |
| `unknown_tests` | `INTEGER` | False | False | `server=0; application=0` |  |
| `pass_rate` | `FLOAT` | True | False | `—` |  |
| `duration_ms` | `INTEGER` | True | False | `—` |  |
| `ocp_pod_name` | `VARCHAR(255)` | True | False | `—` |  |
| `ocp_node` | `VARCHAR(255)` | True | False | `—` |  |
| `ocp_namespace` | `VARCHAR(255)` | True | False | `—` |  |
| `ocp_metadata` | `JSON` | True | False | `—` |  |
| `minio_prefix` | `VARCHAR(1000)` | True | False | `—` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `primary_suite_name` | `VARCHAR(500)` | True | False | `—` |  |
| `suite_names` | `JSON` | True | False | `—` |  |
| `event_archive` | `JSONB` | True | False | `—` |  |
| `event_archive_at` | `DATETIME` | True | False | `—` |  |
| `start_time` | `DATETIME` | True | False | `—` |  |
| `end_time` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `primary_release_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_test_runs_created_at` (unique=False): `test_runs.created_at`; options `{}`
- Index `ix_test_runs_ingestion_identity` (unique=False): `test_runs.ingestion_identity`; options `{}`
- Index `ix_test_runs_primary_suite_name` (unique=False): `test_runs.primary_suite_name`; options `{}`
- Index `ix_test_runs_project_pr` (unique=False): `test_runs.project_id, test_runs.pr_number`; options `{"postgresql_where": "pr_number IS NOT NULL"}`
- Index `ix_test_runs_project_release_created` (unique=False): `test_runs.project_id, test_runs.primary_release_id, test_runs.created_at`; options `{}`
- Index `ix_test_runs_project_status` (unique=False): `test_runs.project_id, test_runs.status`; options `{}`
- Index `ix_test_runs_project_status_created` (unique=False): `test_runs.project_id, test_runs.status, test_runs.created_at`; options `{}`
- Index `uq_test_runs_legacy_build` (unique=True): `test_runs.project_id, test_runs.build_number, test_runs.jenkins_job`; options `{"postgresql_where": "ingestion_identity IS NULL"}`
- Index `uq_test_runs_project_ingestion_identity` (unique=True): `test_runs.project_id, test_runs.ingestion_identity`; options `{"postgresql_where": "ingestion_identity IS NOT NULL"}`

ORM navigation and cascade declarations:

```python
project: Mapped['Project'] = relationship('Project', back_populates='test_runs')
test_cases: Mapped[list['TestCase']] = relationship('TestCase', back_populates='test_run', lazy='dynamic')
```

## test_step_runs

[backend/app/models/postgres.py:1191](../../backend/app/models/postgres.py#L1191)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `canonical_test_case_id` | `UUID` | False | False | `—` | canonical_test_cases.id / CASCADE |
| `source_test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `ordinal` | `INTEGER` | False | False | `—` |  |
| `depth` | `INTEGER` | False | False | `server=0; application=0` |  |
| `name` | `VARCHAR(2000)` | False | False | `—` |  |
| `keyword` | `VARCHAR(50)` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `duration_ms` | `INTEGER` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `canonical_test_case_id`
- `ForeignKeyConstraint` `unnamed`: `source_test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_test_step_runs_canonical_run_ordinal`: `canonical_test_case_id, source_test_run_id, ordinal`
- Index `ix_test_step_runs_source_run` (unique=False): `test_step_runs.source_test_run_id`; options `{}`

## test_steps

[backend/app/models/postgres.py:1127](../../backend/app/models/postgres.py#L1127)

One granular step in the LATEST-RUN-ONLY snapshot for a logical test.

LOCKED retention model (migration 0093): steps anchor to the project-scoped
``canonical_test_cases`` identity — exactly ONE snapshot per
``(project_id, test_fingerprint)`` — NOT to the per-run ``test_cases`` rows
that accumulate. On ingest of a newer run, ingestion DELETEs this canonical
test's steps and INSERTs the new ones inside the ingestion-pipeline
transaction. ``source_test_run_id`` is provenance only (which run produced
the snapshot). ``parent_step_id`` (self-FK, CASCADE) models nested steps.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `canonical_test_case_id` | `UUID` | False | False | `—` | canonical_test_cases.id / CASCADE |
| `source_test_run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `parent_step_id` | `UUID` | True | False | `—` | test_steps.id / CASCADE |
| `ordinal` | `INTEGER` | False | False | `—` |  |
| `depth` | `INTEGER` | False | False | `server=0; application=0` |  |
| `name` | `VARCHAR(2000)` | False | False | `—` |  |
| `keyword` | `VARCHAR(50)` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `—` |  |
| `duration_ms` | `INTEGER` | True | False | `—` |  |
| `start_ms` | `BIGINT` | True | False | `—` |  |
| `assertion_message` | `TEXT` | True | False | `—` |  |
| `assertion_trace` | `TEXT` | True | False | `—` |  |
| `expected_value` | `TEXT` | True | False | `—` |  |
| `actual_value` | `TEXT` | True | False | `—` |  |
| `parameters` | `JSONB` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `canonical_test_case_id`
- `ForeignKeyConstraint` `unnamed`: `parent_step_id`
- `ForeignKeyConstraint` `unnamed`: `source_test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_test_steps_canonical_ordinal` (unique=False): `test_steps.canonical_test_case_id, test_steps.ordinal`; options `{}`
- Index `ix_test_steps_parent` (unique=False): `test_steps.parent_step_id`; options `{}`
- Index `ix_test_steps_source_run` (unique=False): `test_steps.source_test_run_id`; options `{}`

ORM navigation and cascade declarations:

```python
canonical_test_case: Mapped['CanonicalTestCase'] = relationship('CanonicalTestCase', back_populates='steps')
children: Mapped[list['TestStep']] = relationship('TestStep', back_populates='parent', cascade='all, delete-orphan', lazy='select')
parent: Mapped[Optional['TestStep']] = relationship('TestStep', back_populates='children', remote_side='TestStep.id')
attachments: Mapped[list['TestAttachment']] = relationship('TestAttachment', back_populates='test_step', cascade='all, delete-orphan', lazy='select')
```

## test_strategies

[backend/app/models/postgres.py:2879](../../backend/app/models/postgres.py#L2879)

AI-generated or manually authored test strategy document for a project.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(500)` | False | False | `—` |  |
| `version_label` | `VARCHAR(50)` | False | False | `application=v1.0` |  |
| `status` | `VARCHAR(30)` | False | False | `application=draft` |  |
| `objective` | `TEXT` | True | False | `—` |  |
| `scope` | `TEXT` | True | False | `—` |  |
| `out_of_scope` | `TEXT` | True | False | `—` |  |
| `test_approach` | `TEXT` | True | False | `—` |  |
| `risk_assessment` | `JSON` | True | False | `—` |  |
| `test_types` | `JSON` | True | False | `—` |  |
| `entry_criteria` | `JSON` | True | False | `—` |  |
| `exit_criteria` | `JSON` | True | False | `—` |  |
| `environments` | `JSON` | True | False | `—` |  |
| `automation_approach` | `TEXT` | True | False | `—` |  |
| `defect_management` | `TEXT` | True | False | `—` |  |
| `ai_generated` | `BOOLEAN` | False | False | `application=True` |  |
| `generation_context` | `TEXT` | True | False | `—` |  |
| `ai_model_used` | `VARCHAR(100)` | True | False | `—` |  |
| `created_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `approved_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `approved_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `approved_by_id`
- `ForeignKeyConstraint` `unnamed`: `created_by_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_ts_project` (unique=False): `test_strategies.project_id`; options `{}`

## test_suite_owners

[backend/app/models/postgres.py:891](../../backend/app/models/postgres.py#L891)

Explicit owner for a (project, suite_name) pair (migration 0076).

Keyed by suite_name (string) to match the legacy aggregated Test Suites
view served from ``/api/v1/test-management/suites``. Falls back to
``Project.manager_user_id`` when no row exists for a given suite.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `suite_name` | `VARCHAR(500)` | False | False | `—` |  |
| `owner_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `owner_user_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_test_suite_owners_proj_suite`: `project_id, suite_name`
- Index `ix_test_suite_owners_project_id` (unique=False): `test_suite_owners.project_id`; options `{}`

## test_suites

[backend/app/models/postgres.py:856](../../backend/app/models/postgres.py#L856)

Project-scoped grouping of test cases.

Replaces the prior string-based ``suite_name`` model with a first-class
entity. Every project gets a row with ``is_default=True`` named
``Default Suite ({project.name})`` — new test cases ingested without an
explicit suite are auto-assigned to it.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(500)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `is_default` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `tags` | `JSON` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_test_suites_project_name`: `project_id, name`
- Index `ix_test_suites_project_default` (unique=True): `test_suites.project_id`; options `{"postgresql_where": "is_default IS TRUE"}`
- Index `ix_test_suites_project_id` (unique=False): `test_suites.project_id`; options `{}`

ORM navigation and cascade declarations:

```python
project: Mapped['Project'] = relationship('Project', back_populates='test_suites')
canonical_test_cases: Mapped[list['CanonicalTestCase']] = relationship('CanonicalTestCase', back_populates='test_suite', cascade='all, delete-orphan')
```

## user_invitations

[backend/app/models/postgres.py:4138](../../backend/app/models/postgres.py#L4138)

Email invite token for onboarding new users.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `email` | `VARCHAR(255)` | False | False | `—` |  |
| `role` | `VARCHAR(20)` | False | False | `application=UserRole.QA_ENGINEER` |  |
| `token` | `VARCHAR(64)` | False | False | `—` |  |
| `invited_by_id` | `UUID` | True | False | `—` | users.id / SET NULL |
| `is_used` | `BOOLEAN` | False | False | `application=False` |  |
| `expires_at` | `DATETIME` | False | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `invited_by_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `token`
- Index `ix_user_invitations_email` (unique=False): `user_invitations.email`; options `{}`

## user_ui_dismissals

[backend/app/models/postgres.py:4662](../../backend/app/models/postgres.py#L4662)

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

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `user_id` | `UUID` | False | False | `—` | users.id / CASCADE |
| `dismissal_key` | `VARCHAR(100)` | False | False | `—` |  |
| `dismissed_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_user_ui_dismissal`: `user_id, dismissal_key`
- Index `ix_uuid_user` (unique=False): `user_ui_dismissals.user_id`; options `{}`

## users

[backend/app/models/postgres.py:159](../../backend/app/models/postgres.py#L159)



| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `email` | `VARCHAR(255)` | False | False | `—` |  |
| `username` | `VARCHAR(100)` | False | False | `—` |  |
| `full_name` | `VARCHAR(255)` | True | False | `—` |  |
| `hashed_password` | `VARCHAR(255)` | False | False | `—` |  |
| `role` | `VARCHAR(20)` | False | False | `application=VIEWER` |  |
| `is_active` | `BOOLEAN` | False | False | `application=True` |  |
| `is_service_account` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `is_synthetic` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `must_change_password` | `BOOLEAN` | False | False | `application=False` |  |
| `avatar_color` | `VARCHAR(20)` | True | False | `—` |  |
| `mfa_enabled` | `BOOLEAN` | False | False | `application=False` |  |
| `mfa_enrolled_at` | `DATETIME` | True | False | `—` |  |
| `mfa_last_used_step` | `BIGINT` | True | False | `—` |  |
| `last_login_at` | `DATETIME` | True | False | `—` |  |
| `failed_login_attempts` | `INTEGER` | False | False | `application=0` |  |
| `locked_until` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_users_email` (unique=True): `users.email`; options `{}`
- Index `ix_users_username` (unique=True): `users.username`; options `{}`

## value_metric_assumptions

[backend/app/models/postgres.py:6562](../../backend/app/models/postgres.py#L6562)

Per-project tunable assumptions for the engineer-hours-saved model
(PMF US-12.1, migration 0112).

One row per project; a MISSING row resolves to the defaults in
``value_metrics_service.EffectiveAssumptions`` (triage 20 min/failure,
blocked-run wait 30 min, defect filing 15 min) — no project-creation
hook needed (same pattern as ``QuarantineLifecyclePolicy``).

Every defaulted column carries a matching ``server_default`` so the
migration DDL and the ORM cannot drift (the #433 lesson).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `triage_minutes_per_failure` | `FLOAT` | False | False | `server=20.0; application=20.0` |  |
| `blocked_run_wait_minutes` | `FLOAT` | False | False | `server=30.0; application=30.0` |  |
| `defect_filing_minutes` | `FLOAT` | False | False | `server=15.0; application=15.0` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `unnamed`: `project_id`

## webhook_deliveries

[backend/app/models/postgres.py:6109](../../backend/app/models/postgres.py#L6109)

Audit trail for every webhook delivery attempt.

One row per (subscription, event emission). Retries update the same
row — ``attempt_count`` is incremented and the final outcome lands in
``status``. Rows older than 30 days are pruned by a celery beat task
to keep the table bounded.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `subscription_id` | `UUID` | False | False | `—` | webhook_subscriptions.id / CASCADE |
| `run_id` | `UUID` | True | False | `—` | test_runs.id / SET NULL |
| `event_type` | `VARCHAR(64)` | False | False | `—` |  |
| `event_payload` | `JSONB` | True | False | `—` |  |
| `delivery_key` | `VARCHAR(64)` | True | False | `—` |  |
| `status` | `VARCHAR(20)` | False | False | `application=PENDING` |  |
| `attempt_count` | `INTEGER` | False | False | `application=0` |  |
| `dispatch_attempts` | `INTEGER` | False | False | `server=0; application=0` |  |
| `dispatch_failures` | `INTEGER` | False | False | `server=0; application=0` |  |
| `next_dispatch_at` | `DATETIME` | True | False | `—` |  |
| `dispatch_lease_expires_at` | `DATETIME` | True | False | `—` |  |
| `dispatch_token` | `UUID` | True | False | `—` |  |
| `http_status` | `INTEGER` | True | False | `—` |  |
| `response_preview` | `VARCHAR(2000)` | True | False | `—` |  |
| `error` | `TEXT` | True | False | `—` |  |
| `delivered_at` | `DATETIME` | True | False | `—` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `run_id`
- `ForeignKeyConstraint` `unnamed`: `subscription_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_webhook_delivery_dispatch_due` (unique=False): `webhook_deliveries.status, webhook_deliveries.next_dispatch_at`; options `{}`
- Index `ix_webhook_delivery_run_id` (unique=False): `webhook_deliveries.run_id`; options `{}`
- Index `ix_webhook_delivery_status` (unique=False): `webhook_deliveries.status, webhook_deliveries.created_at`; options `{}`
- Index `ix_webhook_delivery_sub_created` (unique=False): `webhook_deliveries.subscription_id, webhook_deliveries.created_at`; options `{}`
- Index `uq_webhook_delivery_delivery_key` (unique=True): `webhook_deliveries.delivery_key`; options `{}`

## webhook_subscriptions

[backend/app/models/postgres.py:6063](../../backend/app/models/postgres.py#L6063)

Customer-managed outbound webhook subscription.

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `name` | `VARCHAR(255)` | False | False | `—` |  |
| `target_url` | `VARCHAR(1000)` | False | False | `—` |  |
| `events` | `JSONB` | False | False | `application=list` |  |
| `enabled` | `BOOLEAN` | False | False | `application=True` |  |
| `has_secret` | `BOOLEAN` | False | False | `application=False` |  |
| `max_retries` | `INTEGER` | False | False | `application=5` |  |
| `last_delivered_at` | `DATETIME` | True | False | `—` |  |
| `last_failure_at` | `DATETIME` | True | False | `—` |  |
| `last_error` | `TEXT` | True | False | `—` |  |
| `failure_count` | `INTEGER` | False | False | `application=0` |  |
| `total_delivered` | `INTEGER` | False | False | `application=0` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |
| `updated_by_user_id` | `UUID` | True | False | `—` | users.id / SET NULL |

Constraints and indexes:

- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by_user_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- Index `ix_webhook_sub_enabled` (unique=False): `webhook_subscriptions.enabled, webhook_subscriptions.project_id`; options `{}`
- Index `ix_webhook_sub_project` (unique=False): `webhook_subscriptions.project_id`; options `{}`

## workflow_definitions

[backend/app/models/postgres.py:5418](../../backend/app/models/postgres.py#L5418)

One immutable-on-publish version of a project workflow (E3.1).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `workflow_id` | `VARCHAR(80)` | False | False | `—` |  |
| `version` | `INTEGER` | False | False | `—` |  |
| `name` | `VARCHAR(120)` | False | False | `—` |  |
| `description` | `TEXT` | True | False | `—` |  |
| `base` | `VARCHAR(16)` | False | False | `—` |  |
| `definition` | `JSONB` | False | False | `—` |  |
| `status` | `VARCHAR(16)` | False | False | `server=draft; application=draft` |  |
| `published_at` | `DATETIME` | True | False | `—` |  |
| `eval_verdict` | `VARCHAR(24)` | True | False | `—` |  |
| `eval_coverage` | `FLOAT` | True | False | `—` |  |
| `eval_gate_run_id` | `UUID` | True | False | `—` | ai_eval_gate_runs.id / SET NULL |
| `evaluated_at` | `DATETIME` | True | False | `—` |  |
| `eval_regression_accepted` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `eval_regression_reason` | `TEXT` | True | False | `—` |  |
| `eval_regression_accepted_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `eval_regression_accepted_at` | `DATETIME` | True | False | `—` |  |
| `created_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `updated_by` | `UUID` | True | False | `—` | users.id / SET NULL |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |
| `updated_at` | `DATETIME` | False | False | `server=now(); onupdate=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_workflow_definitions_base`: `base IN ('offline', 'deep', 'live')`
- `CheckConstraint` `ck_workflow_definitions_eval_coverage`: `eval_coverage IS NULL OR (eval_coverage >= 0 AND eval_coverage <= 1)`
- `CheckConstraint` `ck_workflow_definitions_eval_evidence`: `(eval_verdict IS NULL AND eval_coverage IS NULL AND eval_gate_run_id IS NULL AND evaluated_at IS NULL) OR (eval_verdict IS NOT NULL AND eval_coverage IS NOT NULL AND eval_gate_run_id IS NOT NULL AND evaluated_at IS NOT NULL)`
- `CheckConstraint` `ck_workflow_definitions_eval_verdict`: `eval_verdict IS NULL OR eval_verdict IN ('pass', 'fail', 'insufficient_samples')`
- `CheckConstraint` `ck_workflow_definitions_published_at`: `(status = 'published' AND published_at IS NOT NULL) OR (status = 'draft' AND published_at IS NULL)`
- `CheckConstraint` `ck_workflow_definitions_regression_acceptance`: `(eval_regression_accepted IS FALSE AND eval_regression_reason IS NULL AND eval_regression_accepted_by IS NULL AND eval_regression_accepted_at IS NULL) OR (eval_regression_accepted IS TRUE AND eval_verdict = 'fail' AND length(trim(eval_regression_reason)) > 0 AND eval_regression_accepted_by IS NOT NULL AND eval_regression_accepted_at IS NOT NULL)`
- `CheckConstraint` `ck_workflow_definitions_status`: `status IN ('draft', 'published')`
- `CheckConstraint` `ck_workflow_definitions_version_positive`: `version >= 1`
- `ForeignKeyConstraint` `unnamed`: `created_by`
- `ForeignKeyConstraint` `unnamed`: `eval_gate_run_id`
- `ForeignKeyConstraint` `unnamed`: `eval_regression_accepted_by`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `updated_by`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_workflow_definitions_project_workflow_version`: `project_id, workflow_id, version`
- Index `ix_workflow_definitions_project_status` (unique=False): `workflow_definitions.project_id, workflow_definitions.status`; options `{}`

## workflow_replay_corpus

[backend/app/models/postgres.py:5512](../../backend/app/models/postgres.py#L5512)

A project run output cached for deterministic workflow evaluation (E9.5).

| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |
|---|---|---|---|---|---|
| `id` | `UUID` | False | True | `application=uuid4` |  |
| `project_id` | `UUID` | False | False | `—` | projects.id / CASCADE |
| `pipeline_run_id` | `UUID` | False | False | `—` | agent_pipeline_runs.id / CASCADE |
| `test_run_id` | `UUID` | False | False | `—` | test_runs.id / CASCADE |
| `step_id` | `VARCHAR(80)` | False | False | `—` |  |
| `agent_id` | `VARCHAR(120)` | False | False | `—` |  |
| `prompt_version` | `VARCHAR(80)` | False | False | `—` |  |
| `input_hash` | `VARCHAR(64)` | False | False | `—` |  |
| `output` | `JSONB` | False | False | `—` |  |
| `stage_status` | `VARCHAR(20)` | False | False | `—` |  |
| `degraded` | `BOOLEAN` | False | False | `server=false; application=False` |  |
| `cost_usd` | `FLOAT` | False | False | `server=0; application=0.0` |  |
| `latency_ms` | `INTEGER` | False | False | `server=0; application=0` |  |
| `created_at` | `DATETIME` | False | False | `server=now()` |  |

Constraints and indexes:

- `CheckConstraint` `ck_workflow_replay_cost_nonnegative`: `cost_usd >= 0`
- `CheckConstraint` `ck_workflow_replay_input_hash`: `input_hash ~ '^[0-9a-f]{64}$'`
- `CheckConstraint` `ck_workflow_replay_latency_nonnegative`: `latency_ms >= 0`
- `ForeignKeyConstraint` `unnamed`: `pipeline_run_id`
- `ForeignKeyConstraint` `unnamed`: `project_id`
- `ForeignKeyConstraint` `unnamed`: `test_run_id`
- `PrimaryKeyConstraint` `unnamed`: `id`
- `UniqueConstraint` `uq_workflow_replay_agent_prompt_input`: `agent_id, prompt_version, input_hash`
- Index `ix_workflow_replay_project_run` (unique=False): `workflow_replay_corpus.project_id, workflow_replay_corpus.pipeline_run_id`; options `{}`
