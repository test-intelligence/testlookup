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
  SAML 2.0 SSO and SCIM 2.0, retention and purge, feature flags, audit and
  activity logs, integrations (Jira, Slack, Microsoft Teams, email, GitHub
  checks, GitLab, outbound webhooks, Splunk, OpenShift).
- **Interfaces**: web UI with an in-app guide at `/docs`, REST API with
  Swagger at `/api-docs`, `testlookup` CLI, an MCP server for AI assistants.
- **Deployment**: Docker Compose (development, demo, release images,
  air-gapped override, monitoring add-on), Kubernetes overlays (generic,
  single-node K3s, OpenShift, EKS/GKE/AKS), an air-gapped offline bundle,
  backups and upgrades.

### Added

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
