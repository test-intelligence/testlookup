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
- **A GitHub Enterprise server is never mistaken for github.com.** The GitHub
  integration took any API base whose text contained `api.github.com` (for
  example `https://api.github.com.corp.example/api/v3`) for public GitHub, so
  commit links pointed at github.com and the fixer built its clone URL — which
  carries the project's token — against github.com. The host is now compared
  exactly.
- **SCIM user filters can no longer stall a request.** The `userName eq ...`
  parser backtracked quadratically on a long run of spaces (8,000 took
  ~110 ms); it is linear now and accepts exactly the same filters.
- The theme-token check (`npm run check:theme`) reads `--token : value` and
  no longer lets `--status-passeds` answer for `--status-passed`: a `\s` in a
  template literal had silently become the letter `s`.
- Triaged the open CodeQL high alerts: the four above are fixed (plus an e2e
  spec's URL regex now escapes every metacharacter); the rest are false
  positives or test-only code, with dismissal reasons in the pull request.
