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

### Removed

- Internal working material (run evidence, agent handovers, review registers,
  live probes against a private deployment, owner-specific CI and deploy
  pipelines, and three superseded copies of the user and install guides) is no
  longer part of the repository.
- The `make k8s-stop-homelab` / `k8s-restart-homelab` targets, which called
  scripts that were never in the repository.
- `scripts/setup-minio.sh` (`make setup-minio` now runs the Compose
  `minio-setup` service), and the MinIO bucket-notification wiring it set up:
  the default ingest path never needed it.

### Security

- No deploy script, seed or test carries a default admin password any more;
  every path generates one or requires it from the environment.
- Example configuration uses placeholders only (`qai_your_api_key_here`,
  `testlookup.example.com`).
- Security reports go through GitHub private vulnerability reporting
  ([SECURITY.md](SECURITY.md)).
