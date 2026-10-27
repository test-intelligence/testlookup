# Changelog

All notable changes to TestLookup are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may
contain breaking changes; [UPGRADING.md](UPGRADING.md) lists them).

## [Unreleased] — 0.1.0

The first public release. TestLookup is a self-hosted test-intelligence
platform: it ingests test results from CI and live test runs, explains
failures, tracks flakiness and decides release readiness, with AI that runs
locally by default.

### What 0.1.0 contains

- **Ingestion**: batch and file upload of JUnit, TestNG, Robot Framework,
  NUnit 3, Visual Studio TRX, xUnit v2, Playwright JSON, pytest-json-report,
  Cypress/Mochawesome, Cucumber JSON and Allure results, or a zip of them;
  live streaming from the Python, Java (TestNG, JUnit 5), JavaScript (Jest,
  Mocha) and Go reporters; CI context (GitHub Actions, GitLab, Jenkins, Azure
  Pipelines, CircleCI) and commit ranges.
- **Runs and failures**: run history, run detail with analysis and evidence
  tabs, run compare and bisect-from-green, failure clustering by signature,
  defects with Jira promotion and recurrence de-duplication, an inbox of
  assigned failures and approvals, ownership rules.
- **Flaky tests**: flip-based flakiness scoring, quarantine proposals and a
  quarantine-aware CI verdict (`testlookup ci-verdict`).
- **Release decisions**: release gate with versioned gate policies, releases
  with phases, compliance packs.
- **Analytics**: overview, trends, coverage, an explorer with saved views,
  suites and canonical test cases, value metrics, summary and analysis
  reports, scheduled digests.
- **AI**: rules / ML / LLM failure classification with confidence and
  provenance, an agent pipeline with budgets and human review, deep
  investigation, Ask AI chat over your data, a knowledge base with RAG test
  generation, AI evaluation. Offline by default (`AI_OFFLINE_MODE=true`) with
  Ollama; OpenAI-compatible servers, OpenAI, Gemini, Anthropic and OpenRouter
  are opt-in.
- **Test management**: authored test cases, plans, strategies, reviews and
  audit.
- **Administration**: projects and five roles, API keys, MFA and lockout,
  SAML 2.0 and OIDC SSO and SCIM 2.0, retention and purge, feature flags, audit and
  activity logs, integrations (Jira, Slack, Microsoft Teams, email, GitHub
  checks, GitLab, outbound webhooks, Splunk, OpenShift).
- **Interfaces**: web UI with an in-app guide at `/docs`, REST API with
  Swagger at `/api-docs`, `testlookup` CLI, an MCP server for AI assistants.
- **Deployment**: Docker Compose (development, demo, release images,
  air-gapped override, monitoring add-on), Kubernetes overlays (generic,
  single-node K3s, OpenShift, EKS/GKE/AKS), an air-gapped offline bundle,
  backups and upgrades.

### Added

- **Activity feed: run, analysis, investigation and release events now
  appear** (wave W21a, ACT-01 slice (a); branch
  `feat/df-activity-ledger-a-20261027`; no migration). Seventeen event types
  were registered — offered as `/activity` filters — and recorded by nothing.
  They now have producers: release phase added / changed / advanced / removed,
  a run linked to and unlinked from a release (seen from the release and the
  run), a release closed, a release synced from GitHub / GitLab / Jira, a run
  deleted, live runs started / finalized / recovered, an upload that failed
  ingestion, an AI analysis that failed after its retries, and a deep
  investigation started / completed. A new quality-gate ratchet,
  `backend.activity-registry-emitted`, fails when a registered event type has
  no producer (44 left, baselined shrink-only). Tests:
  `backend/tests/integration/test_activity_ledger_slice_a_postgres.py` (real
  handlers against PostgreSQL, rows read back) and the guard's self-tests in
  `scripts/test_quality_gate.py`.
- **Identity and secrets: SSO role mapping, OIDC, SAML single logout, SCIM
  groups, Vault references** (wave W20, ADM-08 + ADM-09 + ADM-10; branch
  `feat/df-identity-secrets-20261027`; migrations 0235–0237). Settings › SSO
  gets a role-mapping editor (IdP group → role, default role, group
  attribute) with a server-side preview (`POST
  /api/v1/sso/configs/{id}/preview-role`). **OIDC**: a configuration may now
  be OpenID Connect — issuer, client id, write-only client secret (kept in
  `secret_refs`, scope `sso_oidc`, shown only as "(set)"), scopes, groups
  claim; `/api/v1/sso/oidc/login` → the IdP (state + nonce + PKCE S256, single
  use, 10 minutes) → `/api/v1/sso/oidc/callback` exchanges the code and
  validates the ID token against the IdP's JWKS (RS256 / ES256 only; `iss`,
  `aud`, `azp`, `exp`, `iat`, `nonce`), then the same role mapping and JIT
  provisioning as SAML; an IdP that says the email is not verified never
  takes over an existing account; `/login` says "Sign in with <name>". 0237
  makes the SAML columns NULL-able under a CHECK by provider type. **SAML
  single logout** (IdP-initiated): `/api/v1/sso/slo` (Redirect and POST
  bindings) verifies the IdP's signed LogoutRequest (SHA-2, Issuer,
  Destination, freshness, single use), revokes that user's refresh tokens and
  access tokens, and answers a Success LogoutResponse; the SP metadata
  advertises it only while an IdP SLO URL is set. **SCIM `/Groups`** mapped
  to project roles (0236; memberships SCIM created carry `source =
  scim:<group>` and are the only ones reconciliation touches; Project access
  shows "via SCIM group <name>"). **Vault references** for any secret field
  (`vault:<path>#<key>`, KV v2, token or AppRole auth; 0235). Fixed with it:
  every real SAML sign-in was refused — the assertion verifier called
  `XMLVerifier.VerifyConfig`, which the pinned signxml 5.1.0 does not have
  (now a `SignatureConfiguration` with the same SHA-2 allow-list); creating an
  SSO configuration could insert its audit row first (500); a remounted
  `/sso/callback` spent the one-time code twice. Tests:
  `tests/regression/test_oidc_id_token_validation.py` (alg `none`, HS256 keyed
  with the public key, wrong aud / iss, expired, nonce, unknown kid, state
  reuse, SSRF on discovery), `tests/integration/test_sso_oidc_postgres.py`
  (end to end against the fake OIDC IdP in `tests/fakes/fake_integrations.py`),
  `tests/regression/test_saml_slo.py` (both bindings, real signatures, the
  verifier fix), `tests/integration/test_scim_groups_postgres.py`,
  `tests/regression/test_vault_secret_references.py`, the role preview, the
  migration shapes, and vitest for the settings page, login button and
  callback.
- **GitHub App authentication, and GitLab does what GitHub does** (wave W19,
  INT-06 + INT-05; branch `feat/df-git-hosts-20261026`; migration 0234). An
  administrator registers one GitHub App under Settings › GitHub (`PUT
  /api/v1/settings/github-app`; the private key is write-only, validated as an
  RSA PEM and shown back only as "(set)"); a project then chooses *GitHub App*
  instead of a personal access token and installs the App (`GET
  /api/v1/github/app/install-url`, a signed, user-bound 10-minute state;
  `POST /api/v1/github/app/setup-callback` records the installation). Every
  GitHub call — checks, PR comments, CODEOWNERS, milestone sync, the commit
  range, the fixer — now reads its credential through one seam
  (`github_auth.get_github_token`): an installation token minted from an
  RS256 App JWT and cached until five minutes before it expires, or the PAT.
  GitLab parity: a run whose repository is the project's GitLab project gets
  its commit range and changed files from GitLab's compare (Changes tab,
  failure suspects; commit links go to GitLab), `POST /releases/sync` accepts
  `source: "gitlab"` (milestones keyed on their iid, so a rename renames the
  release), the sync dialog and Settings › GitLab offer *Sync milestones now*,
  and change correlation falls back to GitLab's commits. 0234 adds
  `github_integrations.auth_kind` / `installation_id` and turns `github_checks`
  and `gitlab` on for a fresh install. Fixed with it: the run header no longer
  says "Jenkins" for a run without a Jenkins job. Tests:
  `tests/regression/test_github_app_auth.py`, `tests/regression/test_gitlab_parity.py`,
  `tests/integration/test_github_app_postgres.py`,
  `tests/integration/test_gitlab_release_sync_postgres.py`, `tests/test_migration_0234.py`.

