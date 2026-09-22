# REST endpoint index

[Documentation home](../README.md) · [Regeneration](../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../handoff/verification.md).

This indexes every OpenAPI HTTP operation. Exact parameters, body schemas and declared responses are in the linked domain pages and [OpenAPI JSON](openapi.json). Source descriptions are declarations; see [known summary-report behavior gaps](../pipelines/reporting.md#aggregation-and-evidence). Authentication dependencies and handler-raised errors supplement OpenAPI: absence of `security` does **not** imply anonymous access. See [authentication and errors](../api/README.md).

| Method | Path | Summary | Reference |
|---|---|---|---|
| GET | `/` | Root | [System](api/system.md) |
| GET | `/api/v1/activity/event-types` | The activity event registry, grouped by category | [Activity](api/activity.md) |
| GET | `/api/v1/admin/dlq` | List Dead Letters | [Admin DLQ](api/admin-dlq.md) |
| POST | `/api/v1/admin/dlq/{entry_id}/replay` | Replay Dead Letter | [Admin DLQ](api/admin-dlq.md) |
| GET | `/api/v1/admin/maintenance/ai-cache` | Read Ai Cache Stats | [Admin Maintenance](api/admin-maintenance.md) |
| POST | `/api/v1/admin/maintenance/backfill-placeholder-test-cases` | Trigger Placeholder Backfill | [Admin Maintenance](api/admin-maintenance.md) |
| POST | `/api/v1/admin/maintenance/backfill-unassigned-failures` | Trigger Unassigned Failures Backfill | [Admin Maintenance](api/admin-maintenance.md) |
| GET | `/api/v1/admin/maintenance/dlq` | Read Dead Letters | [Admin Maintenance](api/admin-maintenance.md) |
| POST | `/api/v1/admin/maintenance/drain-active-live-sessions` | Trigger Drain Active Sessions | [Admin Maintenance](api/admin-maintenance.md) |
| POST | `/api/v1/admin/maintenance/outbox/requeue` | Requeue Failed Outbox Operations | [Admin Maintenance](api/admin-maintenance.md) |
| GET | `/api/v1/admin/storage/deleted-projects` | Get Deleted Project Storage | [Admin Storage](api/admin-storage.md) |
| GET | `/api/v1/agents/active-runs` | Get Active Live Runs | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/active-runs/{run_id}` | Get Live Run State | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/catalog` | List Agent Catalog | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/catalog/{agent_id}` | Get Agent Catalog Entry | [Agents](api/agent-operations.md) |
| POST | `/api/v1/agents/defect-command` | Defect Command | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/event-log/health` | Get Pipeline Event Log Health | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/invocations/{invocation_id}` | Get Invocation | [Agent Invocations](api/agent-invocations.md) |
| POST | `/api/v1/agents/invocations/{invocation_id}/cancel` | Cancel Invocation | [Agent Invocations](api/agent-invocations.md) |
| GET | `/api/v1/agents/invocations/{invocation_id}/events` | Stream Invocation Events | [Agent Invocations](api/agent-invocations.md) |
| POST | `/api/v1/agents/invocations/{invocation_id}/events/ticket` | Issue Invocation Stream Ticket | [Agent Invocations](api/agent-invocations.md) |
| POST | `/api/v1/agents/invocations/{invocation_id}/retry` | Retry Invocation | [Agent Invocations](api/agent-invocations.md) |
| GET | `/api/v1/agents/pipelines` | List Pipelines | [Agents](api/agent-operations.md) |
| POST | `/api/v1/agents/pipelines/bulk-trigger` | Bulk Trigger Pipelines | [Agents](api/agent-operations.md) |
| POST | `/api/v1/agents/pipelines/trigger` | Trigger Pipeline | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/pipelines/{pipeline_id}` | Get Pipeline | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/pipelines/{pipeline_id}/agentic-runtime` | Get Agentic Runtime | [Agents](api/agent-operations.md) |
| POST | `/api/v1/agents/pipelines/{pipeline_id}/cancel` | Cancel Pipeline | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/pipelines/{pipeline_id}/replay` | Get Pipeline Replay | [Agents](api/agent-operations.md) |
| POST | `/api/v1/agents/pipelines/{pipeline_id}/retry` | Retry Pipeline | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/pipelines/{pipeline_id}/stages` | Get Pipeline Stages | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/pipelines/{pipeline_id}/timeline` | Get Pipeline Timeline | [Agents](api/agent-operations.md) |
| POST | `/api/v1/agents/regression-watch` | Regression Watch | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/runs/{run_id}/pipeline-status` | Get Pipeline Status | [Agents](api/agent-operations.md) |
| GET | `/api/v1/agents/runs/{run_id}/summary` | Get Run Summary | [Agents](api/agent-operations.md) |
| POST | `/api/v1/agents/{agent_id}/invoke` | Invoke Agent | [Agent Invocations](api/agent-invocations.md) |
| POST | `/api/v1/ai-eval/agent-stack-release-gate` | Run Agent Stack Release Gate | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/agent-stack-release-gate/runs` | List Agent Stack Gate Runs | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/baselines` | List Baselines | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/baselines` | Set Baseline | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/dashboard` | Get Quality Dashboard | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/datasets` | List Datasets | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/datasets` | Create Dataset | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/datasets/from-feedback` | Create Dataset From Feedback | [AI Evaluation](api/ai-evaluation.md) |
| DELETE | `/api/v1/ai-eval/datasets/{dataset_id}` | Delete Dataset | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/drift` | Get Drift | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/gates/{checksum}` | Get Eval Manifest | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/golden-datasets/seed` | Seed Golden Datasets | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/label-health` | Get Training Label Health | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/pre-release-gate` | Run Pre Release Gate | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/report-cycles` | List Report Eval Cycles | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/report-cycles` | Create Report Eval Cycle | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/report-cycles/readiness` | Get Report Eval Readiness | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/reviewer-quality` | Run Reviewer Quality | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/ai-eval/runs` | List Eval Runs | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/runs/evaluate/{dataset_id}` | Run Evaluation | [AI Evaluation](api/ai-evaluation.md) |
| POST | `/api/v1/ai-eval/tier-comparison` | Run Tier Comparison | [AI Evaluation](api/ai-evaluation.md) |
| GET | `/api/v1/analytics/ai-summary` | Ai Analysis Summary | [Analytics](api/analytics.md) |
| POST | `/api/v1/analytics/classify-uncategorized` | Classify Uncategorized Failures | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/coverage` | Coverage Stats | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/defects` | List Defects | [Analytics](api/analytics.md) |
| POST | `/api/v1/analytics/defects` | Create Defect | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/failure-categories` | Failure Categories | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/flake-load` | Flake Load | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/flaky-scores` | Flaky Scores | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/flaky-tests` | Flaky Tests | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/kind-evidence` | Kind Evidence | [Analytics](api/analytics.md) |
| POST | `/api/v1/analytics/notify-owner` | Notify Suite Owner | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/suite-detail` | Suite Detail | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/systemic-clusters` | Systemic Clusters | [Analytics](api/analytics.md) |
| GET | `/api/v1/analytics/top-failing` | Top Failing Tests | [Analytics](api/analytics.md) |
| POST | `/api/v1/analyze` | Analyze Test Case | [AI Analysis](api/ai-analysis.md) |
| GET | `/api/v1/analyze/{test_case_id}` | Fetch existing AI analysis for a test case (no re-run) | [AI Analysis](api/ai-analysis.md) |
| GET | `/api/v1/audit-dashboard/categories` | List Categories | [Audit Dashboard](api/audit-dashboard.md) |
| GET | `/api/v1/audit-dashboard/events` | List Audit Events | [Audit Dashboard](api/audit-dashboard.md) |
| GET | `/api/v1/audit-dashboard/export` | Export Audit Events | [Audit Dashboard](api/audit-dashboard.md) |
| GET | `/api/v1/audit-dashboard/observability/{project_id}` | Get Project Observability | [Audit Dashboard](api/audit-dashboard.md) |
| POST | `/api/v1/auth/change-password` | Change Password | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/dev-login` | Dev Login | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/first-time-reset` | First Time Reset | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/login` | Login | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/logout` | Logout | [Authentication](api/authentication.md) |
| GET | `/api/v1/auth/me` | Get Me | [Authentication](api/authentication.md) |
| PATCH | `/api/v1/auth/me` | Update Me | [Authentication](api/authentication.md) |
| GET | `/api/v1/auth/me/dismissals` | List My Dismissals | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/me/dismissals` | Dismiss Prompt | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/mfa/disable` | Disable Mfa | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/mfa/enroll/confirm` | Confirm Enrollment | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/mfa/enroll/start` | Start Enrollment | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/mfa/recovery-codes` | Reissue Recovery Codes | [Authentication](api/authentication.md) |
| GET | `/api/v1/auth/mfa/status` | Mfa Status | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/mfa/verify` | Verify Mfa | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/refresh` | Refresh Tokens | [Authentication](api/authentication.md) |
| POST | `/api/v1/auth/register` | Register | [Authentication](api/authentication.md) |
| GET | `/api/v1/auth/users` | List Users | [Authentication](api/authentication.md) |
| GET | `/api/v1/billing/overview` | Billing Overview | [LLM Cost Budget](api/llm-cost-budget.md) |
| GET | `/api/v1/canonical-test-cases` | List Canonical Cases | [Test Suites](api/test-suites.md) |
| POST | `/api/v1/canonical-test-cases/bulk-link` | Bulk Link Canonicals To Suite | [Test Suites](api/test-suites.md) |
| GET | `/api/v1/canonical-test-cases/orphaned` | List Orphaned Canonical Cases | [Test Suites](api/test-suites.md) |
| GET | `/api/v1/canonical-test-cases/{canonical_id}` | Get Canonical Case | [Test Suites](api/test-suites.md) |
| POST | `/api/v1/canonical-test-cases/{canonical_id}/confirm-retirement` | Confirm Canonical Retirement | [Test Suites](api/test-suites.md) |
| POST | `/api/v1/canonical-test-cases/{canonical_id}/link` | Link Canonical To Suite | [Test Suites](api/test-suites.md) |
| DELETE | `/api/v1/canonical-test-cases/{canonical_id}/managed-link` | Unlink Canonical Managed Case | [Test Suites](api/test-suites.md) |
| POST | `/api/v1/canonical-test-cases/{canonical_id}/promote` | Promote Canonical Case | [Test Suites](api/test-suites.md) |
| GET | `/api/v1/canonical-test-cases/{canonical_id}/runs` | List Canonical Run History | [Test Suites](api/test-suites.md) |
| GET | `/api/v1/chat/run-summaries` | Get Run Summaries | [Chat](api/chat.md) |
| GET | `/api/v1/chat/sessions` | List Sessions | [Chat](api/chat.md) |
| POST | `/api/v1/chat/sessions` | Create Session | [Chat](api/chat.md) |
| DELETE | `/api/v1/chat/sessions/{session_id}` | Delete Session | [Chat](api/chat.md) |
| GET | `/api/v1/chat/sessions/{session_id}/messages` | Get Messages | [Chat](api/chat.md) |
| POST | `/api/v1/chat/sessions/{session_id}/messages` | Send Message | [Chat](api/chat.md) |
| DELETE | `/api/v1/compliance-packs/{pack_id}` | Delete Compliance Pack | [Compliance Pack](api/compliance-pack.md) |
| GET | `/api/v1/compliance-packs/{pack_id}/download` | Download Compliance Pack | [Compliance Pack](api/compliance-pack.md) |
| POST | `/api/v1/compliance-packs/{pack_id}/retire` | Retire Compliance Pack | [Compliance Pack](api/compliance-pack.md) |
| POST | `/api/v1/debug/generate-test-run` | Generate Mock Test Run | [Debug](api/debug.md) |
| GET | `/api/v1/deep-investigate/defects/pending-review` | List Pending Defects | [Deep Investigation](api/deep-investigation.md) |
| POST | `/api/v1/deep-investigate/defects/{defect_id}/review` | Review Defect | [Deep Investigation](api/deep-investigation.md) |
| POST | `/api/v1/deep-investigate/{run_id}` | Trigger Deep Investigation | [Deep Investigation](api/deep-investigation.md) |
| GET | `/api/v1/deep-investigate/{run_id}/clusters` | Get Failure Clusters | [Deep Investigation](api/deep-investigation.md) |
| GET | `/api/v1/deep-investigate/{run_id}/clusters/ranked` | Get Ranked Clusters | [Deep Investigation](api/deep-investigation.md) |
| GET | `/api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/defect-candidate` | Get Cluster Defect Candidate | [Deep Investigation](api/deep-investigation.md) |
| GET | `/api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/duplicate-check` | Check Cluster Duplicate | [Deep Investigation](api/deep-investigation.md) |
| POST | `/api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/promote` | Promote Cluster To Defect | [Deep Investigation](api/deep-investigation.md) |
| GET | `/api/v1/deep-investigate/{run_id}/findings` | Get Deep Findings | [Deep Investigation](api/deep-investigation.md) |
| DELETE | `/api/v1/dev/seed` | Delete Seed Data | [Dev Seed Data](api/dev-seed-data.md) |
| POST | `/api/v1/dev/seed` | Load Seed Data | [Dev Seed Data](api/dev-seed-data.md) |
| POST | `/api/v1/dev/seed/reset` | Reset Seed Data | [Dev Seed Data](api/dev-seed-data.md) |
| GET | `/api/v1/dev/seed/status` | Seed Status | [Dev Seed Data](api/dev-seed-data.md) |
| GET | `/api/v1/digests/preview` | Preview Digest | [Digests](api/digests.md) |
| GET | `/api/v1/digests/subscriptions` | List Subscriptions | [Digests](api/digests.md) |
| POST | `/api/v1/digests/subscriptions` | Create Subscription | [Digests](api/digests.md) |
| DELETE | `/api/v1/digests/subscriptions/{sub_id}` | Delete Subscription | [Digests](api/digests.md) |
| GET | `/api/v1/digests/subscriptions/{sub_id}` | Get Subscription | [Digests](api/digests.md) |
| PATCH | `/api/v1/digests/subscriptions/{sub_id}` | Update Subscription | [Digests](api/digests.md) |
| POST | `/api/v1/digests/subscriptions/{sub_id}/pause` | Pause Subscription | [Digests](api/digests.md) |
| POST | `/api/v1/digests/subscriptions/{sub_id}/resume` | Resume Subscription | [Digests](api/digests.md) |
| GET | `/api/v1/feature-flags` | List Feature Flags | [Feature Flags](api/feature-flags.md) |
| POST | `/api/v1/feature-flags` | Create Feature Flag | [Feature Flags](api/feature-flags.md) |
| DELETE | `/api/v1/feature-flags/{key}` | Delete Feature Flag | [Feature Flags](api/feature-flags.md) |
| GET | `/api/v1/feature-flags/{key}` | Get Feature Flag | [Feature Flags](api/feature-flags.md) |
| PATCH | `/api/v1/feature-flags/{key}` | Update Feature Flag | [Feature Flags](api/feature-flags.md) |
| GET | `/api/v1/feature-flags/{key}/status` | Get Feature Flag Status | [Feature Flags](api/feature-flags.md) |
| POST | `/api/v1/feedback/jira-webhook` | Jira Resolution Webhook | [Feedback & Training](api/feedback-training.md) |
| GET | `/api/v1/feedback/stats` | Get Feedback Stats | [Feedback & Training](api/feedback-training.md) |
| POST | `/api/v1/feedback/{analysis_id}` | Submit Feedback | [Feedback & Training](api/feedback-training.md) |
| PUT | `/api/v1/feedback/{analysis_id}` | Update Feedback | [Feedback & Training](api/feedback-training.md) |
| GET | `/api/v1/fixer/attempts/{attempt_id}` | Get Fixer Attempt | [Fixer](api/fixer.md) |
| GET | `/api/v1/identity/events` | List Identity Events | [Identity & Audit](api/identity-audit.md) |
| GET | `/api/v1/identity/sync-status` | Get Identity Sync Status | [Identity & Audit](api/identity-audit.md) |
| POST | `/api/v1/ingest` | Ingest test results (JSON batch) | [Ingest](api/ingest.md) |
| POST | `/api/v1/ingest/file` | Upload test result file (JUnit/TestNG XML, Allure/Cypress/Playwright JSON) | [Ingest](api/ingest.md) |
| GET | `/api/v1/ingest/uploads/{task_id}` | Poll the status of an uploaded report's async processing | [Ingest](api/ingest.md) |
| GET | `/api/v1/integration-health/history/{provider}` | Get Provider History | [Integration Health](api/integration-health.md) |
| POST | `/api/v1/integration-health/probe` | Trigger Probe | [Integration Health](api/integration-health.md) |
| GET | `/api/v1/integration-health/status` | Get All Status | [Integration Health](api/integration-health.md) |
| GET | `/api/v1/integration-health/trends` | Get Health Trends | [Integration Health](api/integration-health.md) |
| POST | `/api/v1/integrations/jira` | Create Jira Defect | [Integrations](api/integrations.md) |
| GET | `/api/v1/investigations/{investigation_id}` | Get Investigation | [Agent Investigations](api/agent-investigations.md) |
| POST | `/api/v1/investigations/{investigation_id}/cancel` | Cancel Investigation | [Agent Investigations](api/agent-investigations.md) |
| GET | `/api/v1/keys` | List Api Keys | [API Keys](api/api-keys.md) |
| POST | `/api/v1/keys` | Create Api Key | [API Keys](api/api-keys.md) |
| DELETE | `/api/v1/keys/{key_id}` | Revoke Api Key | [API Keys](api/api-keys.md) |
| GET | `/api/v1/knowledge-sources` | List Knowledge Sources | [Knowledge Sources](api/knowledge-sources.md) |
| POST | `/api/v1/knowledge-sources` | Create Knowledge Source | [Knowledge Sources](api/knowledge-sources.md) |
| POST | `/api/v1/knowledge-sources/connectors/test` | Test Connector | [Knowledge Sources](api/knowledge-sources.md) |
| GET | `/api/v1/knowledge-sources/governance/domain-allowlist` | Get Domain Allowlist | [Knowledge Sources](api/knowledge-sources.md) |
| PUT | `/api/v1/knowledge-sources/governance/domain-allowlist` | Update Domain Allowlist | [Knowledge Sources](api/knowledge-sources.md) |
| DELETE | `/api/v1/knowledge-sources/{source_id}` | Delete Knowledge Source | [Knowledge Sources](api/knowledge-sources.md) |
| GET | `/api/v1/knowledge-sources/{source_id}` | Get Knowledge Source | [Knowledge Sources](api/knowledge-sources.md) |
| PATCH | `/api/v1/knowledge-sources/{source_id}` | Update Knowledge Source | [Knowledge Sources](api/knowledge-sources.md) |
| GET | `/api/v1/knowledge-sources/{source_id}/freshness` | Get Freshness | [Knowledge Sources](api/knowledge-sources.md) |
| POST | `/api/v1/knowledge-sources/{source_id}/sync` | Trigger Sync | [Knowledge Sources](api/knowledge-sources.md) |
| GET | `/api/v1/knowledge-sources/{source_id}/sync-history` | Get Sync History | [Knowledge Sources](api/knowledge-sources.md) |
| GET | `/api/v1/me/assigned-failures` | List My Assigned Failures | [My Failures](api/my-failures.md) |
| GET | `/api/v1/me/assigned-failures/count` | My Assigned Failures Count | [My Failures](api/my-failures.md) |
| PUT | `/api/v1/me/assigned-failures/{test_case_id}/reassign` | Reassign Assigned Failure | [My Failures](api/my-failures.md) |
| GET | `/api/v1/me/assigned-failures/{test_case_id}/reassign-options` | Get Reassign Options | [My Failures](api/my-failures.md) |
| PUT | `/api/v1/me/assigned-failures/{test_case_id}/triage` | Update Failure Triage Status | [My Failures](api/my-failures.md) |
| GET | `/api/v1/memory/projects/{project_id}` | List Project Memories | [Agent Memory](api/agent-memory.md) |
| POST | `/api/v1/memory/projects/{project_id}/recall` | Recall Similar Memories | [Agent Memory](api/agent-memory.md) |
| GET | `/api/v1/memory/runs/{run_id}/timeline` | Get Run Memory Timeline | [Agent Memory](api/agent-memory.md) |
| GET | `/api/v1/metrics/detection-timing` | Detection Timing | [Metrics](api/metrics.md) |
| GET | `/api/v1/metrics/flaky-readiness` | Flaky Readiness | [Metrics](api/metrics.md) |
| GET | `/api/v1/metrics/summary` | Dashboard Summary | [Metrics](api/metrics.md) |
| GET | `/api/v1/metrics/tia-readiness` | Tia Readiness | [Metrics](api/metrics.md) |
| GET | `/api/v1/metrics/trends` | Trend Data | [Metrics](api/metrics.md) |
| GET | `/api/v1/notifications/history` | List History | [Notifications](api/notifications.md) |
| POST | `/api/v1/notifications/history/read-all` | Mark All Read | [Notifications](api/notifications.md) |
| GET | `/api/v1/notifications/history/unread-count` | Unread Count | [Notifications](api/notifications.md) |
| POST | `/api/v1/notifications/history/{log_id}/read` | Mark Read | [Notifications](api/notifications.md) |
| GET | `/api/v1/notifications/preferences` | List Preferences | [Notifications](api/notifications.md) |
| POST | `/api/v1/notifications/preferences` | Upsert Preference | [Notifications](api/notifications.md) |
| DELETE | `/api/v1/notifications/preferences/{pref_id}` | Delete Preference | [Notifications](api/notifications.md) |
| PUT | `/api/v1/notifications/preferences/{pref_id}` | Update Preference | [Notifications](api/notifications.md) |
| GET | `/api/v1/notifications/projects/{project_id}/transition-policy` | Get Transition Policy | [Notifications](api/notifications.md) |
| PUT | `/api/v1/notifications/projects/{project_id}/transition-policy` | Update Transition Policy | [Notifications](api/notifications.md) |
| POST | `/api/v1/notifications/test` | Send Test | [Notifications](api/notifications.md) |
| POST | `/api/v1/observability/frontend` | Receive frontend telemetry batch | [Observability](api/observability.md) |
| GET | `/api/v1/onboarding/events` | List Usage Events | [Onboarding](api/onboarding.md) |
| POST | `/api/v1/onboarding/track` | Track Usage Event | [Onboarding](api/onboarding.md) |
| POST | `/api/v1/onboarding/{project_id}/complete` | Mark Step Complete | [Onboarding](api/onboarding.md) |
| POST | `/api/v1/onboarding/{project_id}/detect` | Detect Progress | [Onboarding](api/onboarding.md) |
| POST | `/api/v1/onboarding/{project_id}/restore` | Mark Step Restored | [Onboarding](api/onboarding.md) |
| POST | `/api/v1/onboarding/{project_id}/skip` | Mark Step Skipped | [Onboarding](api/onboarding.md) |
| GET | `/api/v1/onboarding/{project_id}/status` | Get Status | [Onboarding](api/onboarding.md) |
| GET | `/api/v1/performance/budgets` | Get Performance Budgets | [Performance](api/performance.md) |
| GET | `/api/v1/performance/search-config` | Get Search Config | [Performance](api/performance.md) |
| GET | `/api/v1/projects` | List Projects | [Projects](api/projects.md) |
| POST | `/api/v1/projects` | Create Project | [Projects](api/projects.md) |
| DELETE | `/api/v1/projects/{project_id}` | Delete Project | [Projects](api/projects.md) |
| GET | `/api/v1/projects/{project_id}` | Get Project | [Projects](api/projects.md) |
| PUT | `/api/v1/projects/{project_id}` | Update Project | [Projects](api/projects.md) |
| GET | `/api/v1/projects/{project_id}/activity` | Project activity feed (keyset paginated) | [Activity](api/activity.md) |
| GET | `/api/v1/projects/{project_id}/activity/entity/{entity_type}/{entity_id}` | Everything that happened to one entity | [Activity](api/activity.md) |
| GET | `/api/v1/projects/{project_id}/activity/export` | Export the filtered activity history (QA_LEAD+) | [Activity](api/activity.md) |
| GET | `/api/v1/projects/{project_id}/activity/{event_id}` | One activity event, with its before/after diff | [Activity](api/activity.md) |
| GET | `/api/v1/projects/{project_id}/agent-actions` | List Agent Actions | [Agent Actions](api/agent-actions.md) |
| PATCH | `/api/v1/projects/{project_id}/agent-actions/{action_id}` | Transition Agent Action | [Agent Actions](api/agent-actions.md) |
| GET | `/api/v1/projects/{project_id}/agent-configs` | List Agent Configs | [Agent Configs](api/agent-configs.md) |
| GET | `/api/v1/projects/{project_id}/agent-configs/{agent_id}` | Get Agent Config | [Agent Configs](api/agent-configs.md) |
| PUT | `/api/v1/projects/{project_id}/agent-configs/{agent_id}` | Put Agent Config | [Agent Configs](api/agent-configs.md) |
| GET | `/api/v1/projects/{project_id}/agent-policies` | List Agent Policies | [Agent Investigations](api/agent-investigations.md) |
| PUT | `/api/v1/projects/{project_id}/agent-policies/{agent_id}` | Update Agent Policy | [Agent Investigations](api/agent-investigations.md) |
| GET | `/api/v1/projects/{project_id}/agent-runs` | List Agent Runs | [Agent Investigations](api/agent-investigations.md) |
| GET | `/api/v1/projects/{project_id}/analyses/lookup` | Lookup Latest Analysis | [Feedback & Training](api/feedback-training.md) |
| GET | `/api/v1/projects/{project_id}/attribution-rules` | List Attribution Rules | [Release Attribution Rules](api/release-attribution-rules.md) |
| POST | `/api/v1/projects/{project_id}/attribution-rules` | Create Attribution Rule | [Release Attribution Rules](api/release-attribution-rules.md) |
| POST | `/api/v1/projects/{project_id}/attribution-rules/preview` | Preview Attribution Rule | [Release Attribution Rules](api/release-attribution-rules.md) |
| DELETE | `/api/v1/projects/{project_id}/attribution-rules/{rule_id}` | Delete Attribution Rule | [Release Attribution Rules](api/release-attribution-rules.md) |
| PUT | `/api/v1/projects/{project_id}/attribution-rules/{rule_id}` | Update Attribution Rule | [Release Attribution Rules](api/release-attribution-rules.md) |
| POST | `/api/v1/projects/{project_id}/default-qa-lead/reset-password` | Reset Default Qa Lead Password Endpoint | [Projects](api/projects.md) |
| POST | `/api/v1/projects/{project_id}/defects/jira` | Create Jira Defect One Click | [Defects](api/defects.md) |
| GET | `/api/v1/projects/{project_id}/defects/jira/metadata` | Jira Defect Metadata | [Defects](api/defects.md) |
| GET | `/api/v1/projects/{project_id}/defects/jira/preview` | Jira Defect Preview | [Defects](api/defects.md) |
| POST | `/api/v1/projects/{project_id}/deletion/execute` | Execute Criteria Deletion | [Retention](api/retention.md) |
| GET | `/api/v1/projects/{project_id}/deletion/jobs` | List Deletion Jobs | [Retention](api/retention.md) |
| GET | `/api/v1/projects/{project_id}/deletion/jobs/{job_id}` | Get Deletion Job | [Retention](api/retention.md) |
| POST | `/api/v1/projects/{project_id}/deletion/preview` | Preview Criteria Deletion | [Retention](api/retention.md) |
| GET | `/api/v1/projects/{project_id}/duplicate-candidates` | List Duplicate Candidates | [Duplicate Detection](api/duplicate-detection.md) |
| POST | `/api/v1/projects/{project_id}/duplicate-candidates/detect` | Run Duplicate Detection | [Duplicate Detection](api/duplicate-detection.md) |
| POST | `/api/v1/projects/{project_id}/duplicate-candidates/{candidate_id}/dismiss` | Dismiss Duplicate Candidate | [Duplicate Detection](api/duplicate-detection.md) |
| POST | `/api/v1/projects/{project_id}/duplicate-candidates/{candidate_id}/merge` | Merge Duplicate Candidate | [Duplicate Detection](api/duplicate-detection.md) |
| POST | `/api/v1/projects/{project_id}/fix-outcomes` | Record Fix Outcome | [Feedback & Training](api/feedback-training.md) |
| GET | `/api/v1/projects/{project_id}/fixer/attempts` | List Fixer Attempts | [Fixer](api/fixer.md) |
| GET | `/api/v1/projects/{project_id}/fixer/config` | Get Fixer Config | [Fixer](api/fixer.md) |
| PUT | `/api/v1/projects/{project_id}/fixer/config` | Put Fixer Config | [Fixer](api/fixer.md) |
| POST | `/api/v1/projects/{project_id}/fixer/run` | Start Fixer Run | [Fixer](api/fixer.md) |
| GET | `/api/v1/projects/{project_id}/flaky-coach` | Get Project Flaky Coach | [Test Health](api/test-health.md) |
| POST | `/api/v1/projects/{project_id}/flaky-coach/refresh` | Refresh Project Flaky Coach | [Test Health](api/test-health.md) |
| DELETE | `/api/v1/projects/{project_id}/github-integration` | Delete Github Integration | [GitHub Integration](api/github-integration.md) |
| GET | `/api/v1/projects/{project_id}/github-integration` | Get Github Integration | [GitHub Integration](api/github-integration.md) |
| PUT | `/api/v1/projects/{project_id}/github-integration` | Upsert Github Integration | [GitHub Integration](api/github-integration.md) |
| POST | `/api/v1/projects/{project_id}/github-integration/test` | Test Github Integration | [GitHub Integration](api/github-integration.md) |
| GET | `/api/v1/projects/{project_id}/integrations/gitlab` | Get Gitlab Integration | [GitLab Integration](api/gitlab-integration.md) |
| PUT | `/api/v1/projects/{project_id}/integrations/gitlab` | Upsert Gitlab Integration | [GitLab Integration](api/gitlab-integration.md) |
| POST | `/api/v1/projects/{project_id}/integrations/gitlab/test` | Test Gitlab Integration | [GitLab Integration](api/gitlab-integration.md) |
| GET | `/api/v1/projects/{project_id}/investigations` | List Investigations | [Agent Investigations](api/agent-investigations.md) |
| GET | `/api/v1/projects/{project_id}/llm-quota` | Get Project Quota | [LLM Cost Budget](api/llm-cost-budget.md) |
| PUT | `/api/v1/projects/{project_id}/llm-quota` | Upsert Project Quota | [LLM Cost Budget](api/llm-cost-budget.md) |
| GET | `/api/v1/projects/{project_id}/llm-usage` | Get Current Usage | [LLM Cost Budget](api/llm-cost-budget.md) |
| GET | `/api/v1/projects/{project_id}/llm-usage/history` | Get Usage History | [LLM Cost Budget](api/llm-cost-budget.md) |
| GET | `/api/v1/projects/{project_id}/members` | List Project Members | [User Management](api/user-management.md) |
| POST | `/api/v1/projects/{project_id}/members` | Add Project Member | [User Management](api/user-management.md) |
| DELETE | `/api/v1/projects/{project_id}/members/{user_id}` | Remove Project Member | [User Management](api/user-management.md) |
| PATCH | `/api/v1/projects/{project_id}/members/{user_id}` | Update Project Member Role | [User Management](api/user-management.md) |
| GET | `/api/v1/projects/{project_id}/ownership/codeowners/coverage` | Codeowners Coverage | [Service Ownership](api/service-ownership.md) |
| POST | `/api/v1/projects/{project_id}/ownership/codeowners/import` | Import Codeowners | [Service Ownership](api/service-ownership.md) |
| GET | `/api/v1/projects/{project_id}/ownership/resolve/{cluster_id}` | Resolve Cluster Ownership Endpoint | [Service Ownership](api/service-ownership.md) |
| GET | `/api/v1/projects/{project_id}/ownership/rules` | List Ownership Rules | [Service Ownership](api/service-ownership.md) |
| POST | `/api/v1/projects/{project_id}/ownership/rules` | Create Ownership Rule | [Service Ownership](api/service-ownership.md) |
| POST | `/api/v1/projects/{project_id}/ownership/rules/bulk-import` | Bulk Import Rules | [Service Ownership](api/service-ownership.md) |
| GET | `/api/v1/projects/{project_id}/ownership/rules/export` | Export Ownership Rules | [Service Ownership](api/service-ownership.md) |
| DELETE | `/api/v1/projects/{project_id}/ownership/rules/{rule_id}` | Delete Ownership Rule | [Service Ownership](api/service-ownership.md) |
| PATCH | `/api/v1/projects/{project_id}/ownership/rules/{rule_id}` | Update Ownership Rule | [Service Ownership](api/service-ownership.md) |
| GET | `/api/v1/projects/{project_id}/ownership/team-channels` | List Team Channels | [Service Ownership](api/service-ownership.md) |
| DELETE | `/api/v1/projects/{project_id}/ownership/team-channels/{team_name}` | Delete Team Channel | [Service Ownership](api/service-ownership.md) |
| PUT | `/api/v1/projects/{project_id}/ownership/team-channels/{team_name}` | Upsert Team Channel | [Service Ownership](api/service-ownership.md) |
| GET | `/api/v1/projects/{project_id}/quarantine/manifest` | Get Quarantine Manifest | [Flaky Quarantine](api/flaky-quarantine.md) |
| GET | `/api/v1/projects/{project_id}/quarantine/policy` | Get Quarantine Lifecycle Policy | [Flaky Quarantine](api/flaky-quarantine.md) |
| PUT | `/api/v1/projects/{project_id}/quarantine/policy` | Update Quarantine Lifecycle Policy | [Flaky Quarantine](api/flaky-quarantine.md) |
| GET | `/api/v1/projects/{project_id}/reports/analysis` | Download Analysis Report | [Analysis Report](api/analysis-report.md) |
| POST | `/api/v1/projects/{project_id}/reset` | Reset Project Data | [Projects](api/projects.md) |
| GET | `/api/v1/projects/{project_id}/retention-policy` | Get Retention Policy | [Retention](api/retention.md) |
| PUT | `/api/v1/projects/{project_id}/retention-policy` | Put Retention Policy | [Retention](api/retention.md) |
| POST | `/api/v1/projects/{project_id}/retention-policy/preview` | Preview Retention Purge | [Retention](api/retention.md) |
| POST | `/api/v1/projects/{project_id}/retention-policy/purge` | Enqueue Retention Purge | [Retention](api/retention.md) |
| GET | `/api/v1/projects/{project_id}/reviews` | List Reviews | [Reviews](api/reviews.md) |
| GET | `/api/v1/projects/{project_id}/storage` | Get Project Storage | [Retention](api/retention.md) |
| GET | `/api/v1/projects/{project_id}/value-metrics/assumptions` | Get Value Metric Assumptions | [Value Metrics](api/value-metrics.md) |
| PUT | `/api/v1/projects/{project_id}/value-metrics/assumptions` | Put Value Metric Assumptions | [Value Metrics](api/value-metrics.md) |
| GET | `/api/v1/projects/{project_id}/workflows` | List Workflows | [Workflows](api/workflows.md) |
| POST | `/api/v1/projects/{project_id}/workflows` | Create Workflow | [Workflows](api/workflows.md) |
| DELETE | `/api/v1/projects/{project_id}/workflows/{workflow_id}` | Delete Workflow | [Workflows](api/workflows.md) |
| GET | `/api/v1/projects/{project_id}/workflows/{workflow_id}` | Get Workflow | [Workflows](api/workflows.md) |
| PUT | `/api/v1/projects/{project_id}/workflows/{workflow_id}` | Update Workflow | [Workflows](api/workflows.md) |
| POST | `/api/v1/projects/{project_id}/workflows/{workflow_id}/evaluate` | Evaluate Workflow | [Workflows](api/workflows.md) |
| POST | `/api/v1/projects/{project_id}/workflows/{workflow_id}/fork` | Fork Workflow | [Workflows](api/workflows.md) |
| POST | `/api/v1/projects/{project_id}/workflows/{workflow_id}/publish` | Publish Workflow | [Workflows](api/workflows.md) |
| POST | `/api/v1/projects/{project_id}/workflows/{workflow_id}/validate` | Validate Workflow | [Workflows](api/workflows.md) |
| GET | `/api/v1/quarantine` | List Quarantine Requests | [Flaky Quarantine](api/flaky-quarantine.md) |
| POST | `/api/v1/quarantine` | Create Proposal | [Flaky Quarantine](api/flaky-quarantine.md) |
| GET | `/api/v1/quarantine/stats` | Get Quarantine Stats | [Flaky Quarantine](api/flaky-quarantine.md) |
| GET | `/api/v1/quarantine/{request_id}` | Get Quarantine Request | [Flaky Quarantine](api/flaky-quarantine.md) |
| POST | `/api/v1/quarantine/{request_id}/approve` | Approve Quarantine | [Flaky Quarantine](api/flaky-quarantine.md) |
| POST | `/api/v1/quarantine/{request_id}/reject` | Reject Quarantine | [Flaky Quarantine](api/flaky-quarantine.md) |
| POST | `/api/v1/quarantine/{request_id}/release` | Release Quarantine | [Flaky Quarantine](api/flaky-quarantine.md) |
| GET | `/api/v1/release-gate-policies` | List Policies | [Release Gate Policies](api/release-gate-policies.md) |
| POST | `/api/v1/release-gate-policies` | Create Policy | [Release Gate Policies](api/release-gate-policies.md) |
| GET | `/api/v1/release-gate-policies/effective/{project_id}` | Get Effective Policy | [Release Gate Policies](api/release-gate-policies.md) |
| GET | `/api/v1/release-gate-policies/history/{project_id}` | Get Policy History | [Release Gate Policies](api/release-gate-policies.md) |
| POST | `/api/v1/release-gate-policies/simulate` | Simulate Policy | [Release Gate Policies](api/release-gate-policies.md) |
| GET | `/api/v1/release-gate-policies/system-history` | Get System Policy History | [Release Gate Policies](api/release-gate-policies.md) |
| GET | `/api/v1/release-gate-policies/{policy_id}` | Get Policy | [Release Gate Policies](api/release-gate-policies.md) |
| PATCH | `/api/v1/release-gate-policies/{policy_id}` | Update Policy | [Release Gate Policies](api/release-gate-policies.md) |
| POST | `/api/v1/release-gate-policies/{policy_id}/deactivate` | Deactivate Policy | [Release Gate Policies](api/release-gate-policies.md) |
| POST | `/api/v1/release-gate-policies/{policy_id}/publish` | Publish Policy | [Release Gate Policies](api/release-gate-policies.md) |
| GET | `/api/v1/release-readiness/{run_id}` | Get Release Decision | [Release Readiness](api/release-readiness.md) |
| POST | `/api/v1/release-readiness/{run_id}/override` | Override Release Decision | [Release Readiness](api/release-readiness.md) |
| GET | `/api/v1/releases` | List Releases | [Releases](api/releases.md) |
| POST | `/api/v1/releases` | Create Release | [Releases](api/releases.md) |
| POST | `/api/v1/releases/sync` | Sync Releases From External | [Releases](api/releases.md) |
| DELETE | `/api/v1/releases/{release_id}` | Delete Release | [Releases](api/releases.md) |
| GET | `/api/v1/releases/{release_id}` | Get Release | [Releases](api/releases.md) |
| PUT | `/api/v1/releases/{release_id}` | Update Release | [Releases](api/releases.md) |
| POST | `/api/v1/releases/{release_id}/activate` | Activate Release Endpoint | [Releases](api/releases.md) |
| POST | `/api/v1/releases/{release_id}/compliance-pack` | Generate Compliance Pack | [Compliance Pack](api/compliance-pack.md) |
| GET | `/api/v1/releases/{release_id}/compliance-packs` | List Compliance Packs | [Compliance Pack](api/compliance-pack.md) |
| GET | `/api/v1/releases/{release_id}/gate` | Get Release Gate | [Releases](api/releases.md) |
| GET | `/api/v1/releases/{release_id}/gate/baseline` | Get Release Gate Baseline | [Releases](api/releases.md) |
| POST | `/api/v1/releases/{release_id}/gate/evaluate` | Evaluate Release Gate | [Releases](api/releases.md) |
| POST | `/api/v1/releases/{release_id}/outcomes` | Mark Release Outcome | [Releases](api/releases.md) |
| POST | `/api/v1/releases/{release_id}/phases` | Add Phase | [Releases](api/releases.md) |
| GET | `/api/v1/releases/{release_id}/phases/gate` | Get Release Phase Gate | [Releases](api/releases.md) |
| DELETE | `/api/v1/releases/{release_id}/phases/{phase_id}` | Delete Phase | [Releases](api/releases.md) |
| PUT | `/api/v1/releases/{release_id}/phases/{phase_id}` | Update Phase | [Releases](api/releases.md) |
| POST | `/api/v1/releases/{release_id}/phases/{phase_id}/gate/evaluate` | Evaluate Release Phase Gate | [Releases](api/releases.md) |
| POST | `/api/v1/releases/{release_id}/test-runs` | Link Test Run | [Releases](api/releases.md) |
| DELETE | `/api/v1/releases/{release_id}/test-runs/{run_id}` | Unlink Test Run | [Releases](api/releases.md) |
| POST | `/api/v1/reports/email-trends` | Email Trends Report | [Reports](api/reports.md) |
| GET | `/api/v1/reports/runs/{run_id}/evidence-bundle` | Export Evidence Bundle | [Reports](api/reports.md) |
| GET | `/api/v1/reports/runs/{run_id}/pdf` | Export Run Report Pdf | [Reports](api/reports.md) |
| POST | `/api/v1/reports/runs/{run_id}/share` | Create Share Link Endpoint | [Reports](api/reports.md) |
| GET | `/api/v1/reports/runs/{run_id}/share-links` | List Share Links Endpoint | [Reports](api/reports.md) |
| DELETE | `/api/v1/reports/share-links/{link_id}` | Revoke Share Link Endpoint | [Reports](api/reports.md) |
| GET | `/api/v1/reports/summary` | Get Summary Report | [Summary Report](api/summary-report.md) |
| GET | `/api/v1/reports/summary/pdf` | Export Summary Report Pdf | [Summary Report](api/summary-report.md) |
| GET | `/api/v1/reviews/{review_id}` | Get Review | [Reviews](api/reviews.md) |
| POST | `/api/v1/reviews/{review_id}/accept` | Accept Review | [Reviews](api/reviews.md) |
| POST | `/api/v1/reviews/{review_id}/reject` | Reject Review | [Reviews](api/reviews.md) |
| GET | `/api/v1/runs` | List Runs | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/compare` | Compare Two Runs | [Run Compare](api/run-compare.md) |
| GET | `/api/v1/runs/compare/latest` | Compare Latest Suite Runs | [Run Compare](api/run-compare.md) |
| GET | `/api/v1/runs/failed-ids` | List Failed Run Ids | [Test Runs](api/test-runs.md) |
| DELETE | `/api/v1/runs/{run_id}` | Delete Run | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}` | Get Run | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/attribution` | Run Attribution | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/baseline-diff` | Get Run Baseline Diff | [Run Intelligence](api/run-intelligence.md) |
| GET | `/api/v1/runs/{run_id}/commit-range` | Get Run Commit Range | [Commit Attribution](api/commit-attribution.md) |
| GET | `/api/v1/runs/{run_id}/decision-reports` | List Run Decision Reports | [Run Intelligence](api/run-intelligence.md) |
| POST | `/api/v1/runs/{run_id}/decision-reports/{report_id}/feedback` | Submit Decision Report Feedback | [Feedback & Training](api/feedback-training.md) |
| GET | `/api/v1/runs/{run_id}/decision-trail` | AI decision trail for a test run | [Decision Trail](api/decision-trail.md) |
| GET | `/api/v1/runs/{run_id}/downstream-status` | Get Run Downstream Status | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/export` | Export Intelligence Report | [Run Intelligence](api/run-intelligence.md) |
| GET | `/api/v1/runs/{run_id}/intelligence` | Get Run Intelligence Endpoint | [Run Intelligence](api/run-intelligence.md) |
| POST | `/api/v1/runs/{run_id}/intelligence/refresh` | Refresh Intelligence | [Run Intelligence](api/run-intelligence.md) |
| POST | `/api/v1/runs/{run_id}/investigations` | Start Investigation | [Agent Investigations](api/agent-investigations.md) |
| POST | `/api/v1/runs/{run_id}/recover-live` | Recover Live Run From Buffer | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/regression-diff` | Get Regression Diff | [Test Runs](api/test-runs.md) |
| POST | `/api/v1/runs/{run_id}/release` | Set Run Release | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/step-flips` | Get Run Step Flips | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/summary` | Get Run Summary By Mode | [Run Intelligence](api/run-intelligence.md) |
| GET | `/api/v1/runs/{run_id}/suspects` | Get Run Suspects | [Commit Attribution](api/commit-attribution.md) |
| GET | `/api/v1/runs/{run_id}/test-health` | Get Test Health | [Test Health](api/test-health.md) |
| GET | `/api/v1/runs/{run_id}/tests` | List Test Cases | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/tests/{test_id}` | Get Test Case | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/tests/{test_id}/history` | Get Test Case History Endpoint | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/tests/{test_id}/rich-detail` | Get Enriched Test Case Detail Endpoint | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/tests/{test_id}/step-flips` | Get Test Case Step Flips | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/runs/{run_id}/tests/{test_id}/steps` | Get Test Case Steps | [Test Runs](api/test-runs.md) |
| GET | `/api/v1/saved-views` | List Saved Views | [Saved Views](api/saved-views.md) |
| POST | `/api/v1/saved-views` | Create Saved View | [Saved Views](api/saved-views.md) |
| DELETE | `/api/v1/saved-views/{view_id}` | Delete Saved View | [Saved Views](api/saved-views.md) |
| GET | `/api/v1/saved-views/{view_id}` | Get Saved View | [Saved Views](api/saved-views.md) |
| PATCH | `/api/v1/saved-views/{view_id}` | Update Saved View | [Saved Views](api/saved-views.md) |
| GET | `/api/v1/scim-tokens` | List Scim Tokens | [SCIM Tokens](api/scim-tokens.md) |
| POST | `/api/v1/scim-tokens` | Create Token | [SCIM Tokens](api/scim-tokens.md) |
| DELETE | `/api/v1/scim-tokens/{token_id}` | Revoke Scim Token | [SCIM Tokens](api/scim-tokens.md) |
| GET | `/api/v1/scim/v2/ResourceTypes` | Scim Resource Types | [SCIM 2.0](api/scim-2-0.md) |
| GET | `/api/v1/scim/v2/ResourceTypes/{resource_type}` | Scim Resource Type | [SCIM 2.0](api/scim-2-0.md) |
| GET | `/api/v1/scim/v2/Schemas` | Scim Schemas | [SCIM 2.0](api/scim-2-0.md) |
| GET | `/api/v1/scim/v2/Schemas/{schema_uri}` | Scim Schema | [SCIM 2.0](api/scim-2-0.md) |
| GET | `/api/v1/scim/v2/ServiceProviderConfig` | Scim Service Provider Config | [SCIM 2.0](api/scim-2-0.md) |
| GET | `/api/v1/scim/v2/Users` | Scim List | [SCIM 2.0](api/scim-2-0.md) |
| POST | `/api/v1/scim/v2/Users` | Scim Create | [SCIM 2.0](api/scim-2-0.md) |
| DELETE | `/api/v1/scim/v2/Users/{user_id}` | Scim Delete | [SCIM 2.0](api/scim-2-0.md) |
| GET | `/api/v1/scim/v2/Users/{user_id}` | Scim Get | [SCIM 2.0](api/scim-2-0.md) |
| PATCH | `/api/v1/scim/v2/Users/{user_id}` | Scim Patch | [SCIM 2.0](api/scim-2-0.md) |
| PUT | `/api/v1/scim/v2/Users/{user_id}` | Scim Replace | [SCIM 2.0](api/scim-2-0.md) |
| GET | `/api/v1/scoring-model` | Get Scoring Model | [Scoring Model](api/scoring-model.md) |
| GET | `/api/v1/sdk` | List available client SDKs | [SDK Downloads](api/sdk-downloads.md) |
| GET | `/api/v1/sdk/{lang}` | Download a client SDK | [SDK Downloads](api/sdk-downloads.md) |
| GET | `/api/v1/search` | Search Test Cases | [Search](api/search.md) |
| GET | `/api/v1/search/entity-counts` | Get Entity Counts | [Search](api/search.md) |
| GET | `/api/v1/search/global` | Global Search Endpoint | [Search](api/search.md) |
| GET | `/api/v1/search/index-status` | Get Index Status | [Search](api/search.md) |
| POST | `/api/v1/search/reindex` | Trigger Reindex | [Search](api/search.md) |
| GET | `/api/v1/search/similar/{test_case_id}` | Find Similar Failures | [Search](api/search.md) |
| GET | `/api/v1/settings/ai` | Get Ai Config | [Settings](api/settings.md) |
| PUT | `/api/v1/settings/ai` | Update Ai Config | [Settings](api/settings.md) |
| GET | `/api/v1/settings/ai/model-status` | Get Ai Model Status | [Settings](api/settings.md) |
| GET | `/api/v1/settings/audit-log` | Get Audit Log | [Settings](api/settings.md) |
| GET | `/api/v1/settings/flags` | List Feature Flags | [Settings](api/settings.md) |
| DELETE | `/api/v1/settings/flags/{flag_key}` | Remove Feature Flag | [Settings](api/settings.md) |
| PUT | `/api/v1/settings/flags/{flag_key}` | Update Feature Flag | [Settings](api/settings.md) |
| GET | `/api/v1/settings/integrations` | Get Integrations Config | [Settings](api/settings.md) |
| PUT | `/api/v1/settings/integrations` | Update Integrations Config | [Settings](api/settings.md) |
| GET | `/api/v1/settings/integrations/health` | Get Integration Health | [Settings](api/settings.md) |
| GET | `/api/v1/settings/mfa-policy` | Get Mfa Policy | [Settings](api/settings.md) |
| PUT | `/api/v1/settings/mfa-policy` | Update Mfa Policy | [Settings](api/settings.md) |
| GET | `/api/v1/settings/smtp` | Get Smtp Config | [Settings](api/settings.md) |
| PUT | `/api/v1/settings/smtp` | Update Smtp Config | [Settings](api/settings.md) |
| POST | `/api/v1/settings/smtp/test` | Test Smtp Config | [Settings](api/settings.md) |
| GET | `/api/v1/settings/storage` | Get Storage Config | [Settings](api/settings.md) |
| PUT | `/api/v1/settings/storage` | Update Storage Config | [Settings](api/settings.md) |
| GET | `/api/v1/shared/reports/{token}` | View Shared Report | [Shared Reports](api/shared-reports.md) |
| GET | `/api/v1/shared/reports/{token}/pdf` | Download Shared Report Pdf | [Shared Reports](api/shared-reports.md) |
| POST | `/api/v1/sso/acs` | Saml Acs | [SSO / SAML](api/sso-saml.md) |
| GET | `/api/v1/sso/configs` | List Sso Configs | [SSO / SAML](api/sso-saml.md) |
| POST | `/api/v1/sso/configs` | Create Sso Config | [SSO / SAML](api/sso-saml.md) |
| DELETE | `/api/v1/sso/configs/{config_id}` | Delete Sso Config | [SSO / SAML](api/sso-saml.md) |
| GET | `/api/v1/sso/configs/{config_id}` | Get Sso Config | [SSO / SAML](api/sso-saml.md) |
| PATCH | `/api/v1/sso/configs/{config_id}` | Update Sso Config | [SSO / SAML](api/sso-saml.md) |
| POST | `/api/v1/sso/configs/{config_id}/test` | Test Sso Connection | [SSO / SAML](api/sso-saml.md) |
| GET | `/api/v1/sso/login-url` | Get Sso Login Url | [SSO / SAML](api/sso-saml.md) |
| GET | `/api/v1/sso/metadata` | Get Sp Metadata | [SSO / SAML](api/sso-saml.md) |
| GET | `/api/v1/sso/status` | Get Sso Status | [SSO / SAML](api/sso-saml.md) |
| GET | `/api/v1/stream/active` | List Active Sessions | [Live Stream](api/live-stream.md) |
| POST | `/api/v1/stream/events/batch` | Ingest Event Batch | [Live Stream](api/live-stream.md) |
| POST | `/api/v1/stream/ingest` | Ingest Via Api Key | [Live Stream](api/live-stream.md) |
| POST | `/api/v1/stream/sessions` | Create Session | [Live Stream](api/live-stream.md) |
| DELETE | `/api/v1/stream/sessions/{session_id}` | Close Session | [Live Stream](api/live-stream.md) |
| GET | `/api/v1/stream/sessions/{session_id}` | Get Session | [Live Stream](api/live-stream.md) |
| GET | `/api/v1/stream/sse/{project_id}` | Sse Stream | [Live Stream](api/live-stream.md) |
| GET | `/api/v1/suites` | List Suites | [Test Suites](api/test-suites.md) |
| POST | `/api/v1/suites` | Create Suite | [Test Suites](api/test-suites.md) |
| DELETE | `/api/v1/suites/{suite_id}` | Delete Suite | [Test Suites](api/test-suites.md) |
| GET | `/api/v1/suites/{suite_id}` | Get Suite | [Test Suites](api/test-suites.md) |
| PATCH | `/api/v1/suites/{suite_id}` | Update Suite | [Test Suites](api/test-suites.md) |
| POST | `/api/v1/suites/{suite_id}/set-default` | Set Default | [Test Suites](api/test-suites.md) |
| GET | `/api/v1/suites/{suite_id}/test-cases` | List Suite Test Cases | [Test Suites](api/test-suites.md) |
| DELETE | `/api/v1/test-cases/{test_case_id}/review` | Clear Test Case Review | [Test Execution Reviews](api/test-execution-reviews.md) |
| GET | `/api/v1/test-cases/{test_case_id}/review` | Get Test Case Review | [Test Execution Reviews](api/test-execution-reviews.md) |
| PUT | `/api/v1/test-cases/{test_case_id}/review` | Upsert Test Case Review | [Test Execution Reviews](api/test-execution-reviews.md) |
| GET | `/api/v1/test-management/audit` | Get Audit Log | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/batches/{batch_id}` | Get Batch | [RAG Generation](api/rag-generation.md) |
| POST | `/api/v1/test-management/batches/{batch_id}/accept` | Batch Accept | [RAG Generation](api/rag-generation.md) |
| POST | `/api/v1/test-management/batches/{batch_id}/cases/{case_id}/accept` | Accept Case | [RAG Generation](api/rag-generation.md) |
| POST | `/api/v1/test-management/batches/{batch_id}/cases/{case_id}/reject` | Reject Case | [RAG Generation](api/rag-generation.md) |
| GET | `/api/v1/test-management/batches/{batch_id}/coverage` | Get Batch Coverage | [RAG Generation](api/rag-generation.md) |
| GET | `/api/v1/test-management/batches/{batch_id}/eval` | Batch Eval | [RAG Generation](api/rag-generation.md) |
| GET | `/api/v1/test-management/cases` | List Test Cases | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases` | Create Test Case | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/ai-coverage` | Ai Coverage Analysis | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/ai-generate` | Ai Generate Cases | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/ai-generate/async` | Ai Generate Cases Async | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/ai-task/{task_id}` | Get Ai Task Status | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/evidence-gaps` | Get Test Case Evidence Gaps | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/export/excel` | Export Test Cases Excel | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/needs-review` | Cases Needing Review | [RAG Generation](api/rag-generation.md) |
| POST | `/api/v1/test-management/cases/rag-generate` | Rag Generate | [RAG Generation](api/rag-generation.md) |
| POST | `/api/v1/test-management/cases/rag-retrieve` | Rag Retrieve | [RAG Generation](api/rag-generation.md) |
| GET | `/api/v1/test-management/cases/stale` | List Stale Cases | [RAG Generation](api/rag-generation.md) |
| DELETE | `/api/v1/test-management/cases/{case_id}` | Deprecate Test Case | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/{case_id}` | Get Test Case | [Test Management](api/test-management.md) |
| PATCH | `/api/v1/test-management/cases/{case_id}` | Update Test Case | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/{case_id}/ai-review` | Ai Review Case | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/{case_id}/allowed-transitions` | Get Allowed Transitions | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/{case_id}/citations` | Get Case Citations | [RAG Generation](api/rag-generation.md) |
| GET | `/api/v1/test-management/cases/{case_id}/comments` | List Comments | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/{case_id}/comments` | Add Comment | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/{case_id}/dismiss-stale` | Dismiss Stale | [RAG Generation](api/rag-generation.md) |
| GET | `/api/v1/test-management/cases/{case_id}/history` | Get Test Case History | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/{case_id}/request-review` | Request Review | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/{case_id}/review-action` | Review Action | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/cases/{case_id}/reviews` | Get Reviews | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/cases/{case_id}/transition` | Transition Test Case | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/plans` | List Plans | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/plans` | Create Plan | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/plans/ai-create` | Ai Create Plan | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/plans/ai-create/async` | Ai Create Plan Async | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/plans/{plan_id}` | Get Plan | [Test Management](api/test-management.md) |
| PATCH | `/api/v1/test-management/plans/{plan_id}` | Update Plan | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/plans/{plan_id}/export/pdf` | Export Test Plan Pdf | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/plans/{plan_id}/export/word` | Export Test Plan Word | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/plans/{plan_id}/items` | List Plan Items | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/plans/{plan_id}/items` | Add Plan Item | [Test Management](api/test-management.md) |
| DELETE | `/api/v1/test-management/plans/{plan_id}/items/{item_id}` | Remove Plan Item | [Test Management](api/test-management.md) |
| PATCH | `/api/v1/test-management/plans/{plan_id}/items/{item_id}/execute` | Record Execution | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/rag/status` | Rag Status | [RAG Generation](api/rag-generation.md) |
| GET | `/api/v1/test-management/strategies` | List Strategies | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/strategies/ai-generate` | Ai Generate Strategy | [Test Management](api/test-management.md) |
| POST | `/api/v1/test-management/strategies/ai-generate/async` | Ai Generate Strategy Async | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/strategies/{strategy_id}` | Get Strategy | [Test Management](api/test-management.md) |
| PUT | `/api/v1/test-management/strategies/{strategy_id}` | Update Strategy | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/strategies/{strategy_id}/export/pdf` | Export Test Strategy Pdf | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/strategies/{strategy_id}/export/word` | Export Test Strategy Word | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suite-owners` | List Suite Owners | [Test Management](api/test-management.md) |
| PUT | `/api/v1/test-management/suite-owners/{suite_name}` | Set Suite Owner | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suite-reviews` | List Suite Reviews | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suite-reviews/by-run/{test_run_id}` | List Reviews For Run | [Test Management](api/test-management.md) |
| PUT | `/api/v1/test-management/suite-reviews/by-run/{test_run_id}/{suite_name}` | Upsert Review For Run Suite | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suites` | List Test Suites In Scope | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suites/{suite_name}/cases` | Get Suite Test Cases | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suites/{suite_name}/changes` | Get Suite Changes | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suites/{suite_name}/deleted` | Get Suite Deleted | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suites/{suite_name}/membership` | Get Suite Membership | [Test Management](api/test-management.md) |
| GET | `/api/v1/test-management/suites/{suite_name}/trend` | Get Suite Trend In Scope | [Test Management](api/test-management.md) |
| POST | `/api/v1/training/export` | Trigger Export | [Feedback & Training](api/feedback-training.md) |
| POST | `/api/v1/training/finetune` | Trigger Finetune | [Feedback & Training](api/feedback-training.md) |
| POST | `/api/v1/training/promote` | Promote Model | [Feedback & Training](api/feedback-training.md) |
| GET | `/api/v1/training/status` | Get Training Status | [Feedback & Training](api/feedback-training.md) |
| GET | `/api/v1/users` | List Users | [User Management](api/user-management.md) |
| POST | `/api/v1/users` | Admin Create User | [User Management](api/user-management.md) |
| POST | `/api/v1/users/invite` | Invite User | [User Management](api/user-management.md) |
| GET | `/api/v1/users/{user_id}` | Get User | [User Management](api/user-management.md) |
| PATCH | `/api/v1/users/{user_id}` | Update User Profile | [User Management](api/user-management.md) |
| GET | `/api/v1/users/{user_id}/access-audit` | Get User Access Audit | [User Management](api/user-management.md) |
| GET | `/api/v1/users/{user_id}/memberships` | Get User Memberships | [User Management](api/user-management.md) |
| PATCH | `/api/v1/users/{user_id}/role` | Update User Role | [User Management](api/user-management.md) |
| PATCH | `/api/v1/users/{user_id}/status` | Update User Status | [User Management](api/user-management.md) |
| GET | `/api/v1/value-metrics` | Get Metrics | [Value Metrics](api/value-metrics.md) |
| GET | `/api/v1/value-metrics/by-team` | Get Metrics By Team | [Value Metrics](api/value-metrics.md) |
| GET | `/api/v1/value-metrics/export` | Export Metrics | [Value Metrics](api/value-metrics.md) |
| GET | `/api/v1/value-metrics/methodology` | Get Methodology Page | [Value Metrics](api/value-metrics.md) |
| GET | `/api/v1/webhooks` | List Webhook Subscriptions | [Outbound Webhooks](api/outbound-webhooks.md) |
| POST | `/api/v1/webhooks` | Create Webhook Subscription | [Outbound Webhooks](api/outbound-webhooks.md) |
| GET | `/api/v1/webhooks/events` | List Webhook Events | [Outbound Webhooks](api/outbound-webhooks.md) |
| DELETE | `/api/v1/webhooks/{subscription_id}` | Delete Webhook Subscription | [Outbound Webhooks](api/outbound-webhooks.md) |
| GET | `/api/v1/webhooks/{subscription_id}` | Get Webhook Subscription | [Outbound Webhooks](api/outbound-webhooks.md) |
| PATCH | `/api/v1/webhooks/{subscription_id}` | Update Webhook Subscription | [Outbound Webhooks](api/outbound-webhooks.md) |
| GET | `/api/v1/webhooks/{subscription_id}/deliveries` | List Webhook Deliveries | [Outbound Webhooks](api/outbound-webhooks.md) |
| POST | `/api/v1/webhooks/{subscription_id}/deliveries/{delivery_id}/replay` | Replay Webhook Delivery | [Outbound Webhooks](api/outbound-webhooks.md) |
| POST | `/api/v1/webhooks/{subscription_id}/test` | Test Webhook Subscription | [Outbound Webhooks](api/outbound-webhooks.md) |
| GET | `/health/details` | Full dependency status — for ops dashboards | [Health](api/health.md) |
| GET | `/health/ingestion` | Live-stream ingestion health — gate + queue + memory | [Health](api/health.md) |
| GET | `/health/live` | Liveness probe — is the process alive? | [Health](api/health.md) |
| GET | `/health/ready` | Readiness probe — are critical dependencies up? | [Health](api/health.md) |
| GET | `/health/version` | Build identity — version, revision, build date (no probes) | [Health](api/health.md) |
| POST | `/webhooks/minio` | Minio Webhook | [Webhooks](api/webhooks.md) |
| POST | `/ws/events/{run_id}` | Ingest Live Event | [Live Reporting](api/live-reporting.md) |

Total: **550 HTTP operations**, **460 paths**, **81 domain pages**.
