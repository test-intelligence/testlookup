# Upgrading TestLookup

How to move an existing install to a newer version, and what each version
changes that you may have to act on. Release mechanics for maintainers are in
[RELEASING.md](RELEASING.md); every change is listed in
[CHANGELOG.md](CHANGELOG.md).

## The supported path

Upgrade one release line at a time (for example 0.1.x to 0.2.x), always to the
latest patch release of the target line.

1. **Read this file's section for every version you cross.**
2. **Back up first.** Compose: `make backup` (PostgreSQL, MongoDB and the object
   store into `backups/`; see
   [user-guide/administration.md](user-guide/administration.md)). Kubernetes:
   run the backup CronJob on demand
   ([k8s/components/backup/README.md](k8s/components/backup/README.md)).
3. **Check readiness.** Compose: `make preflight`.
4. **Upgrade.** Compose from a clone: `git pull` then `make upgrade` (it runs
   `alembic upgrade head` before restarting the application). Compose release
   images: set `TESTLOOKUP_VERSION=vX.Y.Z` in `.env` and
   `docker compose -f docker-compose.release.yml up -d` (the `db-migrate`
   service migrates first). Kubernetes: `scripts/deploy-k8s.sh` or the overlay
   of your choice (the migration Job runs before the rollout; see
   [docs/operations/deployment.md](docs/operations/deployment.md)). Single-node
   K3s: `./k3ssetup/deploy-k3s.sh --skip-registry --skip-models`.
5. **Verify.** `GET /health/ready` returns HTTP 200 with `status` set to `ready` and `GET /health/version` reports
   the new commit.

Database migrations only move forward in production. To roll back, restore the
backup taken in step 2 together with the previous version's images.

## After 0.1.0 (the next release)

Security fixes in this release change what some roles may do and what some API
calls answer. The release's migration step applies schema changes through
`0265`, including SSO, test case, telemetry and upload receipt changes.

### Upgrade ingestion workers before enabling uploads on the new API

Apply `0265` and upgrade every worker that consumes the ingestion queue before
opening the new API to uploads. Workers on the previous release can reject the
new upload task argument. New workers continue to consume jobs queued by the
previous API. If your rollout replaces API and worker roles together, pause
uploads until all ingestion workers and the API report the candidate revision.
Keep PostgreSQL connections in direct or session mode and allocate at least two
connections per ingestion worker child. [Upload dispatch and recovery](docs/operations/upload-dispatch-recovery.md).

### Upgrade observability profiles with their images

Deploy matching worker images and manifests together. The new reference worker launch and readiness probes depend on the process helper in the candidate image. Check every application's running digest and `/health/version`, then confirm a representative upload reaches a persisted outcome. [Worker launch and readiness](docs/operations/worker-readiness.md#image-and-profile-compatibility) · [Release rollout evidence](docs/operations/release-assurance.md#verify-a-kubernetes-rollout).

Review existing dashboards and alerts for the bounded labels, expected role inventory, and optional objectives and receiver changes. If you enable durable telemetry, assign storage, access, and alert-delivery owners and run the profile checks before widening production use. Rehearse rollback with the previous compatible backup, image set, and manifests. [Metric cardinality](docs/operations/metric-cardinality.md) · [Production observability checklist](docs/operations/observability-checklist.md).

### Check `.env` if you installed the release images by hand

The by-hand steps in `docker-compose.release.yml`, the offline bundle's "Next
steps" and the air-gapped guide used to run `scripts/gen-dev-env.sh` without
`--release`. That writes the local-development settings. Open `.env`; it must
have:

```
APP_ENV=production
APP_DEBUG=false
DEV_AUTO_LOGIN_ENABLED=false
```

If it has `APP_ENV=development` and `DEV_AUTO_LOGIN_ENABLED=true`, anyone who
can reach the UI port can sign in as an administrator without a password.
Correct the three lines and run
`docker compose -f docker-compose.release.yml up -d`. Installs made with
`install.sh` already have the safe values.

### GitHub App on GitHub Enterprise Server

The deployment's GitHub App now signs in only at `GITHUB_APP_API_BASE_URL`
(default `https://api.github.com`). On GitHub Enterprise Server set
`GITHUB_APP_API_BASE_URL=https://<your-host>/api/v3` for the API and the
workers. A project whose integration names another API base gets no App token.
Personal access tokens are not affected.

### Integration tokens and base URLs

Changing a GitHub or GitLab integration's base URL to a different host clears
the stored token; enter it again in the same form. A `vault:` reference is
accepted only on the administrator settings pages, not in a project's
integration token or a webhook signing secret.

### Default QA lead password reset

The reset on Projects > Members works only for the shared, auto-created
QA-lead account. For a project whose default QA lead is a person's own account
it answers 409.

### Project roles decide writes together with the instance role

A user has a role on the instance and a role in each project. A write into a
project now needs the route's role in both; before, the instance role and any
membership were enough. An instance ADMIN is not affected, and reads are
unchanged. A member whose project role is lower than their instance role gets
403 "Requires at least <role> role for this project" on actions their project
role does not cover.

Before upgrading, list those members and raise the project role where needed:

```sql
SELECT u.username, u.role AS instance_role, p.slug, m.role AS project_role
FROM project_members m
JOIN users u ON u.id = m.user_id
JOIN projects p ON p.id = m.project_id
WHERE u.role <> 'ADMIN' AND m.role <> u.role;
```

People added through the app normally have the same role in both places: a
project's creator and service accounts get their instance role, and an invited
member gets the role chosen in the invitation.

### Actions that now need a role

| Action | Needs | Before |
|---|---|---|
| Create or edit a test plan, add or remove items, record an execution; edit a test strategy | TESTER | any member |
| Set a suite-review verdict; start AI plan, strategy or coverage generation; run the duplicate-case sweep; recover a live run | QA_ENGINEER | any member |
| Change a strategy's status; create a public share link; read a webhook's delivery history | QA_LEAD | any member |
| Queue the training export or a fine-tune; probe the deployment-wide integrations | ADMIN | QA_LEAD |

