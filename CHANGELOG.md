# Changelog

All notable changes to TestLookup are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may
contain breaking changes; [UPGRADING.md](UPGRADING.md) lists them).

## [Unreleased]

### Added

- Release verification: journey **TL-AUTH-003** (one account per browser profile, P1, disposable stack) checks the D-0019 fix on the live stack. In two tabs of one browser, a sign-in as a viewer moves the admin's tab onto the viewer with the note, a new tab opens as the viewer, and the viewer's sign-out signs the other tab out (`frontend/tests/release/session.spec.ts`, `e2e-runner/catalogue.yaml`).

### Changed

- Wording: the app, the in-app guide, the README and the user guide say "release" where they said "ship". Home's verdict banner reads "release cleared", "review before release" or "release blocked by unresolved failures"; Run intelligence reads "release cleared" or "release blocked"; the Releases page counts "Ready to release", says a GO release "is ready to release", and its "Shipping this week" panel is "Due this week". The escape rate counts defects filed after a release "went live". The README tagline is "Know why your software tests fail. Know when your release is ready." API fields keep their names (`ready_to_ship` is unchanged).
- Releases: the "Offline bundle" workflow takes a release version (`gh workflow run offline-bundle.yml -f version=X.Y.Z`) and bundles the image digests that release promoted instead of rebuilding them, refusing if they are not CI's images of the tagged commit. Every bundle run also produces `SHA256SUMS` for `install.sh` and the files it downloads.
- Tests: real-Postgres tests now cover failure attribution's previous-run outcome (D-0004), the suite trend's day counts, flaky-score upserts, live-run finalisation and the failed-run list behind "re-analyse all". A Skip-AI upload is driven through to a scripted model and makes no model reservation (D-0006). Every native producer report in `e2e-runner/fixtures/` is detected and parsed to its exact tests in the backend job, not only at release time. `backend/tests/integration/test_attribution_baseline_postgres.py` and five neighbours, `backend/tests/regression/test_native_producer_reports_parse.py`.
- Tests: security tests pin that an expired, deactivated or orphaned API key is refused on reads, ingest and streaming; that replaying a refresh token revokes the user's whole token family; that SAML sign-in reads claims only from the element the identity provider signed and refuses SHA-1; that a project role gate follows the project membership and always sits next to a project access check; that no route's role floor drops without a reviewed snapshot change; that no XML or `.xlsx` upload can read a local file or expand entities; that a member of one project reads and writes nothing of another through path, query or body ids; and the CSP and Referrer-Policy values. `backend/tests/test_security_*.py`, `backend/tests/integration/test_security_*_postgres.py`.
- Tests: the run list, run detail, run tests and keyword search reads have a fixed number of SQL statements whatever the page holds; every page-size parameter is bounded and a raised bound needs a snapshot change; every Celery task's queue is one the supported topologies run; two concurrent uploads of one build end as one run with one row per test; and the five most-opened pages make exactly their expected requests on first load. No test measures time. `backend/tests/test_perf_*.py`, `backend/tests/integration/test_perf_*_postgres.py`, `frontend/tests/ci-e2e/perf-hot-pages-request-inventory.spec.ts`.
- Tests: browser journeys load the every-role pages as a tester and as a viewer (no refusal toast, no action offered below its API floor), walk Live executions and Getting started, pin the health banner and the empty-state wording, send a tester away from all 33 management paths, open the remaining management pages as a QA lead, and show "—" for an all-skipped run on a release's linked runs, Run compare and the Agents live card. `frontend/tests/ci-e2e/role-crawl-tester-viewer.spec.ts` and five neighbours.
- Tests: the CLI's `ci-verdict` gate (every page read, fail-closed on API errors and on a stuck run), its `auth` commands and its one-time token refresh, the Python SDK's live-batch retry limits, the JS SDK's CI-context detection, the Go SDK's session lifecycle, and the MCP write tools' dry runs, bearer-claim checks and tool catalogue now have tests. `cli/tests/test_ci_verdict_command.py`, `client/js/tests/ci-context.test.mjs`, `client/go/testlookup/session_test.go`, `mcp/tests/test_mcp_tool_catalogue_snapshot.py` and five neighbours.
- Release checks: the PostgreSQL integration job runs the eleven new Postgres test modules, and two release-phase tests that need the database (`backend/tests/test_release_activation_event.py`, `test_release_phase_ledger_events.py`) but were missing from its list, so they never ran. The twelve chart-data comparability tests (`test_chart_data_comparability_postgres.py`), which skipped in that job because they required `fakeredis`, now use the job's Redis and run. `backend/tests/regression/test_every_dsn_test_runs_in_ci.py` fails when a test that needs the database is missing from that list, unmarked, or skipped there for a package the lock does not ship.

### Fixed

