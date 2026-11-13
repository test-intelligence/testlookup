# Changelog

All notable changes to TestLookup are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may
contain breaking changes; [UPGRADING.md](UPGRADING.md) lists them).

## [Unreleased] — 0.1.0

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

### Deprecated

- `verifySsl` in the JavaScript reporter never had an effect and now warns; start Node with `NODE_EXTRA_CA_CERTS` to trust a private CA.
- The legacy feature-flag routes `GET/PUT/DELETE /api/v1/settings/flags[/{key}]` still work but answer with a `Deprecation` header and a `Link` to `/api/v1/feature-flags`.

### Removed

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
  - shell scripts are executable on Linux and macOS clones.
  - the K3s and OpenShift deploys create the object-store buckets when run from Git Bash on Windows, including with a Git Bash-style `KUBECONFIG=/c/...` (Git Bash rewrote the in-pod script path, so that step always failed there).
- Documentation: THREAT_MODEL.md describes the system that ships, the guides now say SAML single logout, OIDC and Kubernetes backups work, and the compliance guide no longer claims a legal sign-off.

### Security

- **Network exposure:**
  - the development stacks bind every port to `127.0.0.1` (`<SERVICE>_PUBLISH_ADDR` opens one on purpose);
  - the release stack keeps PostgreSQL, the S3 store and Ollama on loopback, and the single-VM template keeps the API there too;
  - the monitoring add-on binds to loopback, with Prometheus's unauthenticated lifecycle API off;
  - the public Kubernetes ingress and the OpenShift API Routes no longer route the unauthenticated `/metrics` (the OpenShift API host has one Route per backend path: `testlookup-api`, `-webhooks`, `-docs`, `-ws` and `-health`);
  - the bundled Ollama runs with `OLLAMA_NO_CLOUD=1`, so it no longer calls ollama.com at startup and every few hours or serves cloud models;
  - ChromaDB is reachable only by the backend and the workers.
- **Host-header path confusion (CVE-2026-48710):** the login rate limiter, the demo read-only gate, the forced password change, rate-limit keys and request logs read the routed path, so a crafted `Host` header can no longer skip the login rate limit; starlette 1.10 also fixes the underlying bug.
- **Weak secrets:** a production start refuses placeholder database, S3 and Redis passwords copied from the example files and a JWT, app or webhook secret shorter than 32 characters, `.env` is written with mode 600, `install.sh` says where the admin password is instead of printing it and runs nothing if its download is cut short, no script, seed or test carries a default admin password, and example configuration uses placeholders only.
- **Open sign-up and passwordless login:** self-registration is off by default and a sign-up is a Viewer (it was a role that can create API keys), and `install.sh` writes a production `.env` without dev-login.
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
- **Injection:** a project's Splunk index must be a plain name, Slack and Teams digest text is escaped (a test named `<!channel>` no longer pings the channel), and pasted agent input reaches the model only inside data markers that are stripped from the output.
- **ReDoS:** the PRD heading parser (CodeQL py/polynomial-redos) and the SCIM `userName` filter parser run in linear time, and two regexes in development tooling were fixed in the same CodeQL triage.
- **Sensitive data in logs:** Celery worker and beat logs go through the same redaction as the API, and twenty log lines that named a person by username or e-mail now log the user id.
- **Public bucket:** the unused, publicly readable `testlookup-attachments` bucket is no longer created.
- **Demo hardening:** `docker-compose.demo.yml` gives the app containers no route to the internet, memory, CPU and process ceilings, and `no-new-privileges`, and demo sign-in refuses admin, service and MFA accounts and is rate-limited.
- **Supply chain:**
  - the backend and MCP images install hash-locked Python requirements (`--require-hashes`, every transitive package pinned; `make lock-python` regenerates them);
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