Members below QA_LEAD see a webhook's target URL as `https://host/...`.

### Sessions

- Access tokens last 30 minutes by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`,
  was 720). A value set in your `.env` or manifests stays as it is; the 0.1.0
  `.env.example` set 720, so check it. `expires_in` in token responses follows
  the setting.
- The web app, the CLI and the MCP server renew on their own. The Python, JS
  and Go SDKs do not refresh a password sign-in: a run longer than the token
  lifetime should use an API key.
- A refresh token used again after it was rotated signs the user out of every
  device. The web app serializes refreshes within and across tabs so ordinary
  concurrent requests share the first rotated pair.
- The SSE and WebSocket live streams refuse a logged-out token, a refresh
  token and an account on a temporary password, and an SSE stream ends when
  its token expires. Clients reconnect with a fresh access token.
- A live ingestion session ends 24 hours after it was opened, and at once when
  its API key is revoked or its owner is deactivated or removed from the
  project. A run longer than 24 hours opens a new session.

### API answers that changed

| Call | Now | Before |
|---|---|---|
| Sign-in on a locked account, wrong password | 429 with `Retry-After` | 401 |
| `POST /auth/change-password`, `/auth/mfa/disable`, `/auth/mfa/recovery-codes` after repeated wrong guesses | 429 | 400 without limit |
| `POST /auth/first-time-reset`, `/auth/change-password`, `/auth/logout` with an API key | 403 | accepted |
| Create a user, register or accept an invitation with a username containing other than letters, digits, `.`, `_`, `-` | 422 | accepted |
| Gate evaluate with an unknown phase id | 404 | 200 "no such phase" |
| Link a run, or evaluate a gate, with a phase of another release | 404 | accepted |
| `GET .../ownership/resolve/{cluster_id}` when the project has no such cluster | 404 | answered from another project's cluster |
| `POST /search/reindex?project_id=` as QA_LEAD | 403 | queued an instance-wide reindex |
| `GET /test-management/cases/ai-task/{task_id}` for a task that is not yours | 404 | the task's result |
| `GET /webhooks/{id}/deliveries?limit=` above 200 | 422 | accepted |
| Create an `uploaded_document` knowledge source with a key outside the project | 422 | accepted |
| `GET /ai-eval/runs/{id}` for another project's run, as QA_LEAD | 404 | 200 |
| A second project's `run_start` on `/ws/events` with a run id another project uses (project-bound keys) | 202 | 409 |
| MCP `create_share_link` without `confirm=true` | returns a notice, creates nothing | created the link |
| `POST /deep-investigate/{run_id}` with a `mode` other than `deep` or `offline` | 422 | queued as `deep` |
| `GET /runs?status=`, `GET /runs/{id}/tests?status=`, `GET /quarantine?status_filter=`, `GET /analytics/defects?resolution_status=` with a value that is not a status | 422 | 200 with an empty list |
| The same four filters with a status in lower case | the matching rows | 200 with an empty list (runs, quarantine) |
| `GET /runs/{id}/summary` or `/export` with a `mode` other than executive, developer or manager | 422 | the executive summary |
| `GET /agents/pipelines?status=` with an unknown status | 422 | the failed pipelines |
| `GET /digests/preview?period=` other than `daily` or `weekly` | 422 | a one-day digest |
| `POST /training/promote` with a `track` other than classifier, reasoning or embedding | 422 | recorded as promoted |
| Promote a cluster to a defect with a `severity` other than CRITICAL, HIGH, MEDIUM or LOW (any case) | 422 | stored as sent |
| Create or update a notification preference with an event name that does not exist | 422 | stored, never fired |
| Create or update a release or phase with a `status` in mixed case | stored lower-case | stored as sent |
| Create or edit a test case with a `priority` other than critical, high, medium, low or P0 to P3 | 422, unless the edit sends the value the case already has | stored as sent |
| Create or edit a test case with an `automation_status` outside the four values, a `test_type` with other characters or over 50, or a `severity` over 20 characters | 422 | stored, or a database error |
| Create or edit a test case with `priority`, `severity`, `test_type` or `automation_status` in upper or mixed case | stored lower-case (`P1` as `high`) | stored as sent |
| `GET /test-management/plans?status=` or `GET /search?status=` with a value that is not a status | 422 | 200 with an empty list |

Existing usernames that contain `@` keep working for sign-in.

### Failure classification without a model

- **Auto mode classifies with rules when no model answers.** A model service
  that does not resolve, refuses the connection or is refused by the provider
  policy now counts as unavailable, and a model call that fails falls back to
  the rules engine. Failures analysed before the upgrade keep their "Analysis
  failed" text until they are analysed again.
- **Deep investigation runs in Rules mode.** `POST /deep-investigate/{run_id}`,
  the bulk deep trigger and the estimate's gate no longer refuse Rules mode
  (`reason_code` `rules_mode` is gone); only the Settings › AI toggle refuses.
- **History is read from the test's own project, last 30 days.** A test that
  passed and then failed every run since one change reads Product Bug, not
  Flaky. Categories, confidence and the flaky flag of re-analysed failures can
  change.
- **Migration `0258`** adds `test_cases.failure_category_source` and marks test
  cases with a human correction as `human`. The pipeline now replaces an earlier
  analysis's category on the test case but never one a person set. Categories
  set with the bulk Classify before the upgrade are not marked and can be
  replaced by a later analysis.

| Call | Now | Before |
|---|---|---|
| `POST /deep-investigate/{run_id}` in Rules mode | accepted | 422 "requires LLM or Auto mode" |
| `POST /analyze` for a test with a human correction | returns the correction | the engine's answer, replacing the correction |

### The local model path (batch 7)

- **`AI_TIMEOUT_SECONDS` in `.env.example` is now 300** (the code default). An
  install that copied the old example keeps 120 s until it changes the value.
- **A local provider analyses one failure at a time** unless
  `LLM_MAX_CONCURRENT_ANALYSES` is set; set it on a GPU host to keep parallel
  calls (at most 3 is used).
- **`POST /api/v1/test-management/plans/ai-create/async`** now answers 400 when
  the project has no approved cases and 409 when no model can answer, instead of
  queueing a task that cannot succeed; the task itself ends as `failure` with a
  sentence rather than `success` with an error object.
- **Agent pipelines** (`GET /api/v1/agents/pipelines...`) gain an optional
  `stage_quality_detail` for degraded pipelines.
- **The CLI's `ci-verdict` table** no longer prints `Class Name`; use `-o json`
  for it.

### Follow-ups to batches 5 and 6

- **A history import queues two more tasks** after it commits, both scoped to
  the imported project: `recompute_flaky_scores` and `take_coverage_snapshot`
  (which now takes an optional `project_id`). Workers that route tasks by name
  need no change; both run on the `default` queue.
- **Agent-proposed quarantines** (`detection_method: "agent_action"`) record no
  `proposed_by_user_id`; the approving person is still `approved_by_user_id`.
- **Sentences the API returns changed wording**: agent-action outcomes and the
  `review_not_accepted` error ("Accept the run report first ..."), the review
  gate's audit lines ("runs' reports would have been withheld"), the
  observability card header, the `analysis.retried` / `analysis.cancelled`
  activity sentences and the analysis-compare messages ("has no analysis to
  compare"). Codes and fields are unchanged; match on those, not the text.
- **Guide anchors renamed**: `#ai-reports` is now `#run-reports`,
  `#ai-history` `#analysis-history`, and
  `#root-cause-ai-deep-investigation` `#root-cause-deep-investigation`.