- Suites: a suite's **Charts** tab waits for both of its reads before drawing. When the per-day trend answered after the suite's details, the test-by-run heatmap loaded at once, before you scrolled near it, and the two charts then appeared above it and pushed it down the page. It now loads when you scroll near it, below the charts.
- Signing in as a different user, or signing out, in one tab now applies to every open tab of the same browser. A tab left on the previous account reloads (with a note saying why, and losing any unsaved edits of that account) instead of showing that account and sending its requests, and it can no longer put the previous account back for the next tab or reload. Signing in on a browser that already holds a session now loads the new account's details, not the previous account's. (D-0019, `frontend/src/store/authStore.crossTab.test.ts`, `frontend/src/services/api.explicitBearer.test.ts`, `frontend/tests/ci-e2e/auth-account-switch-tabs.spec.ts`)
- Settings no longer lists **MFA & lockout** and **Retention & purge** for a QA lead. Both pages are for administrators, and a QA lead who followed either link saw only "Admin access required". The MFA page also no longer reads the policy for a non-administrator. (D-0017, `frontend/src/components/layout/settingsNav.test.ts`, `frontend/tests/ci-e2e/settings-admin-only-items.spec.ts`)
- Inbox: reassigning a failure by hand (in the Inbox, through the API or the MCP `assign_failure` tool) is now recorded in the project's Activity log, as `Failure <test> reassigned from <A> to <B>`. Re-choosing the current assignee records nothing, and an agent's reassignment stays one `agent.action_executed` entry. (D-0021, `backend/tests/regression/test_failure_reassign_writes_activity.py`, `backend/tests/integration/test_activity_failure_reassign_postgres.py`, `frontend/tests/ci-e2e/inbox-reassign-activity.spec.ts`)
- Audit dashboard: a report export or share-link view is listed once under **All categories** (as a **report** event), not also as an **access** event, so the event count no longer counts it twice. The count is now the real number of matching events (it stopped at 100 per source), pages hold every event, the **report** category honours the actor filter and redacts event details like the other categories, and an action is a report event only when it starts with `report_`. (D-0018, `backend/tests/regression/test_audit_unified_feed_counts_once.py`, `backend/tests/integration/test_audit_unified_feed_postgres.py`, `frontend/tests/ci-e2e/audit-feed-counts-once.spec.ts`)
- Ask AI no longer says it cannot give links, or tells you to search for a build or test by hand, when its answer names them: it is now told that each build and test it names from its lookups is shown as a link under the answer, and points you to those links (the `chat_system` prompt is version 5, with a fresh prompt attestation). (D-0010, `backend/tests/test_chat_agent_stream.py`, `frontend/tests/ci-e2e/chat-conversation.spec.ts`)
- Runs: opening a run that does not exist, or that belongs to a project you are not a member of, now shows **Run not found** with no tabs and no actions. It used to show an Analysis tab that said it could not load, yet offered **Analyze failures**, **Trigger pipeline**, **Deep investigation** and **Refresh AI analysis** (the server refused each). While a run is loading, its actions are disabled. (D-0023, `frontend/src/pages/RunDetailPage.tabs.test.tsx`, `frontend/src/pages/DeepInvestigationPage.run.test.tsx`, `frontend/tests/ci-e2e/run-actions-inaccessible.spec.ts`)
- Runs: a run's **Evidence** tab no longer says no agent pipeline has analysed the run when you open the run's link with another of your projects selected. It lists the run's own pipelines, and a pipeline list that fails to load says so instead of claiming the run was never analysed. (D-0020, `frontend/src/hooks/useAgentRuns.test.ts`, `backend/tests/integration/test_pipelines_list_run_scope_postgres.py`, `frontend/tests/ci-e2e/run-evidence-project-scope.spec.ts`)
- Failure attribution: a failing test in a project's first run, or one that did not run (or was skipped) in the previous run, no longer has its verdict evidence say it was already failing. The evidence now says there is no earlier result to compare against, and `last_green_run_id` names the previous run only when the test passed in it. The attribution API adds `inputs.previous_outcome`. (D-0004, `backend/tests/regression/test_phase4_attribution_verdict.py`, `frontend/tests/ci-e2e/run-attribution-baseline.spec.ts`)
- AI triage: the flakiness-history evidence no longer calls a test consistently failing, flaky or historically stable from fewer than 5 recorded executions; it gives the counts and says it is too early to tell. One failed execution read "CONSISTENTLY FAILING — likely a genuine product bug". (D-0008, `backend/tests/regression/test_flakiness_tool_single_observation.py`, `frontend/tests/ci-e2e/triage-flakiness-evidence.spec.ts`)
- Summary report: **Latest run per suite** labels its pass rate **per test execution** instead of **per unique test** (it sums the latest runs' results, so a test run by two suites counts twice), and the guide no longer says its totals are smaller than **All runs in window**. The API's `totals.pass_rate_basis` and `meta.pass_rate_basis` say `executions` in that mode and in the window's run-aggregate fallback. (D-0012, `backend/tests/test_summary_report_service.py`, `frontend/tests/ci-e2e/rollout-summary.spec.ts`)
- Search: the headline counts the rows keyword search covers (tests, runs, suites, defects and releases), and the semantic index's test-result count is a separate, labelled stat (**Semantic index (tests)**). Before, a ready semantic index put its test-only count under "items indexed across 6 entity types". (D-0015, `frontend/src/pages/SearchPage.test.tsx`, `frontend/tests/ci-e2e/search-rail.spec.ts`)
- Search: the **Flaky** chip no longer shows a stored count beside a Flaky search's results. That count was open quarantine requests, a different population, so a project with a flaky history and no open request read **Flaky 0** beside a matching flaky test. Flaky tests are found from their run history, so the chip shows a number only for a query (its matches). **Searchable data** names the quarantine-request count as such. (D-0013, `frontend/src/pages/SearchPage.test.tsx`, `frontend/tests/ci-e2e/search-rail.spec.ts`)
- Release gate: a QA lead is no longer offered an override on a quick-look recommendation that has no stored decision to override (the save failed). **Override decision** is disabled and says to run deep investigation first, as the run's Analysis tab already did. (D-0007, `frontend/src/pages/ReleaseGatePage.test.tsx`, `frontend/tests/ci-e2e/fold-release-gate.spec.ts`)
- Deep Investigation: the Agent investigation panel no longer keeps showing the previous run's investigation, verdict and proposed actions after you pick a different run. An investigation you picked, or just started, belongs to its run; the newly selected run shows its own investigation or its empty state. (D-0009, `frontend/src/components/investigator/InvestigatorCockpit.test.tsx`, `frontend/tests/ci-e2e/investigator-suggest-proposals.spec.ts`)
- Runs: a run whose tests were all skipped no longer shows a red 0.0% pass rate; it shows **—** ("no test was executed"), and the window's **Avg pass rate** leaves it out. Releases, Run compare and the Agents page's live executions follow the same rule. (D-0005, `frontend/src/utils/runPassRate.test.ts`, `frontend/tests/ci-e2e/runs-skipped-pass-rate.spec.ts`)
- Uploading with **Skip AI analysis** (or `--no-ai`, `run_ai=false`) no longer starts an automatic Investigator run, with its model calls, for that run's newly failing tests; it also no longer uses the project's daily investigation budget. Transition notifications are still sent. The checkbox, the CLI help and the guides now say what is skipped. (D-0006, `backend/tests/regression/test_skip_ai_does_not_auto_investigate.py`)
- Run Compare: a Viewer or Tester opening a comparison with no saved AI comparison report no longer makes the server write and save one (a paid model call charged to the project). They see the report once a QA engineer or above has opened the comparison; until then the page says none exists yet. (D-0003, `backend/tests/regression/test_run_compare_viewer_does_not_generate.py`)
- Dashboard: "latest suite" in the header names the suite of the newest run, the run TestLookup received last and the first row on **Runs**. It used to name the run with the highest build label, which could be an older run of another suite. The Runs table now also asks for its newest 500 runs by when they were received. `GET /api/v1/runs` takes `order=newest` (the default, `build`, is unchanged). (D-0022, `frontend/src/pages/OverviewPage.test.tsx`, `backend/tests/integration/test_run_list_natural_order_postgres.py`, `frontend/tests/ci-e2e/overview-latest-suite.spec.ts`)
- Charts on Trends, Coverage, Failures and Explore no longer say "No runs have been ingested for this project yet" when the project's runs are older than 30 days; an empty chart then says no data matches the current window. The check asked the server's default 30-day window instead of all time. (D-0002, `frontend/src/components/reports/catalogue/useEverHadRun.test.tsx`, `frontend/tests/ci-e2e/rollout-heatmaps.spec.ts`)
- Dashboard: a project whose newest run is more than 30 days old no longer gets the first-run setup guide in place of the dashboard. The page says how old the newest run is and offers a wider window. The two "has this project had a run?" checks asked the server's default 30-day window instead of all time. (D-0001, `frontend/src/pages/OverviewPage.test.tsx`, `frontend/tests/ci-e2e/overview-first-run-window.spec.ts`)
- Defects: the table's heading names the status tab you picked and counts the rows it shows ("Closed defects 1", or "1 of 3" while a search narrows the list), instead of reading "All defects 0" under the Closed tab while the All tab said 2. (D-0014, `frontend/src/pages/DefectsPage.test.tsx`, `frontend/tests/ci-e2e/defects-triage.spec.ts`)
- Defects: a defect whose test run is linked to more than one release is listed once, so the banner's P0 count, the All and Open tabs and the KPI tiles agree; the row's release name is now the run's primary release. GPT-6 saw one open P0 listed twice beside an open count of 1. (D-0011, `backend/tests/integration/test_defects_list_fields_postgres.py`, `frontend/tests/ci-e2e/defects-counts-agree.spec.ts`)
- In-app guide: Troubleshooting said the `flaky_auto_quarantine` flag is off by default. It is on for a new install, as the feature-flag reference says.
- Python SDK: closing a live session (`LiveSession`, the pytest plugin, or `LiveStream`) waits for a results batch the background flusher is already sending, then closes it, and retries the server's 409 "retry close". Before, closing cancelled the flusher, and a batch it was sending was lost: its results never reached the run.
- Sign-in: the page you return to after signing in is refused when its saved path holds a tab, line feed or other control character, and falls back to the overview. A URL parser deletes those characters, so a saved path like `/<tab>/evil.example` read as the other host `evil.example`. The saved path comes from the browser's own address bar, which never holds them, so no real sign-in was affected. New property-based (fuzz) tests found it; they now run thousands of generated inputs against the return path and the external-link guard on every change.
- Live-ingest stress test (`backend/scripts/stress_live_ingest.py`): the frames-per-second report counts a whole-second span as exactly that many 1 s windows. Before, a float rounding error in the span could add an extra window that was always empty, which lowered the reported frames-per-second p95 and made the script's unit test fail now and then in CI.

### Security

- SSO: a first sign-in is no longer linked automatically to an existing account with the same e-mail. Linking needs the new per-configuration switch "Link existing accounts by verified e-mail" (`link_existing_accounts_by_email`, off by default). Administrator accounts are never linked. OIDC needs `email_verified: true`; SAML matches on the NameID. A link is recorded as the identity event `ACCOUNT_LINKED`.
- SSO: a sign-in must finish in the browser that started it (short-lived cookies set by `/sso/login-url` and `/sso/oidc/login`). SAML sign-in therefore needs HTTPS; `SAML_BROWSER_BINDING=false` turns the check off for a plain-HTTP site.
- SSO: the four sign-in routes are limited to 60 requests a minute per address, and a SAML response refused before its signature verifies is logged, not stored as an identity event.
- SSO: a SAML assertion must carry `NotOnOrAfter` and `Recipient` and be usable for at most `SAML_MAX_ASSERTION_LIFETIME_SECONDS` (10 minutes) when it arrives.
- SCIM: a token that is not bound to an SSO configuration reaches only accounts that SCIM created or that have signed in through an identity provider; every other user id answers 404. Such a token cannot change a userName or e-mail (403).
- SCIM: no token can deactivate, delete or demote the last active administrator (409), also when the delete action is `erase`. Group members are read and written only inside the token's reach.
- Jira: a project reaches only its own Jira project through the shared Jira account. With a Jira key on the project, issues, epics, links, status write-backs and release syncs are accepted only for that Jira project; without one, members below QA lead use the default Jira project and a QA lead or administrator may name another. A refused key answers 422 and nothing is sent to Jira.
- Jira: only an administrator can set or change a project's Jira key. The picker data (`GET /projects/{id}/defects/jira/metadata`) needs QA engineer and lists only the Jira projects the caller may use.

- Health routes: `/health/details` and `/health/ingestion` need a signed-in user or an API key (any role). `/health/live`, `/health/ready` and `/health/version` stay public. A failed check reports a fixed sentence, not the exception text. `METRICS_TOKEN` can require a bearer token on `/metrics`, and `API_DOCS_ENABLED=false` turns the API reference routes off (both unchanged by default). The MCP `health_check` tool falls back to the public probes when it has no sign-in.
- Rate limits: in production and staging the counters are kept in Redis, so a limit holds across workers and pods (`RATE_LIMIT_SHARED_STORAGE`); they fall back to per-process counters while Redis is unreachable. Sign-in is also limited per typed account name, from any address (`LOGIN_ATTEMPTS_PER_USERNAME_PER_MINUTE`, default 20; 429 with `Retry-After`). The browser telemetry route is rate limited per address, accepts at most 50 items per batch and drops the query and fragment of reported addresses.
- Tracing: query strings are removed from the addresses recorded on trace spans, and the path of Slack, Teams, Discord and Google Chat webhook addresses is masked.
- Audit: `PATCH /api/v1/users/{user_id}` writes an access-audit row for each of role, active status, e-mail and username that changes.
- Shared reports: every value the share page and the PDF print is escaped or converted to a number first, and the share responses carry a strict Content-Security-Policy, `nosniff`, `no-referrer`, `no-store` and `noindex`. An imported snapshot could carry markup into the page.
- Run export: the downloaded file name is built from a cleaned build number and quoted in the header; a run with no AI summary exports without an error.
- Bucket-drop ingestion: a build written straight to the object store is held to the result limit and to 50 MiB per object (`INGEST_DROP_MAX_OBJECT_BYTES`); over either it is refused once, not retried. A sentinel key must be `<project>/runs/<build>/.../upload_complete.json`; `uploads` cannot be a new project's slug.
- Failure clustering works in a temporary in-memory collection per run. The one stored collection shared by every project is no longer written.
- HTTP capture redaction (server and the Python and Java SDKs): header values are also replaced when the name is `Authentication` or ends in `-key`, `auth`, `session` or `sessionid` (with exceptions such as `Idempotency-Key`), and nine more query names are covered (`pwd`, `jwt`, `otp`, `subscription-key` and others). The Python and Java reporters replace credentials quoted in a failure message or stack trace before sending.
- JS and Go SDKs: a redirect is followed only to the same scheme, host and port; credentials are never sent to another origin.
- CLI: text from the server is printed literally (no Rich markup, control characters removed). `testlookup auth logout` ends the session on the server and removes a stored API key as well as the tokens. `auth login` takes `--password-stdin`; `--password` prints a warning; a plain-http server address that is not this machine prints a warning.
- CLI: `ci-verdict` exits 2 for a run that is still in progress or reports no tests, where it used to exit 0. `--timeout-seconds` waits for the run; `--allow-partial` judges it anyway. The pytest plugin no longer aborts a test run when the server cannot be reached at session start, and waits at most 30 seconds per batch.
- A non-ASCII `X-Webhook-Secret` or bearer value on `/webhooks/minio` and `/ws/events/{run_id}` answers 403, not 500.
- The Cloud Run and GCP VM example env files suggest a 30-minute access token (was one day).

- Install text: the two places that told users to install `testlookup-reporter` from PyPI by its bare name now give the supported install from the repository or the SDK download. The project has not published that name, so anyone could have registered it. A quality-gate guard refuses a bare-name pip or npm install of a `testlookup...` package in tracked files.
- CI: the CLI and SDK wheels are built from a hashed build lock (`scripts/requirements-build.txt`) with `--no-build-isolation`, and the image job builds its wheel before it logs in to the registry. Before, `pip wheel` downloaded the newest build backend unpinned in the one job that can push images and request signing tokens. The release gate installs PyYAML from its hashed lock.
- Releases: the offline bundle is attested only on a `workflow_dispatch` run from `main`, and the documented verification commands pass `--signer-workflow` and `--source-ref refs/heads/main`, so an attestation from another branch or workflow is not accepted.
- Installer: when `install.sh` installs a release tag it downloads that release's `SHA256SUMS` and checks every file it fetched before running anything; a mismatch or a missing sums file stops the install. A tag in `TL_REF` or `TESTLOOKUP_VERSION` now selects both the files and the images of that release.
- Release and single-VM Compose stacks: Redis does not start without `REDIS_PASSWORD`, and every Redis URL carries it. The backend logs a startup warning in production when a Redis URL names another host without a password. The backend-image services run with `no-new-privileges` and all capabilities dropped. The installer prints a warning that the web port is plain HTTP on every interface, and tells the operator to remove `INITIAL_ADMIN_PASSWORD` from `.env` after the first sign-in.
- Scripts: secrets are no longer passed on a command line in `install.sh`; a failed `chmod 600` stops the script; `scripts/ops/backup.sh` writes backups with owner-only permissions. The OpenShift deploy script creates its credentials file owner-only, and the file is git-ignored.
- API: responses carry `Strict-Transport-Security` when the request arrived over https through a trusted proxy, so API and MCP responses that do not pass through the frontend have it too. Request bodies are limited while they are read: 10 MiB on ordinary routes and 100 MiB on the ingest and import routes (`REQUEST_BODY_MAX_BYTES`, `REQUEST_BODY_UPLOAD_MAX_BYTES`); a larger body answers 413.
- Kubernetes: the GKE overlay redirects http to https (and its Ingress keeps the application host, which a patch used to drop). The cloud ingresses publish four exact health paths, not the whole `/health/` prefix. The `dev` overlay runs `APP_ENV=production` on every workload and its Ingress uses an internal class.
- EKS Terraform: new opt-in variables `enable_network_policy` (the VPC CNI enforces NetworkPolicy) and `require_imdsv2` (nodes require IMDSv2 with a hop limit of 1), both off by default. The deployment guides state that the cluster must enforce NetworkPolicy and name the switch for EKS, AKS and GKE; without enforcement the shipped NetworkPolicies do nothing.

- Knowledge sources: an `internal_url` source follows the same rules as an `external_url` one. It is not fetched in offline mode and its domain must be on the allow-list when one is set. Both kinds are checked again on every sync and for each redirect target. Before, the `internal_url` label skipped both gates although the connector can only reach public hosts.
- Knowledge sources: a Jira or Confluence `external_id` must be an issue key (`ABC-123`) or a numeric page id. Other values went into the request path and a JQL string as they were, with the deployment's Jira or Confluence credential.
- MCP: every id a tool puts into a backend path is percent-encoded, a UUID is required where the backend takes one, and the final address is checked before sending. A crafted id (`../`, `?`, `#`) could send a tool's request to a different backend route.
- MCP: text copied from test reports is marked as data in tool results (`<test-output>` blocks with a notice), so a test name or error message cannot pose as instructions to the assistant. The server reads its env file from next to `server.py` or from `TESTLOOKUP_ENV_FILE`, never from the working directory, and warns when the sign-in goes over plain http to another host.
- Fixer: the `workflow_dispatch` runner reruns the test without the patch, so its result no longer counts as validation. The attempt is recorded as an error ("baseline only ... fix merit unknown") and no pull request is opened; before, a passing rerun marked the patch validated and suggest mode opened a pull request saying so. A patch whose paths are absolute, contain `.` or `..`, a backslash, or point under `.github/` is rejected. The docker runner drops all capabilities, forbids new privileges and runs with a read-only root filesystem.
- LLM settings: a provider key that comes from the environment (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`) is sent only to the provider's own endpoint. A custom base URL needs a key stored in Settings > AI Configuration; saving one without a key answers 422. The Ollama availability probes obey offline mode and answer with a fixed word, not the text of whatever replied.
- OpenShift: a project that saved its own namespace is limited to it. A report that names another namespace is ingested, but its pod and namespace are not stored and no pod lookup is made for it.
- Links: `ci_run_url` is kept only when it is an http(s) address (other values are dropped and the upload still succeeds). Chat answers treat `//host` links as external, show an external link's host next to its text, and render any non-http(s) link as plain text. The GitHub App install button only follows an http(s) address.

Fixes from the maintainers' security audit of 2026-11-18. Several change what a role may do or what an API call answers; see "Upgrade notes" below the list.

- Roles: a write into a project now needs the route's role in that project as well as on the instance. Before, a route checked the instance role and only that the caller was a member of the project, so a user who was a QA lead on the instance and a viewer in one project could write to that project as a lead. The lower of the two roles now decides; an instance ADMIN is not affected. The refusal is 403 "Requires at least <role> role for this project". Reads are unchanged.
- Roles: routes that any project member could call now need a role. TESTER: create or edit a test plan, add or remove plan items, record an execution, edit a test strategy. QA_ENGINEER: set a suite-review verdict, start AI plan, strategy or coverage generation, run the duplicate-case sweep, recover a live run. QA_LEAD: change a strategy's status, create a public share link for a run report, read a webhook's delivery history. ADMIN: queue the training export or a fine-tune, and probe the deployment-wide integrations (a QA lead can still probe one project's GitHub or GitLab integration). A strategy's `status` accepts only `draft`, `review`, `approved`, `active`, `suspended`, `closed` or `archived`.
- Webhooks: members below QA_LEAD see a subscription's `target_url` as scheme and host only (`https://host/...`). For Slack, Teams and Discord style receivers the full URL is the credential. The deliveries `limit` is 1 to 200.
- Sign-in: a locked account answers 429 with `Retry-After` for every attempt, whatever the password. Before, a wrong password answered 401 and the right one 429, which told a guesser which password was correct during the lock.
- Sign-in: wrong passwords and wrong codes on `POST /auth/change-password`, `POST /auth/mfa/disable` and `POST /auth/mfa/recovery-codes` count toward the account lockout and are rate limited per address (10 a minute). Before, a stolen session could guess without limit.
- Sessions: access tokens last 30 minutes by default (was 12 hours). A refresh token that is used again after it was rotated ends every session of that user, access tokens included. `POST /auth/logout?all=true` signs out every device. Two browser tabs that refresh at the same moment are not treated as theft (`JWT_REFRESH_REUSE_LEEWAY_SECONDS`, default 10).
- Sessions: the live stream endpoints (`GET /api/v1/stream/sse/{project_id}` and the `/ws/live/{project_id}` WebSocket) apply the same token checks as the rest of the API. They used to accept a logged-out token and an account still on a temporary password; the SSE route also accepted a refresh token. An SSE stream ends when its token expires.
- API keys: `POST /auth/first-time-reset`, `POST /auth/change-password` and `POST /auth/logout` refuse an API key (403). A key whose owner was still on a temporary password could set that owner's password.
- Accounts: a new username may contain only letters, digits, `.`, `_` and `-`. A username equal to another user's e-mail address made that user's sign-in by e-mail fail with a 500; sign-in now prefers the e-mail match and never answers 500 for a clash. E-mail addresses are compared without regard to case when an account is created or changed.
- Startup: with `APP_ENV=development` (the default when unset) the API logs a warning for each secret that is still the placeholder from the source, and refuses to start on a placeholder `JWT_SECRET_KEY` unless `ALLOW_INSECURE_DEV_SECRETS=true`.
- Live ingestion: a stream session's token stops working when the API key that opened it is revoked, its owner is deactivated or removed from the project, and after 24 hours in any case. Before, it kept working as long as batches kept arriving.
- Live ingestion: `/ws/events` run ids are per project for project-bound keys. Before, the first project to use a run id blocked every other project from it for 25 hours and could tell whether an id was in use.
- Releases: evaluating a phase gate and linking a test run refuse a phase that does not belong to the release in the path (404 "Phase not found"). An unknown phase id on gate evaluate also answers 404 (was 200 with "no such phase").
- Ownership: resolving a failure cluster's owner looks only at clusters and tests of the project in the path, and takes the project's newest cluster with that label. A cluster nobody owns answers `confidence: "none"` (was `"high"` with empty fields).
- Search: `POST /api/v1/search/reindex?project_id=` (an empty value) is the instance-wide reindex and needs ADMIN. A QA lead could queue it.
- Test management: `GET /test-management/cases/ai-task/{task_id}` answers only for an AI task the caller queued or one queued for a project they can access, and 404 otherwise. It used to return the result of any background task by id.
- Knowledge sources: an `uploaded_document` source's storage key must sit under its own project's id (422 on create, and refused again at sync).
- AI evaluation: a QA lead sees only evaluation runs made for projects they can access, plus runs with no project. A run of another project answers 404.
- MCP: the `create_share_link` tool sends nothing until it is called with `confirm=true`, and its description tells the assistant to ask the user first.

#### Upgrade notes

- **Project roles.** Before upgrading, list members whose project role is below their instance role; after the upgrade they can only do in that project what the lower role allows. Raise the project role where that is not intended (Project settings, Members). The query is in [UPGRADING.md](UPGRADING.md).
- **Access-token lifetime.** `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 30. An install whose `.env` sets it (the 0.1.0 `.env.example` set 720) keeps its value: lower it by hand. The web app, the CLI and the MCP server get a new token on their own. A script or an SDK run that signs in with a password and works for more than 30 minutes should use an API key.
- **Development starts.** A backend started in development with the placeholder `JWT_SECRET_KEY` no longer starts. Run `scripts/gen-dev-env.sh`, or set `ALLOW_INSECURE_DEV_SECRETS=true` on a machine nobody else can reach.
- **Callers that will now get 403, 404, 422 or 429** are listed per call in [UPGRADING.md](UPGRADING.md). The web app still shows some controls to roles that can no longer use them; the server's message is shown when they are used.

- ChromaDB: the backend always tells the Chroma client which embedding function a collection uses. Before, on the default embedder (the bundled MiniLM model), it passed none, and the client then built whatever function the collection's settings on the Chroma server named. Chroma has no login of its own, so anything able to write to it could have made the backend send search text, and the value of any of its environment variables, to a server of its choosing. Existing collections open as before and nothing is stored differently, so no reindex is needed. Chroma stays reachable only by the backend and the workers (compose's internal network, the `allow-chromadb` network policy), because the four ChromaDB advisories still have no fixed release.
- Supply chain: every `pip install` in CI and in the backend and release-runner images now installs from a hashed lock. Before, ten of them took whatever PyPI served at that moment. The new locks are `scripts/requirements-ci.txt` (test tools for the guard-test jobs), `scripts/requirements-sdk-test.txt` (the Python SDK and CLI tests, compiled from their `pyproject.toml` files), `backend/requirements-cli-test.txt`, `backend/requirements-ragas-lock.txt` (the optional Ragas extra) and `e2e-runner/requirements-lock.txt`, which replaces `e2e-runner/constraints.txt`. `make lock-python` regenerates them all and Dependabot updates them. `pip check` fails a job when a package needs something its lock does not hold.
- Releases: a release run of the "Offline bundle" workflow signs SLSA build provenance for the bundle, its `.sha256` file and `SHA256SUMS`, and saves it beside them as `testlookup-offline-<version>.intoto.jsonl`, to attach to the GitHub release. `gh attestation verify <file> --repo test-intelligence/testlookup` checks a download.
- `SECURITY.md` links straight to the private vulnerability report form.
- Dependency advisories that have no fixed release, in code TestLookup does not run, are recorded in `osv-scanner.toml` files. Each entry gives the reason and expires after 90 days, so OSV scanners (including OpenSSF Scorecard's Vulnerabilities check) report only findings that need action. The entries cover ChromaDB's server and auth providers, Ragas' multi-modal metric and diskcache (both in the optional Ragas extra), and glob and minimatch (development and test tooling only). The sample projects now require pytest 9.0.3 or later.

## [0.1.0] - 2026-11-14

The first public release. TestLookup is a self-hosted test-intelligence platform for software development teams: it ingests test results from CI and from live test runs, explains failures, tracks flaky tests and decides whether a release is ready, with AI that runs locally by default.

Everything under Added is new to the public. The other sections matter to anyone who ran a build from `main` before this release; [UPGRADING.md](UPGRADING.md) lists the steps such an install needs.

### Added

#### Ingestion and integrations

- Upload results in the browser, through the batch API or with the CLI in eleven formats: JUnit, TestNG, Robot Framework, NUnit 3, Visual Studio TRX, xUnit v2, Playwright JSON, pytest-json-report, Cypress/Mochawesome, Cucumber JSON and Allure, or a ZIP of them.
- Stream results while tests run from reporters for Python (pytest, Robot Framework 5–7), Java (TestNG, JUnit 5 and 6), JavaScript (Jest, Mocha, Playwright, Vitest, Cypress, Cucumber JS) and Go, with a ready snippet for each runner under Live › Connect a test runner.
- Runs carry their CI context (GitHub Actions, GitLab, Jenkins, Azure Pipelines, CircleCI) and commit range.
- Drop-folder ingest picks up a run written under `<project>/runs/<build>/` in the bundled S3 store within about a minute of its `upload_complete.json`, through the same checks and de-duplication as an upload (it needs the beat process, which the dev-lite stack does not run).
- A run can record its context (runtime versions, lockfile hashes, image digests and an allowlist of 12 non-secret environment variables, redacted on both sides), which the Python SDK and the CLI collect automatically.
- Each test result can carry up to 20 redacted HTTP exchanges, captured by the Python SDK (`--testlookup-capture-http` for `requests` and `httpx`) or the Java SDK's RestAssured filter, shown on the test's HTTP exchanges tab and checked for contract drift against earlier runs.
- Integrations with Jira, Slack, Microsoft Teams, email, GitHub checks and pull-request comments, GitLab, outbound webhooks, Splunk, OpenShift and Prometheus, with a per-project Splunk index and OpenShift namespace.
- GitHub can authenticate as a GitHub App instead of a personal access token, and commit links follow a GitHub Enterprise server.
- GitLab matches GitHub: commit ranges and changed files on the Changes tab, failure suspects, and milestone sync into releases.
- The Prometheus integration takes a URL, a bearer token kept in the secret store and metric templates, which deep investigation queries for the failing service outside offline mode.
- Integration Health lists every project's GitHub and GitLab integration as its own row, probed every 15 minutes with that project's credentials.
- Outbound webhooks pause themselves after a configurable run of failed deliveries (default 10), e-mail the last editor and the project's QA leads, and resume with Re-enable.

#### Failure analysis and AI

- Run history and run detail with Analysis, Evidence, Changes and Timing tabs, Run Compare, failure clustering by signature, and an Inbox of assigned failures and approvals.
- A failing test shows the build where it last passed and the build where it first failed, and lists only the commits between them when a git host is connected; the Runs page's Bisect button opens that view.
- The Changes tab shows a Configuration drift card ("poetry.lock changed · python 3.11.8 → 3.11.9") and an advisory "Likely affected tests" ranking that never selects, skips or gates a test.
- Execution time is kept for every test, suite and run, with a per-worker timeline and the slowest tests on Run › Timing, a CSV export, Trends › Execution time, and duration-regression detection that can fail an opt-in gate rule.
- Run › Timing also lays the Splunk, OpenShift and Prometheus evidence the AI analysis fetched on one time axis, without fetching anything new.
- AI analysis attaches Splunk log correlation and OpenShift pod events before the model runs, so a small local model still sees them.
- Flaky tests get a four-signal flakiness score with a confidence band, quarantine proposals a QA lead approves, a Policy tab (SLA, auto-defect, auto-release after N passes, active-quarantine cap) and a quarantine-aware CI verdict (`testlookup ci-verdict`).
- Defects can be triaged from the list (status, assignee and team, a Jira link with status write-back), narrowed to one release the way the release gate counts them, charted over time and measured by escape rate, and a promotion that needs approval waits in Inbox › Approvals.
- "Create epic for this cluster", opt-in per project, files one Jira epic for a failure cluster that recurred in 3 or more runs, and only when a person clicks Create.
- QA leads can add classification hints ("error message contains … → Infrastructure") that classify new failures before any AI engine, and ownership rules resolve Java stack frames through configurable source roots.
- Failure classification runs as rules, a trainable ML tier (Train now and a nightly retrain) and an LLM, each with confidence and provenance.
- AI runs offline by default with Ollama (`AI_OFFLINE_MODE=true`, default model `qwen2.5:7b-instruct`), and OpenAI-compatible servers, OpenAI, Gemini, Anthropic and OpenRouter are opt-in.
- Every AI analysis is kept as a numbered version with its provenance, so you can view or diff earlier answers and read a "vs previous run" card against the previous run of the same suite on the same branch.
- The agent pipeline runs with budgets and human review, shows Graph, Audit and Compare tabs, can use a published workflow as the project default, and runs bulk re-analysis as a tracked, cancellable job.
- Deep investigation offers an error-signature pre-scan, a history, a cost and time estimate measured from the project's own runs, and a tighten-only run configuration.
- Agents can propose typed actions (quarantine a flaky test, reassign a failure, file an internal defect) that a QA lead approves in Inbox › Approvals, and in Act mode an approved action runs, is audited and can be undone.
- A review gate sets the date from which unreviewed AI reports stop leaving TestLookup, and accepting a report marks the run's pull-request or merge-request comment "Reviewed by a person".
- The summary agent accepts pasted failures as inline input (`POST /api/v1/agents/agent.summary.v1/invoke`), redacted before storage, with a "Try an agent" form on Pipeline runs.
- Ask AI chats over your data and keeps its suggested questions in view.
- The knowledge base grounds AI test generation (RAG), with a choice of faithfulness evaluator: the single-prompt judge, or Ragas in an image built with `--build-arg INSTALL_RAGAS=1`.
- AI evaluation replays a candidate tier on a dataset with per-item results, gates it against a baseline (PASS, FAIL or Not enough evidence), keeps an agent-stack gate history and offers a labelling queue for live shadow comparisons.
- An hourly cross-run check flags performance drift, rising flakiness, coverage drops and an AI budget near its cap, on an Insights card and in Inbox › Insights.
- Settings › AI › Embedding switches the embedding model (the bundled MiniLM or a local Ollama model) and re-embeds every collection in the background while search keeps working.

#### Release decisions

- A release gate with versioned gate policies (Versions, Compare, New version from this) and a simulator, whose pass-rate bands set the target line on Trends, Live and Overview.
- Releases have phases, a gate panel (scorecard, blocking tests, comparison with a baseline release), a gate chip per phase, an active release and a responsible project member, and completing a phase whose gate is not Go asks for a reason.
- A QA lead can Hold, Approve with conditions or Override to GO from a run's Analysis tab through the audited override, and "Why this score?" shows the policy's weights.
- Release planning can copy phases from another release, draft phases from a PRD (`.md`, `.txt` or `.docx`), export the schedule as ICS or CSV, show a month calendar, chart a 12-week release-health trend and map runs to releases with attribution rules.
- Compliance packs carry an Ed25519 signature and verify offline with `testlookup compliance-pack verify` or the MCP tool `verify_compliance_pack`, and older packs still verify after the key is rotated.
- Share links for a report take a layout and an expiry, are listed per run and can be revoked.
- `testlookup release gate <release>` prints the stored verdict and exits with a stable code for CI (0 GO or CONDITIONAL_GO, 1 NO_GO, 2 infrastructure error, 3 not evaluated).

#### Dashboards, search and activity

- Overview, Trends, Coverage, an Explorer with saved views, suites and canonical test cases, Value metrics, summary and analysis reports, and scheduled digests.
- Every page with a time window offers 14 days.
- Trends can be exported as PDF or e-mailed, shows a server-computed verdict and suite sparklines, and takes timeline notes pinned to a day or range.
- Coverage shows when each suite last ran and a History tab of daily catalogue snapshots, Value metrics can be split by team, and charts can be customised and saved with a view.
- The heatmap, coverage map, scatter and Sankey export as true vector SVG.
- Expected run cadences ("a run of <suite> arrives by <time> on <days>") record each window as met, missed or paused, with an opt-in missed-run notification.
- Notifications follow a per-project policy, "Quality gate failed" is delivered when a gate moves to NO_GO, and digests can follow one release or a saved report view and carry the release verdict.
- The Activity feed records run, analysis, release, configuration, integration and test-management events, can update live, and every role gets Inbox › Activity with @mentions from test-case comments.
- Search adds hybrid and semantic modes that fall back to keyword search with the reason, `key:value` filters typed in the box (the Syntax table lists them), saved and recent searches kept on the server, a top-bar preview and a Similar failures tab.
- Settings › Performance shows measured p50, p95 and p99 latency against each budget, and a Slow queries tab lists statement shapes slower than `SLOW_QUERY_MS` without their values.
- A Paper theme (soft light, low glare) joins Lab, and the whole app follows one UI standard: one colour per failure category, one Go / Conditional / No-Go vocabulary, one severity scale and one type scale.
- Details and forms open in the page beside or under what you clicked instead of in dialogs, and the open item is in the URL, so a refresh or a shared link reopens it.
- Shared links unfurl with a preview card.

#### Test management

- Authored test cases, plans, strategies, reviews and audit.
- A test case is edited in place and saved as one new version, with a warning when someone else saved first and status moves labelled by their outcome.
- A per-project setting can require review before a test case becomes Active.
- Select test cases to change their status, priority, tags, suite or assigned person in bulk, with a result per case.
- A case's History compares any version with the current one and restores its content, and its Timeline merges versions, reviews, comments, executions, defects and requirement changes.
- The library has multi-value filters kept in the URL, presets, saved case views, a server-computed health banner with an automation target, and CSV or Excel import of up to 2,000 rows as drafts.
- Test cases can link requirement keys, and Insights shows requirement coverage (flag `test_case_requirement_traceability`).
- Suites can be edited, and a test case opens beside the list with previous and next.

#### API, CLI, SDKs and MCP

- A REST API with Swagger at `/api-docs`, and an in-app guide at `/docs`.
- Each deployment serves the `testlookup` CLI as a wheel (`pip install <deployment>/api/v1/sdk/cli/<wheel>`), and the CLI accepts release and project names wherever it takes an id.
- CLI commands cover releases and gates, quarantine, Jira defects, investigations, compliance packs, the activity feed, managed test cases, project export and import, run timing (`--fail-on-regression`), execution analytics and AI history (`ai-history`, `compare-ai --fail-on-verdict-regression`); see [cli/README.md](cli/README.md).
- An MCP server for AI assistants with 80 tools, including the release gate, activity, AI history, run timing, agent actions and managed test cases.
- Reporter SDKs for Python, JavaScript, Java and Go, each with a README, that resolve credentials in the same order (explicit value, JVM property, environment variable, config file).
- The Python and Java reporters can trust a private CA (`TESTLOOKUP_CA_CERT`) or skip verification (`TESTLOOKUP_INSECURE`).

#### Administration, security and compliance

- Projects with five roles, API keys, MFA and account lockout, feature flags with per-project allow-lists, and audit and activity logs.
- Single sign-on with SAML 2.0 (including IdP-initiated single logout) and OpenID Connect (PKCE and ID-token validation), with IdP groups mapped to roles and a server-side preview.
- SCIM 2.0 provisions users and maps groups to project roles, and an identity provider's user delete can deactivate the account (the default) or erase the person.
- Any secret field can reference Vault (`vault:<path>#<key>`, KV v2, token or AppRole auth).
- Invitations by link (e-mailed when SMTP is set, always copyable, resendable and revocable) and password reset by e-mail or by a link an administrator sends.
- Administrators can search and page users, restore an archived project, see who changed a feature flag and why, and revoke any user's API key bound to a project.
- A fresh install creates the first admin with a random password that is shown once and must be changed at first sign-in.
- Retention holds on a release, a compliance pack or a project stop every purge or deletion of what they cover.
- Every purge keeps the newest runs and the latest passing runs (`keep_last_runs`, default 10), and a criteria deletion can first write a signed archive and delete nothing until that archive is verified (and, if you chose to download it first, until you confirm it).
- Retention defaults can be set for the whole instance, previewed per project and switched on with a typed confirmation, and each project can choose its purge hour or pause purges for 7 days.
- A person can download everything TestLookup holds about them and ask to be erased, and an administrator can export or erase a person while the append-only audit trail is kept (GDPR Articles 15, 17(3) and 20).
- Project export and import moves a project's settings and test catalog, and optionally its run history, plan results, members matched by e-mail and reviews, as a signed archive that never carries secrets; an import always creates a new project and sends nothing.
- Large project archives upload in resumable parts (up to 2 GB) and download straight to disk through a one-time link.
- Billing shows covered spend, overage and a month-end projection for a project with an included allowance.
- License notices ship with every artefact: `/THIRD_PARTY_LICENSES.txt`, `/LICENSE` and `/NOTICE` in the web UI, and `/licenses` in each image.
- The in-app guide has a legal notice (`/docs/legal`) that quotes the Apache-2.0 no-warranty and liability terms, and the README has a Disclaimer.

#### Deployment and operations

- Docker Compose stacks for development, release images, an air-gapped override and a monitoring add-on, and `install.sh`, whose evaluation profile (`TL_PROFILE=demo`) pulls about 0.95 GB of images.
- Kubernetes overlays for generic clusters, single-node K3s, OpenShift (including an air-gapped Artifactory variant) and EKS, GKE or AKS through `scripts/deploy-k8s.sh --cloud=aws|gcp|azure`.
- An offline bundle for air-gapped installs.
- Backups and upgrades with `make backup` and `make upgrade` on Compose, and a Kubernetes backup CronJob with retention, a restore Job and alerts.
- `DEMO_MODE` runs a read-only public demo that refuses every write, signs visitors in with one button as a non-admin demo user, and ships with `docker-compose.demo.yml` and `scripts/demo/` to set up, harden, watch and reset a VM nightly ([runbook](docs/operations/public-demo.md)).
- An "Open in GitHub Codespaces" button runs the full app with demo data.
- The single-node K3s scripts read every site-specific value from a local env file, render a per-deploy overlay, and prune old images from the in-cluster registry, keeping the newest three builds.
- `backend/scripts/s3_setup.py` creates the object-store buckets and can copy every object from a previous S3 endpoint.
- The Grafana dashboard charts the quarantine, webhook, digest and compliance-pack jobs and marks each deploy.
- Operator documentation: a [configuration reference](docs/operations/configuration.md), a [feature-flag catalogue](docs/operations/feature-flags.md), [integration setup](docs/operations/integrations.md), one deployment guide per mode, the SUPPORT, GOVERNANCE, MAINTAINERS, UPGRADING and RELEASING guides, a [data and privacy](DATA_AND_PRIVACY.md) statement, a citation file and an AI-assisted contributions policy in [CONTRIBUTING.md](CONTRIBUTING.md).

### Changed

- Bundled services moved to permissive licences: Valkey 8 replaces Redis (settings and `redis://` URLs are unchanged), versitygw replaces the bundled S3 server (copy existing data over, see UPGRADING.md), and MongoDB (SSPL) is no longer in the offline bundle.
- The default local model is `qwen2.5:7b-instruct` (Apache-2.0) instead of `qwen2.5:3b-instruct`, whose licence forbids commercial use.
- The Python and JavaScript reporters are licensed Apache-2.0 (they were MIT), like the rest of the project.
- The server, the web UI, the OpenAPI document and the Kubernetes labels all report version 0.1.0, and `install.sh`, the release Compose file and Kubernetes pull the published `ghcr.io/test-intelligence/testlookup` images.
- Platform upgrades:
  - images and CI run Python 3.14 (the CLI still supports Python 3.11 and later) and Node 26;
  - background work runs on Celery 5.7 and redis-py 8, and every Redis client makes one attempt per call with explicit timeouts;
  - the MCP server runs on the MCP Python SDK 2, and its SSE transport always applies DNS-rebinding protection and the host and origin allowlist;
  - the AI stack runs on LangChain 1.x;
  - the API runs on starlette 1.10, whose CORS middleware adds `Origin` to the `Vary` header of every response.
- A new install starts with these features on, while an existing install keeps its stored value: `manual_upload`, `outbound_webhooks`, `github_checks`, `gitlab`, `perf_regression_detection`, `release_phase_gate_enforcement`, `release_compliance_pack`, `llm_cost_budget`, the four read-only deep-investigation stages, and `test_case_requirement_traceability` (off after an upgrade).
- These features are on for every install: `cross_run_insights`, `drop_folder_watcher`, `aiq_gap_refinement`, and `flaky_auto_quarantine` wherever nobody set it (nothing is quarantined until a QA lead approves).
- Server-side feature flags are evaluated for the project and user they concern, and rollout buckets hash with SHA-256, so a flag at a partial rollout re-buckets its users once.
- Integration settings saved in the UI win over the deployment environment (offline mode overrides everything, a project's value beats the global one, and saving a field empty hands it back to the environment).
- `GITHUB_TOKEN` and `GITHUB_REPO` now feed only the deployment-wide GitHub probe; runs read commits through each project's GitHub integration.
- The retention purge runs hourly in each project's own window hour (default 02:00 UTC) and always keeps the newest 10 runs, the latest passing run and each suite's latest passing run, so purges keep more than before.
- "Compare to previous run" means the newest finished run of the same suite on the same branch, not one of the runs loaded in the page.
- The Trends GO line follows the gate policy's bands (95% by default, was a fixed 90%), and Trends no longer assumes a nightly schedule when no run cadence is configured.
- A search containing a known `key:value` filters instead of matching the text, and the search API returns `parsed`, `fallback_reason` and `semantic_status`.
- Settings › AI values (analysis mode, deep investigation, confidence threshold, timeout) are honoured by every path, and every LLM call is bounded by the AI timeout (default 300 s, range 5–1800 s).
- The ML tier adds scikit-learn and SciPy to the backend image (about 190 MB).
- Fine-tuning is an operator setting (`PUT /api/v1/settings/ai`, see [docs/operations/fine-tuning.md](docs/operations/fine-tuning.md)) and no longer has a toggle in Settings › AI.
- All Projects is offered to administrators and to anyone who belongs to two or more projects, and for a non-administrator it counts only their own projects.
- Review before Active is off for every project until a QA lead turns it on, editing no longer moves an approved test case to Needs update, and Link Suite links only approved and active cases unless asked.
- Digest subscriptions that could never deliver (a Per Release digest without a release, a Per Suite digest without a suite, another project's view or release) are refused with 422, and the "Flaky test detected" preference becomes "Test newly flaky".
- The UI reads "Go / Conditional / No-Go" and sentence-case statuses ("Failed", not "FAILED"), so a script that matched the old all-caps words needs the new ones.
- A closed live run is finalized in about a second, and `/ws/live` sends each browser at most one frame every 200 ms (a client that sends `"protocol": 2` gets `live_batch` frames).
- A live session writes one `ws_session` audit row instead of a connect and a disconnect row per connection.
- Suite membership moves to the test catalog: a daily parity check compares the old tables with the catalog, an administrator can cut over after seven clean days, the flag `suite_membership_legacy_writes` is the rollback, and the old tables are dropped one release after the cutover.
- Every page loads about 14 kB (gzip) less JavaScript before it renders, and deleting a run is faster on a large catalog.
- Without `SMTP_FROM`, e-mail is sent from `noreply@example.com` (it was `noreply@testlookup.io`, a domain the project does not use); set `SMTP_FROM` to an address of your own.
- The example release-test Jenkins pipeline moved to `e2e-runner/Jenkinsfile.release-e2e`; point an existing job's Script Path there.
- `make build` tags images `ghcr.io/test-intelligence/testlookup/<image>:local` instead of `testlookup/<image>:latest`.
- The single-node K3s path is `k3ssetup/` and `k8s/overlays/k3s/` (it was `homelabsetup/` and `k8s/overlays/homelab/`): run `./k3ssetup/deploy-k3s.sh` or `make k8s-deploy-k3s`. The old variable, site file and credentials file names are still read, and the rendered resources keep their names ([UPGRADING.md](UPGRADING.md)).
- The Go reporter's module path is `github.com/test-intelligence/testlookup/client/go` (it was `github.com/testlookup/testlookup-go`, which `go get` could not fetch): `go get github.com/test-intelligence/testlookup/client/go@main` works, releases tag `client/go/vX.Y.Z`, and a `require`, `replace` or `import` line that names the old path needs the new one.

### Deprecated

- `verifySsl` in the JavaScript reporter never had an effect and now warns; start Node with `NODE_EXTRA_CA_CERTS` to trust a private CA.
- The legacy feature-flag routes `GET/PUT/DELETE /api/v1/settings/flags[/{key}]` still work but answer with a `Deprecation` header and a `Link` to `/api/v1/feature-flags`.

### Removed

- `scripts/createAdmin.py`, a second copy of the admin creator that the K3s, OpenShift and generic Kubernetes deploys piped into the backend pod. They run the image's `/app/scripts/create_admin.py`, as the release stack does: `kubectl -n testlookup exec deployment/testlookup-backend -- sh -c 'python /app/scripts/create_admin.py'`.
- Four database tables and three MongoDB collections that nothing ever wrote; the migration refuses to drop a table that holds a row, `backend/scripts/drop_dead_mongo_collections.py` drops the empty collections, and an older project export that lists a removed table still imports.
- The Slack "Default Channel" and project "Jenkins Job Pattern" fields, which did nothing (the API still accepts and ignores `slack_default_channel`).
- `TESTLOOKUP_UPLOAD_MODE` in the Python and Java reporters, which changed nothing.
- `scripts/setup-minio.sh` and its bucket-notification wiring (`make setup-minio` now runs the Compose `minio-setup` service, and the authenticated `POST /webhooks/minio` stays for an external MinIO).
- The per-cloud deploy workflows: deploy to EKS, GKE or AKS with `scripts/deploy-k8s.sh --cloud=…`, or from CI as described in [Deploy from CI](docs/operations/deploy-cloud.md#deploy-from-ci).

### Fixed

- Reporters:
  - the pytest plugin sends a `qai_` key as `X-API-Key` and applies the configured suite and release;
  - every `TESTLOOKUP_*` variable works the same in `TestLookupReporter`, the pytest plugin and `LiveStream`;
  - the Jest reporter records each test once, and the JavaScript package loads with `require()`;
  - the JUnit 5 extension records results on JUnit 5.11 with real durations, and it and the TestNG listener read `testlookup.properties`.
- SAML sign-in works from the sign-in page; earlier builds refused every real SAML assertion.
- The Anthropic provider works; its LangChain package was never installed.
- Chroma's vectors survive a container restart; after upgrading, reindex search and re-sync knowledge sources ([UPGRADING.md](UPGRADING.md)).
- Requests no longer hold two database connections at once, which had stalled workers and caused random "Token revocation status cannot be verified" errors, and that message now names the real cause.
- A re-analysed run shows each failure cluster once, AI defect drafts are saved again, and "Trigger pipeline" re-analyses a run instead of dropping the request as a duplicate.
- The analysis mode set in Settings › AI no longer reverts to the deployment default, the ML tier trains on a real database, and a promoted fine-tuned model is used.
- A retention purge no longer empties an active project's search index.
- Project export works in a production worker's connection pool, signed archives are byte-reproducible, and a project reset also deletes the project's run documents in MongoDB.
- Controls that pretended to work now work or say what is true, among them test plan executions, the MCP dashboard and readiness tools, Live's SDK snippets, Value metrics export and "Page not found" for unknown URLs.
- An inline summary no longer presents caller-supplied failures as the run's pass rate or release readiness.
- UI controls are offered only to the roles the API accepts.
- Billing no longer overwrites a quota with defaults after a failed read, and Add User's temporary password and Invite User's link are shown once in the page.
- PII redaction no longer masks part of a run id or other UUID as a card number.
- Commit links follow the project's GitHub Enterprise server.
- An erasure also rewrites an @mention that ends a sentence.
- The compliance-pack failure alert can fire.
- The RAG faithfulness gate reads the AI settings, and the AI evaluation gate no longer compares a legacy metric with a replay baseline.
- Editing a test case no longer fails with a 500 on PostgreSQL when the change touches its suite or assigned person.
- Deployment:
  - the air-gapped OpenShift overlay can reach its datastores, and the OpenShift API Route reaches the backend;
  - pods keep serving for 10 s after they are told to stop, so a deploy no longer answers 504;
  - release-image workers and beat report healthy, and the bucket setup creates every bucket the backend writes to;
  - Kubernetes overlays default `DEV_AUTO_LOGIN_ENABLED=false` instead of refusing to start, and the single-VM Compose file gives the backend working MongoDB credentials;
  - shell scripts are executable on Linux and macOS clones;
  - the single-VM Compose overlay runs the images it builds: the backend, workers, migration, seed and frontend kept the development file's source bind mounts, because `volumes: []` in an override removes nothing (it is `volumes: !reset []` now, which needs Docker Compose 2.24.4 or newer);
  - the release Compose frontend and MCP server have healthchecks;
  - the MCP, beat and Chroma healthchecks work under Podman 4 (Ubuntu 24.04's `podman`). Podman's Docker API splits an exec-form healthcheck's arguments at every space, so the MCP server never reported healthy and `compose up --wait` failed. Those checks now use `CMD-SHELL`, or, for beat's `pgrep -f`, an exec-form pattern with no literal spaces. `backend/tests/regression/test_compose_healthchecks_survive_podman_4.py` checks every Compose healthcheck;
  - the cloud, generic and OpenShift Kubernetes overlays no longer render the placeholder `testlookup-secrets` Secret, so `kubectl apply -k` and `scripts/deploy-k8s.sh` no longer fail on its placeholder values or try to overwrite the Secret you created;
  - the OpenShift deploy makes a generated admin password one that must be changed at first sign-in, as the K3s and generic Kubernetes deploys already did;
  - the K3s and OpenShift deploys create the object-store buckets when run from Git Bash on Windows, including with a Git Bash-style `KUBECONFIG=/c/...` (Git Bash rewrote the in-pod script path, so that step always failed there).
- Documentation: THREAT_MODEL.md describes the system that ships, the guides now say SAML single logout, OIDC and Kubernetes backups work, and the compliance guide no longer claims a legal sign-off.
- Documentation: the feature-flag reference and the guides now say that a new install has the GitHub and GitLab integrations switched on (migration 0234) and that quarantine proposals ship on; they listed all three as off. `backend/tests/regression/test_new_install_flag_defaults_are_documented.py` checks every flag a migration turns on for new installs.
- Test plans: a plan's executed, passed, failed and blocked counts include the execution just recorded. They reported the counts from before it, so a plan with one passed run read 0 executed and 0 passed. A removed item also stayed in the counts, and an AI-generated plan read 0 cases. The recount ran its query before the change reached the database. `backend/tests/integration/test_plan_counts_follow_the_change_postgres.py` covers it.
- JavaScript SDK: the Vitest reporter closes its session under Vitest 4 and newer, which removed the `onFinished` hook. The results were sent, but the session never closed, so the run never got its totals and stayed open. The reporter now finishes on `onTestRunEnd`, and closes once when Vitest 3 calls both hooks.
- JavaScript SDK: closing a live session waits for a results batch that is already on its way, and retries the server's 409 "retry close". Before, a batch still in flight when the session closed could be lost, so the run missed those results and its totals. The Vitest reporter, which closes right after its last result, failed this way in about one release run in seven. Every reporter in the package closes its session this way.
- Release tests: the upgrade rehearsal (TL-DEPLOY-001) waits up to 120 s for the backend's `/health/ready` after it starts a build, instead of failing on the 503 that a freshly started backend returns while it opens its database connections. That made the rehearsal fail at random on a busy host. When a build still does not come up, the job log names the build, the phase and each application container's state.
- Users: Add User, Invite User and Edit User show the server's reason in the form when it refuses, for example "Validation error: email — value is not a valid email address: The part after the @-sign is a special-use or reserved name…" for an address on `.local` or `.test`. Before, Add User answered "Failed to create user" beside the shared toast, both gone in seconds, and an administrator could not tell why the account was not created; Edit User handed a 422's list to the toast. The guides say an account's e-mail must be on an ordinary domain.

### Security

- **Network exposure:**
  - the development stacks bind every port to `127.0.0.1` (`<SERVICE>_PUBLISH_ADDR` opens one on purpose), and the dev-lite stack's MongoDB requires the password from `.env`, like the other stacks;
  - the release stack keeps PostgreSQL, the S3 store and Ollama on loopback, and the single-VM template keeps the API there too;
  - the monitoring add-on binds to loopback, with Prometheus's unauthenticated lifecycle API off;
  - the public Kubernetes ingress and the OpenShift API Routes no longer route the unauthenticated `/metrics` (the OpenShift API host has one Route per backend path: `testlookup-api`, `-webhooks`, `-docs`, `-ws` and `-health`);
  - the bundled Ollama runs with `OLLAMA_NO_CLOUD=1`, so it no longer calls ollama.com at startup and every few hours or serves cloud models;
  - ChromaDB is reachable only by the backend and the workers.
- **Host-header path confusion (CVE-2026-48710):** the login rate limiter, the demo read-only gate, the forced password change, rate-limit keys and request logs read the routed path, so a crafted `Host` header can no longer skip the login rate limit; starlette 1.10 also fixes the underlying bug.
- **Weak secrets:** a production start refuses placeholder database, S3 and Redis passwords copied from the example files and a JWT, app or webhook secret shorter than 32 characters, `.env` is written with mode 600, `install.sh` says where the admin password is instead of printing it and runs nothing if its download is cut short, no script, seed or test carries a default admin password, and example configuration uses placeholders only.
- **Open sign-up and passwordless login:** self-registration is off by default and a sign-up is a Viewer (it was a role that can create API keys), `install.sh` writes a production `.env` without dev-login, and `DEV_AUTO_LOGIN_ENABLED` defaults to `false` in code (only the development stacks turn it on), so an API started without that setting never offers passwordless admin.
- **Authorization:**
  - a Viewer can no longer create, edit or comment on test cases through the API, and deleting one needs a QA engineer;
  - project API keys and synthetic accounts cannot decide agent actions, and the person who requested an investigation cannot approve its actions;
  - no API key can place or release a retention hold.
- **Cross-project data:** digest subscriptions check every saved view and scope id against their own project, the cluster duplicate check no longer lists other projects' defect titles, AI evaluation history no longer shows other projects' comparisons, and All Projects counts and caches only the caller's projects.
- **SSRF and egress:**
  - Splunk, OpenShift and Jira hosts on loopback, link-local or unspecified addresses are refused when saved and again before any request;
  - the legacy Jira ticket paths respect offline mode;
  - the Fixer recognises public GitHub by hostname (CodeQL incomplete URL substring sanitization), so its clone token never goes to a look-alike host;
  - the Ragas evaluator judges with the configured model inside the cost cap instead of OpenAI's default.
- **Images in chat answers:** an image in an AI answer is shown as a link instead of loaded, and the CSP's `img-src` allows only the app itself, `data:` and `blob:` (it allowed any `https:` host), so a prompt-injected answer can no longer send data out through an image URL.
- **Kubernetes service-account tokens:** the frontend, MCP server, Ollama and the bundled data stores, which never call the Kubernetes API, no longer get a token mounted.
- **Injection:** a project's Splunk index must be a plain name, Slack and Teams digest text is escaped (a test named `<!channel>` no longer pings the channel), and pasted agent input reaches the model only inside data markers that are stripped from the output.
- **ReDoS:** the PRD heading parser (CodeQL py/polynomial-redos) and the SCIM `userName` filter parser run in linear time, and two regexes in development tooling were fixed in the same CodeQL triage.
- **Sensitive data in logs:** Celery worker and beat logs go through the same redaction as the API, and twenty log lines that named a person by username or e-mail now log the user id.
- **Cloud credentials:** on Kubernetes the backend and workers can no longer reach the cloud metadata service (`169.254.169.254`, and `fd00:ec2::254` on AWS), which hands out the node's cloud credentials; their egress allowed every address.
- **EKS API endpoint:** the AWS Terraform has no default for `public_access_cidrs` (it was `0.0.0.0/0`), so the cluster API is open only to the CIDRs you set.
- **Public bucket:** the unused, publicly readable `testlookup-attachments` bucket is no longer created.
- **Synthetic-data endpoint:** `POST /api/v1/debug/generate-test-run` files generated results into any project an instance admin names. It now answers 404 outside development, as the dev seed endpoints already did; it was mounted in production too. Staging and production refuse before any credential check (`backend/tests/integration/test_debug_router_is_development_only.py`).
- **Demo hardening:** `docker-compose.demo.yml` gives the app containers no route to the internet, memory, CPU and process ceilings, and `no-new-privileges`, and demo sign-in refuses admin, service and MFA accounts and is rate-limited.
- **Supply chain:**
  - the backend and MCP images install hash-locked Python requirements (`--require-hashes`, every transitive package pinned; `make lock-python` regenerates them) and nothing else: the backend image no longer upgrades pip or installs watchfiles unpinned;
  - every CI job installs the frontend with `npm ci` from the committed lockfile;
  - a weekly workflow scans the third-party images (PostgreSQL, Valkey, MongoDB, versitygw, Ollama, Chroma, busybox, Flower and the monitoring add-on) for HIGH and CRITICAL findings with a fixed version, and so does every pull request that changes one;
  - base images are pinned by digest, the frontend's nginx by version, and every GitHub Action by commit SHA; the release job checks crane's checksum before running it;
  - published images carry signed SLSA build provenance: `gh attestation verify oci://ghcr.io/test-intelligence/testlookup/backend:<tag> --owner test-intelligence`;
  - a gitleaks secret scan checks every pull request, every push to `main` and the local push check, with extra rules for TestLookup API keys, K3s node tokens and Cloudflare tunnel tokens.
- **Dependencies:**
  - Ollama 0.40.10 (GHSA-x8qc-fggm-mpqg / CVE-2026-7482, heap out-of-bounds read in the GGUF loader), whose Kubernetes container drops every capability; the image grows to about 3.4 GB;
  - starlette 1.10.0 (CVE-2026-54283, urlencoded form limits ignored; CVE-2026-48710; CVE-2026-48817, CVE-2026-48818 and CVE-2026-54282);
  - the monitoring add-on's Grafana 13.3.1, pinned by digest (GHSA-3w66-95m3-8jxg, GHSA-9g84-39mm-q4p3, GHSA-f74p-cwhp-x2wx), which no longer downloads an unpinned plugin at every start;
  - higher floors for the MCP server (PyJWT 2.14) and the CLI (cryptography 49, pydantic 2.4);
  - python-jose is replaced by PyJWT 2.15.1 (GHSA-3qf3-8w2g-rqmx, no fixed release), and tokens issued before the upgrade stay valid;
  - LangChain 1.x fixes CVE-2026-34070 (langchain-core) and CVE-2026-104873 (langgraph-sdk); GHSA-gr75-jv2w-4656, GHSA-r7w7-9xr2-qq2r and GHSA-g48c-2wqr-h844 against the old pins were in features TestLookup does not call;
  - pypdf 6.21.4 (CVE-2026-115572, CVE-2026-115580, CVE-2026-115585 and CVE-2026-119203 to CVE-2026-119209, denial of service through crafted PDFs);
  - pymongo 4.18.3 (CVE-2026-106435) and aiohttp 3.14.4 (CVE-2026-111064, CVE-2026-111066);
  - DOMPurify 3.4.17 (GHSA-cg2g-xh64-v4q7, GHSA-mrv2-3pqr-j5jr) and follow-redirects 1.16.2 (CVE-2026-111755, CVE-2026-111756, CVE-2026-116689), zod 4.6.7 (GHSA-mrhx-c7xc-m3w5), and every other npm advisory with a fixed release (KaTeX, Mermaid, PostCSS, Vitest, undici, Babel, humanfs, brace-expansion, source-map-js);
  - chromadb stays at 1.5.9 (GHSA-f4j7-r4q5-qw2c, GHSA-36p7-vc44-83pf, GHSA-2wm9-hf6c-p5cr, GHSA-xph7-9rjv-w5fr; no fixed release): the advisories are in chromadb's Python server and auth providers, which TestLookup does not run, and because the client trusts the embedding configuration the server returns, only the backend and workers may connect to Chroma.
- Report vulnerabilities through GitHub private vulnerability reporting ([SECURITY.md](SECURITY.md)).