- **Live streaming from Playwright, Vitest, Cypress, Cucumber JS and Robot
  Framework** (wave W18b, DEV-03; branch `feat/df-live-reporters-20261026`; no
  migration). The JavaScript SDK gains `testlookup-reporter/playwright-reporter`
  (each attempt as it ends; a retry is `metadata.attempt` 2; one suite per
  Playwright project, the project part of the test's identity),
  `vitest-reporter` (Vitest 3's `onTestCaseResult`, Vitest 1/2's
  `onTaskUpdate`, skipped tests at the end), `cypress-plugin` (per spec at
  `after:spec`; the last attempt is the result) and `cucumber-formatter`
  (Cucumber's message stream: one result per scenario attempt, the worst step
  decides, the feature is the suite), on one shared adapter: options, then
  `TESTLOOKUP_*` / `testlookup.properties`; off with one warning when not
  configured; a network error never fails the run. Each loads under `require()`
  and ESM `import()`. The Python SDK ZIP gains `testlookup_robot_listener.py`
  (Listener API v3, Robot Framework 5-7). `/live` › Connect a test runner offers
  a runner picker under JavaScript (Jest / Mocha, Playwright, Vitest, Cypress,
  Cucumber) and Python (pytest, Robot Framework) with each snippet.
  Cucumber-JVM is not included. Tests: `client/js/tests/live-adapters.test.mjs`,
  `client/tests/test_robot_listener.py`, `frontend/src/components/live/sdkSnippets.test.ts`.

- **Gate a release from CI, and the CLI / MCP catch up with the UI** (wave W18a,
  DEV-05, DEV-01 and DEV-04; branch `feat/df-cli-mcp-sdk-20261026`; no migration).
  `testlookup release gate <release>` prints the release's stored verdict (verdict,
  pass rate, evidence, blockers) and exits with a stable code — `0` GO or
  CONDITIONAL_GO (`1` with `--fail-on CONDITIONAL_GO`), `1` NO_GO, `3` not
  evaluated / not enough evidence, `2` infrastructure error, failing closed
  (`--fail-open` for availability); `--evaluate` records a fresh verdict first;
  the CLI README has the table and GitHub Actions / GitLab snippets. Every
  `--release` (and `<release>`) now takes a release's name or version as well as
  its id, and every `--project` a project name: a name several releases share,
  or none, exits `4` listing the candidates with their ids or the closest names —
  never a guess. New groups mirror the MCP tools' endpoints: `release list`,
  `quarantine list|approve|reject|release` (`--reason` recorded),
  `defects jira preview|create`, `investigations start|list|show`,
  `compliance-pack list|generate|download`, and `activity list|show|types|export`
  (the Activity tab's feed, keyset paged: a page with more says so, `--all`
  follows the cursor; one event's before/after diff; CSV / NDJSON export). MCP
  gains `get_release_gate` (the verdict as data), `list_project_activity` and
  `get_activity_event`, and `get_chart_data` takes release and project names.
  Tests: `cli/tests/test_release_resolver.py`, `test_release_commands.py`,
  `test_activity_commands.py`; `mcp/tests/test_mcp_release_resolver.py`,
  `test_mcp_release_gate.py`, `test_mcp_activity.py`.

- **Invitations that work, password reset, and measured performance** (wave
  W17b, ADM-01, ADM-02 and ADM-06; branch `feat/df-accounts-admin-b-20261026`;
  migration 0233). **Invite User** now produces a link someone can redeem:
  only a hash of the token is stored, the link is `/accept-invite#token=…`
  (the token in the fragment, the email no longer in the link), it is emailed
  when SMTP is configured ("Invitation emailed to …") and always shown to copy
  ("SMTP is not configured — copy this link"), and the invite can name the
  projects the person joins. The accept page shows who invited them and as
  what, creates the account with that role and those projects and signs them
  in — also with self-registration off; a used, expired, revoked or wrong link
  gets one message. **Pending invitations** (User Management, admins) lists
  open invitations with Resend (a new link; the old one stops working) and
  Revoke. Old `/register?token=` links redirect to the accept page. **Forgot
  password?** on the sign-in page: with SMTP it emails a single-use,
  60-minute link and answers every address the same way (the lookup runs
  after the response; three requests per mailbox per hour); without SMTP it
  says to ask an administrator, who can **Send password reset link** from a
  user's row (never their own; audited, no token kept). Using a link sets the
  password, clears a lockout, signs the account out everywhere and leaves
  two-factor sign-in on. New public routes `POST
  /api/v1/auth/invitations/{preview,accept}` and `POST
  /api/v1/auth/password/{forgot,reset}` are rate-limited; `GET
  /api/v1/auth/options` adds `password_reset_email`. **Settings ›
  Performance** shows the measured p50 / p95 / p99 and request count beside
  each latency budget for the last 15 minutes, hour or 24 hours, red where p95
  is over budget and "No requests" where nothing was measured (timed in-app on
  every API pod, or read from `PROMETHEUS_URL` when set;
  `GET /api/v1/performance/observed`), and a **Slow queries** tab for
  administrators lists the statement shapes slower than `SLOW_QUERY_MS`
  (default 500 ms) with count, mean, max and last seen — no values are ever
  recorded (`GET /api/v1/performance/slow-queries`; `pg_stat_statements` when
  readable). Tests: `test_invitations_postgres.py`,
  `test_password_reset_postgres.py`, `test_migration_0233.py`,
  `test_account_link_routes_guarded.py`, `test_observed_performance.py`, and
  the accept-invite, forgot / reset, User Management invitations, one-time
  link and Performance vitest suites.

- **Account and admin basics: user search and paging, restore an archived
  project, who changed a feature flag and why, and the CLI served by the
  deployment** (wave W17a, ADM-03, ADM-04, ADM-07 and DEV-02; branch
  `feat/df-accounts-admin-20261026`; migration 0232). **Users** pages 50 at a
  time with "1–50 of N", Prev / Next and a search box that matches the
  username, email or full name on the server (`GET /api/v1/users` takes `q`
  and returns the filtered total in `X-Total-Count`), instead of stopping at
  200; **Project access › Add Member** searches the server too, so any user
  can be found by typing. **Projects** has an Active / Archived switch for
  administrators: archiving now records when and by whom, and **Restore**
  (`POST /api/v1/projects/{id}/restore`, ADMIN) brings a project back with its
  members and runs while its API keys and default QA lead stay revoked (the
  confirmation says so); `GET /api/v1/projects?include_archived=true` lists
  archived projects for administrators. **Feature flags** shows when and by
  whom each flag last changed, takes an optional reason with a change (kept on
  the audit row) and lists a flag's changes under **History**
  (`GET /api/v1/feature-flags/{key}/history`). The deployment serves the
  `testlookup` CLI as a wheel (`/api/v1/sdk/cli/info`,
  `/api/v1/sdk/cli/<wheel>`), and the getting-started guide shows
  `pip install <deployment>/api/v1/sdk/cli/<wheel>` (from a clone when the
  deployment has none). Tests: `test_user_list_search_postgres.py`,
  `test_feature_flag_reason_postgres.py`, `test_project_restore_postgres.py`,
  `test_migration_0232.py`, `test_sdk_cli_wheel.py`, and the Users, Project
  access, Projects, Feature flags and getting-started vitest suites.

- **AI evaluation you can act on: candidate replays with per-item diffs, real
  gate verdicts, the agent-stack gate history, and a labelling queue for live
  tier pairs** (wave W16, AGT-09 and AGT-13; branch
  `feat/df-ai-eval-20261025`). Settings › AI evaluation now has six tabs:
  Overview, Datasets, Gate, Agent stack, Tiers and Reviewer. On **Datasets**,
  an administrator evaluates a candidate tier (Rules, ML when a model is
  trained, or the configured local model; a tier that cannot run says why) on
  a dataset: the run is replayed through the real engine in the background
  (queued, running with progress, then completed, or partial with its scored
  items kept when a worker is lost), and **Items** lists each item as correct,
  incorrect, unscored or error, with the expected and candidate outputs side by
  side as text (administrators only: a feedback-built dataset carries text from
  every project). A replay run can be **set as the baseline**, and **Gate**
  compares any completed run with the active baseline and shows the server's
  verdict and rules: PASS, FAIL or Not enough evidence (the tab no longer
  answers "No baseline configured" for every task, and the duplicate-detection
  gate now asks the agent that does the work). **Agent stack** runs every
  required gate at once and keeps a history whose rows open their manifest
  (checksum, prompt / model / routing versions); the history no longer includes
  other projects' tier comparisons. **Reviewer** shows the nightly
  reviewer-quality measurements read-only. **Tiers** (QA leads, one project)
  adds a labelling queue for live shadow pairs: the incumbent and candidate
  outputs side by side with a link to the run, labelled Incumbent / Candidate /
  Both / Neither (plus the failure category on a root-cause pair); a label is
  final, a root-cause pair whose test case has a person's verdict is labelled
  from it nightly, an unlabelled pair is deleted after 30 days (a new
  Retention › Preview purge row), and a tier comparison can include the live
  labels. Labels and comparisons are recorded in the activity log. One
  migration (`0231`): replay columns on `ai_eval_runs` and `ai_eval_baselines`
  with constant defaults (existing rows read as legacy label agreement) and a
  new `ai_eval_item_outputs` table.
- **Agent actions you approve: an Inbox queue, typed proposals in Suggest,
  and an Act mode whose approved actions run, are audited and can be undone**
  (wave W15, AGT-01, AGT-02, AGT-03 under owner decision D16; branch
  `feat/df-agent-approvals-act-20261025`). Inbox › Approvals gains an
  **Agent actions** source (QA leads): each waiting proposal shows its
  target, the proposing agent and run, whether its AI report is accepted, and
  why Approve is not offered yet; Reject takes a reason from the reviews'
  closed list (notes are stored redacted and never returned); **Recently
  decided** shows what happened — executed with a link to the result,
  acknowledged, rejected, undone, or "the outcome is unknown" (never retried).
  The run page's Decision panel shows each proposed action's status. In
  **Suggest**, the Investigator files typed proposals — quarantine a known-flaky
  offender, reassign a newly failed test to its suite owner, promote an internal
  defect for a concentrated regression — instead of text only (targets already
  handled are skipped; a newer investigation replaces older waiting proposals for
  the same target; Shadow files nothing). **Act** is now available for the
  Investigator (Settings › AI agents) with per-type opt-in; a person still
  approves each action, the AI report must be accepted first, and an approved
  action runs within a minute through one of four reviewed adapters (quarantine,
  reassign, internal defect — never Jira —, acknowledge), re-checking the payload,
  the approver's role and the target's project at execution; reversible actions
  offer **Undo**. A cleanup job settles an action stuck mid-run as "outcome
  unknown". The MCP server gains a read-only `list_agent_actions`. **Changes for
  existing installs:** the agent-action PATCH now refuses project API keys and
  synthetic accounts (the list keeps API-key access) and records an audit row and
  an activity event for every decision; the person who requested an
  investigation cannot approve the changes it proposes, nor accept its report
  when it proposes act-mode actions; a decision report's self-review refusal now
  applies only to act-mode proposals (it refused every requester before, because
  the lookup never matched); existing decision-report items become
  acknowledgeable; a `{code, message}` error now toasts as its sentence
  (it read "message: …"). No migration. Tests:
  `tests/test_agent_action_types.py`, `tests/test_agent_action_adapter_base.py`,
  `tests/test_agent_config_act.py`, `tests/test_agent_action_vocab_frontend_parity.py`,
  `tests/test_agent_actions_router.py`, `tests/test_investigator_action_plan.py`,
  `tests/test_investigator_structured_actions.py`, `tests/test_agent_action_executor.py`,
  `tests/test_agent_action_adapters.py`, `tests/test_agent_action_adapter_registry.py`,
  `tests/test_reap_stuck_agent_actions.py`, four `*_postgres.py`
  (`test_agent_actions_queue_postgres`, `test_investigator_proposals_postgres`,
  `test_agent_action_executor_postgres`, `test_agent_act_end_to_end_postgres`),
  `mcp/tests/test_mcp_agent_actions.py`, the Inbox / Decision panel / cockpit /
  Settings vitests, three `tests/ci-e2e/` specs (`inbox-agent-actions`,
  `agent-act-mode`, `investigator-suggest-proposals`) and two visual specs
  (`prod-agent-actions`, `prod-investigator-proposals`).
- **Agent operations: a pipeline's Audit and Compare, a graph for every
  pipeline shape, project default workflows, tracked bulk jobs, and a real
  Deep Investigation pre-scan, history, estimate and run configuration**
  (wave W14, AGT-05, AGT-06, AGT-08, AGT-12, AGT-07; branch
  `feat/df-agent-ops-20261025`). A pipeline's **Agent stages** (on
  `/agents` and the Run page's Evidence tab) have **Graph · Audit · Compare**
  tabs: Audit shows the replay document (checksums, checkpoints, route
  decisions, and the audit gaps of an older pipeline as data); Compare puts the
  pipeline beside another of the same run (the previous attempt by default)
  stage by stage. Every pipeline shape now draws as a graph: the built-in deep
  and live pipelines, investigations and forks draw their real stages instead
  of the offline canvas (which silently dropped 11 of the deep workflow's 20
  stages). A QA lead can make a published fork the project's **default
  workflow** per base (Settings › Workflow editor › **Use for new runs**); it
  is used by every trigger that names no workflow — including the automatic
  analysis after each ingest and **Trigger pipeline** on `/agents`, which ran
  built-in offline before —, and a default that no longer resolves runs the
  built-in and says why. **Choose workflow** on `/agents` and the Run page's
  ⋯ **Trigger pipeline** open a workflow picker; a draft version can be
  deleted. Bulk triggers are tracked **bulk jobs** on `/runs` with live
  progress, cancellation of the not-yet-started part, and **Re-trigger all
  matching** (the list's own filter, up to 2,000 runs); a bulk request now
  skips runs whose pipeline is already in progress, a repeated request is not
  queued twice, and bulk deep is ONE server request under the Settings › AI
  gate. Deep Investigation reads the server: the error-signature pre-scan,
  investigated-runs history, a measured cost / time estimate (from the
  project's completed deep runs; a local model costs $0.00; no sample reads
  "not measured"), and a run configuration (failure groups, a lower model
  tier, a cap, fewer evidence sources — tighten-only); **Run** is offered only
  when the server would accept it, and a second trigger answers "already in
  progress" instead of queuing another deep run. Also fixed: publishing or
  evaluating a forked workflow answered 500 (an expired row attribute after the
  evaluation's flush). Migration 0230 (`project_workflow_defaults`). Tests:
  `tests/test_pipeline_compare.py`, `tests/test_trigger_no_workflow_named.py`,
  `tests/test_bulk_trigger_job.py`, `tests/test_bulk_job_worker_skip.py`,
  `tests/test_pipeline_task_payloads_bind.py`, `tests/test_deep_run_overrides.py`,
  `tests/test_deep_gate_in_estimate.py`, `tests/test_workflow_default_service.py`,
  `tests/test_pipeline_admission.py`, `tests/test_agent_ops_vocab_frontend_parity.py`,
  ten `*_postgres.py` (incl. `test_workflow_publish_postgres.py`), the Audit /
  Compare / graph / picker / bulk / deep component and page vitests, five
  `tests/ci-e2e/` specs (`agents-audit-compare`, `agents-generic-graph`,
  `workflow-default`, `runs-bulk-job`, `deep-investigation-config`) and two
  visual specs (`prod-agent-ops`, `prod-agent-ops-b`).
- **Test library: filters that really filter, saved case views, library
  health from the server, CSV / Excel import, a case timeline, requirement
  links, editable suites** (wave W13, TCL-07, TCL-01, TCL-03, TCL-04, TCL-05,
  TCL-06; branch `feat/df-test-library-20261024`). On `/test-management` the
  Status, Type, Priority, Suite and Owner filters each take several values, all
  of them live in the URL, and the four presets (My drafts, **My P0/P1**,
  Unautomated, AI drafts) replace the filters instead of only highlighting;
  bulk selection follows the filters, the export takes the same filters (and
  adds ID, Suite and Description columns), and a filter the merged automation
  list cannot apply says the list shows authored cases only. Priorities read
  critical / high / medium / low everywhere; P0–P3 are accepted as aliases and
  any other priority is refused. Filter sets can be saved as **case views**
  (private or shared within the project). The health banner and Insights read
  one server answer over the whole library (no longer the first 200 cases); a
  QA lead sets the project's **automation target** there, and the flaky and
  ownerless cases are listed. **Import** takes a CSV (UTF-8) or Excel file of
  up to 2,000 rows, previews every row (create, duplicate, invalid with the
  reason) and creates only Drafts (TESTER and above). A case's **Timeline**
  tab merges its versions, reviews, comments, plan and automated executions,
  defects and requirement changes, newest first, with kind chips. Behind the
  `test_case_requirement_traceability` flag (on for new installs, off on
  upgrade until an admin turns it on), a case links requirement keys
  (verifies / partially verifies / related; the key is kept as typed and
  matched case- and bracket-insensitively), the list filters by requirement and
  Insights shows requirement coverage and the requirements no case fully
  verifies yet. `/suites`
  and a suite's page gain **Edit** (name, description, tags), each suite test
  shows when it last ran, and the Suites tab shows a suite with no run yet as
  **Empty**. Migrations 0228 (`projects.automation_target_pct`) and 0229
  (`test_case_requirement_links`). TCL-02 needed no change (W28 shipped bulk
  edits). Tests: `tests/test_test_case_list_filters.py`,
  `tests/test_test_case_export_filters_and_columns.py`,
  `tests/test_library_health_service.py`, `tests/test_test_case_import.py`,
  `tests/test_test_case_timeline.py`, `tests/test_requirement_traceability.py`,
  `tests/test_saved_case_view_schema.py`, `tests/test_test_library_vocab.py`,
  `tests/test_tm_suites_empty_rows.py`, `tests/test_suite_update_activity.py`,
  seven `*_postgres.py`, the `TestManagementPage` filters / health / import /
  timeline / requirements / suites-empty vitest files, the component and model
  tests under `pages/test-management/`, six `tests/ci-e2e/` specs
  (`suites-edit`, `test-management-filters`, `-health`, `-import`, `-timeline`,
  `-requirements`) and `prod-test-library.visual.spec.ts`.
- **Search: semantic and hybrid modes, filters in the search box, saved and
  recent searches on the server, a top-bar preview, Similar failures, search
  usage** (wave W12, SRCH-01, SRCH-03, SRCH-04, SRCH-05; branch
  `feat/df-search-20261024`). On `/search` the Tests scope offers **Hybrid**
  and **Semantic** (choosing one on another scope switches to Tests and says
  so); when the semantic index cannot answer, keyword results are shown with
  the reason ("Semantic index unavailable — showing keyword results.", or that
  a filters-only query needs words). The search box understands `status:`,
  `suite:`, `branch:`, `env:`, `type:`, `since:`, `release:` and `owner:`,
  shows each filter as a removable chip and says when a key is unknown, a value
  is invalid or a kind of result cannot apply a filter (the **Syntax** table
  lists the keys). Saved searches live on the server (no three-slot cap; share
  one within a pinned project; a browser's saved searches are moved there once)
  and recent searches follow the user (the last 20, kept 30 days). Typing two
  or more characters in the top bar previews up to five distinct matches. A
  failed or broken test result has a **Similar failures** tab, and a QA lead
  can **Re-index now** from the Searchable data card. QA leads see today's
  searches, zero-result rate and p95 latency; each search is recorded without
  its text (a keyed hash, lengths and counts, deleted after 30 days, and listed
  on the retention preview). Tests: `tests/test_search_query_parser.py`,
  `tests/test_global_search_filters.py`, `tests/test_search_semantic_fallback.py`,
  `tests/test_search_recents.py`, `tests/test_search_suggestions.py`,
  `tests/test_search_usage_service.py`, `tests/test_search_after_response_writes.py`,
  `tests/test_search_query_text_never_logged.py`, five `*_postgres.py`, the
  `SearchPage` / typeahead / Similar failures vitest files and seven
  `tests/ci-e2e/search-*` / `settings-embedding` specs.
- **The embedding model and the Chroma collection prefix take effect**
  (wave W12, SRCH-02; branch `feat/df-search-20261024`). Settings → AI →
  Embedding says which embedder is in use and which is configured; choosing
  another (the bundled MiniLM, or a local Ollama model — OpenAI is refused while
  offline mode is on) re-embeds every vector collection into new collections
  on the AI worker while search keeps the previous ones, then switches and
  drops the old ones ten minutes later; a failed or stalled re-embed says so and
  **Re-embed now** retries it. Settings → Storage's ChromaDB "Collection" field
  is now a **Collection prefix** (up to 8 lower-case letters, digits or `-`)
  applied to every collection name. Nothing changes on upgrade: every install
  keeps the bundled MiniLM and today's collection names until an admin changes
  one of these settings. Tests: `tests/test_embedding_function.py`,
  `tests/test_chroma_collection_naming.py`, `tests/test_chroma_collection_registry.py`,
  `tests/test_embedding_index_state.py`, `tests/test_embedding_generation_task.py`,
  `tests/test_settings_ai_embedding_change.py`,
  `tests/integration/test_embedding_generation_postgres.py`.
- **Release planning: copy phases, draft phases from a PRD, the schedule as
  ICS / CSV and a calendar, a release-health trend, attribution rules** (wave
  W11, REL-05, REL-07, REL-06, REL-03, REL-09; branch
  `feat/df-release-planning-policy-20261023`). A release with no phases offers
  **Copy phases from…** (another release of the project, dates moved to the new
  start) and **Draft from a PRD…** (paste or upload `.md`, `.txt` or `.docx`:
  the headings become typed phases you edit before anything is saved; the
  configured model is tried in LLM / auto analysis mode and the heading parser
  is always the fallback, with the reason shown). The Releases header exports
  the schedule as an ICS calendar file or a CSV and switches between List and a
  month **Calendar** of releases and phases. **Release health · this week**
  draws 12-week sparklines for in progress, ready to ship, blocked and released,
  with "GO on X of Y evaluated" and "N of M released with a pack". A new
  **Attribution rules** page (Releases ⋯) maps a run's branch, build,
  environment or tag to a release when the run names none, explains the order a
  run is linked in, previews which recent runs a rule would move, and a run that
  lands in no release says **Unattributed**. Tests:
  `test_release_phase_clone.py`, the PRD parser and drafter tests,
  `test_release_schedule_export.py`, the portfolio buckets, the attribution
  preview, four `*_postgres.py` suites, and the specs `releases-plan-phases`,
  `releases-schedule`, `releases-portfolio-trend`, `release-attribution-rules`.

- **Integration health per project, a webhook circuit breaker, and a Changes
  tab that says why** (wave W10, INT-04, INT-07, FAIL-09; branch
  `feat/df-integrations-runtime-20261023`). **Integration Health** lists every
  project's GitHub / GitHub Enterprise and GitLab integration as its own row
  ("GitHub · Payments", its API host, healthy / auth error / unreachable, Probe
  now, a link to its settings), probed every 15 minutes with that project's own
  token and base URL; a row that has never been probed reads "Not probed yet",
  never healthy; a QA lead sees only the projects they belong to; paused
  webhooks are listed too. **Outbound webhooks pause themselves** after N
  deliveries in a row end failed (default 10, set per subscription, 1-100; a
  retried delivery counts once, a refused target counts): the row shows
  PAUSED with the last error and a Re-enable button, the activity log records
  it, and the last editor and the project's QA leads are emailed;
  `OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS` admits a test receiver on a private
  host. The run **Changes** tab lists the commits since the base run from the
  project's own GitHub integration (the Suspects panel's range) and, when it
  cannot, says why: offline mode, no GitHub integration, the GitHub checks
  flag off, no commit hash on the run, or a weak base anchor. Tests:
  `test_project_integration_probes.py`, `test_integration_health_scope.py`,
  `test_integration_health_routes_scoped.py`, `test_webhook_circuit_breaker.py`,
  `test_regression_diff_commit_range.py`, `test_fetch_build_changes_per_project.py`,
  five `*_postgres.py` suites, the vitests of the three pages, and the specs
  `integration-health-projects`, `webhooks-circuit-breaker`,
  `run-changes-commits`.

- **Paper, a soft light theme, and one UI standard across the app** (owner
  request 2026-10-17, wave W29a, UIX-01, 02, 03, 07, 08, 04a, 10, 11, 13).
  **Paper** ("Soft light · low glare": a warm grey page, cards a step lighter,
  softer text and borders) is in the theme picker beside Lab, for long
  reading sessions; Lab keeps its look. Across every page: one colour per
  failure category and kind (the Overview donut, the Failures card, the kind
  badges and the AI category chips agree, with the category word as neutral
  text beside a coloured mark); one decision vocabulary, **Go / Conditional /
  No-Go**, and sentence-case statuses; one severity scale (P0-P3) for defects,
  priorities and investigations; warning, danger and info colours instead of
  borrowed status colours; one button shape (8 px corners, a small size for
  dense rows, a solid danger button); one date and number format; one look
  and wording for loading, empty and error states ("Could not load ...").
  Six new quality-gate guards keep it so (font sizes off the type scale,
  status colour as text on a tint, status hues used for other meanings and
  hand-made spinners may only go down; hand-styled primary buttons and the
  old error wording are refused).

- **Edit a test case and change its status in the page** (owner request
  2026-10-17, wave W28, TCM-01 to TCM-05). On `/test-management` a case's
  Details tab has **Edit**: every field in one in-page form, saved as one new
  version with who saved it and how (person, AI or API). If someone else saved
  first, the form says who changed what and offers **Reload theirs** or **Keep
  editing**; nothing is overwritten silently. A toolbar shows the status moves
  the reader may make, labelled by outcome (**Make active**, **Request review**,
  **Back to draft** …). A per-project setting, **Require review before a test
  case becomes Active** (Edit Project, off by default), decides whether a case
  goes Draft → Active directly or through today's review; with review on, a QA
  lead or admin may approve their own case with a reason, and a step, expected
  result or precondition edit of an approved case is marked "edited after
  approval". Select cases with the row checkboxes (or Ctrl+E / ⌘E) to change
  status, owner, priority, tags or suite in bulk, with a result per case. The
  History tab shows before → after for every version (steps step by step) and
  can **Compare with current** and **Restore this version** (content only; the
  status stays). A plan item shows the case version it ran (`v3 · case now
  v5`) and that version's steps. New routes: `POST
  /api/v1/test-management/cases/bulk`, `POST
  /api/v1/test-management/cases/{id}/restore`. Not built, on purpose: custom
  fields, a workflow designer, requirements management, Jira sync of cases,
  shared step libraries.

- **The chat keeps its suggested questions** (owner request 2026-10-17, wave
  W28, CHAT-01). During a conversation the starter prompts stay as a row of
  chips above the message box; a chip asks its question without clearing what
  you typed. **Hide suggestions** is remembered in this browser.

- **No page opens a popup any more** (wave W27b, PAN-05 to PAN-07, finishing
  the owner's in-page request). The test library (new case, plans, AI
  generate, strategy, suites, approvals, insights, citations, every reason
  prompt), trends notes and email, the activity event detail (`?event=`, Back
  closes it), the value-metrics methodology, the report catalogue panels,
  Customise and Fix tagging, projects, ownership, users, project access, API
  keys, billing, feature flags, project data and every settings page now open
  their forms and details where the action is, and every browser `confirm()`
  became a strip in the page with the same sentence. A generated API key or a
  reset password is shown once in the page, never in a notification. The
  `frontend.no-page-modals` ratchet is at **0** outside its reviewed
  allow-list (menus, popovers, the mobile sidebar, the Help drawer, a chart's
  full-screen view) and now also sees fixed full-height side panels; a crawl
  of 49 routes for every role checks that nothing opens an overlay. Every
  converted flow was pinned first by a characterization spec on the old
  dialogs, which passes unedited.

- **Details and forms open in the page, not over it** (owner request
  2026-10-16, wave W27a, PAN-01 to PAN-04). On `/releases` a release now
  opens beside the cards (the right rail folds while it is open) instead of
  in an overlay: the URL carries `?open=<id>`, so a refresh or a shared link
  reopens it and Back closes it. Its Edit and Link Run forms open inside it,
  New release and Sync releases under the health banner, and Delete, Remove
  phase and Set as active ask in a strip where the button is. On `/runs` the
  upload form and the failure signatures are sections of the page, and a
  run's test opens beside its Tests or Timing list (`?test=`). File defect,
  the decision trail, correct classification and the claim forms open where
  they are used. On `/failures`, `/defects`, `/flaky` (`?test=<fingerprint>`),
  the quarantine tabs, My failures and chat, a row's form opens right under
  its row and never widens the table. Escape closes the innermost form,
  focus goes back to the button that opened it, and a form with typed
  changes asks before it is discarded. The shapes come from three shared
  pieces (`MasterDetail`, `InlineFormSection`, `InlineConfirm`) that
  `/test-management` now uses too, unchanged; a new quality-gate ratchet
  (`frontend.no-page-modals`) only lets the count of page overlays go down
  (81 to 51 in this wave; the rest in W27b). Every converted flow was pinned
  first by a characterization spec on the old dialogs, which passes
  unedited on the new shapes: the requests are byte-identical.

- **Notifications say what they send; digests can follow a release or a view;
  quarantine leaves Experimental** (deferred-features wave W09, NTF-01 to
  NTF-04, FLK-01 to FLK-03). Settings › Email (SMTP) & channels gains a
  **Project notification policy** card (notify on every run, notify on state
  changes, which state changes, the consecutive-failure threshold), and My
  notifications says which events the selected project does not send instead
  of dropping them silently. **Quality gate failed** is now delivered: once
  per recorded change into NO_GO of a release or phase gate, an override, or
  the AI's published decision (never on a read; the bell links to the release
  or the release gate). Digests: **Per Release** names a release (and a run
  linked to it later also triggers the digest); a Daily or Weekly digest can
  follow a saved **report view** (its releases, suites and window), the
  preview shows what it is filtered by, and a deleted view is named rather
  than silently widened. `/flaky` gains a **Policy** tab (SLA, auto-defect,
  auto-release after N passes, detection threshold, the active-quarantine cap
  the Quarantined tab now warns past), a **Flakiness score (4 signals)**
  column with its confidence band and a sort, and the signals with their
  weights in the details panel. No new modal or browser confirm: every new
  form opens in the page.

- **Trends, Coverage and Value metrics tell their own story** (deferred-features
  wave W08, TRND-01, -02, -04, -06, -07, -08, -10 to -13). Trends can be sent:
  **Export PDF** for everyone and **Email report…** for QA leads, through the
  relay saved in Settings › Notifications. Its verdict, score and callouts now
  come from the server (`GET /api/v1/analytics/trend-summary`), suite rows gain
  sparklines and compare links, and anyone from QA engineer up can **pin a
  timeline note** to a day or a range (migration 0218) that shows on every
  chart of that window. Coverage shows when each suite last ran, a **History**
  tab of daily catalogue snapshots (migration 0217; a nightly job fills
  missing days), and the tests behind the untagged callout with a way to file
  them. Value metrics can be split **By team**, with the teams adding up to the
  headline. The coverage map pages through "Other" and counts the tests that
  need review. Charts can be customised and the choices saved with a view; the
  Explorer splits panels by branch, environment, status, failure category or
  ingestion source; and SVG export of the heatmap, coverage map, scatter and
  Sankey is a true vector with a light-background option.

- **Runs and failures answer more at a glance; a 14-day window everywhere**
  (deferred-features wave W07, WIN-01, FAIL-01 to FAIL-08, FAIL-10). Every page
  with a time window now offers **14d** (Summary, Defects, My failures, Value
  metrics, Activity and the Audit dashboard joined the others), so a fortnight
  chosen on one page is no longer reset to a week on the next. The Runs list
  fills its AI verdict column from one request for the runs on screen
  (`GET /api/v1/runs/ai-verdicts`, stored analyses only), and after each
  analysis a background task stores the run's summary so a fresh verdict shows
  without opening the run; builds are grouped by their dominant failure
  signature (`GET /api/v1/runs/failure-signatures`); search, branch and AI
  verdict filters live in the toolbar and the URL (migration 0214 adds the
  stored recommendation); Run Compare picks runs from a server search with
  "Paste run ID". On Failures a top-failing test expands to its latest error,
  stack head, owner and an "Open in run" link; a **Median time to fix** tile
  (`GET /api/v1/analytics/time-to-fix`) reads "Not measured" below five fixes;
  and QA leads can add **classification hints** ("error message contains …
  → Infrastructure", migration 0216) that classify new failures before any AI
  engine, while a person's correction still wins. Ownership gains **Java source
  roots** (migration 0215) so Java stack frames resolve to files and owners.
  Recommended actions list the reader's own role first.

- **Defects can be triaged where they are listed** (deferred-features wave W06,
  DEF-01 to DEF-07). Each row on `/defects` has a menu for a QA engineer:
  **Change status** (Open, In progress, Resolved, Invalid, with a note),
  **Assign** (a project member and a team) and **Link Jira issue**
  (`PATCH /api/v1/projects/{project_id}/defects/{defect_id}`, migration 0213
  adds the assignee). Offline the key is saved locally; with Jira reachable it
  is checked first and a status change is written back to the issue. A defect
  that is In progress still counts as open everywhere, so moving a CRITICAL
  one there does not unblock a release. The page has an **Owner** column
  (assignee, or the team with where it came from) and queue health counts
  ownership again; **Only this release** narrows the list to the defects that
  affect the release the way the release gate counts them; Open, P0/P1 and
  Time to resolve draw the window's history (`GET /analytics/defects/trend`);
  and an **Escape rate** tile (`GET /analytics/defects/escape-rate`: defects
  filed after a release they affect had shipped) says "Not measured" below five
  defects instead of 0 %. A promoted defect that needs approval now waits in
  **Inbox › Approvals › Defects** for a QA lead: Approve files the Jira ticket
  (or says it was not filed while offline), Reject needs a reason and closes it
  as Invalid. An effective promotion is announced as the outbound webhook
  `defect.promoted`. Also fixed on the way: the cluster duplicate check listed
  defect titles from every project; linking a defect to Jira from Integrations
  always failed; the Jira resolution webhook failed once two defects shared a
  key, and now closes and refreshes every one of them.

- **Choose the RAG faithfulness evaluator in AI settings.** While the
  `rag_faithfulness_gate` flag is on, **Settings › AI Configuration ›
  Knowledge-Grounded Generation** offers the single-prompt judge or Ragas,
  and `PUT /settings/ai` takes `rag_faithfulness_evaluator` (`ollama` |
  `ragas`; anything else is a 422). It could only be set by editing the
  `ai_config` row before, and saving any AI setting deleted it. The page and
  `GET /settings/ai` report the strategy that runs
  (`rag_faithfulness_evaluator_effective`) and why it differs from the choice
  (`_reason`): offline mode, from the environment or the stored setting, or
  a backend without the `ragas` package, which is not in `requirements.txt`.
  The gate now falls back to the single-prompt judge in that last case too,
  so a judged case is no longer labelled `ragas` when Ragas never ran.
- **Releases show the real gate, a real owner, and the active release**
  (deferred-features wave W05, GATE-03, REL-01, REL-02, REL-04, REL-08, REL-10).
  A release card no longer computes its own verdict from thresholds it made up:
  it shows the recorded release gate (Go / No-Go / Not evaluated, or "Not
  evaluated yet"), with Pass rate · Evidence · Runs, when it was evaluated, and
  the server's blocking reasons by test name. A QA lead can **Evaluate gate** on a
  card or **Evaluate all in progress**; the release page has a gate panel
  (scorecard, blocking list, comparison with the baseline release) and a live
  gate chip per phase. Completing or skipping a phase whose gate is not Go asks
  for a reason and records it in the activity log
  (`release_phase_gate_enforcement`, on for new installs, migration 0212). A
  release has an **owner** (a project member, set in Edit; new releases default
  to their creator, migration 0211) instead of the project's name. **Set as
  active release** and **Sync releases** (GitHub milestones / Jira fix versions)
  are in the UI; offline, sync says why it cannot run. The right rail lists the
  project's real compliance packs (`GET /api/v1/compliance-packs`, size and
  hash, Download; `release_compliance_pack` on for new installs) and its recent
  release activity from the activity log. Release-gate policies are one active
  per scope by database constraint (migration 0210 demotes duplicates, keeping
  the policy in force); a publish race answers 409.

- **Settings › AI does what it says** (deferred-features wave W04, AIC-01 to
  AIC-07, AGT-04, AGT-16). The analysis mode, Deep Investigation and Fine-Tuning
  toggles, the confidence threshold and the timeout are now read from the stored
  settings by every path, and each value on `GET /settings/ai` says whether it
  is set here or is the deployment default (`*_source`). A pipeline freezes the
  threshold and the timeout when it starts (`ai_config_snapshot`), so a resume
  replays the same numbers and a manual retry after a change starts a new run.
  The threshold decides triage in the pipeline that runs (the decision trail
  records "no analyses above confidence threshold 95 — skipping triage"); the
  timeout (now 5 to 1800 s) bounds every LLM call, an agent can only lower it,
  and a timed-out analysis records "LLM timed out after 5 s" as its fallback.
  Deep investigation answers 422 "Deep investigation is disabled in Settings ›
  AI" on all four ways to start one when it is off, and its buttons are replaced
  by a note. **The ML tier can train**: scikit-learn ships in the image, an
  ADMIN's **Train now** and a nightly retrain at 03:10 UTC
  (`POST /settings/ai/ml/retrain`, `GET /settings/ai/ml/status`: labelled
  samples against the 200 floor, model age and accuracy, the last run), and a
  trained model is published to every pod (`ML_MODEL_SYNC_ENABLED` defaults to
  true). **Billing** shows covered spend, overage, its charge at the overage rate
  and a month-end projection when a project has an included allowance
  (`llm_cost_budget` on for new installs, migration 0207). **Agent stages** are
  listed under Settings › Feature flags, grouped (migration 0208: the four
  read-only deep stages on for new installs, `defect_commander` off), and gap
  detection + report refinement run in deep pipelines behind the project flag
  `aiq_gap_refinement` (migration 0209, on unless `AIQ_GAP_REFINEMENT_ENABLED`
  says otherwise). The Agents page gains **Retry** (resume from the last
  successful stage, or a new run when the configuration changed) and **Stop**
  on each pipeline, and a failed deep investigation offers "Retry from last
  successful stage".

- **AI history: every AI analysis is kept, and compared with the previous
  run's** (deferred-features wave W03, AIH-01 to AIH-07). A re-analysis no longer
  overwrites the last answer: each pipeline stores a numbered version of the
  run's analysis digest and run summary (MongoDB `ai_run_versions`) and of each
  test result's analysis (`ai_analysis_versions`, migration 0205, with a v1
  backfill), with provenance (pipeline, mode, provider and model, prompt
  versions, fallback, trigger; a human correction is a version with its author).
  The run's **Analysis** tab shows a **History** control (pick a version to see
  its stored snapshot, or diff two: verdict, categories, root-cause clusters,
  per-test changes, the engine first) and a **vs previous run** card (the
  previous run of the same suite on the same branch: verdict change, new /
  resolved / persisting root causes, tests whose AI conclusion changed, a
  deterministic summary offline or the AI narrative when an LLM is reachable);
  Run Compare gains an **AI analysis** section and its AI report lists the AI
  changes; a test's History shows its AI category per run and where it changed,
  with each run's stored versions. API: `GET /runs/{id}/ai-history`
  (`/{version_id}`, `/diff`, `/export`), `/runs/{id}/tests/{test_id}/ai-history`,
  `/runs/{id}/previous`, `/runs/{id}/ai-compare`. Retention: AI history is on the
  runs clock, capped at 20 versions per run per kind and 10 per test result
  (migration 0206, set on `/settings/retention`; version 1, the newest and any
  version a published decision report cites are never pruned); the compliance
  pack carries `ai_history.json`; activity `analysis.completed`,
  `analysis.verdict_changed`, `analysis.report_superseded`. CLI `testlookup
  ai-history <run> [--diff 1..latest]`, `testlookup compare-ai <run>
  [--fail-on-verdict-regression]` (exit 8); MCP `get_ai_history`,
  `diff_ai_versions`, `compare_ai_with_previous`.

- **Execution time for tests, suites and runs** (deferred-features wave W02,
  EXEC-01 to EXEC-06): every report format and the live reporters keep each
  test's start, finish, setup / test / teardown split and worker
  (migration 0202); each run records its wall clock, summed test time and
  parallelism, and each suite its own (`suite_executions`, migration 0203,
  with a backfill). A run's **Timing** tab draws the tests on a timeline per
  worker, lists the 20 slowest against their baselines, the suites, workers
  and phases, and exports `run-<build>-timing.csv`; **Trends › Execution
  time** shows wall clock against test time per day, each suite's p50 / p95
  and its change, the slowest tests and the duration distribution
  (`GET /analytics/execution-metrics`, `.csv`); a suite's Charts tab plots its
  execution time per run. **Duration regressions** (migration 0204): a test
  slower than 3σ over its baseline of 10+ runs, or a suite whose last 5 runs'
  median is 1.5× the 20 before, is marked on the run, can fail an opt-in gate
  rule, and announces "became slower" once to subscribed channels
  (`perf_regression_detection`, on for new installs; an existing install
  keeps its setting). CLI: `testlookup runs timing <run>
  [--fail-on-regression]` (exit 7) and `testlookup analytics execution`; MCP:
  `get_run_timing`, `get_execution_metrics`. Where a report carries no clock,
  the UI says so instead of showing 0.

- **Release decisions from the run page, policy weights and share links**
  (deferred-features wave W01): a QA lead can Hold, Approve with conditions or
  Override to GO from a run's Analysis tab through the existing audited
  override (GATE-01); "Why this score?" shows the weights of the project's
  release-gate policy, from `GET /scoring-model?project_id=|run_id=` (GATE-05);
  Share report lets you choose layout and expiry, lists a run's links and
  revokes them (GATE-06).

- `NOTICE`, `THIRD_PARTY_NOTICES.md`, `SUPPORT.md`, `GOVERNANCE.md`,
  `MAINTAINERS.md`, `UPGRADING.md`, `RELEASING.md`, a pull-request template and
  issue forms; a configuration reference for every setting
  (`docs/operations/configuration.md`), a feature-flag catalogue
  (`docs/operations/feature-flags.md`), integration setup
  (`docs/operations/integrations.md`), one deployment guide per mode, and
  READMEs for the JavaScript, Go and Java reporters.
- A first admin account on a fresh install with a random password, shown once
  (release Compose / `install.sh`: `INITIAL_ADMIN_PASSWORD` in the generated
  `.env`, created by the `db-migrate` service; Kubernetes scripts: the
  `testlookup-initial-admin` Secret or the K3s credentials file). It must be
  changed at first sign-in.
- The single-node K3s path (`homelabsetup/`) reads every site-specific value
  from a local file named by `TESTLOOKUP_HOMELAB_ENV`
  (`homelabsetup/homelab.env.example`), renders a per-deploy overlay instead of
  editing the tracked one, and refuses to move an existing install to another
  host name.
- Quality-gate guards: `repo.no-local-only-paths`, `repo.no-private-markers`
  and `licenses.shipped-allowlist` (refuses GPL/AGPL/LGPL/SSPL/BUSL and
  non-commercial licenses for anything shipped).
- `backend/scripts/s3_setup.py` creates the object-store buckets with boto3
  and can copy every object from a previous S3 endpoint.
- Frontend unit tests block every real network request (`src/test/networkGuard.ts`:
  XMLHttpRequest, fetch and WebSocket); a test that makes one fails, naming the
  method, the URL and the test.
- **SAML sign-in works from the sign-in page.** `/sso/login-url` sends a real
  AuthnRequest (HTTP-Redirect binding), so the IdP's response carries
  `InResponseTo`; the IdP's browser POST to `/sso/acs` lands in the app through
  `/sso/callback` with a one-time code exchanged once (`POST /sso/exchange`),
  and returns to the page the sign-in started from. Tokens never ride in a URL.
- `GET /api/v1/auth/options` (public) tells the sign-in page whether to offer
  Register.
- An ADMIN can revoke any user's API key bound to the project (Streaming API
  keys page); every revocation writes an `API_KEY_REVOKED` identity event
  naming the actor and the key's owner.

### Changed

- **A search containing a known `key:value` now filters** (wave W12, SRCH-04).
  `status:failed checkout` used to search for that text; it now returns failed
  results matching "checkout" — on `/search`, on `GET /api/v1/search/global` and
  `GET /api/v1/search`, and in the MCP search tools. Unknown keys are still
  searched as text and reported in the response's new `parsed` block.
  `GET /api/v1/search` and `GET /api/v1/search/similar/{id}` now say why a
  semantic search fell back (`fallback_reason`) and whether the semantic index
  answered (`semantic_status`); Similar failures lists failed and broken results
  only.
- **The gate policy's pass-rate bands set the target everywhere, and the
  pages say which policy decided** (wave W11, GATE-04, GATE-02, GATE-08a,
  GATE-08b; behaviour change). Trends draws the policy's GO line (95% under the
  default bands, was a fixed 90%) with the No-Go zone shaded and names the
  policy; Live follows the bands; Overview's "How this verdict is decided" names
  the policy and the hard caps that forced No-Go (or the built-in rule when no
  policy is published); the release gate page names the band policy and
  version. The policy editor gains **Versions** (with who changed what),
  **Compare** and **New version from this**, and the simulator picks a run of
  the policy's project. `/release-gate` follows the release chosen in the top
  bar and shows that release's recorded verdict ("not evaluated yet" when it has
  none). Digests carry the release verdict line. Tests:
  `test_readiness_policy_in_summary.py`, `test_policy_history_names.py`,
  `test_digest_release_verdict.py` (+ Postgres), `test_email_release_verdict_line.py`,
  and the specs `policy-bands`, `policy-editor`, `release-gate-follows-release`,
  `digests-release-verdict`.

- **Integration settings saved in the UI now drive Splunk, OpenShift and
  legacy Jira** (wave W10, INT-01, INT-03; behaviour change for existing
  installs). The rule, everywhere: `AI_OFFLINE_MODE` overrides everything; a
  project's value beats the global one; a value saved at Settings ›
  Integrations beats the deployment environment; the environment applies only
  to a key never saved, and saving a field empty hands it back to the
  environment. Workers pick a saved change up within 30 seconds. The
  Integrations page now saves only the fields you changed (an earlier save may
  have copied environment values into the stored settings: re-save such a
  field empty to hand it back) and marks values that come from the
  environment. AI analysis attaches the Splunk log correlation and the
  OpenShift pod events server-side, before the model runs, so a small local
  model still sees them. A project's **Splunk index** is used in its log
  searches, and its **OpenShift namespace** for a run that names a pod but no
  namespace (run → project → saved default → environment; the run keeps the
  resolved namespace); both are validated when saved. The OpenShift pod lookup
  moved out of the ingest transaction into a worker task. Outbound webhooks
  (`outbound_webhooks`) start ON for a new install (an existing install keeps
  its setting). The run Changes tab and the build-changes tool read commits
  through the project's GitHub integration; `GITHUB_TOKEN` / `GITHUB_REPO` now
  feed only the deployment-wide GitHub probe. Migrations `0223`
  (integration health keyed by project) and `0224` (webhook breaker columns).
  Tests: `test_integration_runtime_resolver.py`,
  `test_integrations_put_keeps_env_fallback.py`, `test_splunk_ocp_tools_runtime.py`,
  `test_server_side_lookups.py`, `test_log_intelligence_uses_runtime_config.py`,
  `test_ocp_namespace_resolution.py`, `test_project_integration_field_validation.py`,
  the extended integration fake, and the specs `integrations-runtime-settings`,
  `projects-integration-fields`.

- **Lab is easier to read, and stays white** (owner request 2026-10-17, wave
  W29c, UIX-12). Muted text (labels, hints, captions) is darker and now reads
  at least 4.5:1 on every Lab surface (5.78:1 on cards, 4.75:1 on hover; it was
  3.4-4.15:1); the broken and skipped status colours are darker, so their text
  passes on the grey wells and stays distinct from failed; the olive chart
  colour no longer looks like the gold one beside it; cards and menus cast a
  soft, cool shadow instead of the dark themes' black one. Cards, the page,
  body text and the blue accent are unchanged, and no other theme changes.

- **The UI standard reaches the analysis pages and the charts** (owner
  request 2026-10-17, wave W29b, UIX-04b, 05, 06, 09). Failures, Runs, Run
  analysis, Flaky, My failures, Defects, Deep investigation, Coverage and
  Trends, and the shared components, use the type scale: no text under 11 px
  there. Charts share one text style in the page's font across every chart
  engine, solid gridlines and one colour for a single-measure bar; the
  failure-cluster bubbles are readable in Paper, and the failure groups'
  dates read "Sep 12, 2026". Tables share one look: sortable headers that keep
  their case, numbers right-aligned, a grey row hover. Panels share one
  header and every KPI is one tile (12 px card corners, no white sheens).
  Checkboxes and radios follow the theme. The test-management priority pill
  uses the one severity scale, the gate explanation says "No-Go floor", and
  the run-analysis banner states its decision once ("Overridden · proceed
  with mitigation"). On Deep investigation, labels that did not fit at
  1280 px are shorter, with the full name on hover (**Eligible**,
  **Sources**, **Pre-scan**, **Clusters**, **Analyzed**, **Confidence**).
  Two new count guards (hand-styled table headers, hand-made card surfaces)
  and two chart rules (font sizes only from the chart tokens, solid grids)
  keep it so. No API answer changes.

- **What W29a changes on screen.** Buttons have 8 px corners (they were
  pills); "NO GO" / "No-go" / "CONDITIONAL" read "No-Go" / "Conditional" and
  statuses read "Failed" rather than "FAILED"; a release's or a run's
  "conditional" is the warning colour, skipped results the skipped colour;
  the release rail's aging and awaiting-pack tiles use the warning colour
  (they were gold); badge text is the status colour mixed with body text, so
  it reads in every theme. A script or test that matched the old all-caps
  words needs the new ones. No API answer changes.

- **The homelab deploy cleans up after itself** (OPS-01, OPS-02).
  `homelabsetup/deploy-homelab.sh` now prunes old TestLookup images from the
  in-cluster registry after a healthy deploy, keeping the newest three builds
  (`--keep-builds=N`, `--skip-registry-prune`; `homelabsetup/prune-registry.sh`
  runs on its own too, with `--dry-run`), and its registry-mirror precheck
  now reads each node's real result and deletes its own debug pod: the check
  used to pass on every node whatever it found, and its pods were never
  removed.

- **What W28 changes on an existing install.** Migration 0222 adds five
  columns and no table. Every project starts with review OFF, so a Draft case
  can be made Active directly until a QA lead turns **Require review** on for
  that project. Editing a case no longer moves an approved or active case to
  Needs update. A VIEWER can no longer create, edit, comment on, request a
  review of, AI-generate or AI-review test cases through the API (403
  `Requires at least TESTER role`; a TESTER can); deleting a case needs
  QA_ENGINEER. Deprecate, Archive and Reinstate stay QA_LEAD or above. Masked
  parameter values are masked in the `/cases` and `/history` answers, and a
  PATCH that does not resend one keeps it. Link Suite links only approved and
  active cases unless "Include cases that are not Active" is ticked; deprecated
  and archived cases leave the duplicate queue, the evidence gaps and the
  Suites tab's count (they are still listed). A lead approving their own case
  with notes now succeeds (it was 403). The API-key snippet's comment and the
  Insights automation split (now adds to 100) are corrected.

- **CI runs the browser journeys in parallel** (CI-01). The hermetic journeys
  run as four sharded jobs and the chart visual regression as two, instead of
  one after the other in the docs job (85 + 40 minutes on one runner); each
  job stops after 60 minutes.

- **What W09 changes on an existing install.** `flaky_auto_quarantine` turns
  ON where nobody ever set it (migration 0219; a row an admin changed keeps
  its value, and the downgrade reverts only the row it flipped); nothing is
  quarantined until a QA lead approves a proposal. Stored "Flaky test
  detected" preferences become "Test newly flaky", the same event (migration
  0220). A Per Release digest without a release, a Per Suite digest without a
  suite, and a release or saved view of another project are now refused with
  422: such subscriptions never delivered. A Daily or Weekly digest with a
  saved report view is filtered by it. `GET /api/v1/notifications/history`
  items carry `link`, the flaky-coach entries carry `flaky_score`, and the
  quarantine policy carries `max_active_quarantined`.

- **Every LLM call is bounded by the AI timeout** (W04). Calls that had no
  limit (the decision-report and critic agents, flaky and test-health
  reasoning) now stop at Settings › AI "Timeout" (default 300 s) and fall back
  as a timed-out analysis does; a timeout counts towards the provider's circuit
  breaker. Triage in a pipeline follows the Settings › AI confidence threshold,
  no longer the summary agent's own `confidence_min`. An analysis records the
  configured provider and model, not the deployment's. The ML tier adds
  scikit-learn and SciPy to the backend image (about 190 MB).

- **"Compare to previous run" means the previous run on the same branch**
  (W03, owner decision D42). The run menu, the Changes tab and the Runs list ask
  the server (`GET /runs/{id}/previous`): the newest finished run of the same
  primary suite on the same branch, the rule Run Compare's precompute and the
  "vs previous run" card use. It used to pick from the 50 runs loaded in the
  page, on any branch. Run Compare's cached AI reports from before this release
  are regenerated once on first view (prompt `run_compare_v2`), and an
  `ai_analysis` row re-analysed by another model now names that model and time
  (the upsert kept the old ones), so value metrics count a re-analysed row in the
  month of its latest analysis.

- **A test case opens beside the list, not over it** (Test Management > Cases).
  The case view was a modal pinned to the top-right corner over a backdrop, so
  reading the next case meant closing this one first. It now docks beside the
  list (master-detail): the list stays visible with the open row marked, a
  click on another row swaps the case in place, previous / next step through
  the page ("3 of 25"), Expand gives the case the full width, and Esc or the
  open row's second click closes it. The open case is in the URL
  (`?case=<id>`), so a refresh or a shared link reopens it.
- **Default local model is `qwen2.5:7b-instruct`** (Apache-2.0), replacing
  `qwen2.5:3b-instruct`, whose Qwen Research License forbids commercial use.
- **Redis is replaced by Valkey 8** (BSD-3-Clause) in Compose, Kubernetes, the
  offline bundle and CI; settings and `redis://` URLs are unchanged.
- **The bundled S3-compatible server is versitygw** (Apache-2.0), replacing
  the AGPL-licensed `pgsty/silo` server and `pgsty/mc` client; the `MINIO_*`
  settings keep their names. Existing data must be copied over — see
  UPGRADING.md.
- **MongoDB is no longer shipped in the offline bundle**: it is an external
  prerequisite (SSPL-1.0) that air-gapped operators mirror themselves.
- **New installs start with the `manual_upload` feature flag ON** (migration
  0201). An existing install keeps its stored value.
- The Python and JavaScript reporters are licensed Apache-2.0 (were MIT), like
  the rest of the project; every package now carries the license.
- `install.sh` and `docker-compose.release.yml` use the canonical
  `github.com/test-intelligence/testlookup` and
  `ghcr.io/test-intelligence/testlookup`.
- **`install.sh` generates a production `.env`** (`gen-dev-env.sh --release`):
  `APP_ENV=production` and no passwordless dev-login, with the first admin
  created as above. The local/demo path (`make dev`, `make quickstart`) keeps
  dev-login.
- Kubernetes overlays inherit `DEV_AUTO_LOGIN_ENABLED=false` from `k8s/base`;
  before, every production overlay that did not set it refused to start.
- The demo seed reuses an existing `admin` account instead of failing on the
  username.
- `.env.example` pairs `SMTP_PORT=587` with `SMTP_TLS=false` and uses
  placeholder SMTP addresses.
- The bucket setup creates every bucket the backend writes to
  (`test-telemetry`, `knowledge-docs`, `training-data`, `pipeline-artifacts`,
  `compliance-packs`); the unused, publicly readable `testlookup-attachments`
  bucket is no longer created. Health checks accept both versitygw's `/health`
  and the MinIO-style route.
- `docker-compose.gcp-vm.yml` gives the backend a MongoDB URI with the
  credentials the MongoDB container requires.
- The run-history signature panel describes outlier builds from the data
  instead of a fixed sentence.
- The reference tooling is `scripts/generate_reference.py`,
  `scripts/check_reference_docs.py` and `scripts/test_reference_docs.py`
  (were `*handoff*`), and the maintainer guide moved from `docs/handoff/` to
  `docs/maintainers/`.
- Shell scripts with a shebang are executable in Git, so
  `scripts/deploy-k8s.sh`, `homelabsetup/deploy-homelab.sh` and the others run
  as documented on Linux and macOS clones.
- The frontend image installs its dependencies with `npm ci` (exactly
  `package-lock.json`). `npm install` could skip rolldown's optional
  linux-musl binary inside `node:alpine` (npm/cli#4828), and `vite build`
  then failed.
- **Self-registration is off by default** (`REGISTRATION_ENABLED=false`): while
  off, `POST /api/v1/auth/register` answers 403 `registration_disabled` and admins
  add people under Settings → Users. When it is on, a sign-up is a **Viewer**
  (it was QA engineer, which can create API keys).
- Server-side feature flags are evaluated for the project (and user) they are
  about, so a flag allow-listed to one project is on for that project on the
  server as in the UI; `is_enabled(..., any_scope=True)` is the explicit
  deployment-wide question.
- Rollout buckets hash with SHA-256 (were SHA-1): a flag at a partial rollout
  re-buckets its users once.
- **Every page loads about 14 kB (gzip) less JavaScript before it renders**
  (eager bundle 179,330 -> 165,363; the budget is 180,000). The MFA enrolment
  panel and its QR-code library load when enrolment starts; the reset-password
  and SSO-callback pages load when visited; the top bar's timestamps no longer
  pull in date-fns. `npm run check:bundle` now also fails if date-fns or
  qrcode.react reach the eager bundle again.
- **JWTs are signed and verified with PyJWT 2.15.1 instead of python-jose.**
  Tokens issued before the upgrade stay valid and every token type keeps its
  claims. One PyJWT default is deliberately not taken: it rejects an `iat`
  later than the API pod's clock, and `iat` comes from PostgreSQL's clock, so a
  database a second ahead would have refused fresh sign-ins; `iat` must still
  be a number. Code that caught `jose.JWTError` imports `JWTError` /
  `ExpiredSignatureError` from `app.core.security`.
- ChromaDB is reachable only by the backend and the Celery workers: Compose
  puts it on its own `chroma_net` network (internal in the release stack), the
  development stack publishes it on `127.0.0.1:8001` only and the release stack
  not at all, and Kubernetes admits it through a new `allow-chromadb`
  NetworkPolicy (the homelab overlay no longer lists it in
  `allow-data-stores`, which also admitted the MCP server and beat).
- Dependency upgrades from the Dependabot triage: DOMPurify 3.4.16 and KaTeX
  0.18.4 (web UI, via Mermaid; npm overrides), Vitest 4.1.11, PostCSS 8.5.28
  (also under `@tailwindcss/postcss`), undici 8.11.2, @babel/core 7.29.7,
  @humanfs/node 0.16.8, brace-expansion 5.0.12 and source-map-js 1.2.2; the
  docs Mermaid checker moves to Mermaid 11.17.2 with the same DOMPurify and
  KaTeX pins.

### Deprecated

- `verifySsl` in the JavaScript reporter. Node's built-in fetch has no
  per-request TLS switch, so it never had an effect; `verifySsl: false` now
  warns. Start Node with `NODE_EXTRA_CA_CERTS=/path/ca.pem` to trust a private
  CA, or `NODE_TLS_REJECT_UNAUTHORIZED=0` to skip verification.

### Removed

- **Slack "Default Channel" and project "Jenkins Job Pattern" fields** (wave
  W10, INT-02, INT-03b; owner decisions D21, D22). Neither did anything: an
  incoming Slack webhook is bound to one channel (route other channels per
  team in Team channels), and nothing read the Jenkins pattern. The API still
  accepts and ignores `slack_default_channel`; the stored Jenkins value is kept.

- Internal working material (run evidence, working notes, review registers,
  live probes against a private deployment, owner-specific CI and deploy
  pipelines, and three superseded copies of the user and install guides) is no
  longer part of the repository.
- Development-process material: the mutation scripts (`scripts/mutation_check_*.py`)
  and the tests that ran them (they rewrote source files while running), the
  test-intelligence roadmap, the phased ingestion design plan (the maintained
  page is `architecture/INGESTION_SCALE.md`), a load-test write-up and the `benchmarks/`
  results, with the `make benchmark*` targets that ran them.
- The owner-specific cloud deploy workflows (`deploy-eks.yml`,
  `deploy-gke.yml`, `deploy-aks.yml`) and `aws-infra-validate.yml`. EKS, GKE
  and AKS deploy with `scripts/deploy-k8s.sh --cloud=aws|gcp|azure`;
  `infra/aws/scripts/validate.sh` runs locally;
  [Deploy from CI](docs/operations/deploy-cloud.md#deploy-from-ci) describes a
  deploy job built on `require-verified-sha.yml` and the verified digest
  manifest.
- The generated source inventories under `docs/reference/`
  (`source-inventory.csv`, `inventory-counts.json`, `python-contracts.md`,
  `client-surfaces.md`, `api-contract-gaps.md`). The reference keeps the API
  documentation (OpenAPI, endpoint index, domain pages, schemas) and the
  settings, migrations, data-dictionary and worker-task pages.
- The `make k8s-stop-homelab` / `k8s-restart-homelab` targets, which called
  scripts that were never in the repository.
- `scripts/setup-minio.sh` (`make setup-minio` now runs the Compose
  `minio-setup` service), and the MinIO bucket-notification wiring it set up:
  the default ingest path never needed it.
- `TESTLOOKUP_UPLOAD_MODE` in the Python and Java reporters: it was read but
  changed nothing.

### Fixed

- The pre-release gate run without a candidate (Settings › AI evaluation ›
  Gate, the agent-stack gate and the nightly gate) no longer compares the
  legacy label-agreement metric with a replay baseline. Once a replay run was
  set as the baseline, the classification gate read FAIL ("Accuracy dropped
  8.3 %") because it compared two different measures; it now reads Not enough
  evidence and says why: gate a completed replay run against a replay
  baseline. Found walking W16 on the homelab (branch
  `fix/df-ai-eval-gate-kind-20261026`). Test: `test_eval_gate_candidate.py`.

- PII redaction no longer masks a run id as a card number. A random run id
  whose first three groups happen to be all digits
  (`05982379-5519-4289-a773-…`) read as 16 digits and was stored as
  `[REDACTED_CC]-a773-…`, breaking the link to the run; a real card number is
  still redacted. Test: `test_privacy_redaction.py`.

- On `/runs`, picking a branch and then an AI verdict before the list
  refreshed dropped the branch filter; both filters stay now (W29b).

- The homelab deploy's registry-mirror precheck and the registry prune's
  garbage collection failed on a Windows build host: Git Bash rewrote their
  in-container paths, so every node was reported "missing" and the deploy
  stopped. Both run as one `sh -c` string now (OPS-03).

- In the light theme the loading spinner's moving arc was white on white,
  destructive buttons were white text on a pale pink (1.26:1) and avatar
  initials were white on pale tints; all three are legible in every theme
  now (W29a). Five colour names the pages read were never defined, so those
  elements fell back to the browser default; they use defined colours now.

- Editing a test case so that its suite or owner changed failed with a 500 on
  PostgreSQL (the audit row stored a raw UUID in a JSON column); the audit
  values are now JSON-safe and carry no masked parameter value (W28).

- **A deploy no longer answers 504 for a few seconds.** The API and frontend
  pods now keep serving for 10 s after they are told to stop (until the
  ingress has dropped them) and get 45 s to finish their requests; before,
  a request routed to a stopping pod failed (reported on `/chat`).
- **Billing could overwrite a quota with defaults.** A failed quota read
  (anything but "no quota yet") showed the editor with default values, and
  Save wrote them over the real quota; it now shows the error with Retry and
  no Save, and a save refreshes the cached quota.
- **Add User's temporary password and Invite User's link were never shown**
  (the list refresh unmounted the form holding them), and the reset QA-lead
  password, promised "displayed once", was only copied to the clipboard: all
  three are now shown once in the page.
- Also found in W27b: a refused test-case retirement showed two identical
  toasts; after switching activity events, closing returned focus to the
  first one opened; the activity 403 message showed a literal `&apos;`; the
  value-metrics methodology was read again on a reopen.

- **Found while moving the dialogs into the page** (W27a). Deleting a
  release from its own page left its card on `/releases` until the 30 s
  poll; reopening File defect for the same cluster showed an empty form;
  the upload form's Release, Branch and Commit labels all pointed at the
  Build field; Escape inside Correct classification also closed the test
  panel behind it.

- **The RAG faithfulness gate reads the AI settings.** It imported a resolver
  function that does not exist and swallowed the error, so it always used the
  single-prompt evaluator and labelled each judged case with the environment's
  provider instead of the configured one. It now reads `get_effective_ai_config`:
  a stored `rag_faithfulness_evaluator` of `ragas` is honoured unless offline
  mode is on (from the environment or the stored setting), and the label names
  the configured provider. Ragas 0.2+ scores are read correctly, and a failed
  Ragas score now counts as 0, not a pass.
- **The Ragas faithfulness evaluator judges with the configured LLM, inside the
  cost cap.** It was given no model, so Ragas judged with its own default
  (OpenAI, from `OPENAI_API_KEY`), past the provider allowlist and the
  per-project LLM cost cap. It now gets `get_llm()`'s model through
  `BudgetedLLM.as_chat_model()`, so each of its calls is reserved against the
  cap like any other AI call, and a cap refusal is not retried. It scores on the
  request's event loop instead of blocking it in `ragas.evaluate`, and Ragas's
  usage telemetry is off. The default image still leaves Ragas out: it adds 26
  packages, among them pandas and pyarrow (about 360 MB), and needs fsspec,
  jiter and rich at lower versions than the default image runs. Build the
  backend with `--build-arg INSTALL_RAGAS=1` to install
  `backend/requirements-ragas.txt`, which pins that set. A
  Ragas install that fails to import now runs the single-prompt judge and labels
  the case with the provider that judged.
- **"Token revocation status cannot be verified" now says what failed.** It
  read "revocation store unavailable", and the log said "Restore Redis", even
  when Redis and the database were fine and the server had simply run out of
  free database connections. The message now names the cause: no free
  database connection (an overloaded server), the database unreachable, or
  the token cache unreachable; the log names the matching remedy.
- **Fewer requests hold two database connections at once.** Caching a
  freshly computed run summary (on the page, on refresh, or for an export)
  opened a second connection while the request still held its first, and a
  release override did its work in a second connection beside the request's.
  The summary paths now hand their connection back before the cache write,
  and the override uses the request's own.
- **The analysis mode set in Settings › AI no longer reverts** (W04, AIC-04).
  Thirty seconds after a worker read it, the mode fell back to the deployment's
  `ANALYSIS_MODE`, and a Redis expiry did the same after a day; a pipeline
  recorded the deployment's provider and model rather than the configured ones.
  The stored value is now the truth and Redis only a cache. Also fixed: the ML
  trainer queried a column that does not exist (`test_runs.started_at`) and
  never trained on a real database, and a resumed deep pipeline dropped its
  `defect_commander` setting.

- **"Trigger pipeline" re-analyses a run again** (W03). The API answered
  "Pipeline queued" and recorded the request, but the worker dropped the task as
  a duplicate for two hours after any analysis of the run. A person's trigger
  now runs, including one right after their previous pipeline finished (a
  double submit while the first is still queued collapses, for up to two
  minutes, and automatic triggers keep collapsing behind the run's two-hour
  key).

- **Drafting a defect from a cluster no longer ties up a database
  connection while the AI writes.** The draft request kept its connection
  for the whole LLM call, which also needs a connection for its cost check;
  with three per worker, a few drafts at once could stall other requests.
  The connection is now handed back before the call and taken again after.
- **A re-analysed run shows each failure cluster once.** Every analysis keeps
  its own cluster set (investigations refer to those rows), so a run analysed
  twice listed each cluster twice on the run page, in the release council,
  digests, notifications, compliance packs and chat; "defects grouped" and
  the hours-saved figure counted them twice; and actions on a cluster, such
  as promoting it to a defect, failed with "multiple rows". Every reader now
  uses the run's newest analysis; the older sets stay for history.
- **Placeholders that pretended to work now work, or say what is true**
  (deferred-features wave W01, branch `feat/df-honest-ui-20261014`):
  - Test plan items record their execution: the buttons sent POST to a
    PATCH-only route and always got 405 (HON-01).
  - The MCP `get_dashboard_metrics` and `check_release_readiness` tools read a
    key the API never returns (every KPI 0) and invented their own thresholds;
    they now show the server's values and policy verdict, and the readiness
    tool can read a release's stored gate decision (HON-02).
  - `/live` "Connect a test runner" snippets call the JavaScript and Go SDK
    functions that ship, checked by a symbol list and the quality-gate guard
    `frontend.sdk-symbols-fresh` (HON-03).
  - Value Metrics Export downloads with the session token (it saved a 401 body),
    and the export refuses a project the caller cannot read instead of
    answering with an empty file (HON-04).
  - Each failure's Suspects panel attributes commits against that test's own
    failing run, not the project's newest failed run (HON-05).
  - The "Pipeline / How this was decided" timelines on seven pages show the
    records' own times instead of invented 45-second steps, and no longer show
    invented confidence percentages (HON-06).
  - Copy that promised missing features is corrected: the Overview verdict
    describes the readiness band, compliance packs are "tamper-evident", the
    test-case search matches title, tags and ID as its placeholder says, the
    Defects "Escape rate" is "Unlinked to Jira", and Releases no longer offers
    an org template (HON-07).
  - Search "Index health" becomes "Searchable data": keyword rows per entity
    and one semantic-index line that says "not configured" instead of "Down"
    when no ChromaDB answers (HON-08).
  - Unknown URLs show "Page not found" in the app shell instead of silently
    opening Overview; the Releases audit-log link (and old `/audit` bookmarks)
    open Activity filtered to releases (HON-09).
  - The Trends pass-rate delta is in percentage points, as labelled; the Value
    Metrics hours-saved headline follows the window picker (TRND-05).
  - Five stale documentation statements are corrected and `GET /releases`
    returns one shape (HYG-10).

- **AI defect drafts are saved again, and a failure is logged.** Most runs
  never got their LLM-written defect candidates. The model sometimes wrote a
  ticket field (usually the description) as an object rather than text; the
  database refused that row; and because a run's candidates were saved in
  one commit, the rejection discarded all of them without a log line. A run
  analysed twice also lists a cluster twice, and its lookup failed. Ticket
  fields are now stored as text, clipped to their columns; each candidate is
  saved on its own, and one that cannot be saved is logged as a warning; a
  repeated cluster uses its newest row and is built once.
- **Opening several runs' Analysis tabs no longer stalls the backend.** The
  tab's failure-cluster and findings requests (and the deep-investigation
  trigger and promote actions) opened a second database session while the
  request's own session was still open, so each needed two of a worker's
  three connections. A few at once left a worker waiting 30 seconds and
  answering 500. They now use the request's session.
- **No more random "Token revocation status cannot be verified" errors.**
  Opening a run's AI summary with nothing cached wrote its defect candidates
  with up to five LLM calls inside the request, holding two database
  connections throughout; each call's cost check took a third. A worker has
  three, so a few runs opened together left every other request waiting
  30 seconds for a connection, and the token check, the first to give up,
  reported the revocation store as unavailable. The summary now answers at
  once with the rule-based candidates (`defect_candidates_pending: true`)
  and writes the LLM ones in the background, one at a time per worker; the
  next view shows them. A summary with pending candidates is not cached.
- **The air-gapped OpenShift overlay's datastores are reachable.**
  `k8s/overlays/openshift-artifactory` runs PostgreSQL, MongoDB, Redis and the
  object store inside the namespace, under the base `default-deny-all`, but had
  no policy letting anything connect to them, so the workers waited forever
  for Redis and the migration Job and the API could not reach the database.
  The allow now lives in a shared component,
  `k8s/components/colocated-data-stores`, which the overlay and the
  single-node K3s overlay both include. It is also narrower than the K3s
  overlay's own copy: the backend and the workers reach each store on its own
  port, beat reaches Redis only, and the MCP server reaches none (it never
  connected to them).
- **OpenShift's API Route reaches the backend.** Both OpenShift overlays
  publish a `testlookup-api` Route to the backend, but the base
  `allow-backend` policy did not admit OpenShift's router, so the Route never
  reached it. The frontend, backend and MCP allows now admit the router by
  its `openshift-ingress` namespace and by the namespace label
  `policy-group.network.openshift.io/ingress`, which also covers a router on
  the host network (`HostNetwork` endpoint publishing).
- **Commit links go to the project's own GitHub.** The commit range and the
  suspect commits on **Failure Analysis** always linked to github.com, even
  when the project's GitHub integration names a GitHub Enterprise server. They
  now use the integration's API base (enabled or not), so
  `https://ghe.corp/api/v3` links to `https://ghe.corp/<repo>/commit/<sha>`.
  An API base that is not an http(s) URL shows the SHA as plain text instead
  of an unusable or unsafe link.
- **The Anthropic provider works.** `LLM_PROVIDER=anthropic` was accepted,
  offered in **Settings → AI Configuration** and priced, but its LangChain
  package was never installed, so every AI call failed with
  `ModuleNotFoundError`. `langchain-anthropic` 0.3.22 (MIT) is now in
  `backend/requirements.txt`, and a regression test checks that every
  LangChain package the backend imports is declared there.
- **Chroma's vectors survive a restart.** `chromadb/chroma` 1.x writes to
  `/data`, but the volume was mounted at `/chroma/chroma` (a Chroma 0.x
  setting it ignores), so search vectors lived in the container and vanished
  when it was recreated. Compose and Kubernetes now mount the volume at
  `/data`, on a fresh volume or sub-path so 1.x never opens the old 0.x
  files, and the Compose healthcheck no longer calls `curl`, which the image
  does not ship. After upgrading, reindex search and re-sync knowledge sources
  ([UPGRADING.md](UPGRADING.md)).
- **The pytest plugin authenticates with an API key.** It sent every
  credential as `Authorization: Bearer`, and the server reads API keys only from
  `X-API-Key`, so a `qai_` key was refused with 401 before the first test. A
  `qai_` credential is now sent as `X-API-Key` whichever option carries it;
  `--testlookup-api-key` and `TestLookupReporter(api_key=...)` are new.
- **The pytest plugin applies the configured suite and release**
  (`testlookup.suite`, `testlookup.release` and their variables).
- **Every `TESTLOOKUP_*` variable the Python reporter maps works the same in
  `TestLookupReporter`, the pytest plugin and `LiveStream`.** The TLS settings
  (`TESTLOOKUP_CA_CERT`, `TESTLOOKUP_INSECURE`), commit and framework applied
  only to `LiveStream`; build, branch and launch not to `session()`; release
  not to `LiveStream`. `__version__` now matches the package (1.1.0).
- **The Jest reporter records each test once.** Jest 27.4 and later call both
  of its hooks, and most tests were recorded twice.
- **The JavaScript package loads with `require()`.** Its exports offered only
  an `import` condition for a CommonJS build, so Jest and Mocha could not load
  `testlookup-reporter/jest-reporter` or `.../mocha-reporter`; the Mocha
  reporter, a `default` export, also crashed the run ("not a constructor").
  Both now load by package name; the Mocha module's `.default` still works.
- **The JavaScript reporters accept an API key** (`apiKey`,
  `TESTLOOKUP_API_KEY`) and read `testlookup.properties` / `testlookup.yaml`
  and the canonical `TESTLOOKUP_*` variables.
- **The JUnit 5 extension records results.** On JUnit 5.11 it recorded none
  (its result callbacks read a store JUnit had already closed); it also opened
  one session per test class and reported every duration as 0. It now uses one
  session per test run and measures each test.
- **The TestNG listener and the JUnit 5 extension read build, branch, commit and
  launch from `testlookup.properties`**, not only from JVM properties and
  environment variables.
- **The Java reporter trusts a private CA or skips verification** with
  `TESTLOOKUP_CA_CERT` / `TESTLOOKUP_INSECURE` (or `testlookup.ca_cert_path` /
  `testlookup.insecure`, `Builder.caCertPath()` / `insecure()`).
- **Credentials resolve the same way in every reporter**: an explicit token or
  key wins, then a JVM property, an environment variable, the config file; an
  API key beats a token from the same source. The Java reporter let any API key
  in `testlookup.properties` override an explicitly passed token.
- **UI controls match the API's role checks**: release-gate policies, deleting
  releases (and removing phases or unlinking runs) and the audit CSV export are
  ADMIN; saving SMTP is ADMIN (QA leads see it read-only); deleting a suite,
  overriding a release-gate decision and muting a failure are QA lead; filing
  Jira from Failures is QA engineer. Each was offered to roles the API refused.
- The SAML SP metadata no longer advertises `/sso/slo`, which has no handler.
- The retention activation prompt honours its kill switch,
  `retention_activation_nudge`.

### Security

- **pypdf 6.21.2.** The backend's PDF text extraction (knowledge RAG) moves
  from 6.19.0 (CVE-2026-115572, CVE-2026-115580, CVE-2026-115585: long runtimes
  on crafted PDFs).

- **Compliance packs are signed and can be verified** (wave W11, REL-11;
  owner decision D14). A new pack carries an Ed25519 signature
  (`signature.json`, outside the manifest) from a deployment key created on the
  first signed pack; its private key is encrypted at rest and never shown. The
  pack panel says **Signed · key**, **Verify** re-checks every file and the
  signature, and **Public key** downloads the PEM. `testlookup compliance-pack
  verify pack.zip --public-key key.pem [--require-signature]` verifies a pack
  offline and names any changed file (exit 1); the MCP tool
  `verify_compliance_pack` does the same. Admins rotate the key through the API;
  a retired key's public half is kept, so older packs still verify. A pack made
  before this change, or while the key cannot be read, says "unsigned
  (tamper-evident only)". Migration `0225`; the CLI now depends on
  `cryptography`. Tests: `test_compliance_signing.py`,
  `test_compliance_pack_verify.py`, the routes, the CLI and MCP tests,
  `test_compliance_pack_signing_postgres.py` (first-key race, rotation), the
  spec `compliance-pack-verify`.

- **Legacy Jira tickets respect offline mode, and integration hosts are
  checked** (wave W10, INT-01, INT-03). The AI panel's "Create Jira ticket"
  (`POST /api/v1/integrations/jira`) and the triage stage's Jira tickets had no
  `AI_OFFLINE_MODE` check: they now refuse offline, disabled, unconfigured or
  blocked targets with 503 and a sentence, before anything is staged. A
  loopback, link-local or unspecified Splunk, OpenShift or Jira host is refused
  when saved (422 "Target host is not allowed") and again before any request
  carries a credential (private, RFC 1918 hosts stay allowed for on-premises
  servers). A project's Splunk index must be a plain index name, so it cannot
  inject search commands. Tests: `test_legacy_jira_offline_gate.py`,
  `test_url_safety_egress.py`.

- **DOMPurify 3.4.17.** The frontend's and the Mermaid check's DOMPurify
  override moves from 3.4.16 to 3.4.17 (GHSA-cg2g-xh64-v4q7, critical: a
  SAFE_FOR_TEMPLATES bypass; GHSA-mrv2-3pqr-j5jr, high: IN_PLACE hook removal).

- **aiohttp 3.14.4 and follow-redirects 1.16.1.** The backend's aiohttp
  (CVE-2026-111064, CVE-2026-111066: denial of service) and the frontend's
  follow-redirects under axios (CVE-2026-111755, CVE-2026-111756: information
  disclosure) move to their fixed releases; aiohttp 3.14.4 needs yarl 1.25.1.

- **Digest subscriptions check every id they are given** (W09). Creating or
  editing a subscription accepted any `saved_view_id`, and editing accepted
  any `scope_value`, including another project's view or release; both are
  now judged against the subscription's own project (one 422 text each) after
  the project-access check, with a regression test.
- **Slack and Teams digest text escapes names** (W09). Test names, action
  items and the teams' flaky-debt drafts went out as raw markup, so a test
  named `<!channel>` pinged the channel; every line is now escaped once.

- No deploy script, seed or test carries a default admin password any more;
  every path generates one or requires it from the environment.
- Example configuration uses placeholders only (`qai_your_api_key_here`,
  `testlookup.example.com`).
- Security reports go through GitHub private vulnerability reporting
  ([SECURITY.md](SECURITY.md)).
- **SCIM user filters can no longer stall a request.** The `userName eq ...`
  parser backtracked quadratically on a long run of spaces (8,000 took
  ~110 ms); it is linear now and accepts exactly the same filters.
- The theme-token check (`npm run check:theme`) reads `--token : value` and
  no longer lets `--status-passeds` answer for `--status-passed`: a `\s` in a
  template literal had silently become the letter `s`.
- Triaged the open CodeQL high alerts: the SCIM and theme-token fixes above,
  an e2e spec's URL regex that now escapes every metacharacter, and the GitHub
  hostname fix below; the rest are false positives or test-only code, with
  dismissal reasons in the pull request.
- Public GitHub is recognised by the API base URL's hostname. The Fixer's clone
  host and commit links matched the substring `api.github.com`, so a base such
  as `https://api.github.com.example.net` was taken for github.com (CodeQL
  incomplete URL substring sanitization). A base typed without a scheme
  (`api.github.com`) still means github.com; that check had turned it into
  commit links like `api.github.com/owner/repo/commit/<sha>`. Other scheme-less
  bases are left as typed, so the Fixer never sends its clone token to a host
  the API calls refuse.
- Triaged the 44 open Dependabot alerts (2026-10-12). **python-jose is gone**
  (GHSA-3qf3-8w2g-rqmx, critical, no fixed release): the backend uses PyJWT
  2.15.1, and the quality gate's `deps.no-python-jose` guard refuses jose in a
  manifest, an import, or a test stub. Every npm alert with a fixed release is
  upgraded (DOMPurify, KaTeX, Mermaid, PostCSS, Vitest, undici, Babel, humanfs,
  brace-expansion, source-map-js).
- **chromadb** (GHSA-f4j7-r4q5-qw2c, GHSA-36p7-vc44-83pf, GHSA-2wm9-hf6c-p5cr,
  GHSA-xph7-9rjv-w5fr; no fixed release) stays at 1.5.9. The advisories are in
  chromadb's Python server and its auth providers; the deployments run the
  `chromadb/chroma` image, a Rust server with no Python and no authentication,
  and the backend uses the package as a client. The client does build each
  collection's embedding function from the configuration the server returns,
  so whoever can write to Chroma can redirect the backend's embedding calls;
  Compose and Kubernetes now let only the backend and workers connect to it
  (see Changed). Before this, both Compose stacks published it on every host
  interface.
- **langchain 0.3.30, langchain-openai 0.2.12 and langgraph 0.6.11** stay
  pinned until the langchain 1.x migration: the advisories against them
  (GHSA-gr75-jv2w-4656 file-search middleware and prompt/chain loaders,
  GHSA-r7w7-9xr2-qq2r image token counting, GHSA-g48c-2wqr-h844 checkpoint
  msgpack loading) are in features TestLookup does not call; it loads no
  prompt or chain files, counts no image tokens and compiles its graphs without
  a checkpointer.