### Wording and small UI (batch 6)

- **Plan items** (`GET /api/v1/test-management/plans/{id}/items`) gain an optional
  `test_case_title`; null when the case no longer exists.
- **Quarantine conflicts** (approve, reject, release on a request in the wrong
  state) still answer 409, with a sentence that says where the request stands
  ("This request is quarantined now, so it cannot be approved. ...") instead of
  "Cannot approve from status QUARANTINED". Scripts that matched the old text
  should match the status code.
- **Runs list:** the "AI verdict" column and filter are now "Verdict"; the
  values are unchanged.
- **Embedding index** (`GET /api/v1/settings/ai/embedding-index`, and the same
  body in `GET /settings/ai`) gains `embedder_available`: whether this server
  can load the built-in embedder in use; null for Ollama and OpenAI.

### Counts that agree (batch 5)

- **Migration 0260** adds a nullable `proposed_by_user_id` to flaky-test
  quarantine requests. Existing rows keep it empty: a manual proposal made
  before the upgrade reads "Proposed manually", a detected one "Detected
  automatically".
- **The suite catalogue (`GET /api/v1/test-management/suites`) counts each
  test under its own suite.** A multi-suite upload used to file every test
  under the run's largest suite, so that suite's `test_count`, `passed_count`,
  `failed_count` and `pass_rate` described the whole run. Expect those numbers
  to drop for the largest suite of mixed uploads. Authored (managed) cases are
  no longer added to `test_count` or used as `last_run_at`; they are counted in
  the new `authored_count`. A suite with only authored cases has `test_count`
  0 and a null pass rate and last run.
- **Trends and the Home tiles ignore days before a project's first run.** The
  trend summary gains `first_run_day` and `history_days`. A tile trend is
  `null` with `trend_direction: "none"` when the previous window is not fully
  inside the project's history or held fewer than 3 runs.
- **Value metrics › By team** carries `available` and
  `insufficient_data_reason`, the same as the Overview headline; hours are
  still in the body but the page hides them while the headline is withheld.
- **Library health scores automation against the project's target**
  (`min(100, share / target × 100)`), so scores rise for libraries near their
  target. A library with fewer than 10 cases is tagged "Getting started"
  instead of being rated.
- **Inbox:** the per-test "×N" count no longer depends on who is looking; it
  counts the test's failures in the window for every viewer.
- **Flaky tests › Refresh** also queues a flakiness-score recompute for that
  project, and a project's first finished run records its first coverage
  snapshot.

### Permissions, configuration and admin (batch 4)

- **`.env.example` no longer sets sample integration and SMTP values.** The 17
  placeholder lines are commented out. Known sample values (for example a
  `your-...` token or an `example.com` address) now read as "not configured"
  wherever the app decides whether an integration or SMTP is set up, so a new
  install no longer shows "Password saved." or "A webhook is set". If you
  copied the sample file unchanged, those integrations now show as off; set
  real values to turn them on. A release install's generated `.env` carries an
  installer banner instead of "DO NOT use this file in production".
- **The Compose front end proxies `/api-docs` and `/health/`** to the API, so
  `http://localhost:3000/api-docs` works. `/metrics` is still not proxied.
- **Lists in "All Projects"** (test plans, strategies, gate policies) show the
  rows of every project you belong to; they were empty for everyone but
  admins. Naming a project you are not a member of is still a 403.
- **QA engineers can open Releases, read-only.** A QA engineer opens the
  Releases page and a release, with no write controls; the Gate policies tab
  stays QA lead only. Viewers and testers are still sent to the overview, and
  a release notification stays plain text for them.
- **An admin's default QA lead password reset re-enables the account**
  (after a restore, for example). A project QA lead's reset of a turned-off
  account is a 409 and changes nothing, so it cannot undo an admin's
  deactivation. Any reset on an archived project is a 409. The answer adds
  `is_active`.
- **A claim correction on a decision report needs QA_ENGINEER** in the run's
  project (403 below it). Rating a report stays open to every member.
- **Integration health and AI configuration speak plainly.** Probe errors read
  "Can't reach the host..." instead of errno text, the detail goes to the
  server log, and the model status has a new `not_used` code when a provider is
  not used. The Ollama probe is skipped when nothing uses Ollama.

| Call | Now | Before |
|---|---|---|
| `PATCH` a test plan's `status` | 200 (one of the five statuses, else 422) | 500 |
| `POST /onboarding/{project}/skip`, `/restore` | QA_LEAD | any member |
| `POST /onboarding/{project}/complete` (steps other than `view_intelligence`) | QA_ENGINEER | any member |
| `POST /projects/{project_id}/fix-outcomes` | QA_ENGINEER | any member |
| Deep investigation or admin maintenance, queue unreachable | 503 with a plain sentence | 500 with the exception text |
| Legacy Jira issue create, Jira error | 502 with a plain sentence | 500 with the exception text |

A run's release change and a suite move now write `run.release_changed` and
`suite.cases_moved` entries to the Activity log. The audit dashboard shows a
live account's current name on every row; the name stored at the time is kept
as `actor_name_recorded`.

### Results, history and run names (batch 3)

- **Charts, dashboard windows and the Runs list window use a run's execution
  time** (`coalesce(start_time, created_at)`) instead of its upload time. A
  back-filled run moves to the day it ran. Migration `0259` adds the matching
  index `ix_test_runs_project_run_time` (built `CONCURRENTLY`). The Summary
  report still windows on upload time. In chart-rows answers, `created_at`
  now holds the run time.
- `POST /api/v1/ingest` accepts `executed_at`, and its `task_id` can be polled
  at `GET /api/v1/ingest/uploads/{task_id}` (it always answered 404). A batch
  for a deleted build fails at once with `run_deleted` instead of retrying; a
  failed batch leaves a `run.ingest_failed` activity and its queued object is
  deleted. On `POST /ingest/file`, a `commit_range` that is not JSON is a 422.
- The regression diff's baseline is the newest earlier passing run on the
  same branch by execution time; its answer adds `baseline_kind`,
  `baseline_threshold` and `first_failing_test`. The bisect compare can answer
  `state: "supplied"` with the commits stored with the builds.
- Project export keeps commit SHAs, `sha256:` digests and long file paths
  instead of redacting them. Real tokens are still redacted.
- Search returns one hit per test (with its run count), not one per execution.
- **MCP tools default to a 30-day window** (was 7) to match the API, and say
  which window they read. `testlookup runs list` gains `--days` (default 30).
- The CLI warns when a saved profile overrides `TESTLOOKUP_API_KEY` or
  `TESTLOOKUP_URL`; `ci-verdict` also takes the run id as an argument.

### Overrides, reports and approvals (batch 2b)

- An override now queues a snapshot rebuild on the `default` queue; the Runs
  list shows the override without opening the run.
- `executive_panel.status_signal` and `layer2_incident.release_impact` are the
  run's stored decision or `null`; they no longer default to `CONDITIONAL_GO`.
- `ReleaseCouncilResponse` adds `project_id`. Phase gate results add
  `release_blockers` and `exit_criteria_evaluated` (always `false`).
- Compliance pack verify summaries name manifest.json and signature.json
  beside the file count.
- Pipelines in Rules mode stage no report review; existing "Offline Report"
  review rows are left as they are.

### Release verdicts (score model 2)

- **A run with an open critical or high defect reads No-Go**, as its release
  already did. The defect count is the project's open defects, so one open high
  defect holds every run of the project until it is resolved or reclassified.
- **A run with an unquarantined failure not classed flaky or infrastructure
  reads at best Conditional Go.** Quarantine a known flaky test, or correct its
  category, to clear it.
- Open defects weigh by severity in `user_impact`; risk scores of runs with open
  high or critical defects rise.
- Decisions stored before the upgrade keep score model 1 and evaluator v2, and
  their policy replay reports the version difference rather than re-judging
  them. Run a deep investigation again to store a model 2 decision.
- `POST /release-gate-policies/simulate` adds `release_readiness_band` and
  `band_downgrades`; `original_recommendation` is now the verdict the gate shows
  (with the active policy's bands), not the stored one.

### Outbound calls, MCP and the Fixer

- **Custom LLM endpoint.** If `openai` or `openrouter` points at a gateway (a base URL that is not the provider's own) and the key is only in the environment, store the key in Settings > AI Configuration, or set `OPENAI_API_BASE` / `OPENROUTER_BASE_URL` in the environment to the gateway. Otherwise model calls to that endpoint are refused.
- **Knowledge sources.** `internal_url` sources stop syncing in offline mode and must be on the domain allow-list when one is set. Removing a domain from the allow-list now also stops existing sources on it. A Jira or Confluence source whose stored `external_id` is not an issue key or page id takes the id from its URL, and fails to sync if the URL has none.
- **Fixer.** Suggest mode opens pull requests only with the `docker` runner. With `workflow_dispatch`, attempts end as errors marked "baseline only". Docker command templates that write outside `/work` and `/tmp` fail under the read-only root filesystem.
- **OpenShift.** For a project with a saved namespace, reports naming another namespace are no longer enriched with pod data.
- **CI job link.** A `ci_run_url` that is not an http(s) address is dropped. Runs are then matched by job and build number, as they are when no link is sent.
- **MCP.** Put the MCP env file next to `server.py` or name it in `TESTLOOKUP_ENV_FILE`; a `.env` in the working directory is no longer read. Clients that parse resource bodies read the previous body under `data`. Ids passed to tools must be the UUIDs the list tools return.
- **`GET /settings/ai/model-status`.** `ollama_error` is one of `refused_by_policy`, `unreachable`, `unexpected_status`, `invalid_response`.

### Live stream, knowledge sources and logs (security review of 20 November 2026)

- **Live-session SSE.** A client that reads `GET /api/v1/stream/sse/{project_id}?token=...` gets 400. Send the same access token as `Authorization: Bearer <token>` and drop the query parameter. `Last-Event-ID` and the events are unchanged. The web UI uses the WebSocket and is not affected.
- **Confluence knowledge sources stop syncing until you approve their spaces.** As an administrator, send the space keys once:

  ```bash
  curl -X PUT "$TL_URL/api/v1/knowledge-sources/governance/confluence-spaces" \
    -H "Authorization: Bearer $TL_TOKEN" -H "Content-Type: application/json" \
    -d '{"default": ["QA", "DOCS"], "projects": {"<project id>": ["TEAM"]}}'
  ```

  `default` applies to every project; a project listed under `projects` uses its own list instead. `*` in a list means any space the Confluence account reads. Until a space is approved, creating a Confluence source answers 422, and the next sync of an existing source fails with the reason and removes that source's indexed content from knowledge search. After you approve the space, **Sync** on the source indexes it again.
- **Jira knowledge sources.** A source whose issue was moved to another Jira project fails its next sync, and its indexed content is removed from knowledge search; recreate the source with the issue's current key. An epic source no longer includes child issues from other Jira projects.
- **Frontend access log format.** The request is logged as method, path and protocol, without the query string, and the Referer field is gone; three fields are added at the end (`rt=`, `us=`, `urt=`). The nginx error log is at level `crit`. Update a log parser that expects nginx's combined format.
- **Logs you already have.** Access logs written before this release can hold report share tokens (in `/api/v1/shared/reports/...` paths) and, if a client used the SSE route, access tokens. Access tokens expire on their own (30 minutes by default). Restrict or delete those logs, and revoke share links that were open while the logs were readable by people who should not open the reports.

### Install and deployment files

- **Installer default.** `install.sh` with nothing set installs the latest published release, with that release's images, instead of `main` and `:latest`. To install the unreleased tip as before, set `TL_REF=main`. A mirror sets `TL_LATEST_RELEASE_URL`, or names the release with `TL_REF=vX.Y.Z`.
- **First admin without a password.** The release stack with an empty `INITIAL_ADMIN_PASSWORD` no longer creates an admin with a password printed in the `db-migrate` log; it creates none. Set `INITIAL_ADMIN_PASSWORD` in `.env` (the installer does), or run `docker compose -f docker-compose.release.yml exec backend python /app/scripts/create_admin.py` in a terminal, which shows a generated password once.

- **Redis password in Compose.** `docker-compose.release.yml` and the single-VM stack refuse to start without `REDIS_PASSWORD`. Installs made with `install.sh` or the offline bundle already have one. For a hand-made `.env`, set `REDIS_PASSWORD` and put it in `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` (`redis://:<password>@redis:6379/0`), then restart the stack.
- **Request body limit.** A request body over 10 MiB (100 MiB on the ingest and import routes) answers 413. Raise `REQUEST_BODY_MAX_BYTES` / `REQUEST_BODY_UPLOAD_MAX_BYTES` if you send larger ones, and keep them at or above your proxy's limit.
- **Installer and release tags.** `TL_REF=v0.1.0` alone now also selects the `0.1.0` images (it used to run `:latest`), and the files are checked against the release's `SHA256SUMS`.
- **Cloud ingresses.** EKS, GKE and AKS publish `/health/live`, `/health/ready`, `/health/version` and `/health/details` only. A monitor that called another `/health/...` path through the ingress must use one of these or the Service directly. GKE answers http with a redirect to https.
- **`dev` overlay.** Its Ingress uses the `nginx-internal` class; reach a dev deploy through that class or `kubectl port-forward`.
- **NetworkPolicy.** Check that your cluster enforces NetworkPolicy (EKS: the VPC CNI setting; AKS: a network policy option; GKE: Dataplane V2 or network policy). On EKS, read the guide before setting `enable_network_policy = true`: the load balancer's addresses must be admitted to the frontend and backend first.

### Health routes, rate limits, CLI and SDKs

- **Monitoring.** A monitor that read `/health/details` or `/health/ingestion` without a credential gets 401. Send an API key in `X-API-Key`, or use `/health/ready` (public; it names the data store that is down).
- **Rate limits in Redis.** In production and staging the API opens a second, synchronous Redis connection for rate-limit counters. `RATE_LIMIT_SHARED_STORAGE=off` keeps per-process counters. Sign-in answers 429 after 20 attempts a minute for one account name (`LOGIN_ATTEMPTS_PER_USERNAME_PER_MINUTE`).
- **`ci-verdict` in pipelines.** A step that ran `testlookup ci-verdict` right after an upload, with no `--timeout-seconds`, now exits 2 while the run is still in progress. Add `--timeout-seconds N` to wait for the run, or `--allow-partial` to keep the old behaviour.
- **`testlookup auth logout`** also removes the API key stored in the profile. Scripts that expected the key to remain must set it again.
- **JS and Go SDKs.** An endpoint that answers with a redirect to another host, port or scheme (for example http to https) now fails with a message that names the target. Configure the final address.
- **Bucket-drop ingestion.** A dropped build with a file over 50 MiB, or over the result limit, is refused. A project whose slug is `uploads` no longer has its dropped builds ingested.
- **Failure clusters.** The stored `failure_clusters` vector collection is no longer written; an existing one stays until it is dropped with its collection generation and can be deleted.
- **Shared report pages** are sent with `Cache-Control: no-store`, so proxies and browsers no longer cache them.

### Single sign-on, SCIM and Jira

- **SSO and existing accounts.** A person whose first SSO sign-in carries the e-mail of an account that already exists is refused until an administrator turns on "Link existing accounts by verified e-mail" on the SSO configuration (Settings, SSO). Administrator accounts are never linked and keep signing in with their password. With SAML the NameID must be the account's e-mail for a link.
- **SAML needs HTTPS.** Sign-in is tied to the browser with a `Secure` cookie. On a plain-HTTP site set `SAML_BROWSER_BINDING=false`, or `/sso/login-url` answers 400. The ACS URL and the OIDC redirect URI (from `SAML_BASE_URL`) must be on the host the browser uses for the API, and a proxy of your own must pass `Cookie` and `Set-Cookie` for `/api/v1/sso`. A sign-in in flight during the upgrade fails once.
- **SAML assertions.** An identity provider whose assertions lack `Recipient` or `NotOnOrAfter`, or stay usable for more than 10 minutes with no shorter `SubjectConfirmationData` time, is refused. Shorten the lifetime at the provider or raise `SAML_MAX_ASSERTION_LIFETIME_SECONDS`. Test a sign-in after the upgrade while the admin fallback is on.
- **SCIM with an unbound token.** Accounts that were created in TestLookup (not over SCIM) and have never signed in through SSO are invisible to it: a `userName eq` filter returns nothing and the provider's follow-up create fails with 409. Bind the token to the SSO configuration and have those people sign in through SSO once, or manage those accounts in TestLookup. Renames and e-mail changes pushed with an unbound token fail with 403. Do not prune `identity_events` by hand: the `SCIM_USER_CREATED` rows mark the accounts such a token manages.
- **SCIM and the last administrator.** A provider that tries to deactivate or delete the only active administrator gets 409. Make a second administrator active first.
- **Jira keys.** Before upgrading, an administrator should set the Jira key on each project (Projects, edit) whose team files into a Jira project other than the default. Afterwards QA engineers on a project without a key can file and link only in the default Jira project, and QA leads can no longer set the key. One key per project: for a team that files into several Jira projects leave the key empty and have a QA lead file the exceptions. A pending approval that carries another Jira project is approved without a ticket and shows the reason.
- **Jira knowledge sources.** A Jira issue or epic source is accepted only for an issue in the Jira project the project may use. On a project with a Jira key, existing sources that point at another Jira project stop syncing and show the reason; the text they already brought in stays searchable until you archive the source. Archive them, or have an administrator set the project's Jira key to the Jira project they belong to. An epic source on such a project no longer brings in child issues from other Jira projects at its next sync.
- **API clients.** New 422 answers on the Jira filing, linking and release-sync routes; 403 from the Jira picker route for viewers and testers; the Jira status sync result has an `out_of_scope` count; `/sso/login-url` and `/sso/oidc/login` can answer 429.

### Cluster installs (K3s, OpenShift, Kubernetes)

- **K3s `--skip-build`.** A build now records the image digests it pushed in `.k3s-build-records`, beside the credentials file, and `--skip-build` deploys from that record. A tag built before this release has no record, so the first `--skip-build` after upgrading stops with the choices: build once without `--skip-build`, supply `K3S_BACKEND_DIGEST`, `K3S_FRONTEND_DIGEST` and `K3S_MCP_DIGEST`, or accept the registry's answer for that run with `K3S_TRUST_REGISTRY_DIGESTS=true`. If you deploy from more than one checkout, point `K3S_CREDENTIALS_FILE` (or `K3S_BUILD_RECORD_DIR`) at one shared place so they see the same records.
- **K3s deploy output.** The script no longer prints the database, object-store or admin password. Read them from the credentials file (`k3ssetup/.k3s-credentials` by default) or from the Secret `testlookup-secrets`. If an earlier deploy's output was saved somewhere other people can read (a CI log, a ticket), change the passwords it shows.
- **K3s with TLS.** If you serve TLS for the site, set `K3S_PUBLIC_SCHEME=https` in your site file. Deploys then keep your redirect Ingress and Middleware and write `https://` as the public URL and CORS origin. Without it the script behaves as before, except that it no longer deletes the Secret `testlookup-tls-cert`.
- **Generic Kubernetes and OpenShift.** A generated admin password is no longer shown, on a terminal or in the summary. It is in the Secret `testlookup-initial-admin`; the script prints the command that reads it. Delete that Secret after the first sign-in.
- **Kubernetes restore.** `verify` refuses an archive that holds anything but `manifest.json` and the three component files. Archives written by the backup CronJob pass; an archive you repacked by hand (an extra file, a `./` prefix on the names) must be repacked with exactly those four names.
- **EKS.** If you keep your own copy of the `aws-eks` overlay, add the two new rules that admit the load balancer to `allow-frontend` and `allow-backend` before you set `enable_network_policy = true`.

- **K3s.** The next `k3ssetup/deploy-k3s.sh` run adds Redis and MongoDB passwords to `testlookup-secrets` and to the credentials file, creates the MongoDB root user, restarts both stores with authentication and rolls the application pods. Take a backup first. The script checks that the Traefik pods carry the label `app.kubernetes.io/name=traefik` and stops before changing anything if they do not. Do not delete `testlookup-secrets` to start clean: the MongoDB volume keeps its user.
- **OpenShift.** The first deploy with the new script needs cluster-admin, restarts every data store once and adds a Redis password. Afterwards remove the old grant: `oc adm policy remove-scc-from-user anyuid -z default -n <namespace>` (or one run with `REVOKE_DEFAULT_ANYUID=true`). An offline-bundle install must grant `anyuid` to `testlookup-anyuid` before applying the new manifests, and should add `REDIS_PASSWORD`, `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` to its Secret.
- **Frontend container port.** nginx listens on 8080 inside the container. If you publish or route to the container port yourself (your own `ports:` line, a numeric `targetPort`, a pod NetworkPolicy), change the container side from 80 to 8080, and keep the Compose file and the frontend image on the same version. A file of your own in `/etc/nginx/conf.d` must `listen 8080`.
- **Read-only containers.** A custom patch that replaces a whole `volumes` or `volumeMounts` list on the TestLookup Deployments removes the new `emptyDir`s; append with `/-`. A pod of your own in the namespace that uses `hostPath`, a host namespace, a host port or privileged mode is refused by the `baseline` rule when next created.
- **Backups.** With MongoDB authentication on, `MONGO_URI` in the Secret must carry the credentials (the deploy scripts write it that way).

### Development starts need real secrets

With `APP_ENV=development` (the default when the variable is unset) the API
refuses to start while `JWT_SECRET_KEY` is the placeholder from the source.
`scripts/gen-dev-env.sh` writes generated secrets; `ALLOW_INSECURE_DEV_SECRETS=true`
starts anyway and is only for a machine nobody else can reach. Production and
staging already refused placeholder secrets.

## 0.1.0 — the first public release

0.1.0 is the first tagged release. If you run a snapshot built from the
repository before this release (any commit before the `v0.1.0` tag), these
changes apply.

### Redis → Valkey 8

The bundled broker and cache is now **Valkey 8.1** (BSD-3-Clause) instead of
`redis:7-alpine`. Settings (`REDIS_*`, `CELERY_*`) and `redis://` URLs are
unchanged.

Valkey cannot load the data files written by Redis 7.4 or later (Valkey
reads Redis 2.x–7.2 data), and `redis:7-alpine` was 7.4 running with
append-only persistence, so Valkey would refuse to start on the old volume.
TestLookup keeps working state in Redis — Celery queues and results, live-run
buffers (drained to PostgreSQL every 30 seconds), live session tokens, the
dead-letter queue, the token-revocation list, leases, rate limits and caches;
the durable records live in PostgreSQL and MongoDB. Before switching:

1. Stop new work: pause CI uploads and let live runs finish.
2. Let the workers drain (`celery -A app.worker.celery_app inspect active` and
   `inspect reserved` are empty) and wait at least 30 seconds after the last
   live run.
3. Export anything you want from the dead-letter queue
   (`GET /api/v1/admin/maintenance/dlq`).
4. Stop the stack and **remove the old Redis data volume** (Compose:
   `docker volume rm <project>_redis_data`; Kubernetes: the `redis-data` PVC)
   so Valkey starts empty.
5. Start the new version.

Starting empty means: access tokens revoked by a logout or a password change
are accepted again until they expire (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`,
720 by default in 0.1.0) — rotate `JWT_SECRET_KEY` during the upgrade to force everyone to
sign in again if that matters; a promoted fine-tuned model falls back to
`LLM_MODEL` until it is promoted again; the next search-index pass re-scans from
the start; live sessions that were open must reconnect.

To keep the old server instead (its license is then your choice), pin it:
Compose — a `docker-compose.override.yml` for the `redis` service that sets
`image:`, `command:` (`redis-server ...`) and the healthcheck (`redis-cli
ping`), because the new Compose files call `valkey-server` / `valkey-cli`;
single-node K3s — a site patch that sets the `testlookup-redis` container image
back (its command and probes still use the `redis-*` names); OpenShift with
Artifactory — the image plus the command and probes.

### MinIO-compatible server → versitygw

The bundled S3-compatible store is now **versitygw** (Apache-2.0) instead of
the AGPL-licensed `pgsty/silo` server and `pgsty/mc` client. `MINIO_*`
settings and the `minio` / `testlookup-minio` service names are unchanged.
versitygw stores objects as plain files under the `s3/` sub-directory of the
same volume, so it never reads or modifies the old server's files, and it
starts **empty**. Copy your objects across once:

Compose:

1. `docker compose up -d` with the new version (versitygw starts, and the
   setup service creates the empty buckets).
2. Run the old server once more on the old data, on the same network:

   ```bash
   docker run -d --name legacy-s3 --network <project>_testlookup_net \
     -v <project>_minio_data:/data \
     -e MINIO_ROOT_USER=<MINIO_ACCESS_KEY> -e MINIO_ROOT_PASSWORD=<MINIO_SECRET_KEY> \
     <the image you ran before> server /data
   ```

   (PGSTY Silo images name the binary `silo`: `... silo server /data`.)
3. Copy every object across with the backend image's setup script:

   ```bash
   docker compose run --rm \
     -e S3_COPY_FROM_ACCESS_KEY=<MINIO_ACCESS_KEY> -e S3_COPY_FROM_SECRET_KEY=<MINIO_SECRET_KEY> \
     minio-setup python scripts/s3_setup.py --copy-from-endpoint http://legacy-s3:9000
   ```

   It skips objects that are already present with the same content, so it is
   safe to re-run; a failed object makes it exit non-zero.
4. `docker rm -f legacy-s3`. Once you have checked the new store, everything
   on the volume except `s3/` can be deleted.

Kubernetes: run the same copy from a one-off Pod on the node that holds the
`minio-data` PVC, with the old server and the backend image
(`python /app/scripts/s3_setup.py --copy-from-endpoint http://127.0.0.1:9000`,
credentials from `testlookup-secrets`) as two containers.

Kubernetes / K3s installs that cannot migrate yet can pin the previous server
with a site patch on the `testlookup-minio` Deployment (see
[docs/operations/deploy-k3s.md](docs/operations/deploy-k3s.md#site-patches)).
The legacy bucket-notification webhook (`make setup-minio`) is not configured
for versitygw: the default ingest path never needed it.

### MongoDB in the offline bundle

The air-gapped offline bundle no longer contains the MongoDB image (it is
licensed SSPL-1.0). Mirror `mongo:7` into your registry yourself before running
`import-bundle.sh`; see
[user-guide/air-gapped-install.md](user-guide/air-gapped-install.md).

### Chroma's data volume

`chromadb/chroma` 1.x keeps its data in `/data` (`persist_path` in the image's
`/config.yaml`); the Chroma 0.x settings `IS_PERSISTENT` /
`PERSIST_DIRECTORY` are ignored. The manifests mounted the volume at
`/chroma/chroma`, so a 1.x server wrote its collections into the container
and lost them whenever the container was recreated.

The volume is now mounted at `/data`: Compose uses a new named volume,
`chroma_data_v1`, and Kubernetes mounts the same claim on a fresh `chroma-1x`
sub-path. Nothing is deleted: the old `chroma_data` volume, and the 0.x files
at the claim's root, stay as they were. Remove them once you no longer need
them.

Chroma holds derived data only, so after upgrading:

- **Semantic search:** reindex each project with
  `POST /api/v1/search/reindex?project_id=<id>&full=true` (QA lead or admin
  token).
- **Knowledge sources:** sync each source again
  (`POST /api/v1/knowledge-sources/<id>/sync`).
- **Analysis and semantic caches** refill as they are used.
- **Duplicate and defect indexes and agent memory** repopulate as new runs,
  defects and investigations arrive.

### Default local model

The default `LLM_MODEL` is now `qwen2.5:7b-instruct` (Apache-2.0) instead of
`qwen2.5:3b-instruct-q5_K_M` (Qwen Research License, non-commercial). If you
never set `LLM_MODEL`, pull the new model before upgrading
(`ollama pull qwen2.5:7b-instruct`, or `make pull-llm`), or set `LLM_MODEL` to
the model you already have. It needs about 5 GB of disk and 6–8 GB of RAM
during inference; see [user-guide/offline-model-pack.md](user-guide/offline-model-pack.md).

### Feature flag `manual_upload`

New installs start with manual upload **on**. An existing install keeps its
stored value; turn it on in **Settings → Feature flags** if you want the Upload
button.

### Fine-tuning switch moved to the admin API

**Settings → AI** no longer shows the **Fine-Tuning Pipeline** toggle.
Fine-tuning itself is unchanged and stays off by default: an administrator
turns it on or off with `PUT /api/v1/settings/ai` and
`{"finetune_enabled": true}` (or `false`), and `GET /api/v1/training/status`
shows the effective value and where it comes from. A value stored with the old
toggle is kept, and saving the page never changes it. See
[docs/operations/fine-tuning.md](docs/operations/fine-tuning.md).

### Admin accounts and deploy scripts

No script creates an admin with a built-in default password any more. On a
fresh install the deploy generates a random password, shows it once and asks
for a change at first sign-in. **An existing admin is never modified** by a
deploy. If you relied on the old default password anywhere (scripts, tests,
bookmarks), change that account's password now.

A release install (`install.sh`) created before 0.1.0 used the local/demo
`.env`: `APP_ENV=development` with passwordless dev-login. Upgrading keeps your
`.env`, so it keeps that behaviour. To switch to the secure defaults, set
`APP_ENV=production` and `DEV_AUTO_LOGIN_ENABLED=false`, add a strong
`INITIAL_ADMIN_PASSWORD`, and restart: the `db-migrate` service creates the
`admin` account if none exists yet.

### Single-node K3s (`k3ssetup/`)

Site-specific values moved out of the repository into a local env file
(`TESTLOOKUP_K3S_ENV`, template `k3ssetup/k3s.env.example`). Before
your next deploy, create that file with at least the host your install serves
(`K3S_HOST`) and any registry address you used; put the AI provider settings
your overlay used to carry into a site patch (`K3S_PATCHES_DIR`). If the file
is missing, the deploy stops before changing anything because the host would
change. The `make k8s-stop-homelab` / `k8s-restart-homelab` targets were
removed (their scripts were never in the repository).

The path itself was renamed from `homelabsetup/` and `k8s/overlays/homelab/`
to `k3ssetup/` and `k8s/overlays/k3s/`: run `./k3ssetup/deploy-k3s.sh` (was
`deploy-homelab.sh`) and `make k8s-deploy-k3s` (was `k8s-deploy-homelab`), and
point any script of your own at the new paths. Nothing else needs to change:

- `TESTLOOKUP_HOMELAB_ENV`, `~/.config/testlookup/homelab.env` and
  `HOMELAB_NODE<n>_PASS` are still read when their new names
  (`TESTLOOKUP_K3S_ENV`, `k3s.env`, `K3S_NODE<n>_PASS`) are not set;
- an existing `homelabsetup/.homelab-credentials` is still used (git leaves
  the ignored file in place), so no second admin password is generated;
- the rendered manifests keep every resource name, so the next deploy
  updates in place (only comments inside two ConfigMaps change).

### Kubernetes, Terraform and dev-login defaults

- **`testlookup-secrets` is no longer rendered** by `k8s/base` (it held placeholders). Create the Secret before `kubectl apply -k`, as the deploy guide and `scripts/deploy-k8s.sh` already required. The Secret in your cluster stays: `kubectl apply -k` deletes nothing it no longer renders unless you apply with `--prune`.
- **Backend and worker egress excludes the cloud metadata service.** If those pods get cloud credentials from it (an EC2 instance role, GKE Workload Identity, an Azure IMDS managed identity), add an egress rule for it to `allow-backend` and `allow-workers` in your overlay; see [network policies](docs/operations/deploy-kubernetes.md#network-policies). IRSA, EKS Pod Identity and Azure Workload Identity are not affected.
- **`public_access_cidrs` is required** by `infra/aws/terraform`: set your office or VPN CIDRs in your tfvars file (Terraform asks for it otherwise). To keep the old, internet-wide endpoint, set `["0.0.0.0/0"]` explicitly.
- **`DEV_AUTO_LOGIN_ENABLED` defaults to `false`.** The development stacks write `true` to `.env`, so `make dev`, `make dev-lite` and Codespaces are unchanged. If you start the API another way and use Quick Login, set `DEV_AUTO_LOGIN_ENABLED=true` with `APP_ENV=development`.
- **The dev-lite MongoDB requires a password** (`MONGO_USER`/`MONGO_PASSWORD` from `.env`, as in `docker-compose.yml`). MongoDB creates the user only on an empty volume, so remove the old MongoDB volume once, which deletes the local test logs (PostgreSQL's volume stays): `make dev-lite-stop`, `docker volume rm testlookup_lite_mongo_data`, then `make dev-lite`. An `.env` from before `MONGO_PASSWORD` existed needs it added (`scripts/gen-dev-env.sh` writes it for a new `.env`).

### Go reporter module path

The Go reporter's module path is `github.com/test-intelligence/testlookup/client/go`; it was `github.com/testlookup/testlookup-go`, which nothing served, so every user had a `replace` directive pointing at a clone. Rename the path in your `go.mod` (`require` and `replace`) and in your imports:

```go
import "github.com/test-intelligence/testlookup/client/go/testlookup"
```

You can drop the `replace` directive and run `go get github.com/test-intelligence/testlookup/client/go@main` (or `@vX.Y.Z` once `client/go/vX.Y.Z` is tagged); keep it if you build from a clone or from your server's SDK Downloads ZIP.

### Install locations

`install.sh` and `docker-compose.release.yml` now use
`github.com/test-intelligence/testlookup` and
`ghcr.io/test-intelligence/testlookup`. An install created with the old
namespace keeps working only while those images exist; set
`TESTLOOKUP_IMAGE=ghcr.io/test-intelligence/testlookup` in `.env`.
